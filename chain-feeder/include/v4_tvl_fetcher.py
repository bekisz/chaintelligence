"""On-chain TVL computation for Uniswap V4 pools via PoolManager storage reads.

Reads PoolManager contract storage (`eth_getStorageAt`) to get the current
sqrtPriceX96 and liquidity, then computes reserve amounts and USD TVL.

PoolManager (singleton, same on all chains):
    0x000000000004444c5dc75cb358380d2e3de08a90

Storage layout (slot 6 = ``mapping(bytes32 => Pool.State) private _pools``):
    keccak256(abi.encode(poolId, uint256(6))) + 0  -> Slot0 (sqrtPriceX96 + tick)
    keccak256(abi.encode(poolId, uint256(6))) + 3  -> liquidity (lower 128 bits)
"""
import struct
import time
import logging
import requests

from eth_hash.auto import keccak

logger = logging.getLogger(__name__)

POOL_MANAGERS = {
    "Ethereum": "0x000000000004444c5dc75cb358380d2e3de08a90",
    "Arbitrum": "0x360e68faccca8ca495c1b759fd9eee466db9fb32",
    "Optimism": "0x9a13f98cb987694c9f086b1f5eb990eea8264ec3",
    "Base": "0x498581ff718922c3f8e6a244956af099b2652b2b",
    "Polygon": "0x67366782805870060151383f4bbff9dab53e5cd6",
}
POOLS_SLOT = 6

_SEL_DECIMALS = "0x313ce567"

_KOWN_DECIMALS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f": 18,  # DAI
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
    "0x0000000000000000000000000000000000000000": 18,  # native ETH
}

_RPC_URLS = {
    "Ethereum": [
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://ethereum-rpc.publicnode.com",
    ],
    "Arbitrum": [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum.llamarpc.com",
    ],
    "Base": [
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
    ],
}


def _rpc_urls(network):
    env_key = f"RPC_URL_{network.upper()}"
    import os
    urls = []
    env_val = os.environ.get(env_key)
    if env_val:
        urls.extend(u.strip() for u in env_val.split(",") if u.strip())
    env_generic = os.environ.get("RPC_URL")
    if env_generic and env_generic not in urls:
        urls.append(env_generic)
    for u in _RPC_URLS.get(network, _RPC_URLS["Ethereum"]):
        if u not in urls:
            urls.append(u)
    return urls


def call_rpc(method, params, network="Ethereum", retries=3):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    urls = _rpc_urls(network)
    last_err = None
    for _ in range(retries):
        for url in urls:
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if "result" in data:
                        return data["result"]
                    if "error" in data:
                        last_err = data["error"]
                else:
                    last_err = f"HTTP {resp.status_code}"
            except Exception as e:
                last_err = str(e)
                continue
        if last_err:
            time.sleep(1)
    logger.warning(f"RPC call failed after {retries} retries: {method} {params}: {last_err}")
    return None


def call_rpc_batch(calls, network="Ethereum", retries=2):
    """Send a JSON-RPC batch (array of requests), return list of result values."""
    urls = _rpc_urls(network)
    for url in urls:
        try:
            resp = requests.post(url, json=calls, timeout=30)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    return [r.get("result") for r in results]
                if "result" in results:
                    return [results.get("result")]
        except Exception:
            continue
    return [None] * len(calls)


def _pools_storage_slot(pool_id_hex):
    pool_id_bytes = bytes.fromhex(pool_id_hex.removeprefix("0x").lower())
    slot_bytes = POOLS_SLOT.to_bytes(32, "big")
    return int.from_bytes(keccak(pool_id_bytes + slot_bytes), "big")


def _decode_slot0(hex_val):
    if not hex_val or hex_val == "0x":
        return 0, 0
    val = int(hex_val, 16)
    sqrt_price_x96 = val & ((1 << 160) - 1)
    tick_raw = (val >> 160) & ((1 << 24) - 1)
    if tick_raw >= (1 << 23):
        tick = tick_raw - (1 << 24)
    else:
        tick = tick_raw
    return sqrt_price_x96, tick


def _decode_liquidity(hex_val):
    if not hex_val or hex_val == "0x":
        return 0
    val = int(hex_val, 16)
    return val & ((1 << 128) - 1)


def fetch_pool_price_and_tvl(network, pool_id_hex, decimals0, decimals1, price0_usd, price1_usd):
    """Read PoolManager storage and compute current TVL for a V4 pool.

    Returns dict with sqrtPriceX96, tick, liquidity, reserve0, reserve1, tvl_usd,
    or None if pool has no active liquidity.
    """
    pool_manager = POOL_MANAGERS.get(network)
    if not pool_manager:
        logger.debug(f"No PoolManager address configured for {network}")
        return None
    base_slot = _pools_storage_slot(pool_id_hex)
    slot0_hex = call_rpc("eth_getStorageAt", [pool_manager, hex(base_slot), "latest"], network=network)
    if not slot0_hex or slot0_hex == "0x" or slot0_hex == "0x0000000000000000000000000000000000000000000000000000000000000000":
        return None

    sqrt_price_x96, tick = _decode_slot0(slot0_hex)
    if sqrt_price_x96 == 0:
        return None

    liq_hex = call_rpc("eth_getStorageAt", [pool_manager, hex(base_slot + 3), "latest"], network=network)
    liquidity = _decode_liquidity(liq_hex)
    if liquidity == 0:
        return None

    sqrt_price = sqrt_price_x96 / (1 << 96)
    reserve0_raw = liquidity * (1 << 96) // sqrt_price_x96
    reserve1_raw = liquidity * sqrt_price_x96 // (1 << 96)

    amount0 = reserve0_raw / (10 ** decimals0)
    amount1 = reserve1_raw / (10 ** decimals1)

    tvl = amount0 * price0_usd + amount1 * price1_usd

    return {
        "sqrtPriceX96": sqrt_price_x96,
        "tick": tick,
        "liquidity": liquidity,
        "reserve0": amount0,
        "reserve1": amount1,
        "tvl_usd": round(tvl, 2),
    }


def fetch_token_prices_defillama(network, addr0, addr1):
    chain_map = {"Ethereum": "ethereum", "Arbitrum": "arbitrum", "Base": "base"}
    chain = chain_map.get(network, "ethereum")
    url = f"https://coins.llama.fi/prices/current/{chain}:{addr0},{chain}:{addr1}"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code == 200:
            coins = resp.json().get("coins", {})
            p0 = coins.get(f"{chain}:{addr0}", {}).get("price", 0)
            p1 = coins.get(f"{chain}:{addr1}", {}).get("price", 0)
            return p0, p1
    except Exception as e:
        logger.warning(f"DefiLlama price fetch failed: {e}")
    return 0, 0


def fetch_decimals(addr, network="Ethereum"):
    addr_lower = addr.lower()
    if addr_lower in _KOWN_DECIMALS:
        return _KOWN_DECIMALS[addr_lower]
    res = call_rpc("eth_call", [{"to": addr, "data": _SEL_DECIMALS}, "latest"], network=network)
    if res and res != "0x":
        try:
            return int(res, 16)
        except ValueError:
            return 18
    return 18


def compute_v4_tvl_for_pool_row(pool_db_id, c0_addr, c1_addr, fee_bps, pool_id_hex, network="Ethereum"):
    """High-level helper: fetch on-chain TVL for a single DB pool row.

    Returns (tvl_usd, liquidity, error_msg).
    """
    if not pool_id_hex:
        return None, 0, "no pool_id"
    d0 = fetch_decimals(c0_addr, network)
    d1 = fetch_decimals(c1_addr, network)
    p0, p1 = fetch_token_prices_defillama(network, c0_addr, c1_addr)
    if p0 == 0 or p1 == 0:
        p0, p1 = 0, 0

    result = fetch_pool_price_and_tvl(network, pool_id_hex, d0, d1, p0, p1)
    if result is None:
        return None, 0, "no on-chain liquidity"

    tvl = result["tvl_usd"]
    liq = result["liquidity"]
    return tvl, liq, None
