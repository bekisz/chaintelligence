"""Scratch validation: reconstruct routes and detect splits from real swaps.

Validates the token-ordering convention before implementing it in the API:
 - canonical token0 = address-lower token (Uniswap order)
 - swaps.amount0 corresponds to canonical token0, not liquidity_pool.coin0_id
 - input token = the token with POSITIVE amount (route_analyzer convention)
"""
import psycopg2

DSN = "host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow"

def fetch_tx(tx_hash):
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.log_index, s.pool_id,
               (SELECT symbol FROM coin WHERE coin_id=lp.coin0_id),
               (SELECT symbol FROM coin WHERE coin_id=lp.coin1_id),
               cc0.contract_address, cc1.contract_address,
               s.amount0, s.amount1, s.amount_usd, lp.fee_bps
        FROM swaps s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id=lp.coin0_id AND cc0.chain_id=lp.chain_id
        LEFT JOIN coin_contract cc1 ON cc1.coin_id=lp.coin1_id AND cc1.chain_id=lp.chain_id
        WHERE s.tx_hash = %s
        ORDER BY s.log_index
    """, (tx_hash,))
    rows = cur.fetchall()
    conn.close()
    return rows

def canonical_pair(s0, s1, ad0, ad1):
    """Return (tok0_sym, tok1_sym) in canonical Uniswap order (addr-ascending).
    Falls back to stored order when addresses are missing."""
    if ad0 and ad1 and ad0 != ad1:
        if ad0 < ad1:
            return s0, s1
        return s1, s0
    return s0, s1

def parse_row(r):
    log_index, pool_id, s0, s1, ad0, ad1, a0, a1, usd, fee_bps = r
    t0, t1 = canonical_pair(s0.upper(), s1.upper(), ad0, ad1)
    return {
        "log": log_index, "pool": pool_id, "t0": t0, "t1": t1,
        "a0": float(a0), "a1": float(a1), "usd": float(usd), "fee_bps": fee_bps,
    }

def leg_input(leg):
    """Input token = the token with positive amount (Uniswap convention:
    positive delta = token transferred INTO pool = user sells it)."""
    if leg["a0"] > 0:
        return leg["t0"]
    if leg["a1"] > 0:
        return leg["t1"]
    return None

def analyze_tx(tx_hash, start_set, end_set):
    legs = [parse_row(r) for r in fetch_tx(tx_hash)]
    print(f"\n=== tx {tx_hash} ({len(legs)} legs) ===")
    for l in legs:
        print(f"  log={l['log']:>8} pool={l['pool']:>7} "
              f"{l['t0']}(a0={l['a0']:+.4f}) {l['t1']}(a1={l['a1']:+.4f}) "
              f"usd={l['usd']:.0f} input={leg_input(l)} fee={l['fee_bps']}")
    # split detection on queried pair
    pair_pools = set()
    for l in legs:
        inp = leg_input(l)
        if inp in start_set and l["t0"] in end_set or inp in end_set and l["t1"] in start_set:
            pair_pools.add(l["pool"])
    # simpler: any leg whose token pair (as set) equals queried pair
    queried = frozenset(start_set | end_set)
    split_pools = set()
    for l in legs:
        if frozenset((l["t0"], l["t1"])) == queried:
            split_pools.add(l["pool"])
    print(f"  queried pair {sorted(queried)} via {len(split_pools)} pools -> {'SPLIT' if len(split_pools)>=2 else 'NON-SPLIT'}")
    # route: follow input token chain
    return legs

if __name__ == "__main__":
    analyze_tx("0x9f6c2c4d8d14fbd58be65938754a7f59d1e51921a6e498d225cf1951a1bf7efd",
               {"WETH"}, {"USDC"})
    # a known multi-hop candidate: find one via query in caller
