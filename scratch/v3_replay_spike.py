#!/usr/bin/env python3
"""
Spike: single-pool Uniswap V3 historical state reconstruction + archive validation.

Reconstructs the full on-chain state (sqrtPriceX96, tick, active liquidity) of the
tBTC/WBTC 1-bp Uniswap V3 pool (liquidity_pool.id = 9417) from its genesis by replaying
all pool events (Initialize / Mint / Burn / Swap) in strict (block, txIndex, logIndex)
order using a faithful Python port of uniswap-v3-core v1.0.0 math.

Validation:
  1. Per-Swap: simulated post-swap sqrtPriceX96 / liquidity / tick == emitted Swap event.
  2. Final: reconstructed slot0 + liquidity == archive eth_call at the end block.

Read-only. Makes no database or file writes other than its own output.
"""

import os
import sys
import time
import json
import copy
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from eth_hash.auto import keccak

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RPC_URL = os.environ.get(
    "RPC_URL_ETHEREUM",
    "https://eth-mainnet.g.alchemy.com/v2/alch_johzzNhYnoI4H9-36vuZ0",
)
# Bulk eth_getLogs runs on a provider that allows wide ranges on free tier
# (Alchemy free caps eth_getLogs at 10-block ranges). drpc free allows 10k-block ranges.
LOG_RPC_URL = os.environ.get("LOG_RPC_URL", "https://eth.drpc.org")
STATE_RPC_URL = os.environ.get("STATE_RPC_URL", RPC_URL)
POOL_ADDRESS = "0x73a38006d23517a1d383c88929b2014f8835b38b"  # tBTC/WBTC 1bp (pool 9417)
FEE_PIPS = 100            # 1 bp = 0.01%
TICK_SPACING = 1

CHUNK_BLOCKS = 2_000     # eth_getLogs range per call (keeps results small on drpc free)
BATCH_CALLS = 3          # eth_getLogs calls per HTTP request (drpc free max = 3)
MAX_RETRIES = 6
RETRY_BASE_SLEEP = 0.3
RETRY_MAX_SLEEP = 3.0
LOG_CACHE_DIR = os.environ.get("LOG_CACHE_DIR", "/tmp/v3_pool9417_logs")

# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------
_session = requests.Session()


# requests.Session is NOT thread-safe; each worker needs its own (thread-local).
_session_local = threading.local()


def _session():
    s = getattr(_session_local, "s", None)
    if s is None:
        s = _session_local.s = requests.Session()
    return s


def rpc(url, method, params, _retries=MAX_RETRIES):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = None
    for attempt in range(_retries):
        try:
            resp = _session().post(url, json=body, timeout=90)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(RETRY_BASE_SLEEP * (2 ** attempt), RETRY_MAX_SLEEP))
            continue
        if "error" in payload:
            # drpc free is flaky: treat all error codes as transient
            last = payload["error"]
            time.sleep(min(RETRY_BASE_SLEEP * (2 ** attempt), RETRY_MAX_SLEEP))
            continue
        return payload["result"]
    raise RuntimeError(f"RPC {method} failed after {_retries} attempts: {last}")


def get_latest_block():
    return int(rpc(STATE_RPC_URL, "eth_blockNumber", []), 16)


def get_logs(address, from_block, to_block, topics=None):
    logs = []
    lo = from_block
    while lo <= to_block:
        hi = min(lo + CHUNK_BLOCKS - 1, to_block)
        req = {
            "address": address,
            "fromBlock": hex(lo),
            "toBlock": hex(hi),
        }
        if topics is not None:
            req["topics"] = topics
        chunk = rpc(LOG_RPC_URL, "eth_getLogs", [req])
        logs.extend(chunk)
        lo = hi + 1
    return logs


def _batch_rpc(url, method, params_list, _retries=6):
    """Send `params_list` as a JSON-RPC batch, returning results in order.
    Falls back to individual calls after repeated batch-level failures."""
    if not params_list:
        return []
    body = [{"jsonrpc": "2.0", "id": i, "method": method, "params": [p]}
            for i, p in enumerate(params_list)]
    for attempt in range(_retries):
        try:
            resp = _session().post(url, json=body, timeout=120)
            payload = resp.json()
            if not isinstance(payload, list) or len(payload) != len(body):
                raise RuntimeError(f"batch shape {type(payload)}")
            by_id = {x["id"]: x for x in payload}
            results = []
            for i in range(len(body)):
                item = by_id.get(i, {})
                if "error" in item:
                    raise RuntimeError(f"batch item {i}: {item['error']}")
                results.append(item.get("result"))
            return results
        except Exception as exc:  # noqa: BLE001
            if attempt + 1 < _retries:
                time.sleep(min(RETRY_BASE_SLEEP * (2 ** attempt), RETRY_MAX_SLEEP))
                continue
            # fallback: issue each call individually (rpc() has its own retries)
            return [rpc(url, method, [p]) for p in params_list]
    raise RuntimeError("unreachable")


FETCH_WORKERS = 2  # concurrent batch workers (drpc free throttles on bursts)
FETCH_PACE = 0.2   # seconds of pacing per batch submitted


def _chunk_pairs(from_block, to_block):
    pairs = []
    lo = from_block
    while lo <= to_block:
        hi = min(lo + CHUNK_BLOCKS - 1, to_block)
        pairs.append((lo, hi))
        lo = hi + 1
    return pairs


def _chunk_cache_path(lo, hi):
    return os.path.join(LOG_CACHE_DIR, f"{lo}-{hi}.json")


def _load_cached(lo, hi):
    path = _chunk_cache_path(lo, hi)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _save_cached(lo, hi, chunk):
    path = _chunk_cache_path(lo, hi)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(chunk, f)
    os.replace(tmp, path)


def _fetch_batch(address, batch_pairs, topics):
    params = [{
        "address": address,
        "fromBlock": hex(lo),
        "toBlock": hex(hi),
        **({"topics": topics} if topics is not None else {}),
    } for lo, hi in batch_pairs]
    return _batch_rpc(LOG_RPC_URL, "eth_getLogs", params)


def fetch_all_logs(address, from_block, to_block, topics=None):
    """Batched eth_getLogs fetch with per-chunk disk cache (resumable)."""
    os.makedirs(LOG_CACHE_DIR, exist_ok=True)
    pairs = _chunk_pairs(from_block, to_block)
    todo = [p for p in pairs if _load_cached(*p) is None]
    print(f"  {len(pairs)} chunks total, {len(pairs) - len(todo)} cached, {len(todo)} to fetch")

    failed = []
    if todo:
        batches = [todo[i:i + BATCH_CALLS] for i in range(0, len(todo), BATCH_CALLS)]
        ok = 0
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
            futs = []
            for b in batches:
                time.sleep(FETCH_PACE)
                futs.append((ex.submit(_fetch_batch, address, b, topics), b))
            for fut, batch in futs:
                try:
                    results = fut.result()
                except Exception as exc:  # noqa: BLE001
                    failed.extend(batch)
                    print(f"  FAILED chunk(s) {batch[0][0]}..{batch[-1][1]}: {str(exc)[:80]}", flush=True)
                    continue
                for (lo, hi), chunk in zip(batch, results):
                    _save_cached(lo, hi, chunk or [])
                ok += 1
                print(f"  progress: {ok}/{len(batches)} batches done", flush=True)

    logs = []
    for lo, hi in pairs:
        chunk = _load_cached(lo, hi)
        if chunk is not None:
            logs.extend(chunk)
    if failed:
        rng = ", ".join(f"[{lo},{hi}]" for lo, hi in failed[:8])
        raise RuntimeError(f"{len(failed)} chunks still failed: {rng} — re-run to resume (cached)")
    return logs, len(pairs)

    logs = []
    for lo, hi in pairs:
        chunk = _load_cached(lo, hi)
        if chunk is not None:
            logs.extend(chunk)
    if failed:
        rng = ", ".join(f"[{lo},{hi}]" for lo, hi in failed[:8])
        raise RuntimeError(f"{len(failed)} chunks still failed: {rng} — re-run to resume (cached)")
    return logs, len(pairs)


def has_logs(address, from_block, to_block, topic0):
    pairs = []
    lo = from_block
    while lo <= to_block:
        hi = min(lo + CHUNK_BLOCKS - 1, to_block)
        pairs.append((lo, hi))
        lo = hi + 1
    for i in range(0, len(pairs), BATCH_CALLS):
        batch = pairs[i:i + BATCH_CALLS]
        params = [{"address": address, "fromBlock": hex(lo), "toBlock": hex(hi),
                   "topics": [topic0]} for lo, hi in batch]
        for chunk in _batch_rpc(LOG_RPC_URL, "eth_getLogs", params):
            if chunk:
                return True
    return False


def eth_call_at(to, data, block_tag):
    return rpc(STATE_RPC_URL, "eth_call", [{"to": to, "data": data}, block_tag])


# ---------------------------------------------------------------------------
# ABI helpers
# ---------------------------------------------------------------------------
def u256(b):
    return int.from_bytes(b, "big")


def i256(b):
    v = u256(b)
    return v - (1 << 256) if v & (1 << 255) else v


def i24(b):
    v = int.from_bytes(b[-3:], "big")
    return v - (1 << 24) if v & (1 << 23) else v


def ev_sig(sig):
    return "0x" + keccak(sig.encode()).hex()


EV_INIT = ev_sig("Initialize(uint160,int24)")
EV_MINT = ev_sig("Mint(address,address,int24,int24,uint128,uint256,uint256)")
EV_BURN = ev_sig("Burn(address,int24,int24,uint128,uint256,uint256)")
EV_SWAP = ev_sig("Swap(address,address,int256,int256,uint160,uint128,int24)")
EV_SET_FEE = ev_sig("SetFeeProtocol(uint8,uint8,uint8,uint8)")

SIG_SLOT0 = "0x3850c7bd"      # slot0()
SIG_LIQUIDITY = "0x1a686502"  # liquidity()
SIG_TICKBITMAP = "0x5339c296"  # tickBitmap(int16)
SIG_TICKS = "0xf30dba93"       # ticks(int24)
CHECKPOINT_MARGIN_TICKS = 500


def decode_initialize(data):
    return u256(data[0:32]), i24(data[32:64])


def decode_mint(topics, data):
    # Mint(address sender, address indexed owner, int24 indexed tickLower,
    #      int24 indexed tickUpper, uint128 amount, uint256 amount0, uint256 amount1)
    # sender is non-indexed (first data word); tickLower/tickUpper are topics[2]/[3]
    t_lo, t_hi = bytes.fromhex(topics[2][2:]), bytes.fromhex(topics[3][2:])
    return (i24(t_lo), i24(t_hi), u256(data[32:64]),
            u256(data[64:96]), u256(data[96:128]))


def decode_burn(topics, data):
    # Burn(address indexed owner, int24 indexed tickLower, int24 indexed tickUpper,
    #      uint128 amount, uint256 amount0, uint256 amount1)
    # tickLower/tickUpper are indexed (topics[2], topics[3]); data is [amount, amount0, amount1]
    t_lo, t_hi = bytes.fromhex(topics[2][2:]), bytes.fromhex(topics[3][2:])
    return (i24(t_lo), i24(t_hi), u256(data[0:32]),
            u256(data[32:64]), u256(data[64:96]))


def decode_swap(data):
    return (i256(data[0:32]), i256(data[32:64]), u256(data[64:96]),
            u256(data[96:128]), i24(data[128:160]))


def decode_set_fee(data):
    return (u256(data[0:32]), u256(data[32:64]), u256(data[64:96]), u256(data[96:128]))


# ---------------------------------------------------------------------------
# FullMath / SqrtPriceMath / SwapMath (faithful port of v3-core v1.0.0)
# ---------------------------------------------------------------------------
Q96 = 1 << 96
Q128 = 1 << 128
MAX_U256 = (1 << 256) - 1

MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342


def mul_div(a, b, denominator):
    return (a * b) // denominator


def mul_div_rounding_up(a, b, denominator):
    return (a * b + denominator - 1) // denominator


def ceil_div(a, b):
    return (a + b - 1) // b


def get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount, add):
    if amount == 0:
        return sqrt_px96
    numerator1 = liquidity << 96
    if add:
        product = amount * sqrt_px96
        if product // amount == sqrt_px96:
            denominator = numerator1 + product
            if denominator >= numerator1:
                return ceil_div(numerator1 * sqrt_px96, denominator)
        return ceil_div(numerator1, (numerator1 // sqrt_px96) + amount)
    product = amount * sqrt_px96
    assert product // amount == sqrt_px96 and numerator1 > product
    denominator = numerator1 - product
    return ceil_div(numerator1 * sqrt_px96, denominator)


def get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount, add):
    if add:
        quotient = (amount * Q96) // liquidity
        return sqrt_px96 + quotient
    quotient = ceil_div(amount * Q96, liquidity)
    assert sqrt_px96 > quotient
    return sqrt_px96 - quotient


def get_next_sqrt_price_from_input(sqrt_px96, liquidity, amount_in, zero_for_one):
    assert sqrt_px96 > 0 and liquidity > 0
    if zero_for_one:
        return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_in, True)
    return get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount_in, True)


def get_next_sqrt_price_from_output(sqrt_px96, liquidity, amount_out, zero_for_one):
    assert sqrt_px96 > 0 and liquidity > 0
    if zero_for_one:
        return get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount_out, False)
    return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_out, False)


def get_amount0_delta(sqrt_ratio_a, sqrt_ratio_b, liquidity, round_up):
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    numerator1 = liquidity << 96
    numerator2 = sqrt_ratio_b - sqrt_ratio_a
    assert sqrt_ratio_a > 0
    if round_up:
        return ceil_div(ceil_div(numerator1 * numerator2, sqrt_ratio_b), sqrt_ratio_a)
    return (numerator1 * numerator2 // sqrt_ratio_b) // sqrt_ratio_a


def get_amount1_delta(sqrt_ratio_a, sqrt_ratio_b, liquidity, round_up):
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    if round_up:
        return ceil_div(liquidity * (sqrt_ratio_b - sqrt_ratio_a), Q96)
    return (liquidity * (sqrt_ratio_b - sqrt_ratio_a)) // Q96


def compute_swap_step(sqrt_ratio_current, sqrt_ratio_target, liquidity, amount_remaining, fee_pips):
    zero_for_one = sqrt_ratio_current >= sqrt_ratio_target
    exact_in = amount_remaining >= 0

    if exact_in:
        amount_remaining_less_fee = mul_div(amount_remaining, 1_000_000 - fee_pips, 1_000_000)
        amount_in = (get_amount0_delta(sqrt_ratio_target, sqrt_ratio_current, liquidity, True)
                     if zero_for_one else
                     get_amount1_delta(sqrt_ratio_current, sqrt_ratio_target, liquidity, True))
        if amount_remaining_less_fee >= amount_in:
            sqrt_ratio_next = sqrt_ratio_target
        else:
            sqrt_ratio_next = get_next_sqrt_price_from_input(
                sqrt_ratio_current, liquidity, amount_remaining_less_fee, zero_for_one)
    else:
        amount_out = (get_amount1_delta(sqrt_ratio_target, sqrt_ratio_current, liquidity, False)
                      if zero_for_one else
                      get_amount0_delta(sqrt_ratio_current, sqrt_ratio_target, liquidity, False))
        if -amount_remaining >= amount_out:
            sqrt_ratio_next = sqrt_ratio_target
        else:
            sqrt_ratio_next = get_next_sqrt_price_from_output(
                sqrt_ratio_current, liquidity, -amount_remaining, zero_for_one)

    max_ = sqrt_ratio_target == sqrt_ratio_next

    if zero_for_one:
        amount_in = (amount_in if (max_ and exact_in) else
                     get_amount0_delta(sqrt_ratio_next, sqrt_ratio_current, liquidity, True))
        amount_out = (amount_out if (max_ and not exact_in) else
                      get_amount1_delta(sqrt_ratio_next, sqrt_ratio_current, liquidity, False))
    else:
        amount_in = (amount_in if (max_ and exact_in) else
                     get_amount1_delta(sqrt_ratio_current, sqrt_ratio_next, liquidity, True))
        amount_out = (amount_out if (max_ and not exact_in) else
                      get_amount0_delta(sqrt_ratio_current, sqrt_ratio_next, liquidity, False))

    if not exact_in and amount_out > -amount_remaining:
        amount_out = -amount_remaining

    if exact_in and sqrt_ratio_next != sqrt_ratio_target:
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = mul_div_rounding_up(amount_in, fee_pips, 1_000_000 - fee_pips)

    return sqrt_ratio_next, amount_in, amount_out, fee_amount


# ---------------------------------------------------------------------------
# TickMath (faithful port)
# ---------------------------------------------------------------------------
def get_sqrt_ratio_at_tick(tick):
    abs_tick = -tick if tick < 0 else tick
    assert abs_tick <= MAX_TICK, "T"
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if abs_tick & 0x1 else 0x100000000000000000000000000000000
    if abs_tick & 0x2:
        ratio = (ratio * 0xfff97272373d413259a46990580e213a) >> 128
    if abs_tick & 0x4:
        ratio = (ratio * 0xfff2e50f5f656932ef12357cf3c7fdcc) >> 128
    if abs_tick & 0x8:
        ratio = (ratio * 0xffe5caca7e10e4e61c3624eaa0941cd0) >> 128
    if abs_tick & 0x10:
        ratio = (ratio * 0xffcb9843d60f6159c9db58835c926644) >> 128
    if abs_tick & 0x20:
        ratio = (ratio * 0xff973b41fa98c081472e6896dfb254c0) >> 128
    if abs_tick & 0x40:
        ratio = (ratio * 0xff2ea16466c96a3843ec78b326b52861) >> 128
    if abs_tick & 0x80:
        ratio = (ratio * 0xfe5dee046a99a2a811c461f1969c3053) >> 128
    if abs_tick & 0x100:
        ratio = (ratio * 0xfcbe86c7900a88aedcffc83b479aa3a4) >> 128
    if abs_tick & 0x200:
        ratio = (ratio * 0xf987a7253ac413176f2b074cf7815e54) >> 128
    if abs_tick & 0x400:
        ratio = (ratio * 0xf3392b0822b70005940c7a398e4b70f3) >> 128
    if abs_tick & 0x800:
        ratio = (ratio * 0xe7159475a2c29b7443b29c7fa6e889d9) >> 128
    if abs_tick & 0x1000:
        ratio = (ratio * 0xd097f3bdfd2022b8845ad8f792aa5825) >> 128
    if abs_tick & 0x2000:
        ratio = (ratio * 0xa9f746462d870fdf8a65dc1f90e061e5) >> 128
    if abs_tick & 0x4000:
        ratio = (ratio * 0x70d869a156d2a1b890bb3df62baf32f7) >> 128
    if abs_tick & 0x8000:
        ratio = (ratio * 0x31be135f97d08fd981231505542fcfa6) >> 128
    if abs_tick & 0x10000:
        ratio = (ratio * 0x9aa508b5b7a84e1c677de54f3e99bc9) >> 128
    if abs_tick & 0x20000:
        ratio = (ratio * 0x5d6af8dedb81196699c329225ee604) >> 128
    if abs_tick & 0x40000:
        ratio = (ratio * 0x2216e584f5fa1ea926041bedfe98) >> 128
    if abs_tick & 0x80000:
        ratio = (ratio * 0x48a170391f7dc42444e8fa2) >> 128

    if tick > 0:
        ratio = MAX_U256 // ratio

    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


def get_tick_at_sqrt_ratio(sqrt_price_x96):
    assert MIN_SQRT_RATIO <= sqrt_price_x96 < MAX_SQRT_RATIO, "R"
    ratio = sqrt_price_x96 << 32
    r = ratio
    msb = r.bit_length() - 1
    if msb >= 128:
        r = ratio >> (msb - 127)
    else:
        r = ratio << (127 - msb)
    log_2 = (msb - 128) << 64
    for bit in range(63, 49, -1):
        r = (r * r) >> 127
        f = r >> 128
        log_2 |= f << bit
        r >>= f
    log_sqrt10001 = log_2 * 255738958999603826347141
    tick_low = (log_sqrt10001 - 3402992956809132418596140100660247210) >> 128
    tick_hi = (log_sqrt10001 + 291339464771989622907027621153398088495) >> 128
    if tick_low == tick_hi:
        return tick_low
    return tick_hi if get_sqrt_ratio_at_tick(tick_hi) <= sqrt_price_x96 else tick_low


# ---------------------------------------------------------------------------
# BitMath / TickBitmap
# ---------------------------------------------------------------------------
def most_significant_bit(x):
    return x.bit_length() - 1


def least_significant_bit(x):
    return (x & -x).bit_length() - 1


def tick_position(tick):
    return tick >> 8, tick % 256


def flip_tick(bitmap, tick, tick_spacing):
    assert tick % tick_spacing == 0
    word_pos, bit_pos = tick_position(tick // tick_spacing)
    mask = 1 << bit_pos
    bitmap[word_pos] = bitmap.get(word_pos, 0) ^ mask


def next_initialized_tick_within_one_word(bitmap, tick, tick_spacing, lte):
    compressed = tick // tick_spacing
    if tick < 0 and tick % tick_spacing != 0:
        compressed -= 1

    if lte:
        word_pos, bit_pos = tick_position(compressed)
        mask = (1 << bit_pos) - 1 + (1 << bit_pos)
        masked = bitmap.get(word_pos, 0) & mask
        initialized = masked != 0
        if initialized:
            next_tick = (compressed - (bit_pos - most_significant_bit(masked))) * tick_spacing
        else:
            next_tick = (compressed - bit_pos) * tick_spacing
    else:
        word_pos, bit_pos = tick_position(compressed + 1)
        mask = ~((1 << bit_pos) - 1) & MAX_U256
        masked = bitmap.get(word_pos, 0) & mask
        initialized = masked != 0
        if initialized:
            next_tick = (compressed + 1 + (least_significant_bit(masked) - bit_pos)) * tick_spacing
        else:
            next_tick = (compressed + 1 + (255 - bit_pos)) * tick_spacing
    return next_tick, initialized


# ---------------------------------------------------------------------------
# Tick library
# ---------------------------------------------------------------------------
def tick_spacing_to_max_liquidity_per_tick(tick_spacing):
    min_tick = (MIN_TICK // tick_spacing) * tick_spacing
    max_tick = (MAX_TICK // tick_spacing) * tick_spacing
    num_ticks = ((max_tick - min_tick) // tick_spacing) + 1
    return MAX_U256 // num_ticks


def tick_update(ticks, tick, tick_current, liquidity_delta,
                fee_growth_global_0, fee_growth_global_1,
                spl_cum, tick_cum, time, upper, max_liquidity):
    info = ticks.setdefault(tick, {
        "liquidityGross": 0,
        "liquidityNet": 0,
        "feeGrowthOutside0X128": 0,
        "feeGrowthOutside1X128": 0,
        "secondsPerLiquidityOutsideX128": 0,
        "tickCumulativeOutside": 0,
        "secondsOutside": 0,
    })
    liquidity_gross_before = info["liquidityGross"]
    liquidity_gross_after = liquidity_gross_before + liquidity_delta
    assert liquidity_gross_after <= max_liquidity, "LO"
    flipped = (liquidity_gross_after == 0) != (liquidity_gross_before == 0)
    if liquidity_gross_before == 0:
        if tick <= tick_current:
            info["feeGrowthOutside0X128"] = fee_growth_global_0
            info["feeGrowthOutside1X128"] = fee_growth_global_1
            info["secondsPerLiquidityOutsideX128"] = spl_cum
            info["tickCumulativeOutside"] = tick_cum
            info["secondsOutside"] = time
    info["liquidityGross"] = liquidity_gross_after
    if upper:
        info["liquidityNet"] = info["liquidityNet"] - liquidity_delta
    else:
        info["liquidityNet"] = info["liquidityNet"] + liquidity_delta
    return flipped


def tick_cross(ticks, tick, fee_growth_global_0, fee_growth_global_1, spl_cum, tick_cum, time):
    info = ticks[tick]
    info["feeGrowthOutside0X128"] = fee_growth_global_0 - info["feeGrowthOutside0X128"]
    info["feeGrowthOutside1X128"] = fee_growth_global_1 - info["feeGrowthOutside1X128"]
    info["secondsPerLiquidityOutsideX128"] = spl_cum - info["secondsPerLiquidityOutsideX128"]
    info["tickCumulativeOutside"] = tick_cum - info["tickCumulativeOutside"]
    info["secondsOutside"] = time - info["secondsOutside"]
    return info["liquidityNet"]


# ---------------------------------------------------------------------------
# Pool simulation state machine
# ---------------------------------------------------------------------------
class UniswapV3Pool:
    def __init__(self, fee_pips, tick_spacing):
        self.fee = fee_pips
        self.tick_spacing = tick_spacing
        self.max_liquidity_per_tick = tick_spacing_to_max_liquidity_per_tick(tick_spacing)
        self.sqrt_price_x96 = 0
        self.tick = 0
        self.fee_protocol = 0
        self.liquidity = 0
        self.fee_growth_global_0 = 0
        self.fee_growth_global_1 = 0
        self.ticks = {}
        self.tick_bitmap = {}

    def initialize(self, sqrt_price_x96, tick):
        self.sqrt_price_x96 = sqrt_price_x96
        self.tick = tick

    def _modify_position(self, tick_lower, tick_upper, liquidity_delta, tick_current):
        flipped_lower = tick_update(
            self.ticks, tick_lower, tick_current, liquidity_delta,
            self.fee_growth_global_0, self.fee_growth_global_1,
            0, 0, 0, False, self.max_liquidity_per_tick)
        flipped_upper = tick_update(
            self.ticks, tick_upper, tick_current, liquidity_delta,
            self.fee_growth_global_0, self.fee_growth_global_1,
            0, 0, 0, True, self.max_liquidity_per_tick)
        if flipped_lower:
            flip_tick(self.tick_bitmap, tick_lower, self.tick_spacing)
        if flipped_upper:
            flip_tick(self.tick_bitmap, tick_upper, self.tick_spacing)

        if tick_lower <= tick_current < tick_upper:
            self.liquidity += liquidity_delta
            assert self.liquidity >= 0

    def mint(self, tick_lower, tick_upper, amount):
        assert amount >= 0
        if amount == 0:
            return
        self._modify_position(tick_lower, tick_upper, amount, self.tick)

    def burn(self, tick_lower, tick_upper, amount):
        assert amount >= 0
        if amount == 0:
            return
        self._modify_position(tick_lower, tick_upper, -amount, self.tick)

    def swap(self, zero_for_one, amount_specified, sqrt_price_limit_x96):
        sqrt_price_start = self.sqrt_price_x96
        liquidity_start = self.liquidity
        fee_protocol = (self.fee_protocol % 16) if zero_for_one else (self.fee_protocol >> 4)
        exact_input = amount_specified > 0

        state = {
            "amountSpecifiedRemaining": amount_specified,
            "amountCalculated": 0,
            "sqrtPriceX96": sqrt_price_start,
            "tick": self.tick,
            "feeGrowthGlobalX128": self.fee_growth_global_0 if zero_for_one else self.fee_growth_global_1,
            "protocolFee": 0,
            "liquidity": liquidity_start,
        }

        while state["amountSpecifiedRemaining"] != 0 and state["sqrtPriceX96"] != sqrt_price_limit_x96:
            sqrt_price_start_x96 = state["sqrtPriceX96"]

            step_tick_next, step_initialized = next_initialized_tick_within_one_word(
                self.tick_bitmap, state["tick"], self.tick_spacing, zero_for_one)
            if step_tick_next < MIN_TICK:
                step_tick_next = MIN_TICK
            elif step_tick_next > MAX_TICK:
                step_tick_next = MAX_TICK
            sqrt_price_next_x96 = get_sqrt_ratio_at_tick(step_tick_next)

            target = (sqrt_price_limit_x96
                      if (sqrt_price_next_x96 < sqrt_price_limit_x96 if zero_for_one
                          else sqrt_price_next_x96 > sqrt_price_limit_x96)
                      else sqrt_price_next_x96)

            (state["sqrtPriceX96"], amount_in, amount_out, fee_amount) = compute_swap_step(
                state["sqrtPriceX96"], target, state["liquidity"],
                state["amountSpecifiedRemaining"], self.fee)

            if exact_input:
                state["amountSpecifiedRemaining"] -= (amount_in + fee_amount)
                state["amountCalculated"] -= amount_out
            else:
                state["amountSpecifiedRemaining"] += amount_out
                state["amountCalculated"] += (amount_in + fee_amount)

            if fee_protocol > 0:
                delta = fee_amount // fee_protocol
                fee_amount -= delta
                state["protocolFee"] += delta

            if state["liquidity"] > 0:
                state["feeGrowthGlobalX128"] += mul_div(fee_amount, Q128, state["liquidity"])

            if state["sqrtPriceX96"] == sqrt_price_next_x96:
                if step_initialized:
                    liquidity_net = tick_cross(
                        self.ticks, step_tick_next,
                        (state["feeGrowthGlobalX128"] if zero_for_one else self.fee_growth_global_0),
                        (self.fee_growth_global_1 if zero_for_one else state["feeGrowthGlobalX128"]),
                        0, 0, 0)
                    if zero_for_one:
                        liquidity_net = -liquidity_net
                    state["liquidity"] += liquidity_net
                    assert state["liquidity"] >= 0
                state["tick"] = step_tick_next - 1 if zero_for_one else step_tick_next
            elif state["sqrtPriceX96"] != sqrt_price_start_x96:
                state["tick"] = get_tick_at_sqrt_ratio(state["sqrtPriceX96"])

        self.sqrt_price_x96 = state["sqrtPriceX96"]
        self.tick = state["tick"]
        self.liquidity = state["liquidity"]
        if zero_for_one:
            self.fee_growth_global_0 = state["feeGrowthGlobalX128"]
        else:
            self.fee_growth_global_1 = state["feeGrowthGlobalX128"]

        return (amount_specified - state["amountSpecifiedRemaining"], state["amountCalculated"])

    def load_checkpoint(self, state):
        """Load on-chain state snapshot taken at (window_start - 1).
        `state` keys: sqrt_price_x96, tick, liquidity, fee_protocol,
        tick_bitmap (word_pos -> uint256), ticks (tick -> info dict)."""
        self.sqrt_price_x96 = state["sqrt_price_x96"]
        self.tick = state["tick"]
        self.liquidity = state["liquidity"]
        self.fee_protocol = state["fee_protocol"]
        self.tick_bitmap = dict(state["tick_bitmap"])
        self.ticks = {t: dict(info) for t, info in state["ticks"].items()}


# ---------------------------------------------------------------------------
# Checkpoint reconstruction (single pool, via public getters)
# ---------------------------------------------------------------------------
def i256_from_abi(word_hex):
    v = int(word_hex, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


def fetch_pool_checkpoint(block):
    """Reconstruct pool state at a block via public getters (no storage hardcoding)."""
    s0 = eth_call_at(POOL_ADDRESS, SIG_SLOT0, hex(block))
    w = [s0[i:i + 64] for i in range(2, len(s0), 64)]
    if len(w) != 7:
        raise RuntimeError(f"unexpected slot0() shape: {len(w)} words")
    tick = i256_from_abi(w[1])
    checkpoint = {
        "sqrt_price_x96": int(w[0], 16),
        "tick": tick,
        "fee_protocol": int(w[5], 16),
        "unlocked": int(w[6], 16),
        "liquidity": int(eth_call_at(POOL_ADDRESS, SIG_LIQUIDITY, hex(block)), 16),
    }
    return checkpoint


def read_bitmap_word(word_pos, block):
    arg = word_pos.to_bytes(32, "big", signed=True).hex()
    res = eth_call_at(POOL_ADDRESS, SIG_TICKBITMAP + arg, hex(block))
    return int(res, 16)


def read_tick_info(tick, block):
    arg = tick.to_bytes(32, "big", signed=True).hex()
    res = eth_call_at(POOL_ADDRESS, SIG_TICKS + arg, hex(block))
    w = [res[i:i + 64] for i in range(2, len(res), 64)]
    gross = int(w[0], 16)
    net = i256_from_abi(w[1])
    return gross, net


def reconstruct_tick_state(checkpoint, band_lo, band_hi, block):
    """Read tickBitmap + ticks() over the tick band. Returns (bitmap, ticks)."""
    word_lo = band_lo >> 8
    word_hi = band_hi >> 8
    bitmap = {}
    ticks = {}
    for word_pos in range(word_lo, word_hi + 1):
        word = read_bitmap_word(word_pos, block)
        if word == 0:
            continue
        bitmap[word_pos] = word
        for bit in range(256):
            if word & (1 << bit):
                tick = word_pos * 256 + bit
                gross, net = read_tick_info(tick, block)
                ticks[tick] = {
                    "liquidityGross": gross,
                    "liquidityNet": net,
                    "feeGrowthOutside0X128": 0,
                    "feeGrowthOutside1X128": 0,
                    "secondsPerLiquidityOutsideX128": 0,
                    "tickCumulativeOutside": 0,
                    "secondsOutside": 0,
                }
    return bitmap, ticks


# ---------------------------------------------------------------------------
# Event → pool action
# ---------------------------------------------------------------------------
def swap_param_candidates(amount0, amount1, pre_sqrt, post_sqrt):
    """Enumerate the exact-in / exact-out swap interpretations consistent with
    the event's amount signs. Each is (zero_for_one, amount_specified, exact_input).
    For exact-out, amount_specified is the raw NEGATIVE event amount (a negative
    amountSpecified signals exact-output to the pool)."""
    cands = []
    if amount0 > 0:
        cands.append((True, amount0, True))        # z4o exactIn (token0 in)
    if amount1 < 0:
        cands.append((True, amount1, False))       # z4o exactOut (token1 out)
    if amount1 > 0:
        cands.append((False, amount1, True))       # o4z exactIn (token1 in)
    if amount0 < 0:
        cands.append((False, amount0, False))      # o4z exactOut (token0 out)

    up = post_sqrt > pre_sqrt

    def plausibility(c):
        z4o, _amt, exact = c
        expected_up = (not z4o) if exact else z4o
        return 0 if up == expected_up else 1

    cands.sort(key=plausibility)
    return cands


def swap_amounts_reconcile(z4o, exact, consumed, amount_calculated, amount0, amount1):
    # consumed = amount_specified - remaining (gross in for exactIn; the specified
    #            output, negative, for exactOut)
    # amount_calculated = net output (negative) for exactIn; gross input for exactOut
    if exact:
        if z4o:
            return consumed == amount0 and amount_calculated == amount1
        return consumed == amount1 and amount_calculated == amount0
    if z4o:
        return consumed == amount1 and amount_calculated == amount0
    return consumed == amount0 and amount_calculated == amount1


def try_swap_candidates(pool, amount0, amount1, ev_sqrt, ev_liq, ev_tick):
    """Disambiguate exact-in vs exact-out by simulating every consistent
    interpretation and keeping the one whose post-state AND computed amounts
    reproduce the emitted Swap event. Applies the winning interpretation.

    A swap may also be capped by a sqrtPriceLimitX96: the pool stops exactly at
    the limit, which for a capped swap equals the event's post-sqrt. Each
    candidate is therefore tried twice - with an unlimited limit and with
    limit = event post-sqrt - preferring the unlimited interpretation."""
    pre_sqrt = pool.sqrt_price_x96
    snapshot = copy.deepcopy(pool.__dict__)
    candidates = swap_param_candidates(amount0, amount1, pre_sqrt, ev_sqrt)
    matches = []
    for z4o, amt, exact in candidates:
        for limit in ((MIN_SQRT_RATIO + 1) if z4o else (MAX_SQRT_RATIO - 1),
                      ev_sqrt):
            if limit == ev_sqrt:
                valid = (ev_sqrt < pre_sqrt) if z4o else (ev_sqrt > pre_sqrt)
                if not valid:
                    continue
            pool.__dict__.update(copy.deepcopy(snapshot))
            consumed, amount_calculated = pool.swap(z4o, amt, limit)
            post_ok = (pool.sqrt_price_x96 == ev_sqrt
                       and pool.liquidity == ev_liq and pool.tick == ev_tick)
            amt_ok = swap_amounts_reconcile(z4o, exact, consumed, amount_calculated, amount0, amount1)
            if post_ok and amt_ok:
                matches.append((z4o, amt, exact, limit))
                break

    pool.__dict__.update(copy.deepcopy(snapshot))
    if not matches:
        return None, None, None
    chosen = matches[0]
    z4o, amt, exact, limit = chosen
    pool.swap(z4o, amt, limit)
    capped = limit == ev_sqrt
    alternate = (z4o, amt, exact) != candidates[0]
    return chosen, alternate, capped


def apply_events(pool, events):
    stats = {
        "initialize": 0,
        "mint": 0,
        "burn": 0,
        "swap": 0,
        "set_fee": 0,
        "unknown": 0,
        "swap_matched": 0,
        "swap_mode_retry": 0,
        "swap_capped": 0,
        "swap_diverged": 0,
        "first_divergence": None,
    }
    for ev in events:
        topic0 = ev["topics"][0].lower()
        data = bytes.fromhex(ev["data"][2:])
        if topic0 == EV_INIT:
            sqrt_px, tick = decode_initialize(data)
            pool.initialize(sqrt_px, tick)
            stats["initialize"] += 1
        elif topic0 == EV_MINT:
            tl, tu, amount, _, _ = decode_mint(ev["topics"], data)
            pool.mint(tl, tu, amount)
            stats["mint"] += 1
        elif topic0 == EV_BURN:
            tl, tu, amount, _, _ = decode_burn(ev["topics"], data)
            pool.burn(tl, tu, amount)
            stats["burn"] += 1
        elif topic0 == EV_SWAP:
            amount0, amount1, ev_sqrt, ev_liq, ev_tick = decode_swap(data)
            chosen, alternate, capped = try_swap_candidates(pool, amount0, amount1, ev_sqrt, ev_liq, ev_tick)
            stats["swap"] += 1
            if alternate:
                stats["swap_mode_retry"] += 1
            if capped:
                stats["swap_capped"] += 1
            if chosen is None:
                stats["swap_diverged"] += 1
                stats["first_divergence"] = {
                    "blockNumber": ev["blockNumber"],
                    "transactionIndex": ev["transactionIndex"],
                    "logIndex": ev["logIndex"],
                    "transactionHash": ev["transactionHash"],
                    "reason": "no exact-in/exact-out interpretation matched",
                }
                break
            if (pool.sqrt_price_x96 == ev_sqrt and pool.liquidity == ev_liq and pool.tick == ev_tick):
                stats["swap_matched"] += 1
            else:
                stats["swap_diverged"] += 1
                stats["first_divergence"] = {
                    "blockNumber": ev["blockNumber"],
                    "transactionIndex": ev["transactionIndex"],
                    "logIndex": ev["logIndex"],
                    "transactionHash": ev["transactionHash"],
                }
                break
        elif topic0 == EV_SET_FEE:
            p0, p1, _, _ = decode_set_fee(data)
            pool.fee_protocol = p0 + (p1 << 4)
            stats["set_fee"] += 1
        else:
            stats["unknown"] += 1
    return stats


# ---------------------------------------------------------------------------
# Main spike
# ---------------------------------------------------------------------------
def decode_events(logs):
    events = []
    for l in logs:
        events.append({
            "blockNumber": int(l["blockNumber"], 16),
            "transactionIndex": int(l.get("transactionIndex", 0), 16),
            "logIndex": int(l["logIndex"], 16),
            "topics": l["topics"],
            "data": l["data"],
            "transactionHash": l["transactionHash"],
        })
    events.sort(key=lambda e: (e["blockNumber"], e["transactionIndex"], e["logIndex"]))
    return events


def decode_slot0_words(hex_result):
    w = [hex_result[i:i + 64] for i in range(2, len(hex_result), 64)]
    if len(w) != 7:
        raise RuntimeError(f"unexpected slot0() shape: {len(w)} words")
    return {
        "sqrt_price_x96": int(w[0], 16),
        "tick": i256_from_abi(w[1]),
        "observation_index": int(w[2], 16),
        "observation_cardinality": int(w[3], 16),
        "observation_cardinality_next": int(w[4], 16),
        "fee_protocol": int(w[5], 16),
        "unlocked": int(w[6], 16),
    }


def validate_pool(pool, block):
    s0 = decode_slot0_words(eth_call_at(POOL_ADDRESS, SIG_SLOT0, hex(block)))
    onchain_liq = int(eth_call_at(POOL_ADDRESS, SIG_LIQUIDITY, hex(block)), 16)

    def pr(name, got, want):
        ok = got == want
        print(f"  {name:28s} sim={got}  chain={want}  {'MATCH' if ok else '*** MISMATCH ***'}")
        return ok

    ok1 = pr("sqrtPriceX96", pool.sqrt_price_x96, s0["sqrt_price_x96"])
    ok2 = pr("tick", pool.tick, s0["tick"])
    ok3 = pr("liquidity", pool.liquidity, onchain_liq)
    pr("feeProtocol", pool.fee_protocol, s0["fee_protocol"])
    return ok1 and ok2 and ok3


def run_replay_and_validate(pool, events, latest):
    stats = apply_events(pool, events)
    print("\nReplay stats:", json.dumps(stats, default=str, indent=2))

    print("\nValidating against archive eth_call at latest block...")
    ok = validate_pool(pool, latest)

    if stats["swap_diverged"] == 0 and stats["unknown"] == 0 and ok:
        print("\n=== SPIKE PASSED: reconstructed state matches on-chain ===")
    else:
        print("\n=== SPIKE DIVERGED ===")
        if stats["swap_diverged"]:
            d = stats["first_divergence"]
            print(f"First diverging swap at block {d['blockNumber']} tx {d['transactionHash']}")
    return 0 if ok else 1


def run_checkpoint_mode(latest):
    window = int(os.environ.get("WINDOW_BLOCKS", "50000"))
    start = latest - window
    cp_block = start - 1
    print(f"\nCheckpoint mode: window [{start}, {latest}] ({window} blocks), "
          f"checkpoint at block {cp_block}")

    checkpoint = fetch_pool_checkpoint(cp_block)
    print(f"Checkpoint: sqrt={checkpoint['sqrt_price_x96']} tick={checkpoint['tick']} "
          f"liquidity={checkpoint['liquidity']} feeProtocol={checkpoint['fee_protocol']}")

    print(f"\nFetching window pool logs {start}..{latest} ...")
    logs, n_calls = fetch_all_logs(POOL_ADDRESS, start, latest)
    print(f"Fetched {len(logs)} raw logs in {n_calls} eth_getLogs calls")
    events = decode_events(logs)
    print(f"Decoded/ordered {len(events)} events")

    min_t = max_t = checkpoint["tick"]
    for ev in events:
        if ev["topics"][0].lower() == EV_SWAP:
            _, _, _, _, post_tick = decode_swap(bytes.fromhex(ev["data"][2:]))
            min_t = min(min_t, post_tick)
            max_t = max(max_t, post_tick)
    band_lo = min_t - CHECKPOINT_MARGIN_TICKS
    band_hi = max_t + CHECKPOINT_MARGIN_TICKS
    print(f"Tick band [{band_lo}, {band_hi}] (from window extremes +{CHECKPOINT_MARGIN_TICKS})")

    bitmap, ticks = reconstruct_tick_state(checkpoint, band_lo, band_hi, cp_block)
    print(f"Reconstructed {len(bitmap)} bitmap words, {len(ticks)} initialized ticks")
    checkpoint["tick_bitmap"] = bitmap
    checkpoint["ticks"] = ticks

    pool = UniswapV3Pool(FEE_PIPS, TICK_SPACING)
    pool.load_checkpoint(checkpoint)
    return run_replay_and_validate(pool, events, latest)


def run_full_history_mode(latest):
    genesis_override = os.environ.get("GENESIS_BLOCK")
    print("\nLocating pool Initialize event...")
    if genesis_override:
        genesis_block = int(genesis_override)
        print(f"Genesis Initialize (override) at block {genesis_block}")
    else:
        step = 100_000
        top = latest
        chunk_with_init = None
        while True:
            lo = max(0, top - step)
            if has_logs(POOL_ADDRESS, lo, top, EV_INIT):
                chunk_with_init = (lo, top)
                break
            print(f"  no Initialize in [{lo}, {top}], scanning further back...")
            if lo == 0:
                print("ERROR: Initialize event not found")
                sys.exit(1)
            top = lo - 1

        a, b = chunk_with_init
        while a < b:
            mid = (a + b) // 2
            if has_logs(POOL_ADDRESS, a, mid, EV_INIT):
                b = mid
            else:
                a = mid + 1
        genesis_block = a
        init_logs = [l for l in get_logs(POOL_ADDRESS, genesis_block, genesis_block, topics=[EV_INIT])
                     if l["topics"][0].lower() == EV_INIT]
        print(f"Genesis Initialize at block {genesis_block} tx {init_logs[0]['transactionHash']}")

    print(f"\nFetching all pool logs {genesis_block}..{latest} ...")
    logs, n_calls = fetch_all_logs(POOL_ADDRESS, genesis_block, latest)
    print(f"Fetched {len(logs)} raw logs in {n_calls} eth_getLogs calls")
    events = decode_events(logs)
    print(f"Decoded/ordered {len(events)} events")

    pool = UniswapV3Pool(FEE_PIPS, TICK_SPACING)
    return run_replay_and_validate(pool, events, latest)


def main():
    latest = get_latest_block()
    print(f"State RPC:  {STATE_RPC_URL.split('/v2/')[0]}/v2/***")
    print(f"Log RPC:    {LOG_RPC_URL}")
    print(f"Latest block: {latest}")

    mode = os.environ.get("SPIKE_MODE", "checkpoint")
    if mode == "full":
        return run_full_history_mode(latest)
    return run_checkpoint_mode(latest)


if __name__ == "__main__":
    sys.exit(main())
