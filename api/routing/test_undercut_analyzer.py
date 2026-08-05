import random
import unittest
from fractions import Fraction

from undercut_analyzer import simulate, build_pool, quote, SCALE, simulate_two_pools


def _swap(ts, inp, out, usd=100.0, fee_bps=30, **kw):
    # Fields mirror what api/main.py builds: input = spent token amount,
    # output = received token amount. `forward` direction is decided by which
    # list the swap goes into, not by a field here.
    d = {"ts": ts, "input": inp, "output": out, "usd": usd,
         "fee_bps": fee_bps, "cid": 1, "protocol": "Uniswap V4",
         "pool_address": "0xREAL"}
    d.update(kw)
    return d


BASE = dict(range_pct=10.0, fee_pips=300, opening_px=__import__("fractions").Fraction(1, 1),
            p0_usd=1.0, p1_usd=1.0)


def _identical_clone_swaps(usd, cap, fee_pips, n_each=25):
    # Build an exactly competitive demand set against a hypothetical pool that is
    # an identical clone of the real pool (same tier, liquidity, range). The real
    # pool's recorded outputs are the clone's own quotes at each swap start, so
    # every swap ties the router; the deterministic tie-break must then split
    # traffic ~50/50.
    #
    # The swap order fwd, rev, rev, fwd repeating matters: it lets the clone's
    # pre-simulated drift path (serving the even-indexed swaps) alternate between
    # directions so the clone never drains in either direction.
    order = [True, False, False, True] * n_each
    pool = build_pool(cap, BASE["range_pct"], fee_pips,
                      BASE["opening_px"], BASE["p0_usd"], BASE["p1_usd"])
    s_cur = pool["s_open"]
    fwd, rev = [], []
    for i, is_fwd in enumerate(order):
        out_q, sq_next, _ = quote(pool, s_cur, is_fwd, int(round(usd * SCALE)))
        out_val = out_q / SCALE if out_q is not None else 0.0
        sw = _swap(i, usd, out_val, usd=usd, fee_bps=30)
        (fwd if is_fwd else rev).append(sw)
        if i % 2 == 0 and out_q is not None:
            s_cur = sq_next
    return fwd, rev


def _random_swaps(n, seed, vol_min=50.0, vol_max=50000.0):
    # Deterministic (seeded) realistic demand: random direction, uniform random
    # volume. Uniform volume keeps every swap within the pool's capacity so the
    # simulation exercises routing (not drain), unlike tiny uniform swaps which
    # produced an all-or-nothing split.
    rng = random.Random(seed)
    fwd, rev = [], []
    for i in range(n):
        usd = rng.uniform(vol_min, vol_max)
        sw = {"ts": i, "input": usd, "output": usd, "usd": usd, "fee_bps": 30,
              "cid": 1, "protocol": "Uniswap V4", "pool_address": "0xREAL"}
        (fwd if rng.random() < 0.5 else rev).append(sw)
    return fwd, rev


class TestUndercutDrift(unittest.TestCase):
    def test_forward_flow_drains_pool(self):
        # cap = $1000/token => roughly 1000 units of token1 reserve. Each forward
        # swap spends ~100 token0 and takes ~99.7 token1, so the pool drains after
        # ~10 swaps. With the drift model it must NOT serve all 100 swaps (it runs
        # out of token1), unlike the old arbitrage-backed model which served all.
        swaps = [_swap(i, 100.0, 99.0, fee_bps=30) for i in range(100)]
        res = simulate(1000.0, **BASE, swaps=swaps, total_usd=10000.0)
        self.assertGreater(res["div_count"], 0)
        self.assertLess(res["div_count"], len(swaps))
        self.assertEqual(res["reverse_count"], 0)
        self.assertGreater(res["fee_usd"], 0.0)

    def test_reverse_swaps_rebalance_and_restore_service(self):
        # Same forward drain, but interleave reverse swaps. The reverse swaps are
        # counter-direction demand that should (a) be served, and (b) rebalance
        # the pool so it can serve MORE forward swaps than the drained run.
        swaps = [_swap(i, 100.0, 99.0) for i in range(100)]
        drained = simulate(1000.0, **BASE, swaps=swaps, total_usd=10000.0)

        # Reverse swaps: spend end token (token1), receive start token (token0).
        revs = [_swap(i + 0.5, 100.0, 99.0, fee_bps=30) for i in range(50)]
        with_rev = simulate(1000.0, **BASE, swaps=swaps, reverse_swaps=revs,
                            total_usd=10000.0)

        self.assertGreater(with_rev["reverse_count"], 0)
        self.assertGreater(with_rev["reverse_usd"], 0.0)
        # Rebalancing should let the pool keep serving forward swaps.
        self.assertGreater(with_rev["div_count"], drained["div_count"])

    def test_not_competitive_not_diverted(self):
        # Real pool output == input (no slippage/fee) => hypothetical (which only
        # returns ~0.997*input) cannot beat it; nothing diverted.
        swaps = [_swap(0, 100.0, 100.0)]
        res = simulate(1000.0, **BASE, swaps=swaps, total_usd=100.0)
        self.assertEqual(res["div_count"], 0)
        self.assertEqual(res["div_usd"], 0.0)

    def test_two_sided_fee_revenue(self):
        # Reverse (counter-direction) swaps that divert must add to fee_usd.
        swaps = [_swap(i, 100.0, 99.0) for i in range(5)]
        revs = [_swap(i + 0.5, 100.0, 99.0) for i in range(5)]
        res = simulate(100000.0, **BASE, swaps=swaps, reverse_swaps=revs,
                       total_usd=500.0)
        fwd_only = simulate(100000.0, **BASE, swaps=swaps, reverse_swaps=[],
                            total_usd=500.0)
        # With a large cap nothing drains, so both directions fully serve.
        self.assertEqual(res["div_count"], 5)
        self.assertEqual(res["reverse_count"], 5)
        self.assertEqual(res["div_usd"], 500.0)
        self.assertEqual(res["reverse_usd"], 500.0)
        # fee_usd = (div + reverse) * 300/1e6 => two-sided.
        expected = (res["div_usd"] + res["reverse_usd"]) * BASE["fee_pips"] / 1_000_000
        self.assertAlmostEqual(res["fee_usd"], expected)
        self.assertGreater(res["fee_usd"], fwd_only["fee_usd"])

    def test_pool_seeded_at_opening_price_serves_small_order(self):
        # A small forward swap that the pool easily fills and that beats the real
        # pool's output should be diverted.
        pool = build_pool(1000.0, BASE["range_pct"], BASE["fee_pips"],
                          BASE["opening_px"], BASE["p0_usd"], BASE["p1_usd"])
        self.assertGreater(pool["L"], 0)
        res = simulate(1000.0, **BASE, swaps=[_swap(0, 10.0, 9.5)],
                       total_usd=10.0)
        self.assertEqual(res["div_count"], 1)

    def test_identical_clone_splits_traffic_50_50(self):
        # Real pool: 0.3% tier, +/-10% band, $10k liquidity. Hypothetical pool is
        # an identical clone. With tiny back-and-forth swaps on the same pair the
        # two pools tie on every swap, so the deterministic tie-break must give
        # each pool half the volume, half the fees, and half the APR.
        liquidity_usd = 10000.0
        fee_bps = 30                       # 0.3%
        fee_pips = int(round(fee_bps * 100))
        days = 30.0
        cap = liquidity_usd / 2.0          # $5k of each token
        usd = 10.0
        fwd, rev = _identical_clone_swaps(usd, cap, fee_pips)
        total_vol = sum(s["usd"] for s in fwd) + sum(s["usd"] for s in rev)
        res = simulate(cap, range_pct=10.0, fee_pips=fee_pips, swaps=fwd,
                       opening_px=BASE["opening_px"], p0_usd=1.0, p1_usd=1.0,
                       total_usd=total_vol, reverse_swaps=rev)

        # Half the swaps in each direction divert to the clone.
        self.assertEqual(res["div_count"], len(fwd) // 2)
        self.assertEqual(res["reverse_count"], len(rev) // 2)
        self.assertAlmostEqual(res["div_usd"], sum(s["usd"] for s in fwd) / 2)
        self.assertAlmostEqual(res["reverse_usd"], sum(s["usd"] for s in rev) / 2)

        # Total fee revenue across both pools equals the single-pool baseline;
        # the clone earns exactly half, and the real pool keeps the other half.
        baseline_fee = total_vol * fee_pips / 1_000_000
        self.assertAlmostEqual(res["fee_usd"], baseline_fee / 2)
        real_fee = baseline_fee - res["fee_usd"]
        self.assertAlmostEqual(real_fee, baseline_fee / 2)

        # APR = fees / liquidity annualized, identical for both pools, and each
        # is exactly half of what the lone real pool earned before.
        apr = lambda fee: fee / liquidity_usd * (365.0 / days) * 100.0
        hyp_apr = apr(res["fee_usd"])
        real_apr = apr(real_fee)
        before_apr = apr(baseline_fee)
        self.assertAlmostEqual(hyp_apr, real_apr)
        self.assertAlmostEqual(hyp_apr, before_apr / 2)
        self.assertAlmostEqual(real_apr, before_apr / 2)

    def test_two_pools_identical_capture_near_half_each(self):
        # Coupled simulation with two identical pools and realistic random swaps:
        # each pool should capture roughly half the traffic (no recorded price
        # input needed). With 50/50 random direction the split should be near
        # half for both count and volume.
        fwd, rev = _random_swaps(2000, seed=7)
        fwd_total = sum(s["usd"] for s in fwd)
        r = simulate_two_pools(50000.0, 10.0, 3000, 50000.0, 10.0, 3000,
                               fwd, BASE["opening_px"], 1.0, 1.0, fwd_total,
                               reverse_swaps=rev)
        self.assertAlmostEqual(r["comp"]["count"], r["hyp"]["count"], delta=50)
        self.assertAlmostEqual(r["comp"]["usd"], r["hyp"]["usd"], delta=0.15 * fwd_total)
        self.assertAlmostEqual(r["pct"], 50.0, delta=10.0)

    def test_two_pools_larger_competitor_captures_more(self):
        # A deeper competitor gives better fills, so more of the demand should
        # divert to it: hypothetical pool's share must shrink monotonically as
        # competitor liquidity grows.
        fwd, rev = _random_swaps(2000, seed=11)
        fwd_total = sum(s["usd"] for s in fwd)
        pcts = []
        for comp_cap in [50000.0, 100000.0, 200000.0, 500000.0]:
            r = simulate_two_pools(comp_cap, 10.0, 3000, 50000.0, 10.0, 3000,
                                   fwd, BASE["opening_px"], 1.0, 1.0, fwd_total,
                                   reverse_swaps=rev)
            pcts.append(r["pct"])
        self.assertTrue(all(pcts[i] > pcts[i + 1] for i in range(len(pcts) - 1)),
                        "hypothetical share should fall as competitor liquidity grows: %s" % pcts)
        self.assertLess(pcts[0], 60.0)
        self.assertLess(pcts[-1], pcts[0] / 2)


if __name__ == "__main__":
    unittest.main()