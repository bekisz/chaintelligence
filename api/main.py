import sys
import os
import base64
import secrets
import time
import threading
import psycopg2
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import requests
from fastapi import FastAPI, HTTPException, Query, Request, Response, Body, Path
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from anyio import to_thread
from dotenv import load_dotenv
from eth_hash.auto import keccak
import yaml
import hashlib

# Global caches for pool address derivation
from typing import Tuple, Dict

POOL_ADDRESS_CACHE: Dict[Tuple[str, str, int, str, str], str] = {}
FACTORY_HASH_CACHE: Dict[Tuple[str, str], Tuple[str, str]] = {}

# Airflow API Configuration
AIRFLOW_API_URL = os.getenv("AIRFLOW_API_URL", "http://airflow-webserver:8080/api/v2")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "airflow")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "airflow")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WEB_DIR = os.path.join(ROOT_DIR, 'web')
STATIC_DIR = os.path.join(WEB_DIR, 'static')
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# Import routing logic from api/routing

# Load DEX configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'dex-config.yaml')
with open(CONFIG_PATH, 'r') as f:
    DEX_CONFIG = yaml.safe_load(f)


API_ROUTING = os.path.join(ROOT_DIR, 'api', 'routing')
if API_ROUTING not in sys.path:
    sys.path.insert(0, API_ROUTING)

API_RESOURCES = os.path.join(ROOT_DIR, 'api', 'resources')
if API_RESOURCES not in sys.path:
    sys.path.insert(0, API_RESOURCES)

# Import graph discovery client


GRAPH_CLIENT_DIR = os.path.join(ROOT_DIR, 'chain-feeder')
if GRAPH_CLIENT_DIR not in sys.path:
    sys.path.insert(0, GRAPH_CLIENT_DIR)
if os.path.join(GRAPH_CLIENT_DIR, 'include') not in sys.path:
    sys.path.insert(0, os.path.join(GRAPH_CLIENT_DIR, 'include'))

from include.settings import load_distribution_config  # noqa: E402

# Global swap-size distribution bucket parameters (config/swap-distribution.yaml).
DISTRIBUTION_CONFIG = load_distribution_config()

# Import graph discovery client
def get_factory_and_hash(protocol: str, network: str):
    """Return the factory address and init code hash for a given protocol/network.
    Supports network-specific entries in the config file.
    """
    key = protocol.lower().replace(' ', '_')
    cfg = DEX_CONFIG.get(key)
    if not cfg:
        raise ValueError(f"Unsupported protocol '{protocol}'")
    # Direct entries (same config for all networks)
    if isinstance(cfg, dict) and 'factory' in cfg and 'init_hash' in cfg:
        return cfg['factory'], cfg['init_hash']
    # Network-specific entries (case-insensitive)
    net_cfg = cfg.get(network) or cfg.get(network.lower())
    if not net_cfg:
        raise ValueError(f"Unsupported network '{network}' for protocol '{protocol}'")
    return net_cfg['factory'], net_cfg['init_hash']


def to_checksum_address(address: str) -> str:
    """Convert an address to EIP-55 checksum format."""
    addr_lower = address.lower().replace('0x', '')
    if len(addr_lower) != 40:
        return address  # Return as-is if not an EVM address
    address_hash = keccak(addr_lower.encode('ascii')).hex()
    checksum_address = '0x' + ''.join(
        c.upper() if int(address_hash[i], 16) >= 8 else c 
        for i, c in enumerate(addr_lower)
    )
    return checksum_address


def _derive_address(t0_bytes: bytes, t1_bytes: bytes, fee_val: int, factory_hex: str, init_hash_hex: str, is_v2: bool = False) -> str:
    """Derive a pool address via CREATE2.

    V3/V4 formula (PoolAddress.sol):
      salt = keccak256(abi.encode(token0, token1, fee))          # each 32 bytes (padded)

    V2 formula (UniswapV2Library):
      salt = keccak256(abi.encodePacked(token0, token1))         # packed (no padding)
      address = keccak256(0xff || factory || salt || init_hash)[12:]
    """
    if is_v2:
        # V2: abi.encodePacked — just concatenate the two 20-byte addresses
        salt = keccak(t0_bytes + t1_bytes)
    else:
        # V3/V4: abi.encode pads each value to 32 bytes
        salt = keccak(b'\x00' * 12 + t0_bytes + b'\x00' * 12 + t1_bytes + fee_val.to_bytes(32, 'big'))
    f_bytes = bytes.fromhex(factory_hex.removeprefix('0x'))
    ih_bytes = bytes.fromhex(init_hash_hex.removeprefix('0x'))
    derived = '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
    return to_checksum_address(derived)


def route_hash_hex(route_id):
    """Render a signed 64-bit route id as its 16-char lowercase hex hash.

    Route ids are signed 64-bit hashes; JSON numbers would lose precision in
    JS (>2^53), and displaying the raw int isn't address-like, so we send the
    hex form and let the UI style it like a pool/address id."""
    if route_id is None:
        return None
    return format((route_id & ((1 << 64) - 1)), '016x')


async def load_pool_row(identifier: str) -> dict:
    """Resolve a pool identifier (id / V3 address / V4 pool_id) to its pool row.

    Returns the pool dict (pool_id/pool_address/v4_pool_id/chain_id/protocol/
    coin0_id/coin1_id/fee_bps/fee_tier/created_at) or raises HTTPException
    400/404.
    """
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    try:
        cur = conn.cursor()
        if identifier.isdigit():
            col_clause = "lp.id = %s"
            param = int(identifier)
        elif len(identifier) == 42 and identifier.startswith("0x"):
            col_clause = "LOWER(lp.pool_address) = LOWER(%s)"
            param = identifier
        elif len(identifier) == 66 and identifier.startswith("0x"):
            col_clause = "LOWER(lp.pool_id) = LOWER(%s) OR LOWER(lp.pool_address) = LOWER(%s)"
            param = (identifier, identifier)
        else:
            raise HTTPException(
                status_code=400,
                detail="Identifier must be a numeric id, a 42-char 0x contract address, or a 66-char 0x V4 pool_id",
            )
        q = f"""
            SELECT lp.id, lp.pool_address, lp.pool_id, lp.fee_bps, lp.chain_id,
                   ch.name AS network, pr.name AS protocol,
                   c0.coin_id AS c0_id,
                   c1.coin_id AS c1_id,
                   lp.created_at
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            JOIN coin c0 ON lp.coin0_id = c0.coin_id
            JOIN coin c1 ON lp.coin1_id = c1.coin_id
            WHERE {col_clause}
            LIMIT 1
        """
        if isinstance(param, tuple):
            cur.execute(q, param)
        else:
            cur.execute(q, (param,))
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=404, detail="Pool not found")
        (pool_id, pool_address, v4_pool_id, fee_bps, chain_id,
         network, protocol, coin0_id, coin1_id, created_at) = row
        fee_val = round(fee_bps) if fee_bps else None
        return {
            "pool_id": pool_id,
            "pool_address": pool_address or v4_pool_id or "",
            "v4_pool_id": v4_pool_id or pool_address or "",
            "chain_id": chain_id,
            "network": network,
            "protocol": protocol,
            "coin0_id": coin0_id,
            "coin1_id": coin1_id,
            "fee_bps": float(fee_bps) if fee_bps else None,
            "fee_tier": f"{fee_val / 100.0:.2f}%" if fee_val else ("Dynamic" if fee_bps is None else None),
            "created_at": created_at.isoformat() if created_at else None,
        }
    finally:
        conn.close()


async def load_route_row(route_hash: str) -> dict:
    """Validate a 16-char route hash and load its route row.

    Returns the route dict (route_id/pair_id/hops/chain/first_seen/last_seen/
    od_hash) or raises HTTPException 400/404.
    """
    route_hash = route_hash.strip().lower()
    if len(route_hash) != 16 or any(c not in '0123456789abcdef' for c in route_hash):
        raise HTTPException(status_code=400, detail="route_hash must be a 16-char lowercase hex string")
    route_id = int(route_hash, 16)
    if route_id >= (1 << 63):
        route_id -= (1 << 64)

    def _query():
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT r.route_id, r.pair_id, r.hops, r.chain_id, ch.name AS chain,
                       r.first_seen, r.last_seen,
                       pair.origin_symbol, pair.dest_symbol
                FROM route r
                JOIN chain ch ON r.chain_id = ch.id
                JOIN origin_destination_pair pair ON r.pair_id = pair.id
                WHERE r.route_id = %s
            """, (route_id,))
            row = cur.fetchone()
            cur.close()
            return row

    row = await asyncio.to_thread(_query)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No route found for hash {route_hash}")

    (route_id, pair_id, hops, chain_id, chain, first_seen, last_seen,
     origin_symbol, dest_symbol) = row
    return {
        "route_id": route_id,
        "pair_id": pair_id,
        "hops": int(hops or 1),
        "chain_id": chain_id,
        "chain": chain,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "od_hash": route_hash_hex(pair_id),
    }


def format_apr(apr_val):
    if apr_val is None:
        return "N/A"
    pct = apr_val * 100
    if pct >= 10:
        return f"{int(round(pct))}%"
    rounded = round(pct + 1e-9, 1)
    if rounded == 0.0:
        return "0%"
    if rounded == int(rounded):
        return f"{int(rounded)}%"
    return f"{rounded}%"


def resolve_stats_window(days: Optional[float], start_date: Optional[str],
                         end_date: Optional[str], default_range: Optional[tuple] = None) -> Optional[tuple]:
    """Resolve a (start_date, end_date) ISO-string window for windowed stats.

    Precedence: explicit `start_date`/`end_date` > `days` lookback > the
    resource's full available day range (``default_range``). Returns None when
    nothing can be resolved (e.g. a route with no stats rows yet).
    """
    if start_date:
        start = start_date
        end = end_date if end_date else datetime.now().strftime('%Y-%m-%d')
        return (start, end)
    if days is not None and days > 0:
        end = datetime.now()
        start = end - timedelta(days=days)
        return (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    return default_range


import requests
import asyncio

# In-memory cache for DEX Screener TVL to avoid duplicate/rate-limited API calls
# key: (chainId, pool_addr_or_id) -> tvl_usd (float)
DEX_SCREENER_CACHE = {}

def fetch_dexscreener_tvl(network: str, pool_addr: str) -> Optional[float]:
    if not pool_addr:
        return None
        
    net_map = {
        'ethereum': 'ethereum',
        'arbitrum': 'arbitrum',
        'base': 'base',
        'bnb': 'bsc',
        'bsc': 'bsc'
    }
    chain_id = net_map.get(network.lower())
    if not chain_id:
        return None
        
    cache_key = (chain_id, pool_addr.lower())
    if cache_key in DEX_SCREENER_CACHE:
        return DEX_SCREENER_CACHE[cache_key]
        
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pool_addr.lower()}"
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            pair = data.get('pair')
            if pair:
                liq_usd = pair.get('liquidity', {}).get('usd')
                if liq_usd is not None:
                    val = float(liq_usd)
                    DEX_SCREENER_CACHE[cache_key] = val
                    return val
    except Exception as e:
        print(f"Error querying DexScreener for {chain_id}/{pool_addr}: {e}")
        
    return None


def parse_fee_rate(fee_str: str) -> Optional[float]:
    try:
        f_clean = fee_str.split('|')[0].replace('%', '').strip()
        if f_clean == 'Dynamic':
            return 0.0002
        val = float(f_clean)
        if val >= 5:
            return val / 1000000.0
        return val / 100.0
    except Exception:
        return None


TOKEN_HARDNESS_CACHE: Dict[str, int] = {}
TOKEN_HARDNESS_BUILT_AT: float = 0.0

def get_token_hardness_map() -> Dict[str, int]:
    global TOKEN_HARDNESS_CACHE, TOKEN_HARDNESS_BUILT_AT
    now = time.time()
    if not TOKEN_HARDNESS_CACHE or (now - TOKEN_HARDNESS_BUILT_AT > 300):
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT UPPER(c.symbol), GREATEST(
                        c.hardness,
                        CASE 
                            WHEN f.name = 'USD' THEN 950
                            WHEN f.name = 'EUR' THEN 930
                            WHEN f.name = 'GOLD' THEN 850
                            WHEN f.name = 'BTC' THEN 870
                            WHEN f.name = 'ETH' THEN 860
                            WHEN f.name = 'SOL' THEN 700
                            ELSE 0
                        END
                    ) as hardness
                    FROM coin c
                    LEFT JOIN coin_family f ON f.coin_id = c.coin_id
                """)
                h_map = {}
                for sym, h in cur.fetchall():
                    if sym:
                        h_map[sym.upper()] = max(h_map.get(sym.upper(), 0), h or 0)
                TOKEN_HARDNESS_CACHE = h_map
                TOKEN_HARDNESS_BUILT_AT = now
        except Exception as e:
            print(f"Error loading token hardness map: {e}")
    return TOKEN_HARDNESS_CACHE

def get_hardness(symbol: str) -> int:
    sym = (symbol or '').upper()
    h_map = get_token_hardness_map()
    if sym in h_map:
        return h_map[sym]
    if any(u in sym for u in ['USD', 'DAI', 'MIM', 'GHO', 'FRAX']):
        return 950
    if 'EUR' in sym:
        return 930
    if any(b in sym for b in ['BTC', 'WBTC', 'CBBTC', 'TBTC']):
        return 870
    if any(e in sym for e in ['ETH', 'WETH', 'STETH']):
        return 860
    return 0


async def get_enriched_pool_stat(key: str, rev_key: str, aprs: dict, pool_addr: str, pool_network: str, period_days: float, fee_tier: str) -> dict:
    pool_stat = aprs.get(key) or aprs.get(rev_key)
    if pool_stat is None:
        pool_stat = {'apr': None, 'tvl': 0.0, 'volume': 0.0}
        
    tvl_val = pool_stat.get('tvl') or 0.0
    vol_val = pool_stat.get('volume') or 0.0
    apr_val = pool_stat.get('apr')
    
    is_v4 = 'v4' in fee_tier.lower()
    is_unreliable = is_v4 or tvl_val <= 1.0 or (vol_val > 0.0 and tvl_val < (vol_val / period_days) * 0.05)
    
    if pool_addr and is_unreliable:
        ds_tvl = await asyncio.to_thread(fetch_dexscreener_tvl, pool_network, pool_addr)
        
        # Fallback to DeFi Llama TVL if DexScreener fails
        if not ds_tvl or ds_tvl <= 1.0:
            dl_tvl = get_defillama_pool_tvl(pool_addr)
            if dl_tvl and dl_tvl > 1.0:
                ds_tvl = dl_tvl
                
        if ds_tvl and ds_tvl > 1.0:
            tvl_val = ds_tvl
            fee_rate = parse_fee_rate(fee_tier)
            if fee_rate is not None and vol_val > 0:
                fees_earned = vol_val * fee_rate
                apr_val = (fees_earned / tvl_val) * (365.0 / period_days)
                
    return {
        'apr': apr_val,
        'tvl': tvl_val,
        'volume': vol_val
    }


# ---------------------------------------------------------------------------
# DeFi Llama yields index: pool_address(lower) -> yields UUID slug.
#
# The slug in https://defillama.com/yields/pool/<uuid> is a random UUID v4
# assigned by DeFi Llama — it cannot be derived from a pool address. The
# yields.llama.fi/pools record identifies a pool by (chain, project, fee tier
# poolMeta, underlying token addresses) and does NOT carry the pool contract
# address. So we build a reverse index: for each yields record we recompute
# the pool address via the SAME CREATE2 formula used by _derive_address, and
# map that address -> the record's stable UUID. UUIDs are stable, so the index
# is cached with a TTL and rebuilt off the event-loop thread.
# ---------------------------------------------------------------------------
DEFILLAMA_INDEX: Dict[str, dict] = {}
DEFILLAMA_INDEX_BUILT_AT: float = 0.0
DEFILLAMA_INDEX_TTL = 24 * 3600  # 24h
_DEFILLAMA_LOCK = threading.Lock()

# DeFi Llama chain name -> dex_config network key
_DL_CHAIN_TO_NET = {
    'Ethereum': 'ethereum', 'Arbitrum': 'arbitrum', 'Base': 'base',
    'OP Mainnet': 'optimism', 'Polygon': 'polygon', 'BSC': 'bsc',
    'Avalanche': 'avalanche', 'Celo': 'celo',
}

# DeFi Llama project slug -> (dex_config protocol key, is_v2)
_DL_PROJECT_TO_PROTO = {
    'uniswap-v3': ('uniswap_v3', False),
    'pancakeswap-amm-v3': ('pancakeswap_v3', False),
    'uniswap-v2': ('uniswap_v2', True),
}


def _dl_fee_to_bips(pool_meta: Optional[str]) -> Optional[int]:
    """'0.05%' -> 500, '1%' -> 10000, '0.3%' -> 3000 (Uniswap fee units)."""
    if not pool_meta or '%' not in pool_meta:
        return None
    try:
        return round(float(pool_meta.replace('%', '').strip()) * 10000)
    except ValueError:
        return None


# Standard Uniswap V4 mainnet fee -> tickSpacing (only the four canonical tiers).
# Non-standard fees (0.25%, dynamic, etc.) are skipped — their tickSpacing/hooks
# aren't knowable from DeFi Llama's data, so we don't risk a wrong derivation.
_V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}
_NATIVE_ZERO = '0x0000000000000000000000000000000000000000'


def _derive_v4_pool_id(c0_hex: str, c1_hex: str, fee: int, tick_spacing: int) -> str:
    """V4 poolId = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks)),
    assuming hooks = address(0). currency0 < currency1 (sorted).

    Delegates to the shared chain-feeder/include/v4_pool.py implementation so the
    API and the Airflow ingestion use one source of truth.
    """
    from include.v4_pool import derive_v4_pool_id
    return derive_v4_pool_id(c0_hex, c1_hex, fee, tick_spacing)


def _build_defillama_index() -> Dict[str, str]:
    resp = requests.get('https://yields.llama.fi/pools', timeout=30.0)
    resp.raise_for_status()
    pools = resp.json().get('data', [])
    index: Dict[str, str] = {}
    for p in pools:
        project = p.get('project')
        uuid = p.get('pool')
        if not uuid:
            continue
        tokens = p.get('underlyingTokens') or []
        if len(tokens) != 2:
            continue

        # Uniswap V4: derive the on-chain poolId from DeFi Llama's raw
        # underlyingTokens (native 0x0 for ETH — matching Chaintelligence's
        # RPC-derived V4 pool_address) and map it to the UUID. Standard fee
        # tiers only; non-standard fees have no knowable tickSpacing here.
        if project == 'uniswap-v4':
            fee = _dl_fee_to_bips(p.get('poolMeta'))
            if fee is None:
                continue
            tick = _V4_TICK_SPACING.get(fee)
            if tick is None:
                continue
            try:
                pool_id = _derive_v4_pool_id(tokens[0], tokens[1], fee, tick)
            except ValueError:
                continue
            index[pool_id.lower()] = {'uuid': uuid, 'tvl': p.get('tvlUsd')}
            continue

        # V2/V3: CREATE2 derivation
        mapping = _DL_PROJECT_TO_PROTO.get(project)
        if not mapping:
            continue
        proto_key, is_v2 = mapping
        net = _DL_CHAIN_TO_NET.get(p.get('chain'))
        if not net:
            continue
        cfg = DEX_CONFIG.get(proto_key) or {}
        # pancakeswap_v3 uses 'eth' instead of 'ethereum'
        net_cfg = cfg.get(net) or (cfg.get('eth') if net == 'ethereum' else None)
        if not net_cfg or 'factory' not in net_cfg:
            continue
        try:
            t0b = bytes.fromhex(tokens[0].lower().removeprefix('0x'))
            t1b = bytes.fromhex(tokens[1].lower().removeprefix('0x'))
        except ValueError:
            continue
        if is_v2:
            addr = _derive_address(t0b, t1b, 0, net_cfg['factory'], net_cfg['init_hash'], is_v2=True)
        else:
            fee = _dl_fee_to_bips(p.get('poolMeta'))
            if fee is None:
                continue
            addr = _derive_address(t0b, t1b, fee, net_cfg['factory'], net_cfg['init_hash'], is_v2=False)
        index[addr.lower()] = {'uuid': uuid, 'tvl': p.get('tvlUsd')}
    return index


_DEFILLAMA_BUILDING = False

def trigger_defillama_index_build():
    global _DEFILLAMA_BUILDING
    with _DEFILLAMA_LOCK:
        if _DEFILLAMA_BUILDING:
            return
        _DEFILLAMA_BUILDING = True

    def _worker():
        global DEFILLAMA_INDEX, DEFILLAMA_INDEX_BUILT_AT, _DEFILLAMA_BUILDING
        try:
            idx = _build_defillama_index()
            if idx:
                DEFILLAMA_INDEX = idx
                DEFILLAMA_INDEX_BUILT_AT = time.time()
                print(f"[DeFiLlama] yields index built: {len(idx)} pools")
        except Exception as e:
            print(f"[DeFiLlama] yields index build failed: {e}")
        finally:
            with _DEFILLAMA_LOCK:
                _DEFILLAMA_BUILDING = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

def get_defillama_index() -> Dict[str, dict]:
    """Return the cached pool_address->UUID index, triggering background rebuild if stale."""
    now = time.time()
    if not DEFILLAMA_INDEX or (now - DEFILLAMA_INDEX_BUILT_AT >= DEFILLAMA_INDEX_TTL):
        trigger_defillama_index_build()
    return DEFILLAMA_INDEX


def get_defillama_pool_uuid(pool_addr: Optional[str]) -> Optional[str]:
    if not pool_addr:
        return None
    return get_defillama_index().get(pool_addr.lower(), {}).get('uuid')


def get_defillama_pool_tvl(pool_addr: Optional[str]) -> Optional[float]:
    if not pool_addr:
        return None
    return get_defillama_index().get(pool_addr.lower(), {}).get('tvl')


def build_pool_links(
    pool_address: Optional[str],
    v4_pool_id: Optional[str],
    protocol: Optional[str],
    network: Optional[str],
    defillama_uuid: Optional[str] = None,
) -> dict:
    links = {}
    proto_lower = (protocol or "").lower()
    net_lower = (network or "").lower()
    is_v4 = "v4" in proto_lower
    link_addr = (v4_pool_id if is_v4 and v4_pool_id and len(v4_pool_id) == 66 else pool_address) or ""
    if not link_addr:
        if defillama_uuid:
            links["defillama"] = f"https://defillama.com/yields/pool/{defillama_uuid}"
        return links

    net_seg = "ethereum"
    if "base" in net_lower:
        net_seg = "base"
    elif "arbitrum" in net_lower:
        net_seg = "arbitrum"
    elif "optimism" in net_lower:
        net_seg = "optimism"
    elif "polygon" in net_lower:
        net_seg = "polygon"
    elif "bnb" in net_lower or "bsc" in net_lower:
        net_seg = "bnb"

    if "pancake" in proto_lower:
        pchain = "bsc"
        if "base" in net_lower:
            pchain = "base"
        elif "eth" in net_lower:
            pchain = "eth"
        elif "arbitrum" in net_lower:
            pchain = "arb"
        if is_v4:
            if len(link_addr) == 66:
                links["pancakeswap"] = f"https://pancakeswap.finance/liquidity/pool/{pchain}/{link_addr}"
            else:
                links["pancakeswap"] = f"https://pancakeswap.finance/info/infinity/pairs/tokens/{link_addr}?chain={pchain}"
        else:
            links["pancakeswap"] = f"https://pancakeswap.finance/info/v3/pairs/{link_addr}?chain={pchain}"
    elif "uniswap" in proto_lower or "v3" in proto_lower or "v2" in proto_lower:
        uni_net = net_seg
        if uni_net == "bnb":
            uni_net = "bnb"
        links["uniswap"] = f"https://app.uniswap.org/explore/pools/{uni_net}/{link_addr}"

    ds_chain = net_seg
    if ds_chain == "bnb":
        ds_chain = "bsc"
    links["dexscreener"] = f"https://dexscreener.com/{ds_chain}/{link_addr.lower()}"

    revert_net = {"bnb": "bsc", "ethereum": "mainnet"}.get(net_seg, net_seg)
    revert_proto = None
    if "uniswap" in proto_lower:
        revert_proto = "uniswapv4" if "v4" in proto_lower else "uniswapv3"
    elif "pancake" in proto_lower and "v3" in proto_lower and revert_net in ("bsc", "arbitrum"):
        revert_proto = "pancakeswapv3"
    elif "aerodrome" in proto_lower and revert_net == "base":
        revert_proto = "aerodrome"
    if revert_proto:
        num_id = None
        if v4_pool_id and str(v4_pool_id).strip().isdigit():
            num_id = str(v4_pool_id).strip()
        elif pool_address and str(pool_address).strip().isdigit():
            num_id = str(pool_address).strip()

        # If Uniswap V4 and no numeric ID passed directly, look up token_id from DB
        if not num_id and revert_proto == "uniswapv4":
            v4_lookup_key = (v4_pool_id if (v4_pool_id and len(v4_pool_id) == 66) else pool_address) or ""
            if v4_lookup_key:
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT lpp.token_id 
                            FROM liquidity_pool_position lpp
                            JOIN liquidity_pool lp ON lpp.pool_id = lp.id
                            WHERE (lp.pool_id = %s OR lp.pool_address = %s)
                              AND lpp.token_id IS NOT NULL 
                            LIMIT 1
                        """, (v4_lookup_key, v4_lookup_key))
                        row = cur.fetchone()
                        if row and row[0]:
                            num_id = str(row[0]).strip()
                except Exception:
                    pass

        if num_id:
            sub = "uniswapv4-position" if revert_proto == "uniswapv4" else "uniswap-position"
            links["revert"] = f"https://revert.finance/#/{sub}/{revert_net}/{num_id}"
        elif revert_proto != "uniswapv4":
            links["revert"] = f"https://revert.finance/#/pool/{revert_net}/{revert_proto}/{link_addr.lower()}"

    # 1. Block Explorer (Etherscan, Arbiscan, Basescan, BscScan, etc.)
    explorer_addr = pool_address or link_addr
    if explorer_addr and len(explorer_addr) == 42:
        explorer_base = {
            "ethereum": "https://etherscan.io/address/",
            "arbitrum": "https://arbiscan.io/address/",
            "base": "https://basescan.org/address/",
            "bnb": "https://bscscan.com/address/",
            "optimism": "https://optimistic.etherscan.io/address/",
            "polygon": "https://polygonscan.com/address/"
        }.get(net_seg, "https://etherscan.io/address/")
        links["explorer"] = explorer_base + explorer_addr

    # 2. GeckoTerminal
    gecko_chain = {
        "ethereum": "eth",
        "arbitrum": "arbitrum",
        "base": "base",
        "bnb": "bsc",
        "optimism": "optimism",
        "polygon": "polygon_pos"
    }.get(net_seg, "eth")
    links["geckoterminal"] = f"https://www.geckoterminal.com/{gecko_chain}/pools/{link_addr.lower()}"

    # 3. Defined.fi
    defined_chain = {
        "ethereum": "eth",
        "arbitrum": "arbitrum",
        "base": "base",
        "bnb": "bsc",
        "optimism": "optimism",
        "polygon": "polygon"
    }.get(net_seg, "eth")
    links["defined"] = f"https://www.defined.fi/{defined_chain}/{link_addr.lower()}"

    if defillama_uuid:
        links["defillama"] = f"https://defillama.com/yields/pool/{defillama_uuid}"

    return links


try:
    from postgres_fetcher import PostgresFetcher, get_conn
    from route_analyzer import RouteAnalyzer
    from shortcut_finder import ShortcutFinder
    from config import DATA_WAREHOUSE_DB
    import undercut_analyzer as ua
    import swap_distribution as sd
    from graph import (  # JSON:API object-graph serializer
        build_coin_documents, build_coin_family_documents,
        build_od_documents, build_pool_documents, build_route_documents,
    )
except ImportError as e:
    print(f"Error importing routing modules from {API_ROUTING}: {e}")
    sys.exit(1)

app = FastAPI(
    title="Chaintelligence Portal API",
    description="Secure API for Chaintelligence DeFi analytics platform.",
    version="1.1.0",
    docs_url=None,
    redoc_url=None
)

# --- Authentication Middleware ---
PORTAL_USER = os.getenv("PORTAL_USERNAME", "admin")
PORTAL_PASS = os.getenv("PORTAL_PASSWORD", "chaintelligence")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Exempt metadata and backtester routes from authentication
    exempt_paths = ["/api/coins/list", "/api/coins/search-by-symbol", "/api/coin-families", "/api/coin/price-history", "/api/ods/goal-state", "/backtester", "/pool", "/favicon.ico", "/static", "/routing", "/lp", "/health", "/docs", "/swagger", "/openapi.json", "/status", "/health-status", "/pool-arena", "/api/pool-arena", "/api/swap-distribution"]
    if any(request.url.path.startswith(path) for path in exempt_paths) or request.method == "OPTIONS":
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    unauthorized_response = Response(
        status_code=401,
        content="Unauthorized",
        headers={"WWW-Authenticate": "Basic realm='Chaintelligence Portal'"}
    )

    if not auth_header or not auth_header.startswith("Basic "):
        return unauthorized_response

    try:
        encoded_creds = auth_header.split(" ")[1]
        decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
        username, password = decoded_creds.split(":", 1)
        
        is_valid = secrets.compare_digest(username, PORTAL_USER) and \
                   secrets.compare_digest(password, PORTAL_PASS)
        
        if not is_valid:
            return unauthorized_response
    except Exception:
        return unauthorized_response

    return await call_next(request)

# --- Endpoints ---

# Serve static files for routing-web
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(os.path.join(STATIC_DIR, 'favicon.png'))

# Serve LP Backtester as a separate static site
BACKTESTER_DIR = os.path.join(WEB_DIR, 'backtest')
app.mount("/backtester", StaticFiles(directory=BACKTESTER_DIR, html=True), name="backtester")

class AnalysisRequest(BaseModel):
    start_token: str
    end_token: str
    days: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class HistoryFeederRequest(BaseModel):
    force_update: bool = False
    coin_symbols: List[str] = []

class PoolArenaPool(BaseModel):
    name: str = "Pool"
    liquidity_usd: float = 100000.0   # total two-sided liquidity
    range_pct: float = 10.0
    fee_bps: float = 30.0             # 0.3% default

class PoolArenaSwaps(BaseModel):
    count: int = 2000
    seed: int = 7
    vol_min: float = 50.0
    vol_max: float = 50000.0
    direction_bias: float = 0.5       # probability a swap is start->end

class PoolArenaRequest(BaseModel):
    pools: List[PoolArenaPool] = Field(default_factory=lambda: [
        PoolArenaPool(name="Baseline", liquidity_usd=100000.0),
        PoolArenaPool(name="Deep", liquidity_usd=500000.0),
    ])
    swaps: PoolArenaSwaps = Field(default_factory=PoolArenaSwaps)
    days: float = 30.0

def resolve_token_input(input_str: str) -> list[str]:
    """
    Resolve input string to a list of tokens.
    Checks if input is a family name (e.g. 'USD') -> returns ['USDC', 'USDT', ...].
    Otherwise returns [input].
    """
    if input_str == '*':
        return ['*']

    try:
        with get_conn() as conn:
            cur = conn.cursor()

            # Check if it's a family
            # We search case-insensitive for family name in the official coin_family table
            cur.execute("""
                SELECT c.symbol 
                FROM coin_family f
                JOIN coin c ON f.coin_id = c.coin_id
                WHERE UPPER(f.name) = %s
            """, (input_str.upper(),))
            rows = cur.fetchall()

            cur.close()

        if rows:
            return [row[0] for row in rows]

        # Not a family, assume single token
        return [input_str]

    except Exception as e:
        print(f"Error resolving token family: {e}")
        return [input_str]

def resolve_od_set_side(term: str) -> dict:
    """Resolve one O&D-set side (symbol | coin family | '*' | contract address) into constraints.

    Returned constraints match the corresponding column pair of
    `origin_destination_pair` (origin_coin_id/origin_symbol/origin_contract or
    the dest_* sibling), so the same dict drives both the forward and the
    reversed direction of a `direction=both` query.

    Returns:
        {"wild": bool, "coin_ids": list[int], "symbols": list[str],
         "addresses": list[str]}

    Resolution order:
      - '*' (or empty): wildcard — the side matches every coin.
      - Contract address (0x… / 40 hex chars): matched verbatim against the
        contract column; if the address is tracked in coin_contract its
        coin_id(s) are added so already-normalized pairs match too.
      - Coin family name: expanded to member coin_ids + symbols.
      - Anything else: treated as a coin symbol, resolved to coin_id(s) via
        the coin table (additive so an exact symbol in the pair table matches
        even if the coin lookup misses).
    """
    term = term or ''
    result: dict = {'wild': False, 'coin_ids': [], 'symbols': [], 'addresses': []}

    if not term.strip() or term == '*':
        result['wild'] = True
        return result

    looks_like_addr = term.lower().startswith('0x') or (
        len(term) == 40 and all(c in '0123456789abcdefABCDEF' for c in term)
    )

    try:
        with get_conn() as conn:
            cur = conn.cursor()
            if looks_like_addr:
                addr = term.strip().lower()
                result['addresses'].append(addr)
                cur.execute("""
                    SELECT DISTINCT cc.coin_id
                    FROM coin_contract cc
                    WHERE LOWER(cc.contract_address) = %s
                """, (addr,))
                result['coin_ids'] = [r[0] for r in cur.fetchall()]
                cur.close()
                return result

            # Coin family?
            cur.execute("""
                SELECT c.coin_id, c.symbol
                FROM coin_family f
                JOIN coin c ON f.coin_id = c.coin_id
                WHERE UPPER(f.name) = %s
            """, (term.upper(),))
            rows = cur.fetchall()
            if rows:
                result['coin_ids'] = [r[0] for r in rows]
                result['symbols'] = [r[1] for r in rows]
                cur.close()
                return result

            # Coin symbol (may map to several coin_ids across chains).
            cur.execute("""
                SELECT DISTINCT coin_id, symbol
                FROM coin
                WHERE UPPER(symbol) = UPPER(%s)
            """, (term,))
            rows = cur.fetchall()
            cur.close()
            if rows:
                result['coin_ids'] = [r[0] for r in rows]
                result['symbols'] = [r[1] for r in rows]
            else:
                result['symbols'] = [term]
            return result
    except Exception as e:
        print(f"Error resolving O&D set side '{term}': {e}")
        result['symbols'] = [term.upper() if not looks_like_addr else term]
        return result


def _od_set_side_sql(side: str, res: dict) -> tuple[str, list]:
    """Build the WHERE fragment for one side of an O&D-set match.

    ``side`` is 'origin' or 'dest' and selects the origin_*/dest_* columns.
    A wildcard resolution becomes `1=1`; otherwise the coin_id / symbol /
    contract constraints are OR-ed together (any of them may identify the
    token, so all three are additive).
    """
    if res.get('wild'):
        return "1=1", []

    clauses: List[str] = []
    params: List = []
    if res.get('coin_ids'):
        clauses.append(f"pair.{side}_coin_id = ANY(%s)")
        params.append(res['coin_ids'])
    if res.get('symbols'):
        clauses.append(f"UPPER(pair.{side}_symbol) = ANY(%s)")
        params.append([s.upper() for s in res['symbols']])
    if res.get('addresses'):
        clauses.append(f"LOWER(pair.{side}_contract) = ANY(%s)")
        params.append(res['addresses'])
    if not clauses:
        return "1=0", []
    return "(" + " OR ".join(clauses) + ")", params


# Global memory cache for resolved token symbols to contract addresses per network to prevent repetitive slow DB queries
TOKEN_ADDRESS_CACHE = {}

@app.get("/api/routes/analyze", tags=["Route"])
async def analyze(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None, description="Lookback period in days"),
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network"),
    direction: str = Query("forward", description="Route direction: forward (start->end), reverse (end->start), or both"),
    max_hops: Optional[int] = Query(None, description="Max route hop count (1 for direct routes only)")
):
    """Analyze swap routes between two tokens."""
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            def safe_parse_iso(date_str: str) -> datetime:
                s = date_str.replace('Z', '+00:00').strip()
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    date_part = s.split('T')[0]
                    parts = date_part.split('-')
                    if len(parts) == 3:
                        yr, mo, dy = int(parts[0]), int(parts[1]), int(parts[2])
                        import calendar
                        max_d = calendar.monthrange(yr, mo)[1]
                        if dy > max_d:
                            s_fixed = f"{yr:04d}-{mo:02d}-{max_d:02d}"
                            if 'T' in s:
                                s_fixed += 'T' + s.split('T')[1]
                            return datetime.fromisoformat(s_fixed)
                    raise

            start_dt = safe_parse_iso(start_date)
            if end_date:
                end_dt = safe_parse_iso(end_date)
                if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0 and end_dt.microsecond == 0:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                end_dt = now
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=1)

        # Resolve tokens/families FIRST so we can use them in processing
        start_tokens_list = resolve_token_input(start_token)
        end_tokens_list = resolve_token_input(end_token)
        
        if not start_tokens_list: start_tokens_list = [start_token]
        if not end_tokens_list: end_tokens_list = [end_token]

        fetcher = PostgresFetcher(verbose=True)

        # Build token_filter to prevent fetching millions of irrelevant rows
        token_filter = []
        if "*" not in start_tokens_list:
            token_filter.extend(start_tokens_list)
        if "*" not in end_tokens_list:
            token_filter.extend(end_tokens_list)
        if not token_filter:
            token_filter = None # Fallback if both are wildcards

        # Direction: forward analyzes start->end only, reverse end->start,
        # both merges the two directions. The analyzer itself is strictly
        # directional (it keys off the first log entry spending a start token),
        # so reverse is implemented by swapping the token roles.
        dir_norm = (direction or 'forward').lower()
        if dir_norm not in ('forward', 'reverse', 'both'):
            dir_norm = 'forward'

        from fastapi.responses import StreamingResponse
        import json
        import asyncio
        import psycopg2

        # Token roles per requested direction: forward analyzes start->end with
        # start_tokens as the origin role; reverse swaps the roles so a tx whose
        # first log entry spends the end token is analyzed as end->start.
        analytics_inputs = []
        if dir_norm in ('forward', 'both'):
            analytics_inputs.append(('forward', start_tokens_list, end_tokens_list))
        if dir_norm in ('reverse', 'both'):
            analytics_inputs.append(('reverse', end_tokens_list, start_tokens_list))

        async def generate():
            yield json.dumps({"type": "progress", "pct": 10.0, "message": f"Loading pre-aggregated route stats for {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}..."}) + "\n"
            await asyncio.sleep(0.01)

            async def _fetch_stats(label, s_tokens, e_tokens):
                # Stat-backed fast path: route_daily_stats groups per (route, day),
                # so a long window is a few hundred summed rows instead of a full
                # swaps sweep. Falls back to the streaming swap path on any schema/
                # population error (legacy DB without route tables).
                try:
                    return await asyncio.to_thread(
                        fetcher.fetch_route_stats, start_dt, end_dt,
                        s_tokens, e_tokens, network, label
                    )
                except Exception as e:
                    print(f"[routes/analyze] route-stats path failed ({e}); falling back to swap-stream path", flush=True)
                    return None

            stat_results = await asyncio.gather(
                *[_fetch_stats(label, st, et) for label, st, et in analytics_inputs]
            )

            has_data = any(res is not None and res.get('routes') for res in stat_results)
            if not has_data:
                # No route stats for this token pair in the window. Report an
                # empty result with the available data range sourced from
                # liquidity_pool_daily_stats (no raw-swaps read).
                yield json.dumps({"type": "progress", "pct": 40.0, "message": "No route data for this window..."}) + "\n"
                await asyncio.sleep(0.01)

                def _db_range():
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("SET LOCAL statement_timeout = '30s'")
                        if network and network.lower() != 'all':
                            cur.execute("""
                                SELECT MIN(lph.day)::date, MAX(lph.day)::date
                                FROM liquidity_pool_daily_stats lph
                                JOIN liquidity_pool lp ON lph.pool_id = lp.id
                                JOIN chain ch ON lp.chain_id = ch.id
                                WHERE LOWER(ch.name) = LOWER(%s)
                            """, (network,))
                        else:
                            cur.execute("SELECT MIN(day)::date, MAX(day)::date FROM liquidity_pool_daily_stats")
                        return cur.fetchone()

                row = await asyncio.to_thread(_db_range)
                db_min = row[0].isoformat() if row and row[0] else None
                db_max = row[1].isoformat() if row and row[1] else None
                yield json.dumps({"type": "result", "data": {"routes": [], "total_tx": 0, "total_volume": 0, "db_range": {"min": db_min, "max": db_max}}}) + "\n"
                return
            else:
                yield json.dumps({"type": "progress", "pct": 75.0, "message": "Building routing path graph..."}) + "\n"
                await asyncio.sleep(0.01)

                # Combine per-direction results. Each direction labels its routes so
                # the frontend can distinguish forward vs reverse chains in `both`.
                analysis = {'routes': [], 'total_tx': 0, 'total_volume': 0.0}
                for (label, _s, _e), res in zip(analytics_inputs, stat_results):
                    if not res:
                        continue
                    for r in res.get('routes', []):
                        r['direction'] = label
                    analysis['routes'].extend(res.get('routes', []))
                    analysis['total_tx'] += res.get('total_tx', 0)
                    analysis['total_volume'] += res.get('total_volume', 0.0)
                analysis['routes'].sort(key=lambda r: r.get('volume', 0), reverse=True)

            if max_hops is not None:
                def _get_hops(r):
                    if 'hops' in r and r['hops'] is not None:
                        return r['hops']
                    pt = r.get('path_tokens', [])
                    return max(1, (len(pt) - 1) // 2) if pt else 1
                analysis['routes'] = [r for r in analysis['routes'] if _get_hops(r) <= max_hops]
        
            # --- Enrichment with APRs ---
            # 1. Identify pools
            pools_to_fetch = set()
            for route in analysis.get('routes', []):
                path = route.get('path_tokens', [])
                # Path: [Token, Fee, Token, Fee, Token]
                for i in range(0, len(path) - 2, 2):
                    t0 = path[i]
                    fee = path[i+1]
                    t1 = path[i+2]
                    pools_to_fetch.add((t0, t1, fee))
        
            # 2. Fetch stats
            aprs = {}
            if pools_to_fetch:
                yield json.dumps({"type": "progress", "pct": 80.0, "message": "Querying pool stats & APRs..."}) + "\n"
                await asyncio.sleep(0.01)
                try:
                    aprs = await asyncio.to_thread(
                        fetcher.fetch_pool_stats, list(pools_to_fetch), start_dt, end_dt
                    )
                except Exception as e:
                    print(f"Error fetching pool stats: {e}")


            # 2b. Compute pool addresses deterministically using Create2/Keccak-256
            pool_addresses = {}
            if pools_to_fetch:
                yield json.dumps({"type": "progress", "pct": 90.0, "message": "Generating pool smart contract addresses..."}) + "\n"
                await asyncio.sleep(0.01)
                token_symbols = set()
                # Collect all unique networks needed for these pools
                needed_networks = set()
                for (t0, t1, fee) in pools_to_fetch:
                    token_symbols.add(t0.upper())
                    token_symbols.add(t1.upper())
                    parts = str(fee).split('|')
                    pool_network = parts[2].strip() if len(parts) >= 3 else "Ethereum"
                    needed_networks.add(pool_network)

                token_addresses = {}
                if token_symbols:
                    # Fetch dynamically ONLY for networks actually needed by the pools
                    for target_network in needed_networks:
                        token_addresses[target_network] = {}

                        if target_network not in TOKEN_ADDRESS_CACHE:
                            TOKEN_ADDRESS_CACHE[target_network] = {}

                        # Pre-populate from global cache
                        for sym in token_symbols:
                            if sym in TOKEN_ADDRESS_CACHE[target_network]:
                                token_addresses[target_network][sym] = TOKEN_ADDRESS_CACHE[target_network][sym]

                        # Database lookup for any remaining missing symbols (using central coin_contract table)
                        missing_symbols = [sym for sym in token_symbols if sym not in token_addresses[target_network]]
                        if missing_symbols:
                            try:
                                with get_conn() as conn:
                                    cur = conn.cursor()
                                    db_chain = 'bsc' if target_network.lower() == 'bnb' else target_network.lower()
                                    cur.execute("""
                                        SELECT UPPER(c.symbol), cc.contract_address 
                                        FROM coin_contract cc
                                        JOIN coin c ON cc.coin_id = c.coin_id
                                        JOIN chain ch ON cc.chain_id = ch.id
                                        WHERE (LOWER(ch.name) = %s OR (LOWER(ch.name) = 'bnb' AND %s = 'bsc'))
                                          AND UPPER(c.symbol) = ANY(%s)
                                    """, (db_chain, db_chain, missing_symbols))
                                    for row in cur.fetchall():
                                        if row[1]:
                                            token_addresses[target_network][row[0]] = row[1]
                                            TOKEN_ADDRESS_CACHE[target_network][row[0]] = row[1]
                                    cur.close()
                            except Exception as e:
                                print(f"Error fetching token addresses from DB: {e}")
            
                # Build a list of derivation jobs, each as a dict with all needed info.
                # Errors for individual pools (missing addresses, unsupported protocol/network,
                # fee parsing) are caught inline; only valid jobs make it into the list.
                jobs = []
                v4_keys = []  # Collect V4 pool keys for batch DB lookup
                for (t0, t1, fee) in pools_to_fetch:
                    try:
                        t0_sym, t1_sym = t0.upper(), t1.upper()
                        parts = str(fee).split('|')

                        pool_network = parts[2].strip() if len(parts) >= 3 else "Ethereum"
                        proto_raw = parts[1].strip() if len(parts) >= 2 else "Uniswap V3"
                        proto_lower = proto_raw.lower()

                        if proto_lower in ('v4', 'uniswap v4', 'uniswap-v4',
                                           'pancakeswap v4', 'pancake v4',
                                           'pancakeswap-v4', 'pancake-v4'):
                            # V4: no CREATE2 address; pool_id is fetched from DB
                            # (singleton PoolManager model — applies to both
                            # Uniswap V4 and PancakeSwap V4 / Infinity).
                            # Normalize the protocol label so the key matches
                            # what the V4 pool_id lookup builds from the DB.
                            v4_proto = 'PancakeSwap V4' if 'pancake' in proto_lower else 'Uniswap V4'
                            # Normalize fee to bips for key matching with DB
                            fee_clean_v4 = parts[0].replace('%', '').strip()
                            fee_map_v4 = {'0.01': '100', '0.05': '500', '0.08': '800',
                                          '0.25': '2500', '0.3': '3000', '1.0': '10000'}
                            if fee_clean_v4 in fee_map_v4:
                                fee_norm = fee_map_v4[fee_clean_v4]
                            else:
                                try:
                                    fv = float(fee_clean_v4)
                                    if fv > 0 and fv < 5:
                                        fee_norm = str(int(fv * 10000))
                                    else:
                                        fee_norm = str(int(fv))
                                except:
                                    fee_norm = parts[0]
                            v4_keys.append(f"{t0_sym}-{t1_sym}-{fee_norm}|{v4_proto}|{pool_network}")
                            continue

                        if proto_lower in ('aerodrome',):
                            # Aerodrome (Slipstream, V3-fork) pools are NOT
                            # CREATE2-derivable with the V3 factory/init-hash
                            # (different PoolDeployer + init code). Skip
                            # derivation; pool cards still render from swap
                            # data. APR/address enrichment is a follow-up.
                            continue

                        if proto_lower in ('v2', 'uniswap v2', 'uniswap-v2'):
                            protocol = 'Uniswap V2'
                            # V2 has a single fee tier (0.30%), so fee_val is not used in CREATE2 salt
                            # V2 address derivation uses abi.encodePacked (no padding) — handled below
                        else:
                            protocol = 'Uniswap V3' if proto_lower in ('v3', 'uniswap v3', 'uniswap-v3') else proto_raw

                        addr0 = token_addresses.get(pool_network, {}).get(t0_sym)
                        addr1 = token_addresses.get(pool_network, {}).get(t1_sym)
                        if not addr0 or not addr1:
                            continue

                        fee_clean = parts[0].replace('%', '').strip()
                        fee_map = {'0.01': 100, '0.05': 500, '0.08': 800, '0.3': 3000, '1.0': 10000}
                        fee_val = fee_map.get(fee_clean) or int(float(fee_clean) * 10000)

                        # Sorted token addresses (contract creation order)
                        tokens = sorted([addr0.lower(), addr1.lower()])
                        t0_bytes = bytes.fromhex(tokens[0][2:])
                        t1_bytes = bytes.fromhex(tokens[1][2:])
                        key = f"{t0}-{t1}-{fee}"

                        # Map network name to config key (e.g. "BNB" → "bsc")
                        net_map = {"BNB": "bsc", "ETH": "ethereum"}
                        cfg_network = net_map.get(pool_network, pool_network.lower())

                        # Retrieve factory and init_hash (cache hit avoids config lookup)
                        fh_key = (protocol, cfg_network)
                        if fh_key in FACTORY_HASH_CACHE:
                            factory_hex, init_hash_hex = FACTORY_HASH_CACHE[fh_key]
                        else:
                            try:
                                factory_hex, init_hash_hex = get_factory_and_hash(protocol, cfg_network)
                                FACTORY_HASH_CACHE[fh_key] = (factory_hex, init_hash_hex)
                            except ValueError as ex:
                                print(f"  Skipping {key}: {ex}")
                                continue

                        is_v2 = (protocol == 'Uniswap V2')
                        pool_cache_key = (tokens[0], tokens[1], fee_val, protocol, pool_network)
                        jobs.append({
                            'key': key,
                            'pool_cache_key': pool_cache_key,
                            't0_bytes': t0_bytes,
                            't1_bytes': t1_bytes,
                            'fee_val': fee_val,
                            'factory_hex': factory_hex,
                            'init_hash_hex': init_hash_hex,
                            'is_v2': is_v2,
                        })
                    except Exception as ex:
                        print(f"  Skipping pool ({t0},{t1},{fee}): {ex}")
                        continue

                # Derive pool addresses in a single thread — keccak is CPU-bound,
                # so per-call to_thread overhead would dominate with hundreds of pools.
                if jobs:
                    def _derive_batch():
                        batch_results = {}
                        for j in jobs:
                            pk = j['pool_cache_key']
                            if pk in POOL_ADDRESS_CACHE:
                                addr = POOL_ADDRESS_CACHE[pk]
                            else:
                                try:
                                    addr = _derive_address(
                                        j['t0_bytes'], j['t1_bytes'], j['fee_val'],
                                        j['factory_hex'], j['init_hash_hex'],
                                        is_v2=j.get('is_v2', False)
                                    )
                                    POOL_ADDRESS_CACHE[pk] = addr
                                except Exception as ex:
                                    print(f"  Error deriving address for {j['key']}: {ex}")
                                    continue
                            batch_results[j['key']] = {
                                "pool_address": addr,
                                "pool_id": addr
                            }
                        return batch_results

                    batch = await asyncio.to_thread(_derive_batch)
                    pool_addresses.update(batch)

                def _lookup_db_pools(pools_to_fetch=None):
                    """Fetch pool identifiers and IDs from DB in one query."""
                    db_results = {}
                    where_clause = ""
                    params = []
                    if pools_to_fetch:
                        symbols = set()
                        for (t0, t1, fee) in pools_to_fetch:
                            symbols.add(t0.upper())
                            symbols.add(t1.upper())
                        if symbols:
                            where_clause = " WHERE UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)"
                            sym_list = list(symbols)
                            params = [sym_list, sym_list]
                    try:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute(f"""
                                 SELECT ch.name AS network, pr.name AS protocol,
                                        CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic' ELSE (lp.fee_bps / 100.0)::text || '%%' END AS fee_tier,
                                        lp.pool_id,
                                        UPPER(c0.symbol) AS s0,
                                        UPPER(c1.symbol) AS s1,
                                        cc0.contract_address AS t0_addr,
                                        lp.pool_address,
                                        lp.id AS cid
                                 FROM liquidity_pool lp
                                 JOIN chain ch ON lp.chain_id = ch.id
                                 JOIN protocol pr ON lp.protocol_id = pr.id
                                 JOIN coin c0 ON lp.coin0_id = c0.coin_id
                                 JOIN coin c1 ON lp.coin1_id = c1.coin_id
                                 LEFT JOIN coin_contract cc0
                                     ON cc0.coin_id = lp.coin0_id
                                    AND cc0.chain_id = lp.chain_id
                                 {where_clause}
                             """, params)
                            for net, proto, fee_tier, pid, sym0, sym1, t0_addr, lp_addr, cid in cur.fetchall():
                                v4_pool_id = pid
                                v4_pool_addr = lp_addr
                                if proto == 'PancakeSwap V4':
                                    if pid and len(pid) == 66:
                                        pass
                                    elif t0_addr:
                                        v4_pool_addr = t0_addr
                                        v4_pool_id = t0_addr
                                
                                if not v4_pool_addr:
                                    v4_pool_addr = pid
                                
                                value = {
                                    "pool_address": v4_pool_addr or "",
                                    "pool_id": v4_pool_id or v4_pool_addr or "",
                                    "cid": cid
                                }
                                
                                s0_norm = 'WETH' if sym0 == 'ETH' else ('WBNB' if sym0 == 'BNB' else sym0)
                                s1_norm = 'WETH' if sym1 == 'ETH' else ('WBNB' if sym1 == 'BNB' else sym1)

                                fee_keys = {fee_tier}
                                if '%' in fee_tier:
                                    clean_pct = fee_tier.replace('%', '').strip()
                                    fee_keys.add(clean_pct)
                                    try:
                                        val = float(clean_pct)
                                        bps = int(round(val * 10000))
                                        fee_keys.add(str(bps))
                                    except ValueError:
                                        pass
                                else:
                                    try:
                                        val = float(fee_tier)
                                        if val < 5:
                                            pct_str = f'{val:.6f}'.rstrip('0').rstrip('.')
                                            fee_keys.add(f'{pct_str}%')
                                            fee_keys.add(str(int(round(val * 10000))))
                                        else:
                                            bps = int(val)
                                            pct = bps / 10000.0
                                            pct_str = f'{pct:.6f}'.rstrip('0').rstrip('.')
                                            fee_keys.add(f'{pct_str}%')
                                            fee_keys.add(str(bps))
                                    except ValueError:
                                        pass

                                proto_variants = {proto, proto.lower(), proto.replace(' ', '-').lower()}
                                if 'v3' in proto.lower():
                                    proto_variants.update({'v3', 'uniswap v3', 'pancakeswap v3'})
                                elif 'v4' in proto.lower():
                                    proto_variants.update({'v4', 'uniswap v4', 'pancakeswap v4'})
                                elif 'v2' in proto.lower():
                                    proto_variants.update({'v2', 'uniswap v2', 'pancakeswap v2'})

                                net_variants = {net, net.lower(), ''}

                                for fk in fee_keys:
                                    if not fk:
                                        continue
                                    for pv in proto_variants:
                                        for nv in net_variants:
                                            suffix = f"|{pv}|{nv}" if nv else f"|{pv}"
                                            db_results[f"{s0_norm}-{s1_norm}-{fk}{suffix}"] = value
                                            db_results[f"{s1_norm}-{s0_norm}-{fk}{suffix}"] = value
                                            db_results[f"{sym0.upper()}-{sym1.upper()}-{fk}{suffix}"] = value
                                            db_results[f"{sym1.upper()}-{sym0.upper()}-{fk}{suffix}"] = value

                                if lp_addr:
                                    db_results[lp_addr.lower()] = value
                                if pid:
                                    db_results[pid.lower()] = value
                    except Exception as ex:
                        print(f"  Error looking up DB pools: {ex}")
                    return db_results

                db_batch = await asyncio.to_thread(_lookup_db_pools, pools_to_fetch)
                # Preserve derived CREATE2 addresses for V2/V3 pools (the DB
                # may contain stale/wrong addresses); only overlay the internal cid
                # from the DB. V4 pools, which have no CREATE2 derivation, still get
                # their pool_address/pool_id from the DB.
                db_by_addr = {}
                for k, v in db_batch.items():
                    if isinstance(v, dict) and v.get("cid"):
                        p_addr = (v.get("pool_address") or "").lower()
                        p_id = (v.get("pool_id") or "").lower()
                        if p_addr: db_by_addr[p_addr] = v
                        if p_id: db_by_addr[p_id] = v

                for key, db_val in db_batch.items():
                    if key in pool_addresses:
                        # Prefer actual database pool_address when available;
                        # fallback to derived CREATE2 address if missing.
                        if db_val.get("pool_address"):
                            pool_addresses[key]["pool_address"] = db_val["pool_address"]
                            pool_addresses[key]["pool_id"] = db_val.get("pool_id") or db_val["pool_address"]
                        pool_addresses[key]["cid"] = db_val.get("cid")
                    else:
                        pool_addresses[key] = db_val

                # Overlay CID & metadata by pool address / pool ID matching
                for k, v in pool_addresses.items():
                    if isinstance(v, dict):
                        p_addr = (v.get("pool_address") or "").lower()
                        p_id = (v.get("pool_id") or "").lower()
                        match_val = db_by_addr.get(p_addr) or db_by_addr.get(p_id)
                        if match_val:
                            if not v.get("cid"):
                                v["cid"] = match_val.get("cid")
                            if not v.get("pool_address"):
                                v["pool_address"] = match_val.get("pool_address", "")
                            if not v.get("pool_id"):
                                v["pool_id"] = match_val.get("pool_id") or match_val.get("pool_address", "")

            # Trigger background warming of DeFi Llama yields index if stale (non-blocking).
            get_defillama_index()

            # 3. Inject into routes
            # Pre-collect unique pool enrichment tasks to run concurrently and prevent duplicate calls
            unique_enrichment_jobs = {}
            days = max(1, (end_dt - start_dt).days)
            for route in analysis.get('routes', []):
                path = route.get('path_tokens', [])
                for i in range(1, len(path), 2):
                    t0 = path[i-1]
                    fee = path[i]
                    t1 = path[i+1]
                    t0_norm = 'WETH' if t0.upper() == 'ETH' else ('WBNB' if t0.upper() == 'BNB' else t0.upper())
                    t1_norm = 'WETH' if t1.upper() == 'ETH' else ('WBNB' if t1.upper() == 'BNB' else t1.upper())
                    key = f"{t0_norm}-{t1_norm}-{fee}"
                    rev_key = f"{t1_norm}-{t0_norm}-{fee}"

                    if key not in unique_enrichment_jobs and rev_key not in unique_enrichment_jobs:
                        pool_info = pool_addresses.get(key) or pool_addresses.get(rev_key)
                        if not pool_info:
                            fee_parts = fee.split('|')
                            clean_fee = fee_parts[0].strip()
                            clean_proto = fee_parts[1].strip() if len(fee_parts) >= 2 else ""
                            clean_net = fee_parts[2].strip() if len(fee_parts) >= 3 else ""
                            alt_keys = [
                                f"{t0_norm}-{t1_norm}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t1_norm}-{t0_norm}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t0_norm}-{t1_norm}-{clean_fee}|{clean_proto}",
                                f"{t1_norm}-{t0_norm}-{clean_fee}|{clean_proto}",
                                f"{t0.upper()}-{t1.upper()}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t1.upper()}-{t0.upper()}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t0.upper()}-{t1.upper()}-{clean_fee}|{clean_proto}",
                                f"{t1.upper()}-{t0.upper()}-{clean_fee}|{clean_proto}",
                            ]
                            for ak in alt_keys:
                                if ak in pool_addresses:
                                    pool_info = pool_addresses[ak]
                                    break
                        if not pool_info:
                            pool_info = {}

                        if isinstance(pool_info, str):
                            pool_info = {"pool_address": pool_info, "pool_id": pool_info, "cid": None}
                        pool_addr = pool_info.get("pool_address")
                        fee_parts = fee.split('|')
                        pool_network = fee_parts[2].strip() if len(fee_parts) >= 3 else "Ethereum"

                        unique_enrichment_jobs[key] = (key, rev_key, aprs, pool_addr, pool_network, days, fee)

            enrichment_results = {}
            if unique_enrichment_jobs:
                job_keys = list(unique_enrichment_jobs.keys())
                results = await asyncio.gather(*[get_enriched_pool_stat(*unique_enrichment_jobs[k]) for k in job_keys])
                for k, res in zip(job_keys, results):
                    enrichment_results[k] = res

            # Pre-load canonical routes indexed strictly by (origin, dest, exact pool CID tuple)
            route_by_pool_tuple = {}
            try:
                with get_conn() as db_conn:
                    d_cur = db_conn.cursor()
                    d_cur.execute("""
                        SELECT r.route_id, pair.id, UPPER(pair.origin_symbol), UPPER(pair.dest_symbol), array_agg(h.pool_id ORDER BY h.seq) AS pool_ids
                        FROM route r
                        JOIN origin_destination_pair pair ON pair.id = r.pair_id
                        JOIN route_hop h ON h.route_id = r.route_id
                        GROUP BY r.route_id, pair.id, pair.origin_symbol, pair.dest_symbol
                    """)
                    for rid, pair_id, orig, dest, pids in d_cur.fetchall():
                        route_by_pool_tuple[(orig, dest, tuple(pids))] = (rid, pair_id)
                    d_cur.close()
            except Exception as _ex:
                print(f"Error pre-loading route_by_pool_tuple: {_ex}")

            for route_idx, route in enumerate(analysis.get('routes', [])):
                path = route.get('path_tokens', [])
                new_path = []
                for i in range(len(path)):
                    item = path[i]
                    if i % 2 == 1: # This is a fee node
                        # Previous token is at i-1, next at i+1
                        t0 = path[i-1]
                        fee = item
                        t1 = path[i+1]
                    
                        t0_norm = 'WETH' if t0.upper() == 'ETH' else ('WBNB' if t0.upper() == 'BNB' else t0.upper())
                        t1_norm = 'WETH' if t1.upper() == 'ETH' else ('WBNB' if t1.upper() == 'BNB' else t1.upper())

                        key = f"{t0_norm}-{t1_norm}-{fee}"
                        rev_key = f"{t1_norm}-{t0_norm}-{fee}"
                        pool_info = pool_addresses.get(key) or pool_addresses.get(rev_key)

                        if not pool_info:
                            fee_parts = fee.split('|')
                            clean_fee = fee_parts[0].strip()
                            clean_proto = fee_parts[1].strip() if len(fee_parts) >= 2 else ""
                            clean_net = fee_parts[2].strip() if len(fee_parts) >= 3 else ""

                            alt_keys = [
                                f"{t0_norm}-{t1_norm}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t1_norm}-{t0_norm}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t0_norm}-{t1_norm}-{clean_fee}|{clean_proto}",
                                f"{t1_norm}-{t0_norm}-{clean_fee}|{clean_proto}",
                                f"{t0.upper()}-{t1.upper()}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t1.upper()}-{t0.upper()}-{clean_fee}|{clean_proto}|{clean_net}",
                                f"{t0.upper()}-{t1.upper()}-{clean_fee}|{clean_proto}",
                                f"{t1.upper()}-{t0.upper()}-{clean_fee}|{clean_proto}",
                            ]
                            for ak in alt_keys:
                                if ak in pool_addresses:
                                    pool_info = pool_addresses[ak]
                                    break
                        if not pool_info:
                            pool_info = {}

                        if isinstance(pool_info, str):
                            pool_info = {"pool_address": pool_info, "pool_id": pool_info, "cid": None}
                        pool_addr = pool_info.get("pool_address")
                        pool_id = pool_info.get("pool_id")
                        cid = pool_info.get("cid")
                        
                        fee_parts = fee.split('|')
                        pool_network = fee_parts[2].strip() if len(fee_parts) >= 3 else "Ethereum"

                        enriched = enrichment_results.get(key) or enrichment_results.get(rev_key)
                        if not enriched:
                            enriched = await get_enriched_pool_stat(
                                key=key,
                                rev_key=rev_key,
                                aprs=aprs,
                                pool_addr=pool_addr,
                                pool_network=pool_network,
                                period_days=days,
                                fee_tier=fee
                            )
                        
                        apr_val = enriched['apr']
                        tvl_val = enriched['tvl']

                        # Replace string fee with object
                        pool_protocol = fee_parts[1].strip() if len(fee_parts) >= 2 else "Uniswap V3"
                        pool_defillama_uuid = get_defillama_pool_uuid(pool_addr)
                        new_path.append({
                            'fee': fee,
                            'apr': apr_val if apr_val is not None else 0.0,
                            'apr_str': format_apr(apr_val),
                            'pool_address': pool_addr,
                            'pool_id': pool_id,
                            'cid': cid,
                            'tvl': tvl_val,
                            'defillama_uuid': pool_defillama_uuid,
                            'links': build_pool_links(pool_addr, pool_id, pool_protocol, pool_network, pool_defillama_uuid),
                        })
                    else:
                        new_path.append(item)
            
                # Calculate a combined APR for the route
                # If there is more than one pool involved, it's a composite route, and APR is not valid.
                pool_nodes = [p for p in new_path if isinstance(p, dict)]
                if len(pool_nodes) > 1:
                    route_apr = 0.0
                    apr_str = "-"
                else:
                    leg_aprs = [p['apr'] for p in pool_nodes if 'apr' in p]
                    leg_apr_strs = [p['apr_str'] for p in pool_nodes if 'apr_str' in p]
                    route_apr = leg_aprs[0] if leg_aprs else 0.0
                    if leg_apr_strs and leg_apr_strs[0] == "N/A":
                        apr_str = "N/A"
                    else:
                        apr_str = format_apr(route_apr) if route_apr > 0 else "0%"
            
                # Determine route-level network from path fee node
                route_network = "Ethereum"
                for p in pool_nodes:
                    if 'fee' in p:
                        fee_parts = p['fee'].split('|')
                        if len(fee_parts) >= 3:
                            route_network = fee_parts[2].strip()
                            break
            
                # Align path_tokens for reverse routes so primary start_token is on left
                # and primary end_token is on right, matching direction='reverse' (left arrow ◄).
                if route.get('direction') == 'reverse' and len(new_path) >= 3 and isinstance(new_path[0], str) and isinstance(new_path[-1], str):
                    if new_path[0].upper() not in start_tokens_list and new_path[-1].upper() in start_tokens_list:
                        new_path = new_path[::-1]
                elif route.get('direction') == 'forward' and len(new_path) >= 3 and isinstance(new_path[0], str) and isinstance(new_path[-1], str):
                    if new_path[0].upper() not in start_tokens_list and new_path[-1].upper() in start_tokens_list:
                        new_path = new_path[::-1]

                analysis['routes'][route_idx]['path_tokens'] = new_path
                analysis['routes'][route_idx]['apr'] = route_apr
                analysis['routes'][route_idx]['apr_str'] = apr_str
                analysis['routes'][route_idx]['network'] = route_network

                # Match exact pool CID sequence against canonical DB routes if missing
                if not analysis['routes'][route_idx].get('route_id'):
                    route_cids = [p['cid'] for p in new_path if isinstance(p, dict) and p.get('cid') is not None]
                    if route_cids and len(new_path) >= 3:
                        orig = str(new_path[0]).upper() if isinstance(new_path[0], str) else ''
                        dest = str(new_path[-1]).upper() if isinstance(new_path[-1], str) else ''
                        match = route_by_pool_tuple.get((orig, dest, tuple(route_cids)))
                        if match:
                            r_id, p_id = match
                            analysis['routes'][route_idx]['route_id'] = r_id
                            if not analysis['routes'][route_idx].get('pair_id'):
                                analysis['routes'][route_idx]['pair_id'] = p_id

            yield json.dumps({"type": "progress", "pct": 98.0, "message": "Formatting routing path data..."}) + "\n"
            await asyncio.sleep(0.01)
            
            yield json.dumps({"type": "progress", "pct": 100.0, "message": "Analysis complete!"}) + "\n"
            await asyncio.sleep(0.01)

            for _r in analysis.get('routes', []):
                if _r.get('route_id') is not None:
                    _r['route_id'] = route_hash_hex(_r['route_id'])
                if _r.get('pair_id') is not None:
                    _r['pair_id'] = route_hash_hex(_r['pair_id'])

            yield json.dumps({"type": "result", "data": analysis}) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/routes/undercut", tags=["Route"])
async def undercut(
    start_token: str,
    end_token: str,
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network"),
    fee_bps: float = Query(..., description="Hypothetical pool fee in basis points (e.g. 97 for 0.97%)"),
    liquidity_usd: float = Query(..., description="Total USD liquidity to deposit in the hypothetical pool"),
    range_pct: float = Query(..., description="Hypothetical pool range as +/- percent (e.g. 0.25 for +/-0.25%)"),
):
    """Counterfactual 'undercut' experiment: how much of a token pair's swap
    traffic a max-expected-output router would divert to a hypothetical 4th pool
    with the given fee tier, liquidity and range. Returns the hypothetical pool
    row plus the existing pools' rows with hypothetical post-diversion stats."""
    try:
        now = datetime.now()
        if start_date:
            s = start_date.replace('Z', '+00:00').strip()
            try:
                start_dt = datetime.fromisoformat(s)
            except ValueError:
                date_part = s.split('T')[0]
                parts = date_part.split('-')
                if len(parts) == 3:
                    import calendar
                    yr, mo, dy = int(parts[0]), int(parts[1]), int(parts[2])
                    max_d = calendar.monthrange(yr, mo)[1]
                    if dy > max_d:
                        s_fixed = f"{yr:04d}-{mo:02d}-{max_d:02d}"
                        if 'T' in s:
                            s_fixed += 'T' + s.split('T')[1]
                        return HTTPException(status_code=400, detail=f"Invalid start_date: {start_date}")
                raise
            if start_date.endswith('Z') or 'T' in start_date:
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            else:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        else:
            start_dt = now - timedelta(days=30)

        if end_date:
            s = end_date.replace('Z', '+00:00').strip()
            try:
                end_dt = datetime.fromisoformat(s)
            except ValueError:
                date_part = s.split('T')[0]
                parts = date_part.split('-')
                if len(parts) == 3:
                    import calendar
                    yr, mo, dy = int(parts[0]), int(parts[1]), int(parts[2])
                    max_d = calendar.monthrange(yr, mo)[1]
                    if dy > max_d:
                        s_fixed = f"{yr:04d}-{mo:02d}-{max_d:02d}"
                        if 'T' in s:
                            s_fixed += 'T' + s.split('T')[1]
                        return HTTPException(status_code=400, detail=f"Invalid end_date: {end_date}")
                raise
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0 and end_dt.microsecond == 0:
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            end_dt = now

        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")

        t0_sym, t1_sym = start_token.strip().upper(), end_token.strip().upper()
        if t0_sym == '*' or t1_sym == '*':
            raise HTTPException(status_code=400, detail="Wildcard tokens are not supported for the undercut experiment")

        fetcher = PostgresFetcher(verbose=False)
        import asyncio
        import math
        from fractions import Fraction

        # Resolve token families (mirror /api/routes/analyze) so the backtest
        # competes against the real market pools that route the pair's traffic.
        # E.g. STETH->ETH flows through the STETH-WETH 1% V3 pool (the live
        # competitor) because WETH is in the ETH coin family; only fetching the
        # exact STETH<->ETH pair would miss it and show only dead V4 pools.
        start_tokens_list = resolve_token_input(t0_sym)
        end_tokens_list = resolve_token_input(t1_sym)
        if not start_tokens_list:
            start_tokens_list = [t0_sym]
        if not end_tokens_list:
            end_tokens_list = [t1_sym]

        token_filter = []
        if "*" not in start_tokens_list:
            token_filter.extend(start_tokens_list)
        if "*" not in end_tokens_list:
            token_filter.extend(end_tokens_list)
        if not token_filter:
            token_filter = None

        raw_swaps = await asyncio.to_thread(
            fetcher.fetch_swaps, start_dt, end_dt,
            token_filter=token_filter, network=network,
            start_tokens=start_tokens_list, end_tokens=end_tokens_list,
            broad=True
        )

        latest_prices = fetcher.fetch_latest_prices(token_filter)
        # Build ALL fetched events for the token families (both directions and
        # any intermediate-hop pools) so each tx's first log entry can decide
        # its direction — matching the top routes table, which treats a tx's
        # direction as whatever its first (lowest log_index) entry initiated.
        swaps = []
        for r in raw_swaps:
            a0f, a1f = float(r.get('amount0', 0) or 0), float(r.get('amount1', 0) or 0)
            if a0f == 0 or a1f == 0 or not (math.isfinite(a0f) and math.isfinite(a1f)):
                continue
            usd = float(r.get('amountUSD', r.get('amount_usd', 0)) or 0)
            t0_in = a0f > 0
            s0 = (r.get('token0_symbol') or '').upper()
            s1 = (r.get('token1_symbol') or '').upper()

            if usd <= 0:
                p0 = latest_prices.get(s0, 0.0) if latest_prices else 0.0
                p1 = latest_prices.get(s1, 0.0) if latest_prices else 0.0
                if p0 == 0.0 and any(x in s0 for x in ['USD', 'EUR']): p0 = 1.0
                if p1 == 0.0 and any(x in s1 for x in ['USD', 'EUR']): p1 = 1.0
                v0 = abs(a0f)
                if v0 > 1e12:
                    v0 /= 1e18
                elif any(b in s0 for b in ['BTC', 'WBTC', 'BTCB']) and v0 > 1e4:
                    v0 /= 1e8

                v1 = abs(a1f)
                if v1 > 1e12:
                    v1 /= 1e18
                elif any(b in s1 for b in ['BTC', 'WBTC', 'BTCB']) and v1 > 1e4:
                    v1 /= 1e8

                if p0 > 0:
                    usd = v0 * p0
                elif p1 > 0:
                    usd = v1 * p1

            swaps.append({
                "ts": datetime.fromtimestamp(r['timestamp'], tz=timezone.utc),
                "log_index": r.get('log_index') or 0,
                "fee_bps": float(r.get('fee_bps') or 0),
                "fee_tier": r.get('fee_tier') or '',
                "t0_in": t0_in,
                "input": abs(a0f if t0_in else a1f),
                "output": abs(a1f if t0_in else a0f),
                "usd": usd,
                "price": Fraction(abs(a1f)) / Fraction(abs(a0f)),
                "protocol": r.get('protocol', 'Uniswap V3'),
                "network": r.get('network', 'Ethereum'),
                "cid": r.get('cid'),
                "pool_address": r.get('pool_address') or '',
                "pool_id": r.get('pool_id') or '',
                "s0": s0,
                "s1": s1,
                "tx_hash": r.get('tx_hash') or '',
            })

        # First log entry per tx decides its direction. A tx counts as a
        # start->end swap only if its FIRST log entry spends the start token —
        # the same rule as the top routes table. Reverse-first txs (arb
        # round-trips that start by buying the start token) are excluded so
        # both tables count the same swaps.
        first_by_tx = {}
        for s in swaps:
            prev = first_by_tx.get(s["tx_hash"])
            if prev is None or s["log_index"] < prev["log_index"]:
                first_by_tx[s["tx_hash"]] = s

        all_events = swaps
        swaps = [s for s in all_events
                 if (s["s0"] if s["t0_in"] else s["s1"]) in start_tokens_list
                 and (s["s1"] if s["t0_in"] else s["s0"]) in end_tokens_list
                 and (first_by_tx[s["tx_hash"]]["s0"] if first_by_tx[s["tx_hash"]]["t0_in"] else first_by_tx[s["tx_hash"]]["s1"]) in start_tokens_list]
        # Reverse-direction (end->start) demand for the two-sided inventory
        # simulation. Same first-log-entry rule mirrored: a tx counts as an
        # end->start swap only if its FIRST log entry spends the end token.
        # These are the real counter-swaps that rebalance the hypothetical pool.
        reverse_swaps = [s for s in all_events
                         if (s["s1"] if s["t0_in"] else s["s0"]) in start_tokens_list
                         and (s["s0"] if s["t0_in"] else s["s1"]) in end_tokens_list
                         and (first_by_tx[s["tx_hash"]]["s0"] if first_by_tx[s["tx_hash"]]["t0_in"] else first_by_tx[s["tx_hash"]]["s1"]) in end_tokens_list]

        if not swaps:
            return {"hypothetical": None, "pools": [], "total_volume": 0,
                    "total_tx": 0, "days": max(1, (end_dt - start_dt).days),
                    "start_token": t0_sym, "end_token": t1_sym, "network": network or "Ethereum",
                    "fee_bps": fee_bps, "liquidity_usd": liquidity_usd, "range_pct": range_pct}

        # Raw swap-event count and volume per pool (before the (tx, pool) dedup
        # below), so the table can show both TXs (unique transactions), Swaps
        # (individual swap events — exceed TXs when one tx emits multiple swaps
        # in the same pool, e.g. aggregator splits), and full log-entry volume.
        raw_pool_swaps = {}
        raw_pool_vol = {}
        for s in swaps:
            pkey = (s.get("cid"), s["fee_bps"], s.get("protocol", "Uniswap V3"), s.get("pool_address", ""))
            raw_pool_swaps[pkey] = raw_pool_swaps.get(pkey, 0) + 1
            raw_pool_vol[pkey] = raw_pool_vol.get(pkey, 0.0) + s["usd"]

        # True totals for the pair (pre-dedup): the number of unique user swaps
        # (TXs, one per tx_hash) and the full volume (sum of ALL log entries),
        # so the backtest reports the same totals as the top routes table.
        unique_tx_count = len({s["tx_hash"] for s in swaps})
        raw_total_usd = sum(s["usd"] for s in swaps)

        # Deduplicate by (tx_hash, pool) so each unique transaction is counted
        # once per pool – matching how the Route Analyzer counts TXs.  When a
        # single transaction emits multiple swap events in the same pool (e.g.
        # aggregator splits), keep the event with the highest USD value.
        seen_tx_pool = {}
        for s in swaps:
            key = (s["tx_hash"], s.get("cid"))
            if key not in seen_tx_pool or s["usd"] > seen_tx_pool[key]["usd"]:
                seen_tx_pool[key] = s
        swaps = list(seen_tx_pool.values())

        # market_prices() needs chronological order
        swaps.sort(key=lambda s: s["ts"])

        # Fee-free price per swap for market-price estimation
        for s in swaps:
            fee_frac = ua.fee_fraction_from_bps(s["fee_bps"])
            s["fee_free_price"] = ua.fee_free_price(s["price"], s["t0_in"], fee_frac)

        # Dedup reverse swaps by (tx, pool) the same way, then sort
        # chronologically so the two-sided simulation can interleave them.
        seen_rev = {}
        for s in reverse_swaps:
            key = (s["tx_hash"], s.get("cid"))
            if key not in seen_rev or s["usd"] > seen_rev[key]["usd"]:
                seen_rev[key] = s
        reverse_swaps = list(seen_rev.values())
        reverse_swaps.sort(key=lambda s: s["ts"])

        days = max(1, (end_dt - start_dt).days)

        # Market price anchor: median of the fee-free market estimates (the mean
        # is skewed by whale swaps with huge slippage). The band is centered on
        # the market median, NOT the opening raw price.
        markets = ua.market_prices(swaps)
        sorted_mk = sorted(float(m) for m in markets)
        center = sorted_mk[len(sorted_mk) // 2]
        opening_px, p0_usd, p1_usd = ua.opening_price_and_usd(swaps)

        # Fallback token USD prices (only used to size the hypothetical pool)
        if p0_usd is None or p1_usd is None:
            prices = fetcher.fetch_latest_prices([t0_sym, t1_sym])
            if p0_usd is None:
                p0_usd = prices.get(t0_sym, 1.0 if 'USD' in t0_sym else 100.0)
            if p1_usd is None:
                p1_usd = prices.get(t1_sym, 1.0 if 'USD' in t1_sym else 100.0)

        fee_pips = int(round(fee_bps * 100))

        # Group swaps by pool (cid, fee_bps, protocol, pool_address) so all distinct competitor pools are retained
        from collections import defaultdict
        by_pool = defaultdict(lambda: {"count": 0, "volume": 0.0, "cid": None,
                                       "pool_address": '', "pool_id": '',
                                       "protocol": 'Uniswap V3', "fee_bps": 0, "s0": '', "s1": '',
                                       "last_ts": None})
        for s in swaps:
            pkey = (s.get("cid"), s["fee_bps"], s.get("protocol", "Uniswap V3"), s.get("pool_address", ""))
            b = by_pool[pkey]
            b["fee_bps"] = s["fee_bps"]
            b["protocol"] = s.get("protocol") or "Uniswap V3"
            b["count"] += 1
            b["volume"] += s["usd"]
            if b["last_ts"] is None or s["ts"] > b["last_ts"]:
                b["last_ts"] = s["ts"]
            if b["cid"] is None and s.get("cid") is not None:
                b["cid"] = s["cid"]
            if not b["pool_address"] and s.get("pool_address"):
                b["pool_address"] = s["pool_address"]
            if not b["pool_id"] and s.get("pool_id"):
                b["pool_id"] = s["pool_id"]
            if not b["s0"] and s.get("s0"):
                b["s0"] = s["s0"]
            if not b["s1"] and s.get("s1"):
                b["s1"] = s["s1"]

        # Pool volume = sum of ALL the pool's log entries (pre-dedup), so
        # per-pool volume and the response total reconcile with the top routes
        # table (which counts every start-token-consuming leg).
        for pkey, st in by_pool.items():
            st["volume"] = raw_pool_vol.get(pkey, st["volume"])

        pools = []
        if by_pool:
            net_label = network or "Ethereum"
            def _fee_label(fb):
                return 'Dynamic' if fb is None else f"{fb / 100.0:g}%"
            try:
                pool_stats = await asyncio.to_thread(
                    fetcher.fetch_pool_stats,
                    [[st["s0"] or t0_sym, st["s1"] or t1_sym,
                      f"{_fee_label(st['fee_bps'])}|{st['protocol']}|{net_label}"] for st in by_pool.values()],
                    start_dt, end_dt,
                    prices=latest_prices,
                    tvl_mode='latest',
                    use_swaps_fallback=True,
                )
            except Exception:
                pool_stats = {}

            # The hypothetical pool only competes with pools that will actually
            # be shown (real TVL > $1 or window volume > $1). Excluding dead
            # pools' swaps keeps the backtest table self-consistent:
            #   sum(displayed count) == sum(displayed hyp_count) + diverted_count
            def _pkey_of(s):
                return (s.get("cid"), s["fee_bps"], s.get("protocol", "Uniswap V3"), s.get("pool_address", ""))
            active_keys = set()
            for pkey, st in by_pool.items():
                pool_key = f"{st['s0'] or t0_sym}-{st['s1'] or t1_sym}-{_fee_label(st['fee_bps'])}|{st['protocol']}|{net_label}"
                rev_pool_key = f"{st['s1'] or t1_sym}-{st['s0'] or t0_sym}-{_fee_label(st['fee_bps'])}|{st['protocol']}|{net_label}"
                stat = pool_stats.get(pool_key) or pool_stats.get(rev_pool_key)
                real_tvl = (stat or {}).get("tvl", 0.0) or 0.0
                if real_tvl > 1.0 or (st["volume"] or 0) > 1.0:
                    active_keys.add(pkey)
            sim_swaps = [s for s in swaps if _pkey_of(s) in active_keys]
            sim_reverse = [s for s in reverse_swaps if _pkey_of(s) in active_keys]
            res = ua.simulate(liquidity_usd / 2.0, range_pct, fee_pips, sim_swaps,
                              Fraction(center), p0_usd, p1_usd, sum(s["usd"] for s in sim_swaps),
                              reverse_swaps=sim_reverse)
        else:
            pool_stats = {}
            res = ua.simulate(liquidity_usd / 2.0, range_pct, fee_pips, [],
                              Fraction(center), p0_usd, p1_usd, 0.0, reverse_swaps=[])

        for pkey, st in sorted(by_pool.items(), key=lambda kv: (kv[1]["volume"] or 0), reverse=True):
            fee_b = st["fee_bps"]
            div_cnt, div_vol = res.get("by_pool", {}).get(pkey, [0, 0.0])
            hyp_vol = max(0.0, st["volume"] - div_vol)
            s0 = st["s0"] or t0_sym
            s1 = st["s1"] or t1_sym
            pool_key = f"{s0}-{s1}-{_fee_label(fee_b)}|{st['protocol']}|{net_label}"
            rev_pool_key = f"{s1}-{s0}-{_fee_label(fee_b)}|{st['protocol']}|{net_label}"
            stat = pool_stats.get(pool_key) or pool_stats.get(rev_pool_key)
            real_tvl = (stat or {}).get("tvl", 0.0) or 0.0
            # Bidirectional volume from DB: pools earn fees on swaps in both
            # directions, so APR must use the full two-way volume — the same
            # source the top Routes table uses.  st["volume"] is directional
            # only (start_token → end_token) and is kept for the simulation
            # (deciding which swaps the hypothetical pool diverts).
            real_vol = (stat or {}).get("volume", 0.0) or 0.0
            fee_vol_for_apr = real_vol if real_vol > st["volume"] else st["volume"]
            orig_fees = fee_vol_for_apr * (ua.fee_fraction_from_bps(fee_b))
            # For post-undercut fees: scale proportionally by volume diverted
            hyp_fees = orig_fees * (hyp_vol / st["volume"]) if st["volume"] > 0 else 0.0

            # Mirror the Show Routes enrichment: when the DB TVL is missing or
            # unreliable, fall back to DexScreener / DeFi Llama for the real TVL
            # so the backtest competitor pools match the routes table.
            # V4 pools have no pool_address; fall back to pool_id for lookups.
            lookup_addr = st.get("pool_address") or st.get("pool_id") or ''
            if lookup_addr:
                enriched = await get_enriched_pool_stat(
                    key=pool_key,
                    rev_key=rev_pool_key,
                    aprs=pool_stats,
                    pool_addr=lookup_addr,
                    pool_network=net_label,
                    period_days=days,
                    fee_tier=f"{_fee_label(fee_b)}|{st['protocol']}|{net_label}",
                )
                # Prefer DexScreener / DeFi Llama real-time TVL whenever it
                # returns a valid value — this matches the top Routes table which
                # calls the same get_enriched_pool_stat function and always uses
                # the external TVL.  The previous `enriched_tvl > real_tvl` guard
                # caused a bug: when the DB had a stale high TVL (e.g. $145K) and
                # DexScreener returned the correct lower value ($79K), the guard
                # silently kept the wrong DB figure.
                enriched_tvl = (enriched or {}).get("tvl") or 0.0
                if enriched_tvl > 1.0:
                    real_tvl = enriched_tvl
            if real_tvl <= 1.0 and st["volume"] <= 1.0:
                continue
            hyp_apr_pct = (hyp_fees / real_tvl) * (365.0 / days) * 100.0 if (hyp_vol > 0 and real_tvl > 0) else 0.0
            real_apr_pct = (orig_fees / real_tvl) * (365.0 / days) * 100.0 if (orig_fees > 0 and real_tvl > 0) else 0.0
            pools.append({
                "fee_bps": fee_b,
                "fee_display": _fee_label(fee_b),
                "protocol": st["protocol"],
                "count": st["count"],
                "swaps": raw_pool_swaps.get(pkey, st["count"]),
                "volume": st["volume"],
                "fees": orig_fees,
                "tvl": real_tvl,
                "apr_pct": real_apr_pct,
                "last_activity": st["last_ts"].isoformat() if st["last_ts"] is not None else None,
                "cid": st["cid"],
                "pool_address": st["pool_address"],
                "pool_id": st["pool_id"],
                "diverted_count": div_cnt,
                "diverted_volume": div_vol,
                "hyp_count": st["count"] - div_cnt,
                "hyp_volume": hyp_vol,
                "hyp_fees": hyp_fees,
                "hyp_apr_pct": hyp_apr_pct,
            })

        hyp_apr_pct = (res["fee_usd"] / liquidity_usd) * (365.0 / days) * 100.0 if liquidity_usd > 0 else 0.0

        return {
            "hypothetical": {
                "fee_bps": fee_bps,
                "fee_display": f"{fee_bps / 100.0:g}%",
                "liquidity_usd": liquidity_usd,
                "range_pct": range_pct,
                "diverted_count": res["div_count"],
                "swaps": res["div_count"],
                "diverted_volume": res["div_usd"],
                "diverted_pct": res["pct"],
                "fee_usd": res["fee_usd"],
                "apr_pct": hyp_apr_pct,
                "in_range": res["in_range"],
                "reverse_count": res["reverse_count"],
                "reverse_volume": res["reverse_usd"],
                "reverse_fee_usd": res["reverse_fee_usd"],
            },
            "pools": pools,
            "total_volume": raw_total_usd,
            "total_tx": unique_tx_count,
            "days": days,
            "start_token": t0_sym,
            "end_token": t1_sym,
            "network": network or "Ethereum",
            "fee_bps": fee_bps,
            "liquidity_usd": liquidity_usd,
            "range_pct": range_pct,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pool-arena/simulate", tags=["Pool Arena"])
async def pool_arena_simulate(req: PoolArenaRequest):
    """Run the coupled N-pool simulation on generated random swap demand.

    The user defines 2+ pools (liquidity / range / fee tier) and the demand
    generator's parameters (swap count, seed, volume bounds, direction bias).
    Every swap is quoted against all pools' current drifted prices and routed
    to the best fill, so no historical price data is required. Returns
    per-pool volume / fees / APR plus a cumulative volume time-series."""
    if not req.pools or len(req.pools) < 2:
        raise HTTPException(status_code=400, detail="Define at least 2 pools")
    if req.swaps.count < 10 or req.swaps.count > 200000:
        raise HTTPException(status_code=400, detail="swaps.count must be in [10, 200000]")
    if not (0.0 <= req.swaps.direction_bias <= 1.0):
        raise HTTPException(status_code=400, detail="direction_bias must be in [0, 1]")

    # Generate the demand server-side (deterministic via seed).
    import random as _random
    from fractions import Fraction as _Fraction
    rng = _random.Random(req.swaps.seed)
    fwd, rev = [], []
    for i in range(req.swaps.count):
        usd = rng.uniform(req.swaps.vol_min, req.swaps.vol_max)
        sw = {"ts": i, "input": usd, "output": usd, "usd": usd, "fee_bps": 30,
              "cid": 1, "protocol": "Arena", "pool_address": "0xARENA"}
        (fwd if rng.random() < req.swaps.direction_bias else rev).append(sw)
    total_fwd = sum(s["usd"] for s in fwd)

    def _run():
        return ua.simulate_pools(
            [{"name": p.name, "cap": p.liquidity_usd / 2.0,
              "range_pct": p.range_pct,
              "fee_pips": int(round(p.fee_bps * 100))} for p in req.pools],
            fwd, _Fraction(1, 1), 1.0, 1.0, total_fwd,
            reverse_swaps=rev, series_every=max(1, req.swaps.count // 100))

    res = await asyncio.to_thread(_run)

    pools_out = []
    for p, r in zip(req.pools, res["pools"]):
        two_way_vol = r["usd"] + r["reverse_usd"]
        fee = r["fee_usd"] + r["reverse_fee_usd"]
        apr = fee / p.liquidity_usd * (365.0 / req.days) * 100.0 if p.liquidity_usd > 0 else 0.0
        pools_out.append({
            "name": p.name,
            "liquidity_usd": p.liquidity_usd,
            "range_pct": p.range_pct,
            "fee_bps": p.fee_bps,
            "count": r["count"],
            "usd": r["usd"],
            "reverse_count": r["reverse_count"],
            "reverse_usd": r["reverse_usd"],
            "volume": two_way_vol,
            "fee_usd": fee,
            "apr_pct": apr,
            "pct": r["pct"],
        })

    return {
        "pools": pools_out,
        "series": res.get("series", []),
        "total_volume": total_fwd + sum(s["usd"] for s in rev),
        "swap_count": req.swaps.count,
        "days": req.days,
    }


@app.get("/api/swap-distribution", tags=["Swap Distribution"])
async def swap_distribution(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None, description="Lookback period in days"),
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network"),
    limit: int = Query(500000, ge=100, le=2000000, description="Max swap rows sampled"),
    exclude_chains: Optional[str] = Query(None, description="Comma-separated chain names to exclude"),
    group_by: str = Query("route", pattern="^route$",
                          description="Split the histogram groups. Only 'route' is served (from pre-aggregated route_daily_stats_bucket); other groupings were dropped with the raw-swaps migration."),
    direction: str = Query("both", pattern="^(both|forward|reverse)$",
                           description="Restrict to a single swap direction: both (default), forward (start→end), or reverse (end→start)"),
    max_hops: Optional[int] = Query(None, description="Max route hop count (1 for direct routes only)")
):
    """Analyze the swap-size distribution for a token route.

    Served from pre-aggregated route_daily_stats_bucket rows (no raw swaps
    reads). Bucket parameters (bucket count, min/max amount USD) come from the
    global config/swap-distribution.yaml. Aggregates per-route bucket
    counts/volumes, fits a lognormal body + Pareto tail, and returns the
    log-binned histogram plus fitted curves for the frontend to render as pure
    SVG.

    Only `group_by=route` is supported. Every route is bucketed daily, so a
    query whose routes have no completed bucket rollup in the window returns no
    data (the raw-swaps fallback was removed).
    """
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            def safe_parse_iso(date_str: str) -> datetime:
                s = date_str.replace('Z', '+00:00').strip()
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}")

            start_dt = safe_parse_iso(start_date)
            if end_date:
                end_dt = safe_parse_iso(end_date)
                if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0 and end_dt.microsecond == 0:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                end_dt = now
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=1)

        if end_dt <= start_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")

        start_list = resolve_token_input(start_token)
        end_list = resolve_token_input(end_token)
        if not start_list:
            start_list = [start_token.strip().upper()]
        if not end_list:
            end_list = [end_token.strip().upper()]
        start_list = [s.strip().upper() for s in start_list]
        end_list = [e.strip().upper() for e in end_list]

        if network and network.lower() in ("all", "*"):
            network = None

        exclude = []
        if isinstance(exclude_chains, str) and exclude_chains:
            exclude = [c.strip().lower() for c in exclude_chains.split(",") if c.strip()]

        def _fetch_and_analyze():
            with get_conn() as conn:
                cur = conn.cursor()
                exclude_sql = ""

                start_wild = "*" in start_list
                end_wild = "*" in end_list

                # Pre-load canonical DB routes matching start_list <-> end_list
                route_infos = {}
                route_id_by_path = {}
                route_id_by_pair = {}
                try:
                    r_cur = conn.cursor()
                    r_cur.execute("""
                        SELECT r.route_id, UPPER(pair.origin_symbol), UPPER(pair.dest_symbol), r.hops, ch.name,
                               array_agg(h.pool_id ORDER BY h.seq) AS pool_ids
                        FROM route r
                        JOIN origin_destination_pair pair ON r.pair_id = pair.id
                        JOIN chain ch ON r.chain_id = ch.id
                        JOIN route_hop h ON h.route_id = r.route_id
                        WHERE ((UPPER(pair.origin_symbol) = ANY(%s) AND UPPER(pair.dest_symbol) = ANY(%s))
                           OR (UPPER(pair.origin_symbol) = ANY(%s) AND UPPER(pair.dest_symbol) = ANY(%s)))
                        GROUP BY r.route_id, pair.origin_symbol, pair.dest_symbol, r.hops, ch.name
                    """, (start_list, end_list, end_list, start_list))
                    for rid, orig, dest, hops, chain_name, pids in r_cur.fetchall():
                        hcur = conn.cursor()
                        hcur.execute("""
                            SELECT h.seq, UPPER(c0.symbol), UPPER(c1.symbol),
                                   CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic'
                                        ELSE (lp.fee_bps / 100.0)::text || '%%' END,
                                   pr.name, ch.name
                            FROM route_hop h
                            JOIN liquidity_pool lp ON h.pool_id = lp.id
                            JOIN protocol pr ON lp.protocol_id = pr.id
                            JOIN chain ch ON lp.chain_id = ch.id
                            JOIN coin c0 ON lp.coin0_id = c0.coin_id
                            JOIN coin c1 ON lp.coin1_id = c1.coin_id
                            WHERE h.route_id = %s
                            ORDER BY h.seq
                        """, (rid,))
                        hop_rows = hcur.fetchall()
                        hcur.close()
                        parts = []
                        for h_seq, h_s0, h_s1, h_fee, h_proto, h_chain in hop_rows:
                            if h_seq == 0:
                                parts.append(h_s0)
                            parts.append(f"-- {h_fee}|{h_proto}|{h_chain} -->")
                            parts.append(h_s1)
                        path_str = " ".join(parts)
                        info = {'route_id': rid, 'origin': orig, 'dest': dest, 'hops': hops, 'chain': chain_name, 'path_str': path_str}
                        route_infos[rid] = info
                        route_id_by_path[path_str] = rid
                        route_id_by_pair[(orig, dest)] = rid
                        route_id_by_pair[(dest, orig)] = rid
                    r_cur.close()
                except Exception as e:
                    pass

                # Every route is bucketed daily into route_daily_stats_bucket
                # using the global swap-distribution.yaml parameters, so any
                # direction-matched route with a completed rollup can be served
                # from the compact daily buckets.
                if group_by == "route" and route_infos:
                    selected_infos = {}
                    for rid, info in route_infos.items():
                        if direction == "forward" and (info['origin'] not in start_list or info['dest'] not in end_list):
                            continue
                        if direction == "reverse" and (info['origin'] not in end_list or info['dest'] not in start_list):
                            continue
                        selected_infos[rid] = info
                    if selected_infos:
                        bucket_count = int(DISTRIBUTION_CONFIG['bucket_count'])
                        min_usd = float(DISTRIBUTION_CONFIG['min_amount_usd'])
                        max_usd = float(DISTRIBUTION_CONFIG['max_amount_usd'])
                        route_id_by_path = {info['path_str']: rid for rid, info in selected_infos.items()}
                        bcur = conn.cursor()
                        bcur.execute("""
                            SELECT b.route_id, b.bucket_index,
                                   b.tx_count, b.sample_count, b.volume_usd, b.fees_usd,
                                   b.log_sum, b.log_sum2
                            FROM route_daily_stats_bucket b
                            WHERE b.route_id = ANY(%s)
                              AND b.day >= %s::date AND b.day <= %s::date
                            ORDER BY b.route_id, b.bucket_index
                        """, (list(selected_infos), start_dt, end_dt))
                        bucket_rows = bcur.fetchall()
                        bcur.close()
                        bucket_groups = {}
                        for rid, bucket_idx, tx_count, count, volume, fees, log_sum, log_sum2 in bucket_rows:
                            group = bucket_groups.setdefault(rid, {
                                'counts': [0] * bucket_count,
                                'sums': [0.0] * bucket_count,
                                'fees': [0.0] * bucket_count,
                                'log_sum': 0.0,
                                'log_sum2': 0.0,
                            })
                            index = int(bucket_idx) - 1
                            if 0 <= index < len(group['counts']):
                                group['counts'][index] += int(count or 0)
                                group['sums'][index] += float(volume or 0.0)
                                group['fees'][index] += float(fees or 0.0)
                            group['log_sum'] += float(log_sum or 0.0)
                            group['log_sum2'] += float(log_sum2 or 0.0)

                        # Serve the routes that have bucket rows in the window.
                        # Every route is bucketed daily, so routes without rows
                        # simply had no qualifying swaps in that window and
                        # contribute zero volume to the aggregate.
                        if bucket_groups:
                            edges = [min_usd * (max_usd / min_usd) ** (i / bucket_count)
                                     for i in range(bucket_count + 1)]
                            aggregate_groups = {}
                            for rid, group in bucket_groups.items():
                                info = selected_infos.get(rid)
                                if info is None:
                                    continue
                                counts = group['counts']
                                nonzero = [i for i, value in enumerate(counts) if value]
                                if not nonzero:
                                    continue
                                group['edges'] = edges
                                group['min'] = edges[nonzero[0]]
                                group['max'] = edges[nonzero[-1] + 1]
                                aggregate_groups[info['path_str']] = group
                            bucket_result = sd.analyze_bucket_groups(aggregate_groups)
                            if bucket_result:
                                bucket_result['route_chains'] = bucket_result['chains']
                                for ch in bucket_result['route_chains']:
                                    r_id = route_id_by_path.get(ch['name'])
                                    if r_id is not None:
                                        ch['route_id'] = route_hash_hex(r_id)
                                bucket_result['dir_chains'] = []
                                bucket_result['fee_tier_chains'] = []
                                bucket_result['protocol_chains'] = []
                                bucket_result['split_chains'] = []
                                bucket_result['hops_chains'] = []
                                return bucket_result

            # The endpoint is served exclusively from pre-aggregated
            # route_daily_stats_bucket data. Queries whose bucket rollup has not
            # completed for the requested window have no data regardless of
            # configuration — the raw-swaps fallback was removed as part of the
            # no-raw-swaps migration.
            return None

        result = await asyncio.to_thread(_fetch_and_analyze)
        if not result:
            return {"data": None, "n": 0, "start_token": ",".join(start_list),
                    "end_token": ",".join(end_list),
                    "network": network, "start_date": start_dt.isoformat(),
                    "end_date": end_dt.isoformat()}
        result["start_token"] = ",".join(start_list)
        result["end_token"] = ",".join(end_list)
        result["network"] = network
        result["start_date"] = start_dt.isoformat()
        result["end_date"] = end_dt.isoformat()
        result["exclude_chains"] = exclude
        result["group_by"] = group_by
        result["direction"] = direction
        return {"data": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[swap-distribution] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error analyzing swap distribution: {e}")


@app.get("/api/swap-time-series", tags=["Route"])
async def swap_time_series(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None, description="Lookback period in days"),
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network"),
    limit: int = Query(500000, ge=100, le=2000000, description="Max swap rows sampled"),
    exclude_chains: Optional[str] = Query(None, description="Comma-separated chain names to exclude"),
    group_by: str = Query("chain", pattern="^(chain|direction|split|hops|route)$",
                          description="Group by category"),
    direction: str = Query("both", pattern="^(both|forward|reverse)$",
                           description="Restrict to swap direction"),
    max_hops: Optional[int] = Query(None, description="Max route hop count (1 for direct routes only)"),
    interval: str = Query("day", pattern="^(auto|day)$", description="Time interval: day (hourly was dropped when time-series moved to pre-aggregated route_daily_stats)")
):
    """Analyze time series of swaps (Volume $, Fees $, Count) over day buckets.

    Served from route_daily_stats (pre-aggregated, no raw swaps reads). Grouping
    is restricted to route-level attributes (chain, direction, split, hops,
    route) because per-leg fee_tier/protocol breakdowns are not part of the
    aggregate.
    """
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            try:
                start_dt = datetime.fromisoformat(start_date)
            except Exception:
                start_dt = now - timedelta(days=7)
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date)
                except Exception:
                    end_dt = now
            else:
                end_dt = now
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=7)

        # Resolve tokens/families (e.g. 'BTC' -> WBTC, CBBTC, TBTC...; 'USD' -> USDC, USDT, DAI...)
        # the same way /api/routes/analyze does, so 'USD' matches stablecoins rather than nothing.
        start_list = []
        for s in start_token.split(","):
            s = s.strip()
            if s:
                start_list.extend(resolve_token_input(s))
        end_list = []
        for e in end_token.split(","):
            e = e.strip()
            if e:
                end_list.extend(resolve_token_input(e))
        start_list = [s.upper() for s in start_list]
        end_list = [e.upper() for e in end_list]
        if not start_list or not end_list:
            raise HTTPException(status_code=400, detail="start_token and end_token are required")

        if network and network.lower() in ("all", "*"):
            network = None

        exclude = []
        if isinstance(exclude_chains, str) and exclude_chains:
            exclude = [c.strip().lower() for c in exclude_chains.split(",") if c.strip()]

        chosen_interval = "day"

        def _fetch_time_series():
            with get_conn() as conn:
                cur = conn.cursor()
                exclude_sql = ""
                params: List = []
                if exclude:
                    exclude_sql = " AND LOWER(ch.name) NOT IN %s"
                    params.append(tuple(exclude))
                net_sql = ""
                if network:
                    net_sql = " AND LOWER(ch.name) = LOWER(%s)"
                    params.append(network)

                # Resolve start/end token membership. A '*' side is a wildcard.
                start_wild = '*' in start_list
                end_wild = '*' in end_list
                start_set = set(start_list)
                end_set = set(end_list)

                def _side_pred(col, syms, wild):
                    if wild or not syms:
                        return "(1=1)", []
                    return f"UPPER({col}) = ANY(%s)", [syms]

                # A route matches when its origin/dest pair aligns with the
                # requested start/end tokens in either direction.
                fwd_o, p1 = _side_pred('pair.origin_symbol', start_list, start_wild)
                fwd_d, p2 = _side_pred('pair.dest_symbol', end_list, end_wild)
                rev_o, p3 = _side_pred('pair.origin_symbol', end_list, end_wild)
                rev_d, p4 = _side_pred('pair.dest_symbol', start_list, start_wild)

                if direction == "forward":
                    pair_sql = f"({fwd_o} AND {fwd_d})"
                    pair_params = p1 + p2
                elif direction == "reverse":
                    pair_sql = f"({rev_o} AND {rev_d})"
                    pair_params = p3 + p4
                else:
                    pair_sql = f"(({fwd_o} AND {fwd_d}) OR ({rev_o} AND {rev_d}))"
                    pair_params = p1 + p2 + p3 + p4

                max_hops_sql = ""
                if max_hops is not None:
                    max_hops_sql = " AND r.hops <= %s"
                    params.append(max_hops)

                q_params = [start_dt, end_dt] + pair_params + params
                cur.execute(f"""
                    SELECT
                        r.route_id,
                        UPPER(pair.origin_symbol),
                        UPPER(pair.dest_symbol),
                        r.hops,
                        ch.name,
                        rs.day,
                        rs.tx_count,
                        rs.swap_count,
                        rs.volume_usd,
                        rs.fees_usd
                    FROM route_daily_stats rs
                    JOIN route r ON rs.route_id = r.route_id
                    JOIN origin_destination_pair pair ON r.pair_id = pair.id
                    JOIN chain ch ON r.chain_id = ch.id
                    WHERE rs.day >= %s::date AND rs.day <= %s::date
                      AND {pair_sql}
                      {net_sql}
                      {exclude_sql}
                      {max_hops_sql}
                """, q_params)
                rows = cur.fetchall()

                if not rows:
                    return None

                # Preload canonical path strings for routes grouped by "route".
                route_paths = {}
                if group_by == "route":
                    route_ids = sorted({r[0] for r in rows})
                    cur.execute("""
                        SELECT
                            h.route_id,
                            UPPER(ci.symbol) AS token_in_sym,
                            UPPER(co.symbol) AS token_out_sym,
                            CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic'
                                 ELSE (lp.fee_bps / 100.0)::text || '%%' END AS fee_display,
                            pr.name AS protocol,
                            ch.name AS network
                        FROM route_hop h
                        JOIN liquidity_pool lp ON h.pool_id = lp.id
                        JOIN protocol pr ON lp.protocol_id = pr.id
                        JOIN chain ch ON lp.chain_id = ch.id
                        LEFT JOIN coin_contract cic ON LOWER(cic.contract_address) = LOWER(h.token_in) AND cic.chain_id = lp.chain_id
                        LEFT JOIN coin ci ON cic.coin_id = ci.coin_id
                        LEFT JOIN coin_contract coc ON LOWER(coc.contract_address) = LOWER(h.token_out) AND coc.chain_id = lp.chain_id
                        LEFT JOIN coin co ON coc.coin_id = co.coin_id
                        WHERE h.route_id = ANY(%s)
                        ORDER BY h.route_id, h.seq
                    """, (route_ids,))
                    hops_by_route = {}
                    for rid, tok_in, tok_out, fee_disp, proto, net in cur.fetchall():
                        hops_by_route.setdefault(rid, []).append((tok_in, tok_out, fee_disp, proto, net))
                    for rid, ordered in hops_by_route.items():
                        parts = []
                        for i, (tok_in, tok_out, fee_disp, proto, net) in enumerate(ordered):
                            if i == 0:
                                parts.append(tok_in or '')
                            parts.append(f"{fee_disp}|{proto}|{net}")
                            parts.append(tok_out or '')
                        if parts:
                            route_paths[rid] = " ".join(parts)
                cur.close()

                # Generate contiguous day buckets.
                buckets_list = []
                cur_b = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                while cur_b <= end_dt:
                    buckets_list.append(cur_b.strftime("%Y-%m-%d"))
                    cur_b += timedelta(days=1)
                bucket_set = set(buckets_list)

                series_data = {}
                for (route_id, origin_sym, dest_sym, hops, chain_name,
                     day, tx_count, swap_count, volume_usd, fees_usd) in rows:
                    b_key = day.isoformat() if hasattr(day, 'isoformat') else str(day)[:10]
                    if b_key not in bucket_set:
                        continue

                    # Determine group key from route-level attributes.
                    fwd = (start_wild or origin_sym in start_set) and (end_wild or dest_sym in end_set)
                    rev = (end_wild or origin_sym in end_set) and (start_wild or dest_sym in start_set)
                    if group_by == "chain":
                        g_key = chain_name
                    elif group_by == "direction":
                        g_key = "forward" if (fwd and not (rev and not fwd)) else "reverse"
                    elif group_by == "split":
                        g_key = "Split" if (hops or 1) > 1 else "Non-split"
                    elif group_by == "hops":
                        hp = int(hops or 1)
                        g_key = f"{hp} hop{'s' if hp > 1 else ''}"
                    elif group_by == "route":
                        g_key = route_paths.get(route_id) or f"{origin_sym or '?'} --> {dest_sym or '?'}"
                    else:
                        g_key = chain_name

                    if g_key not in series_data:
                        series_data[g_key] = {bk: {'volume': 0.0, 'fees': 0.0, 'count': 0} for bk in buckets_list}

                    series_data[g_key][b_key]['volume'] += float(volume_usd or 0.0)
                    series_data[g_key][b_key]['fees'] += float(fees_usd or 0.0)
                    series_data[g_key][b_key]['count'] += int(swap_count or 0)

                # Rank groups by total volume to pick top groups.
                group_totals = {}
                for g_key, b_dict in series_data.items():
                    group_totals[g_key] = sum(m['volume'] for m in b_dict.values())

                sorted_groups = sorted(group_totals.keys(), key=lambda g: group_totals[g], reverse=True)
                top_groups = sorted_groups[:10]
                tail_groups = sorted_groups[10:]

                if tail_groups:
                    series_data["Others"] = {bk: {'volume': 0.0, 'fees': 0.0, 'count': 0} for bk in buckets_list}
                    for g_key in tail_groups:
                        for bk in buckets_list:
                            series_data["Others"][bk]['volume'] += series_data[g_key][bk]['volume']
                            series_data["Others"][bk]['fees'] += series_data[g_key][bk]['fees']
                            series_data["Others"][bk]['count'] += series_data[g_key][bk]['count']
                        del series_data[g_key]
                    top_groups.append("Others")

                formatted_series = {}
                totals_by_bucket = {bk: {'volume': 0.0, 'fees': 0.0, 'count': 0} for bk in buckets_list}

                for g_key in top_groups:
                    vol_arr = []
                    fee_arr = []
                    cnt_arr = []
                    for bk in buckets_list:
                        v = series_data[g_key][bk]['volume']
                        f = series_data[g_key][bk]['fees']
                        c = series_data[g_key][bk]['count']
                        vol_arr.append(round(v, 2))
                        fee_arr.append(round(f, 2))
                        cnt_arr.append(c)
                        totals_by_bucket[bk]['volume'] += v
                        totals_by_bucket[bk]['fees'] += f
                        totals_by_bucket[bk]['count'] += c
                    formatted_series[g_key] = {
                        'volume': vol_arr,
                        'fees': fee_arr,
                        'count': cnt_arr
                    }

                grand_totals = {
                    'volume': round(sum(totals_by_bucket[bk]['volume'] for bk in buckets_list), 2),
                    'fees': round(sum(totals_by_bucket[bk]['fees'] for bk in buckets_list), 2),
                    'count': sum(totals_by_bucket[bk]['count'] for bk in buckets_list)
                }

                totals_formatted = {
                    'volume': [round(totals_by_bucket[bk]['volume'], 2) for bk in buckets_list],
                    'fees': [round(totals_by_bucket[bk]['fees'], 2) for bk in buckets_list],
                    'count': [totals_by_bucket[bk]['count'] for bk in buckets_list]
                }

                return {
                    'interval': 'day',
                    'timestamps': buckets_list,
                    'groups': top_groups,
                    'series': formatted_series,
                    'totals': totals_formatted,
                    'grand_totals': grand_totals
                }

        result = await asyncio.to_thread(_fetch_time_series)
        if not result:
            return {"data": None, "n": 0, "start_token": ",".join(start_list),
                    "end_token": ",".join(end_list), "network": network,
                    "start_date": start_dt.isoformat(), "end_date": end_dt.isoformat()}

        result["start_token"] = ",".join(start_list)
        result["end_token"] = ",".join(end_list)
        result["network"] = network
        result["start_date"] = start_dt.isoformat()
        result["end_date"] = end_dt.isoformat()
        result["group_by"] = group_by
        result["direction"] = direction
        return {"data": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[swap-time-series] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error analyzing swap time series: {e}")


@app.get("/api/ods/search-by-contract", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "List of origin/destination pairs matching the contract addresses",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "origin_coin_contract_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                 "destination_coin_contract_address": "0x55d398326f99059ff775485246999027b3197955",
                                 "direction": "both",
                                 "chain": "all",
                                 "show_routes": True,
                                 "n": 1,
                                 "ods": [
                                     {
                                         "od_hash": "2ac53c78a580597e",
                                         "chain_id": 4,
                                         "chain": "BNB",
                                         "origin_coin_contract_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                         "destination_coin_contract_address": "0x55d398326f99059ff775485246999027b3197955",
                                         "origin_coin_id": 4,
                                         "dest_coin_id": 240,
                                         "origin_symbol": "WBNB",
                                         "dest_symbol": "USDT",
                                         "first_seen": "2026-06-23T21:55:25+00:00",
                                         "last_seen": "2026-08-14T07:48:29+00:00",
                                         "routes": [
                                             {
                                                 "route_hash": "837dc52fa8bde82c",
                                                 "hops": 1,
                                                 "route_hops": [
                                                     {"seq": 1, "pool_id": 12345,
                                                      "token_in": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                                      "token_in_symbol": "WBNB",
                                                      "token_out": "0x55d398326f99059ff775485246999027b3197955",
                                                      "token_out_symbol": "USDT"}
                                                 ]
                                             }
                                         ]
                                     }
                                 ]
                             }
                         }
                     }
                 }
             }
         })
async def origin_dest_pair_routes(
    origin_coin_contract_address: str = Query(...,
        description="Origin coin contract address (lowercase or mixed case, with or without 0x). Example: `0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c`"),
    destination_coin_contract_address: str = Query(...,
        description="Destination coin contract address (lowercase or mixed case, with or without 0x). Example: `0x55d398326f99059ff775485246999027b3197955`"),
    direction: str = Query("both", pattern="^(both|forward|backward)$",
                           description="Route direction: forward (origin->destination), backward (destination->origin), or both (default). Example: `both`"),
    chain: str = Query("all", description="Filter routes by chain name (e.g. Ethereum, BNB, Base). 'all' (default) returns routes on every chain. Example: `all`"),
    show_routes: bool = Query(True, description="Compatibility flag: when true, routes+hops are embedded (same as `include=routes.hops`). Default: `true`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths of related resources to embed in `included`. Overrides show_routes. Example: `routes.hops.pool,routes.hops.pool.coin0`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets in JSON:API `type[attr1,attr2]` form to slim the payload. Example: `od[chain,origin_symbol,dest_symbol]`"),
    request: Request = None,
):
    """Return origin/destination pairs matching two contract addresses as a JSON:API compound document.

    ``data`` is an array of ``od`` resources and ``included`` holds the
    requested relatives (routes, hops, pools, coins).

    Example (WBNB → USDT on BNB):

        GET /api/ods/search-by-contract?origin_coin_contract_address=0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c&destination_coin_contract_address=0x55d398326f99059ff775485246999027b3197955&chain=BNB&include=routes.hops.pool

        {
          "data": [
            { "type": "od", "id": "2ac53c78a580597e",
              "attributes": { "chain": "BNB", "origin_symbol": "WBNB", "dest_symbol": "USDT", ... },
              "relationships": { "origin_coin": { "data": { "type": "coin", "id": 4 } },
                                 "destination_coin": { "data": { "type": "coin", "id": 240 } },
                                 "routes": { "data": [ { "type": "route", "id": "837dc52fa8bde82c" } ] } } }
          ],
          "included": [ { "type": "route", ... }, { "type": "hop", ... }, { "type": "pool", ... } ]
        }

    The contract addresses are normalized to lowercase 0x form; the same O&D
    can match in either direction when `direction=both`.
    """
    try:
        def _norm_addr(a: str) -> str:
            s = a.strip().lower()
            return s if s.startswith('0x') else '0x' + s

        addr_a = _norm_addr(origin_coin_contract_address)
        addr_b = _norm_addr(destination_coin_contract_address)

        net_sql = ""
        net_params: List = []
        if chain and chain.lower() not in ("all", "*"):
            net_sql = " AND LOWER(ch.name) = LOWER(%s)"
            net_params = [chain]

        if direction == "forward":
            dir_sql = "(LOWER(pair.origin_contract) = %s AND LOWER(pair.dest_contract) = %s)"
            dir_params = [addr_a, addr_b]
        elif direction == "backward":
            dir_sql = "(LOWER(pair.origin_contract) = %s AND LOWER(pair.dest_contract) = %s)"
            dir_params = [addr_b, addr_a]
        else:
            dir_sql = "((LOWER(pair.origin_contract) = %s AND LOWER(pair.dest_contract) = %s)" \
                      " OR (LOWER(pair.origin_contract) = %s AND LOWER(pair.dest_contract) = %s))"
            dir_params = [addr_a, addr_b, addr_b, addr_a]

        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT pair.id, pair.chain_id, ch.name AS chain_name,
                           pair.origin_contract, pair.dest_contract,
                           pair.origin_coin_id, pair.dest_coin_id,
                           pair.origin_symbol, pair.dest_symbol,
                           pair.first_seen, pair.last_seen
                    FROM origin_destination_pair pair
                    JOIN chain ch ON pair.chain_id = ch.id
                    WHERE {dir_sql}
                      {net_sql}
                    ORDER BY ch.name, pair.id
                """, dir_params + net_params)
                rows = cur.fetchall()
                cur.close()
                return rows

        ods_rows = await asyncio.to_thread(_query)

        ods = []
        for (pair_id, chain_id, chain_name, origin_contract, dest_contract,
             origin_coin_id, dest_coin_id, origin_symbol, dest_symbol,
             first_seen, last_seen) in ods_rows:
            ods.append({
                "od_hash": route_hash_hex(pair_id),
                "chain_id": chain_id,
                "chain": chain_name,
                "origin_coin_contract_address": origin_contract,
                "destination_coin_contract_address": dest_contract,
                "origin_coin_id": origin_coin_id,
                "dest_coin_id": dest_coin_id,
                "origin_symbol": origin_symbol,
                "dest_symbol": dest_symbol,
                "first_seen": first_seen.isoformat() if first_seen else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
            })

        if include is None:
            include = "routes.hops" if show_routes else ""

        return build_od_documents(
            ods, include_spec=include, fields_spec=fields,
            links={"self": str(request.url)},
            meta={
                "query": {
                    "origin_coin_contract_address": addr_a,
                    "destination_coin_contract_address": addr_b,
                    "direction": direction,
                    "chain": chain,
                },
                "n": len(ods),
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[origin-dest-pairs] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up origin/destination pair routes: {e}")


@app.get("/api/od/{od_hash}", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "Full origin/destination pair row",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "od_hash": "2ac53c78a580597e",
                                 "chain_id": 4,
                                 "chain": "BNB",
                                 "origin_coin_contract_address": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                                 "destination_coin_contract_address": "0x55d398326f99059ff775485246999027b3197955",
                                 "origin_coin_id": 4,
                                 "dest_coin_id": 240,
                                 "origin_symbol": "WBNB",
                                 "dest_symbol": "USDT",
                                 "first_seen": "2026-06-23T21:55:25+00:00",
                                 "last_seen": "2026-08-14T07:48:29+00:00"
                             }
                         }
                     }
                 }
             }
         })
async def origin_destination_pair_by_hash(
    od_hash: str = Path(..., description="16-char hex pair hash (the pair_hash from search-by-contract). Example: `2ac53c78a580597e`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths of related resources to embed in `included`. Default: `routes.hops.pool,routes.hops.pool.coin0,routes.hops.pool.coin1`. Example: `routes.hops.pool`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets in JSON:API `type[attr1,attr2]` form to slim the payload. Example: `od[chain,origin_symbol,dest_symbol],pool[fee_bps]`"),
):
    """Return one origin/destination pair as a JSON:API compound document.

    This is the aggregate root of the Chaintelligence object graph. The
    response's ``data`` is the ``od`` resource and ``included`` holds the
    requested relatives (routes, hops, pools, coins) all in a single call.

    Example — full drill-down (WBNB → USDT on BNB):

        GET /api/od/2ac53c78a580597e?include=routes.hops.pool,routes.hops.pool.coin0,routes.hops.pool.coin1

        {
          "data": { "type": "od", "id": "2ac53c78a580597e",
            "attributes": { "chain": "BNB", "origin_symbol": "WBNB",
                            "destination_symbol": "USDT", ... },
            "relationships": {
              "origin_coin":      { "data": { "type": "coin", "id": 4 } },
              "destination_coin": { "data": { "type": "coin", "id": 240 } },
              "routes": { "data": [ { "type": "route", "id": "837dc52fa8bde82c" } ] } } },
          "included": [
            { "type": "route", "id": "837dc52fa8bde82c", "attributes": { "hops": 1 }, ... },
            { "type": "hop",   "id": "837dc52fa8bde82c:0", ... },
            { "type": "pool",  "id": 12345, "attributes": { "fee_bps": 500 }, ... },
            { "type": "coin",  "id": 4, "attributes": { "symbol": "WBNB" }, ... }
          ]
        }

    Slim variant — just the pair row with no relatives:

        GET /api/od/2ac53c78a580597e?include=

    ``od_hash`` is the 16-char lowercase hex rendering of the signed 64-bit
    pair id (same format as the ``pair_hash`` field returned by
    /api/ods/search-by-contract).
    """
    try:
        od_hash = od_hash.strip().lower()
        if len(od_hash) != 16 or any(c not in '0123456789abcdef' for c in od_hash):
            raise HTTPException(status_code=400, detail="od_hash must be a 16-char lowercase hex string")
        pair_id = int(od_hash, 16)
        if pair_id >= (1 << 63):
            pair_id -= (1 << 64)

        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT pair.id, pair.chain_id, ch.name AS chain_name,
                           pair.origin_contract, pair.dest_contract,
                           pair.origin_coin_id, pair.dest_coin_id,
                           pair.origin_symbol, pair.dest_symbol,
                           pair.first_seen, pair.last_seen
                    FROM origin_destination_pair pair
                    JOIN chain ch ON pair.chain_id = ch.id
                    WHERE pair.id = %s
                """, (pair_id,))
                row = cur.fetchone()
                cur.close()
                return row

        row = await asyncio.to_thread(_query)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No origin/destination pair found for hash {od_hash}")

        (pair_id, chain_id, chain_name, origin_contract, dest_contract,
         origin_coin_id, dest_coin_id, origin_symbol, dest_symbol,
         first_seen, last_seen) = row

        od_row = {
            "od_hash": route_hash_hex(pair_id),
            "chain_id": chain_id,
            "chain": chain_name,
            "origin_coin_contract_address": origin_contract,
            "destination_coin_contract_address": dest_contract,
            "origin_coin_id": origin_coin_id,
            "dest_coin_id": dest_coin_id,
            "origin_symbol": origin_symbol,
            "dest_symbol": dest_symbol,
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
        }

        include = include if include is not None else \
            "routes.hops.pool,routes.hops.pool.coin0,routes.hops.pool.coin1"
        return build_od_documents(
            [od_row], include_spec=include, fields_spec=fields,
            links={"self": f"/api/od/{od_hash}"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[od-by-hash] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up origin/destination pair: {e}")


@app.get("/api/ods", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "List of origin/destination pairs (optionally with routes embedded)",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": [
                                 {
                                     "type": "od",
                                     "id": "2ac53c78a580597e",
                                     "attributes": {"chain": "BNB", "origin_symbol": "WBNB", "dest_symbol": "USDT"},
                                     "relationships": {
                                         "origin_coin": {"data": {"type": "coin", "id": 4}},
                                         "destination_coin": {"data": {"type": "coin", "id": 240}},
                                         "routes": {"data": [{"type": "route", "id": "837dc52fa8bde82c"}]},
                                     },
                                 }
                             ]
                         }
                     }
                 }
             }
         })
async def list_ods(
    origin_symbol: Optional[str] = Query(None, description="Filter by origin coin symbol (case-insensitive). Example: `WBNB`"),
    destination_symbol: Optional[str] = Query(None, description="Filter by destination coin symbol (case-insensitive). Example: `USDT`"),
    chain: Optional[str] = Query(None, description="Filter by chain name (e.g. Ethereum, BNB, Base). Example: `BNB`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths of related resources to embed. Example: `routes.hops.pool`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `od[chain,origin_symbol,dest_symbol]`"),
    limit: int = Query(50, ge=1, le=500, description="Max O&D rows to return. Default: `50`"),
    offset: int = Query(0, ge=0, description="Number of rows to skip (pagination). Default: `0`"),
    request: Request = None,
):
    """List origin/destination pairs as a JSON:API compound document.

    The aggregate-root view of the object graph: ``data`` is an array of ``od``
    resources and ``included`` holds any requested relatives (routes, hops,
    pools, coins).

    Example — WBNB/USDT pairs on BNB with their routes:

        GET /api/ods?origin_symbol=WBNB&destination_symbol=USDT&chain=BNB&include=routes.hops

    Example — browse all pairs with full drill-down on the first page:

        GET /api/ods?limit=10&include=routes.hops.pool,routes.hops.pool.coin0
    """
    try:
        sql = """
            SELECT pair.id, pair.chain_id, ch.name AS chain_name,
                   pair.origin_contract, pair.dest_contract,
                   pair.origin_coin_id, pair.dest_coin_id,
                   pair.origin_symbol, pair.dest_symbol,
                   pair.first_seen, pair.last_seen
            FROM origin_destination_pair pair
            JOIN chain ch ON pair.chain_id = ch.id
            WHERE 1=1
        """
        params: List = []
        if origin_symbol:
            sql += " AND UPPER(pair.origin_symbol) = UPPER(%s)"
            params.append(origin_symbol)
        if destination_symbol:
            sql += " AND UPPER(pair.dest_symbol) = UPPER(%s)"
            params.append(destination_symbol)
        if chain:
            sql += " AND LOWER(ch.name) = LOWER(%s)"
            params.append(chain)
        sql += " ORDER BY ch.name, pair.last_seen DESC NULLS LAST, pair.id"
        sql += " LIMIT %s OFFSET %s"
        params += [limit, offset]

        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
                cur.close()
                return rows

        ods_rows = await asyncio.to_thread(_query)
        ods = []
        for (pair_id, chain_id, chain_name, origin_contract, dest_contract,
             origin_coin_id, dest_coin_id, origin_symbol_s, dest_symbol_s,
             first_seen, last_seen) in ods_rows:
            ods.append({
                "od_hash": route_hash_hex(pair_id),
                "chain_id": chain_id,
                "chain": chain_name,
                "origin_coin_contract_address": origin_contract,
                "destination_coin_contract_address": dest_contract,
                "origin_coin_id": origin_coin_id,
                "dest_coin_id": dest_coin_id,
                "origin_symbol": origin_symbol_s,
                "dest_symbol": dest_symbol_s,
                "first_seen": first_seen.isoformat() if first_seen else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
            })

        return build_od_documents(
            ods, include_spec=include, fields_spec=fields,
            links={"self": str(request.url)} if request else None,
            meta={"n": len(ods), "limit": limit, "offset": offset},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[list-ods] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing origin/destination pairs: {e}")


@app.get("/api/ods/set", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "List of origin/destination pairs matching an O&D Set definition",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": [
                                 {
                                     "type": "od",
                                     "id": "2ac53c78a580597e",
                                     "attributes": {"chain": "Ethereum", "origin_symbol": "WBTC", "dest_symbol": "USDC"},
                                 }
                             ],
                             "meta": {
                                 "set": {"origin": "BTC", "dest": "USD", "direction": "both",
                                         "chains": ["Ethereum"]},
                             },
                         }
                     }
                 }
             }
         })
async def list_od_set(
    origin: str = Query(..., description="Origin side of the set. Accepts a coin symbol (e.g. `WBTC`), a coin family (e.g. `BTC`, `USD`), `*` (all coins), or a coin contract address (e.g. `0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599`)."),
    dest: str = Query(..., description="Destination side of the set. Same accepted forms as `origin`."),
    direction: str = Query("both", pattern="^(forward|both)$",
                           description="Set direction: `forward` (origin->dest only) or `both` (also includes dest->origin pairs). Default: `both`"),
    chains: Optional[str] = Query(None, description="Comma-separated chain names (e.g. `Ethereum,Base`). `*` or `all` (default) matches every chain."),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths of related resources to embed. Example: `routes.hops.pool`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `od[chain,origin_symbol,dest_symbol]`"),
    limit: int = Query(50, ge=1, le=500, description="Max O&D rows to return. Default: `50`"),
    offset: int = Query(0, ge=0, description="Number of rows to skip (pagination). Default: `0`"),
    request: Request = None,
):
    """List origin/destination pairs matching an O&D Set definition.

    An **O&D Set** is a declarative definition that denotes *every* O&D pair
    matching its criteria. Each side (`origin`, `dest`) is an expressive token
    selector:

      - a coin symbol (`WBTC`, `USDC`) — resolved via the coin table,
      - a coin family (`BTC`, `USD`) — resolved to all family members,
      - `*` — matches every coin on that side,
      - a coin contract address (`0x…`) — matched against the stored contract.

    `direction` controls whether only the `origin -> dest` orientation is kept
    (`forward`) or both orientations are returned (`both`). `chains` restricts
    the set to one or more networks.

    Example — all BTC-family <-> USD-family pairs on Ethereum:

        GET /api/ods/set?origin=BTC&dest=USD&direction=both&chains=Ethereum&include=routes

    The response is the same JSON:API compound document produced by
    `/api/ods`: `data` is an array of `od` resources and `included` holds any
    relatives requested via `include`.
    """
    try:
        origin_res = await asyncio.to_thread(resolve_od_set_side, origin)
        dest_res = await asyncio.to_thread(resolve_od_set_side, dest)

        # Chain selector: '*'/None/'all' -> no chain filter; otherwise match
        # any of the comma-separated names (case-insensitive).
        chain_list: Optional[List[str]] = None
        if chains and chains.strip() and chains.strip().lower() not in ("all", "*"):
            chain_list = [c.strip() for c in chains.split(",") if c.strip()]
            if not chain_list:
                raise HTTPException(status_code=400, detail="chains: no valid chain names provided")

        fwd_o, fp1 = _od_set_side_sql('origin', origin_res)
        fwd_d, fp2 = _od_set_side_sql('dest', dest_res)
        if direction == "forward":
            where_sql = f"({fwd_o} AND {fwd_d})"
            where_params = fp1 + fp2
        else:
            rev_o, rp1 = _od_set_side_sql('dest', origin_res)
            rev_d, rp2 = _od_set_side_sql('origin', dest_res)
            where_sql = f"(({fwd_o} AND {fwd_d}) OR ({rev_o} AND {rev_d}))"
            where_params = fp1 + fp2 + rp1 + rp2

        net_sql = ""
        net_params: List = []
        if chain_list:
            net_sql = " AND LOWER(ch.name) = ANY(%s)"
            net_params = [[c.lower() for c in chain_list]]

        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT pair.id, pair.chain_id, ch.name AS chain_name,
                           pair.origin_contract, pair.dest_contract,
                           pair.origin_coin_id, pair.dest_coin_id,
                           pair.origin_symbol, pair.dest_symbol,
                           pair.first_seen, pair.last_seen
                    FROM origin_destination_pair pair
                    JOIN chain ch ON pair.chain_id = ch.id
                    WHERE {where_sql}
                      {net_sql}
                    ORDER BY ch.name, pair.last_seen DESC NULLS LAST, pair.id
                    LIMIT %s OFFSET %s
                """, where_params + net_params + [limit, offset])
                rows = cur.fetchall()
                cur.close()
                return rows

        ods_rows = await asyncio.to_thread(_query)

        ods = []
        for (pair_id, chain_id, chain_name, origin_contract, dest_contract,
             origin_coin_id, dest_coin_id, origin_symbol_s, dest_symbol_s,
             first_seen, last_seen) in ods_rows:
            ods.append({
                "od_hash": route_hash_hex(pair_id),
                "chain_id": chain_id,
                "chain": chain_name,
                "origin_coin_contract_address": origin_contract,
                "destination_coin_contract_address": dest_contract,
                "origin_coin_id": origin_coin_id,
                "dest_coin_id": dest_coin_id,
                "origin_symbol": origin_symbol_s,
                "dest_symbol": dest_symbol_s,
                "first_seen": first_seen.isoformat() if first_seen else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
            })

        return build_od_documents(
            ods, include_spec=include, fields_spec=fields,
            links={"self": str(request.url)} if request else None,
            meta={
                "set": {
                    "origin": origin,
                    "dest": dest,
                    "direction": direction,
                    "chains": chain_list or "*",
                },
                "n": len(ods),
                "limit": limit,
                "offset": offset,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[list-od-set] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing O&D set: {e}")


@app.get("/api/ods/goal-state", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "Coverage report for every requirement in config/ods-goal-state.yaml",
                 "content": {
                     "application/json": {
                         "example": {
                             "config_path": "config/ods-goal-state.yaml",
                             "checked_at": "2026-08-16T00:00:00+00:00",
                             "checks": [
                                 {
                                     "name": "BTC-USD daily stats since Apr",
                                     "origin": "BTC", "dest": "USD", "direction": "both",
                                     "chains": "*", "layer": "route_daily_stats",
                                     "window": {"start": "2026-04-01", "end": "2026-08-16"},
                                     "pairs": 163, "expected_days": 138, "present_days": 114,
                                     "missing_days": ["2026-04-22"], "status": "partial",
                                 }
                             ],
                             "gaps": [{"name": "BTC-USD daily stats since Apr", "layer": "route_daily_stats",
                                       "chain": "*", "from": "2026-04-22", "to": "2026-05-31", "days": 40}],
                             "n_checks": 1, "not_ok": 1,
                         }
                     }
                 },
             }
         })
async def get_goal_state(estimate: bool = Query(False, description="If true, also estimate how many rows would be pruned outside the keep-windows.")):
    """Report O&D goal-state coverage against the warehouse.

    Reads ``config/ods-goal-state.yaml`` and evaluates every declared
    requirement + layer combination against the actual warehouse data,
    reporting each as ``ok``, ``partial``, ``missing`` or ``stale`` along with
    the specific days (`window`) currently missing. ``gaps`` collapses those
    missing days into contiguous ranges ready for backfill.

    This is the read-only side of the goal-state retention engine (the pruning
    side runs in the ``ods_goal_state_retention`` Airflow DAG and via the CLI
    in ``chain-feeder/include/scripts/ods_goal_state.py``). The requirement
    side-selectors (`origin`, `dest`) and override semantics match ``/api/ods/set``.
    """
    def _run():
        from include.od_retention import load_goal_state, run_checks, export_gaps, prune
        goal = load_goal_state()
        with get_conn() as conn:
            report = run_checks(conn, goal)
            gaps = export_gaps(report)
            estimates = None
            if estimate:
                result = prune(conn, goal, dry_run=True)
                estimates = result['rows']
        return goal, report, gaps, estimates

    try:
        goal, report, gaps, estimates = await asyncio.to_thread(_run)
        not_ok = sum(1 for r in report if r['status'] != 'ok')
        body = {
            "config_path": goal.get('config_path'),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": report,
            "gaps": gaps,
            "n_checks": len(report),
            "not_ok": not_ok,
        }
        if estimates is not None:
            body["dry_run_estimate"] = estimates
        return body
    except Exception as e:
        print(f"[ods-goal-state] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error evaluating goal state: {e}")


@app.get("/api/routes/date-range", tags=["Route"])
async def get_date_range(network: Optional[str] = Query(None, description="Filter by network")):
    """Get the available date range from the swap data, optionally scoped to a network.

    DB work is offloaded to a worker thread so the event loop stays responsive
    (this endpoint is hit on every page load). The date range is cached per
    network for _DATE_RANGE_CACHE_TTL seconds.

    Both modes use index-only MIN/MAX scans:
      - "all":  SELECT MIN(day), MAX(day) FROM liquidity_pool_daily_stats  (idx_lp_daily_stats_day)
      - network: resolve that network's pool_ids, then MIN/MAX over
        liquidity_pool_daily_stats via the (pool_id) index using = ANY(ARRAY(...)).
    The previous shape joined the full 45M-row swaps table to liquidity_pool and
    grouped by network — a post-normalization full scan that blocked the event
    loop for tens of seconds and wedged the whole server.
    """
    cache_key = (network or 'all').lower()
    now = time.time()
    cached = _DATE_RANGE_CACHE.get(cache_key)
    if cached and (now - cached[1]) < _DATE_RANGE_CACHE_TTL:
        return cached[0]

    def _query():
        with get_conn() as conn:
            cur = conn.cursor()
            try:
                # Bound the worst case so a degraded/cold DB can't hold a
                # pool connection (and thus a worker thread) indefinitely.
                cur.execute("SET LOCAL statement_timeout = '30s'")
                if network and network.lower() != 'all':
                    # Per-network min/max from liquidity_pool_daily_stats (small
                    # table, day-granular). The LATERAL join does one index
                    # seek per pool, so cost is bounded by pool count.
                    cur.execute("""
                        SELECT MIN(p.mn)::date, MAX(p.mx)::date FROM (
                            SELECT l.mn, l.mx
                            FROM unnest(ARRAY(
                                SELECT lp.id FROM liquidity_pool lp
                                JOIN chain ch ON lp.chain_id = ch.id
                                WHERE LOWER(ch.name) = LOWER(%s)
                            )) AS pid
                            CROSS JOIN LATERAL (
                                SELECT MIN(lph.day) AS mn, MAX(lph.day) AS mx
                                FROM liquidity_pool_daily_stats lph
                                WHERE lph.pool_id = pid
                            ) l
                        ) p
                    """, (network,))
                else:
                    # Full available data range across all networks.
                    cur.execute("SELECT MIN(day)::date, MAX(day)::date FROM liquidity_pool_daily_stats")
                return cur.fetchone()
            finally:
                cur.close()

    try:
        row = await asyncio.to_thread(_query)
        if row and row[0] and row[1]:
            result = {"min_date": row[0].isoformat(), "max_date": row[1].isoformat()}
        else:
            result = {"min_date": None, "max_date": None}
        _DATE_RANGE_CACHE[cache_key] = (result, time.time())
        return result
    except Exception as e:
        # Serve stale cache rather than erroring if the DB is momentarily slow.
        if cached:
            return cached[0]
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/routes/{route_hash}", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "Single route with its hops, pools and coins",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "type": "route",
                                 "id": "837dc52fa8bde82c",
                                 "attributes": {"hops": 1},
                                 "relationships": {
                                     "pair": {"data": {"type": "od", "id": "2ac53c78a580597e"}},
                                     "hops": {"data": [{"type": "hop", "id": "837dc52fa8bde82c:0"}]},
                                 },
                             }
                         }
                     }
                 }
             }
         })
async def get_route_by_hash(
    route_hash: str = Path(..., description="16-char hex route hash. Example: `837dc52fa8bde82c`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `hops.pool,routes.hops.pool.coin0`. Example: `hops.pool`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `route[hops],pool[fee_bps]`"),
    request: Request = None,
):
    """Return a single route as a JSON:API compound document.

    Example — one route with its hops and pools:

        GET /api/routes/837dc52fa8bde82c?include=hops.pool

    Example — add the pool's two coins and their metadata:

        GET /api/routes/837dc52fa8bde82c?include=hops.pool,hops.pool.coin0,hops.pool.coin1
    """
    try:
        route_row = await load_route_row(route_hash)
        include = include if include is not None else \
            "hops.pool,hops.pool.coin0,hops.pool.coin1"
        return build_route_documents(
            [route_row], include_spec=include, fields_spec=fields,
            links={"self": f"/api/routes/{route_hash}"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[route-by-hash] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up route: {e}")


@app.get("/api/routes/{route_hash}/daily-stats", tags=["Origin & Destination"],
         responses={
             200: {
                 "description": "Daily stats + bucket distribution for one route",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "type": "route",
                                 "id": "837dc52fa8bde82c",
                                 "relationships": {
                                     "daily_stats": {"data": [
                                         {"type": "route_daily_stat", "id": "837dc52fa8bde82c:2026-08-13"}
                                     ]},
                                 },
                             },
                             "included": [
                                 {"type": "route_daily_stat",
                                  "id": "837dc52fa8bde82c:2026-08-13",
                                  "attributes": {"day": "2026-08-13", "tx_count": 19, "swap_count": 19,
                                                 "volume_usd": 1000.44, "fees_usd": 0.10}},
                             ],
                         }
                     }
                 }
             }
         })
async def get_route_daily_stats(
    route_hash: str = Path(..., description="16-char hex route hash. Example: `837dc52fa8bde82c`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `daily_stats,daily_stats_bucket`. Example: `daily_stats`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `route_daily_stat[day,volume_usd]`"),
    days: Optional[float] = Query(None, description="Window lookback in days for `window_stats` (defaults to the route's full available range)"),
    start_date: Optional[str] = Query(None, description="ISO start date (YYYY-MM-DD) for `window_stats`"),
    end_date: Optional[str] = Query(None, description="ISO end date (YYYY-MM-DD) for `window_stats`"),
):
    """Return one route's pre-aggregated daily stats as a JSON:API compound document.

    Served from `route_daily_stats` (per route+day) and
    `route_daily_stats_bucket` (per route+day+bucket log-volume distribution),
    the same pre-aggregated read models used by `/api/routes/analyze` and
    `/api/swap-distribution`.

    When a window is given (`days`, or `start_date`/`end_date`), the route
    resource also carries a `window_stats` attribute with the window-aggregated
    sums and derived metrics (tx/swap counts, volume, fees, market_size,
    avg_volume, pct_volume, last_activity) matching `/api/routes/analyze`.

    Examples:

        GET /api/routes/837dc52fa8bde82c/daily-stats
        GET /api/routes/837dc52fa8bde82c/daily-stats?include=daily_stats
        GET /api/routes/837dc52fa8bde82c/daily-stats?days=7
        GET /api/routes/837dc52fa8bde82c/daily-stats?fields=route_daily_stat[day,volume_usd]
    """
    try:
        route_row = await load_route_row(route_hash)
        include = include if include is not None else "daily_stats,daily_stats_bucket"
        from resources.graph import _fetch_route_stats_range
        default_range = _fetch_route_stats_range([route_row['route_id']])
        window = resolve_stats_window(days, start_date, end_date, default_range)
        return build_route_documents(
            [route_row], include_spec=include, fields_spec=fields,
            links={"self": f"/api/routes/{route_hash}/daily-stats"},
            window=window,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[route-daily-stats] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up route daily stats: {e}")


@app.get("/api/coins/{coin_id:int}", tags=["Coins"],
         responses={
             200: {
                 "description": "Single coin with its contracts and families",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "type": "coin",
                                 "id": 4,
                                 "attributes": {"symbol": "WBNB", "name": "Wrapped BNB", "price": 520.5},
                                 "relationships": {
                                     "contracts": {"data": [{"type": "coin_contract", "id": "4:BNB"}]},
                                     "families": {"data": []},
                                 },
                             }
                         }
                     }
                 }
             }
         })
async def get_coin_by_id(
    coin_id: int = Path(..., description="Integer coin id (primary key of the `coin` table). Example: `4`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `contracts,families`. Example: `contracts`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `coin[symbol,name]`"),
    request: Request = None,
):
    """Return a single coin as a JSON:API compound document.

    Example — coin with its on-chain contracts and families:

        GET /api/coins/4?include=contracts,families

    Example — slim, just the identity fields:

        GET /api/coins/4?fields=coin[symbol,name]
    """
    try:
        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT c.coin_id, c.symbol, c.name, c.slug, c.hardness,
                           c.cmc_rank, c.cmc_id, c.first_historical_data,
                           c.image_url, c.price, c.price_timestamp, c.decimals,
                           c.percent_change_1h, c.percent_change_24h, c.percent_change_7d,
                           c.percent_change_30d, c.percent_change_60d, c.percent_change_90d,
                           c.market_cap, c.market_cap_dominance, c.fully_diluted_market_cap,
                           c.tvl, c.total_supply, c.circulating_supply, c.max_supply,
                           c.cmc_last_updated
                    FROM coin c
                    WHERE c.coin_id = %s
                """, (coin_id,))
                row = cur.fetchone()
                cur.close()
                return row

        row = await asyncio.to_thread(_query)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No coin found for id {coin_id}")

        cols = ["coin_id", "symbol", "name", "slug", "hardness", "cmc_rank", "cmc_id",
                "first_historical_data", "image_url", "price", "price_timestamp", "decimals",
                "percent_change_1h", "percent_change_24h", "percent_change_7d",
                "percent_change_30d", "percent_change_60d", "percent_change_90d",
                "market_cap", "market_cap_dominance", "fully_diluted_market_cap",
                "tvl", "total_supply", "circulating_supply", "max_supply", "cmc_last_updated"]
        coin = dict(zip(cols, row))
        coin['price'] = float(coin['price']) if coin['price'] is not None else None
        for col in ("percent_change_1h", "percent_change_24h", "percent_change_7d",
                    "percent_change_30d", "percent_change_60d", "percent_change_90d",
                    "market_cap", "market_cap_dominance", "fully_diluted_market_cap",
                    "tvl", "total_supply", "circulating_supply", "max_supply"):
            if coin[col] is not None:
                coin[col] = float(coin[col])
        for col in ("first_historical_data", "price_timestamp", "cmc_last_updated"):
            if coin[col] is not None:
                coin[col] = coin[col].isoformat()

        include = include if include is not None else "contracts,families"
        return build_coin_documents(
            [coin], include_spec=include, fields_spec=fields,
            links={"self": f"/api/coins/{coin_id}"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[coin-by-id] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up coin: {e}")


@app.get("/api/pools/search", tags=["Liquidity Pools"])
async def analyze_pools(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None, description="Lookback period in days"),
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network"),
    limit: int = Query(50, ge=1, le=500, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    sort_by: str = Query("volume", pattern="^(volume|tvl|tx_count|cid)$", description="Sort field: volume, tvl, tx_count, or cid"),
    stream: bool = Query(True, description="Stream NDJSON with progress (true) or return plain JSON (false)"),
    include_history: bool = Query(False, description="Include daily history data for each pool"),
):
    """Analyze liquidity pools matching start and end tokens over a date range."""
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            if end_date:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0 and end_dt.microsecond == 0:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                end_dt = now
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=1)

        start_tokens_list = resolve_token_input(start_token)
        end_tokens_list = resolve_token_input(end_token)
        
        if not start_tokens_list: start_tokens_list = [start_token]
        if not end_tokens_list: end_tokens_list = [end_token]

        fetcher = PostgresFetcher(verbose=True)

        token_filter = []
        if "*" not in start_tokens_list:
            token_filter.extend(start_tokens_list)
        if "*" not in end_tokens_list:
            token_filter.extend(end_tokens_list)
        if not token_filter:
            token_filter = None

        latest_prices = fetcher.fetch_latest_prices(token_filter)

        from fastapi.responses import StreamingResponse
        import json
        import asyncio
        import psycopg2

        async def generate():
            yield json.dumps({"type": "progress", "pct": 20.0, "message": "Querying active liquidity pools directly from database history..."}) + "\n"
            await asyncio.sleep(0.01)

            pool_rows = await asyncio.to_thread(
                fetcher.fetch_pool_explorer_data, start_dt, end_dt, start_tokens_list, end_tokens_list, network,
                limit=limit, offset=offset, sort_by=sort_by,
            )

            if not pool_rows:
                yield json.dumps({"type": "result", "data": {"pools": [], "meta": {"total_pools": 0, "total_tx": 0, "total_volume_usd": 0, "limit": limit, "offset": offset}}}) + "\n"
                return

            yield json.dumps({"type": "progress", "pct": 80.0, "message": "Formatting pool explorer results..."}) + "\n"
            await asyncio.sleep(0.01)

            period_days = max(1.0, (end_dt - start_dt).total_seconds() / 86400.0)
            pools = []
            total_tx = 0
            total_fees = 0.0
            total_volume = sum(p['volume_usd'] for p in pool_rows)

            if not DEFILLAMA_INDEX or (time.time() - DEFILLAMA_INDEX_BUILT_AT > DEFILLAMA_INDEX_TTL):
                await asyncio.to_thread(get_defillama_index)

            async def _enrich_pool(p):
                vol_usd = p['volume_usd']
                tvl_usd = p['avg_tvl']
                fee_disp = p['fee_display']
                proto = p['protocol']
                net_val = p['network']
                fee_full = f"{fee_disp}|{proto}|{net_val}"
                pool_addr = p['pool_address'] or p['pool_id']

                fee_pct = (p['fee_bps'] / 10000.0) if p['fee_bps'] is not None else 0.0005
                base_apr = ((vol_usd * fee_pct * (365.0 / period_days)) / tvl_usd) if tvl_usd > 0 else 0.0

                aprs_dict = {fee_full: {'apr': base_apr, 'tvl': tvl_usd, 'volume': vol_usd}}
                enriched = await get_enriched_pool_stat(fee_full, fee_full, aprs_dict, pool_addr, net_val, period_days, fee_full)

                tvl_final = enriched['tvl']
                apr_final = enriched['apr'] if enriched['apr'] is not None else base_apr

                return (p, tvl_final, apr_final, fee_full)

            enriched_results = await asyncio.gather(*[_enrich_pool(p) for p in pool_rows])

            for p, tvl_usd, apr_val, fee_full in enriched_results:
                total_tx += p['tx_count']
                vol_usd = p['volume_usd']
                fee_pct = (p['fee_bps'] / 10000.0) if p['fee_bps'] is not None else 0.0005
                fees_usd = round(vol_usd * fee_pct, 2) if vol_usd > 0 else 0.0
                total_fees += fees_usd

                # Hardness Token Ordering (Softer 1st, Harder 2nd)
                t0, t1 = p['token0'], p['token1']
                h0, h1 = p['h0'], p['h1']
                if h0 > h1:
                    t0, t1 = t1, t0

                pool_addr = p['pool_address']
                defillama_uuid = get_defillama_pool_uuid(pool_addr)
                created = p['created_at']
                created_iso = created.isoformat() if created else None

                pools.append({
                    'id': p['cid'],
                    'pool_address': pool_addr,
                    'chain': p['network'],
                    'protocol': p['protocol'],
                    'token0': {'coin_id': p['coin0_id'], 'symbol': t0},
                    'token1': {'coin_id': p['coin1_id'], 'symbol': t1},
                    'fee_bps': float(p['fee_bps']) if p['fee_bps'] is not None else None,
                    'fee_tier': p['fee_display'],
                    'created_at': created_iso,
                    'defillama_uuid': defillama_uuid,
                    'tvl_usd': tvl_usd,
                    'volume_usd': vol_usd,
                    'fees_usd': fees_usd,
                    'tx_count': p['tx_count'],
                    'apr_percent': round(apr_val * 100, 1),
                    'last_activity': p['last_activity'].isoformat() if p['last_activity'] else None,
                    'links': build_pool_links(pool_addr, p.get('v4_pool_id'), p['protocol'], p['network'], defillama_uuid),
                })

            # Batch-fetch daily history when requested
            if include_history and pools:
                pool_ids = [pool['id'] for pool in pools]
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT pool_id, day, tvl_usd, volume_usd, tx_count
                            FROM liquidity_pool_daily_stats
                            WHERE pool_id = ANY(%s) AND day >= %s::date AND day <= %s::date
                            ORDER BY pool_id, day DESC
                        """, (pool_ids, start_dt, end_dt))
                        hist_by_pool = {}
                        for pid, d, tvl, vol, txc in cur.fetchall():
                            hist_by_pool.setdefault(pid, []).append({
                                'date': d.isoformat(),
                                'tvl_usd': float(tvl) if tvl else None,
                                'volume_usd': float(vol) if vol else None,
                                'tx_count': txc or 0,
                            })
                        cur.close()
                    for pool in pools:
                        pool['history'] = hist_by_pool.get(pool['id'], [])
                except Exception as e:
                    print(f"  Error fetching pool history: {e}")

            yield json.dumps({"type": "progress", "pct": 100.0, "message": "Complete!"}) + "\n"
            await asyncio.sleep(0.01)

            yield json.dumps({"type": "result", "data": {
                "pools": pools,
                "meta": {
                    "total_pools": len(pools),
                    "total_tx": total_tx,
                    "total_volume_usd": total_volume,
                    "total_fees_usd": round(total_fees, 2),
                    "limit": limit,
                    "offset": offset,
                },
            }}) + "\n"

        if stream:
            return StreamingResponse(generate(), media_type="application/x-ndjson")

        # Non-streaming: collect the generator result into a single JSON response.
        full_result = None
        async for msg in generate():
            parsed = json.loads(msg)
            if parsed.get("type") == "result":
                full_result = parsed["data"]
                break
        if full_result is None:
            full_result = {"pools": [], "meta": {"total_pools": 0, "total_tx": 0, "total_volume_usd": 0, "limit": limit, "offset": offset}}
        return full_result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# Cache for the /api/routes/date-range endpoint. The swap date range changes
# at most once per ingestion run (roughly daily), so a 10-minute TTL turns a
# ~120 ms indexed query (or, pre-warm, a heavier scan) into an instant hit for
# every page load / network switch. Key: lowercased network name or "all".
_DATE_RANGE_CACHE: Dict[str, tuple] = {}
_DATE_RANGE_CACHE_TTL = 600  # seconds


@app.get("/api/lp/position-summary", tags=["Liquidity Pool Positions"])
async def lp_summary():
    """Get the latest summary of LP snapshots with APR calculations."""
    try:
        # Ensure the DeFi Llama yields index (pool address / V4 poolId -> UUID)
        # is warm so per-position UUID lookups below are cheap dict hits; the
        # (24h-cached) build runs off the event loop.
        if not DEFILLAMA_INDEX or (time.time() - DEFILLAMA_INDEX_BUILT_AT > DEFILLAMA_INDEX_TTL):
            await asyncio.to_thread(get_defillama_index)
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # Get target addresses from env (only show user's own positions)
        target_addresses_raw = os.getenv("TARGET_ADDRESS", "")
        target_addresses = [a.strip().lower() for a in target_addresses_raw.split(',') if a.strip()]
        
        # 1. Fetch latest state + metadata from view (or join)
        # We still use the view for the base data, but we'll enrich it with APRs
        if target_addresses:
            addr_placeholders = ','.join(['%s'] * len(target_addresses))
            query_latest = f"""
            SELECT 
                id, timestamp, address, protocol, network, position_label, balance_usd,
                assets, unclaimed, images, total_unclaimed_usd, position_key,
                token_id, tick_lower, tick_upper, current_tick,
                price_lower, price_upper, current_price, in_range, fee_tier, NULL as pool_id,
                coin0_claimed_amount, coin1_claimed_amount
            FROM v_lp_snapshots_summary
            WHERE LOWER(address) IN ({addr_placeholders})
            ORDER BY timestamp DESC
            """
            cur.execute(query_latest, target_addresses)
            all_rows = cur.fetchall()
        else:
            query_latest = """
            SELECT 
                id, timestamp, address, protocol, network, position_label, balance_usd,
                assets, unclaimed, images, total_unclaimed_usd, position_key,
                token_id, tick_lower, tick_upper, current_tick,
                price_lower, price_upper, current_price, in_range, fee_tier, NULL as pool_id,
                coin0_claimed_amount, coin1_claimed_amount
            FROM v_lp_snapshots_summary
            ORDER BY timestamp DESC
            """
            cur.execute(query_latest)
            all_rows = cur.fetchall()
            # Fallback if the view returns no rows (e.g., empty DB)
            if not all_rows:
                cur.execute("""
                    SELECT
                        pos.id, pos.timestamp, pos.address, pos.protocol, pos.network, pos.position_label,
                        pos.balance_usd, pos.assets, pos.unclaimed, pos.images, pos.total_unclaimed_usd,
                        pos.position_key, pos.token_id, lp.tick_lower, lp.tick_upper, lp.current_tick,
                        lp.price_lower, lp.price_upper, lp.current_price, lp.in_range,
                        CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic' ELSE (lp.fee_bps / 100.0)::text || '%' END AS fee_tier,
                        lp.id AS pool_id, 0 as coin0_claimed_amount, 0 as coin1_claimed_amount
                    FROM liquidity_pool_position pos
                    JOIN liquidity_pool lp ON pos.pool_id = lp.id
                    ORDER BY pos.timestamp DESC LIMIT 100
                """)
                all_rows = cur.fetchall()
                # After attempting the fallback, if there are still no rows we return a helpful message
                if not all_rows:
                    return JSONResponse(status_code=200, content={"detail": "No liquidity‑pool positions found in the database."})

        latest_positions = {}
        for row in all_rows:
            key = row[11] if row[11] else f"{row[3]}-{row[5]}-{row[4]}"
            if key not in latest_positions:
                latest_positions[key] = row

        # Resolve the real pool_address (and pool_id) per position via the
        # position -> liquidity_pool join. The summary view exposes position_key
        # but not the pool address, which the frontend needs for Uniswap /
        # DexScreener / Revert / DeFi Llama links (parity with Pool Explorer).
        position_keys = [k for k in latest_positions.keys() if k]
        pool_lookup = {}
        if position_keys:
            cur.execute("""
                SELECT lpp.position_key, lp.pool_address, lp.id, lp.pool_id
                FROM liquidity_pool_position lpp
                JOIN liquidity_pool lp ON lpp.pool_id = lp.id
                WHERE lpp.position_key = ANY(%s)
            """, (position_keys,))
            for pk, addr, pid, v4id in cur.fetchall():
                pool_lookup[pk] = {"pool_address": addr, "pool_id": pid, "v4_pool_id": v4id}
                
        # 2. Fetch historical snapshots for APR calculation (Last 8 days)
        # We need raw snapshot data to calculate fee growth
        query_history = """
        SELECT 
            pos.position_key,
            s.timestamp,
            s.balance_usd,
            s.coin0_claimable_amount,
            s.coin1_claimable_amount,
            s.coin0_claimed_amount,
            s.coin1_claimed_amount,
            p.coin0_price,
            p.coin1_price
        FROM liquidity_pool_position_snapshot s
        JOIN liquidity_pool_position pos ON s.position_id = pos.id
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
        -- Join with coins to get CURRENT price for simple USD estimation? 
        -- Or rely on captured USD? The snapshot table lacks captured price history usually, 
        -- so we use current price for older tokens approximation or if snapshot has it.
        -- Actually, let's just fetch amounts and use CURRENT price to value them for consistency.
        JOIN coin c0 ON pool.coin0_id = c0.coin_id
        JOIN coin c1 ON pool.coin1_id = c1.coin_id
        CROSS JOIN LATERAL (SELECT c0.price as coin0_price, c1.price as coin1_price) p
        WHERE s.timestamp > NOW() - INTERVAL '8 days'
        ORDER BY s.timestamp DESC
        """
        cur.execute(query_history)
        history_rows = cur.fetchall()
        
        # Organize history by position_key
        # structure: history[key] = [{ts, bal, c0_rew, c1_rew, p0, p1}, ...]
        history = {}
        for r in history_rows:
            pkey = r[0]
            if pkey not in history: history[pkey] = []
            history[pkey].append({
                'ts': r[1],
                'bal_usd': float(r[2]) if r[2] else 0,
                'rew0': float(r[3]) if r[3] else 0,
                'rew1': float(r[4]) if r[4] else 0,
                'claimed0': float(r[5]) if r[5] else 0,
                'claimed1': float(r[6]) if r[6] else 0,
                'p0': float(r[7]) if r[7] else 0,
                'p1': float(r[8]) if r[8] else 0
            })

        results = []
        for key, latest in latest_positions.items():
            assets = latest[7] if latest[7] else []
            unclaimed = latest[8] if latest[8] else []
            
            import json
            # Parse assets if string to get symbols
            assets_parsed = assets
            if isinstance(assets_parsed, str):
                assets_parsed = json.loads(assets_parsed)
                
            claimed = []
            if len(assets_parsed) >= 2:
                claimed = [
                    {"symbol": assets_parsed[0]["symbol"], "balance": float(latest[22]) if len(latest) > 22 and latest[22] else 0.0, "balanceUSD": 0.0},
                    {"symbol": assets_parsed[1]["symbol"], "balance": float(latest[23]) if len(latest) > 23 and latest[23] else 0.0, "balanceUSD": 0.0}
                ]
            
            # Extract standard fields
            res_obj = {
                "id": latest[0],
                "timestamp": latest[1].isoformat(),
                "address": latest[2],
                "position_key": latest[11],
                "protocol": latest[3],
                "network": latest[4],
                "position_label": latest[5],
                "balance_usd": float(latest[6]) if latest[6] else 0,
                "assets": assets,
                "unclaimed": unclaimed,
                "claimed": claimed,
                "total_unclaimed_usd": float(latest[10]) if latest[10] else 0,
                "images": latest[9],
                "token_id": latest[12],
                "pool_id": (pool_lookup.get(key) or {}).get("pool_id") or latest[21],
                "pool_address": (pool_lookup.get(key) or {}).get("pool_address"),
                "defillama_uuid": get_defillama_pool_uuid((pool_lookup.get(key) or {}).get("pool_address")),
                "links": build_pool_links(
                    (pool_lookup.get(key) or {}).get("pool_address"),
                    (pool_lookup.get(key) or {}).get("v4_pool_id"),
                    latest[3], latest[4],
                    get_defillama_pool_uuid((pool_lookup.get(key) or {}).get("pool_address")),
                ),
                # Range data (indices 12-20)
                "range_data": {
                    "token_id": latest[12],
                    "tick_lower": latest[13],
                    "tick_upper": latest[14],
                    "current_tick": latest[15],
                    "price_lower": float(latest[16]) if latest[16] else None,
                    "price_upper": float(latest[17]) if latest[17] else None,
                    "current_price": float(latest[18]) if latest[18] else None,
                    "in_range": latest[19],
                    "fee_tier": latest[20]
                } if latest[12] else None
            }
            
            # --- APR Calculation ---
            # Algorithm: 
            # 1. Get snapshots for this position
            # 2. Find snapshot ~24h ago and ~7d ago
            # 3. Calculate Fee Growth USD
            # 4. APR = (Growth / Principal) * (365/days)
            
            snaps = history.get(key, [])
            # Sort by desc timestamp (newest first)
            snaps.sort(key=lambda x: x['ts'], reverse=True)
            
            current_snap = snaps[0] if snaps else None
            
            def calculate_apr(days_lookback):
                if not current_snap: return 0.0
                if current_snap['bal_usd'] == 0: return 0.0
                
                target_date = datetime.now(current_snap['ts'].tzinfo) - timedelta(days=days_lookback)
                
                # Find closest snapshot
                prev_snap = None
                for s in snaps:
                    if s['ts'] <= target_date:
                        prev_snap = s
                        break
                
                if not prev_snap: return 0.0
                
                # Calculate Delta Time (in days)
                delta_days = (current_snap['ts'] - prev_snap['ts']).total_seconds() / 86400
                if delta_days < 0.5: return 0.0 # Too short
                
                # Calculate Fee Growth in Tokens (Unclaimed + Claimed)
                curr_fees0 = current_snap['rew0'] + current_snap['claimed0']
                curr_fees1 = current_snap['rew1'] + current_snap['claimed1']
                prev_fees0 = prev_snap['rew0'] + prev_snap['claimed0']
                prev_fees1 = prev_snap['rew1'] + prev_snap['claimed1']
                
                d_r0 = curr_fees0 - prev_fees0
                d_r1 = curr_fees1 - prev_fees1
                
                # If negative, ignore (this should be rare now with claimed amounts tracked)
                if d_r0 < 0: d_r0 = 0
                if d_r1 < 0: d_r1 = 0
                
                # Value in USD using CURRENT prices
                growth_usd = (d_r0 * current_snap['p0']) + (d_r1 * current_snap['p1'])
                
                # APR
                # extrapolated_year = growth_usd * (365 / delta_days)
                # apr = (extrapolated_year / current_snap['bal_usd'])
                if current_snap['bal_usd'] > 0:
                    apr = (growth_usd / current_snap['bal_usd']) * (365.0 / delta_days)
                    return apr
                return 0.0

            if current_snap:
                # Calculate total unclaimed USD for main display if view provided 0
                # Using latest amounts * current prices
                calc_unclaimed_usd = (current_snap['rew0'] * current_snap['p0']) + (current_snap['rew1'] * current_snap['p1'])
                res_obj['total_unclaimed_usd'] = calc_unclaimed_usd
                
                # Enrich Assets USD value (since view returns 0)
                # Parse assets JSON if string or list
                # assets structure: [{'symbol': 'ETH', 'balance': 1.2, 'balanceUSD': 0}, ...]
                import json
                if isinstance(assets, str):
                    assets = json.loads(assets)
                
                # We assume order matches coin0/coin1 from history calculation
                # But safer to match by symbol if possible, or assume 0=coin0, 1=coin1 from view construction
                # View construct: coin0, coin1.
                if len(assets) >= 2:
                    assets[0]['balanceUSD'] = float(assets[0]['balance']) * current_snap['p0']
                    assets[0]['price'] = float(current_snap['p0'])
                    assets[1]['balanceUSD'] = float(assets[1]['balance']) * current_snap['p1']
                    assets[1]['price'] = float(current_snap['p1'])
                    res_obj['assets'] = assets

                unclaimed = res_obj.get('unclaimed', [])
                if isinstance(unclaimed, str):
                    unclaimed = json.loads(unclaimed)
                if len(unclaimed) >= 2:
                    unclaimed[0]['balanceUSD'] = float(unclaimed[0]['balance']) * current_snap['p0']
                    unclaimed[1]['balanceUSD'] = float(unclaimed[1]['balance']) * current_snap['p1']
                    res_obj['unclaimed'] = unclaimed

                claimed = res_obj.get('claimed', [])
                if len(claimed) >= 2:
                    claimed[0]['balanceUSD'] = float(claimed[0]['balance']) * current_snap['p0']
                    claimed[1]['balanceUSD'] = float(claimed[1]['balance']) * current_snap['p1']
                    res_obj['claimed'] = claimed

                # Calculate Deltas for "Accrued" label (since last snapshot? Or 24h?)
                # Existing logic used last snapshot delta. Let's keep that or standardize to 24h?
                # User wants "1d APR". The "accrued" label usually implied "since last check".
                # Let's add explicit APR fields.
                res_obj['apr_1d'] = calculate_apr(1)
                res_obj['apr_7d'] = calculate_apr(7)
                
            else:
                res_obj['apr_1d'] = 0
                res_obj['apr_7d'] = 0
                
                # No snapshot history - fetch prices directly from coin table
                import json
                if isinstance(assets, str):
                    assets = json.loads(assets)
                
                if len(assets) >= 2:
                    # Fetch current prices for these symbols
                    cur.execute("SELECT symbol, price FROM coin WHERE symbol IN (%s, %s)", 
                               (assets[0]['symbol'], assets[1]['symbol']))
                    price_rows = cur.fetchall()
                    price_map = {row[0]: float(row[1]) if row[1] else 0.0 for row in price_rows}
                    
                    assets[0]['price'] = price_map.get(assets[0]['symbol'], 0.0)
                    assets[0]['balanceUSD'] = float(assets[0]['balance']) * assets[0]['price']
                    assets[1]['price'] = price_map.get(assets[1]['symbol'], 0.0)
                    assets[1]['balanceUSD'] = float(assets[1]['balance']) * assets[1]['price']
                    res_obj['assets'] = assets

                unclaimed = res_obj.get('unclaimed', [])
                if isinstance(unclaimed, str):
                    unclaimed = json.loads(unclaimed)
                if len(unclaimed) >= 2:
                    p0 = price_map.get(unclaimed[0]['symbol'], 0.0) if 'price_map' in locals() else 0.0
                    p1 = price_map.get(unclaimed[1]['symbol'], 0.0) if 'price_map' in locals() else 0.0
                    unclaimed[0]['balanceUSD'] = float(unclaimed[0]['balance']) * p0
                    unclaimed[1]['balanceUSD'] = float(unclaimed[1]['balance']) * p1
                    res_obj['unclaimed'] = unclaimed

                claimed = res_obj.get('claimed', [])
                if len(claimed) >= 2:
                    p0 = price_map.get(claimed[0]['symbol'], 0.0) if 'price_map' in locals() else 0.0
                    p1 = price_map.get(claimed[1]['symbol'], 0.0) if 'price_map' in locals() else 0.0
                    claimed[0]['balanceUSD'] = float(claimed[0]['balance']) * p0
                    claimed[1]['balanceUSD'] = float(claimed[1]['balance']) * p1
                    res_obj['claimed'] = claimed

            results.append(res_obj)
            
        results.sort(key=lambda x: x["balance_usd"], reverse=True)
        cur.close()
        conn.close()
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/lp/history", tags=["Liquidity Pool Positions"])
async def lp_history(position_key: str):
    """Get historical events for a specific LP position."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()

        # Fetch events for the specific position key
        query = """
        SELECT
            e.timestamp,
            e.event_type,
            e.amount0,
            e.amount1,
            e.tx_hash,
            c0.symbol,
            c1.symbol,
            ch.name
        FROM liquidity_pool_position_event e
        JOIN liquidity_pool_position pos ON e.position_id = pos.id
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
        JOIN chain ch ON pool.chain_id = ch.id
        JOIN coin c0 ON pool.coin0_id = c0.coin_id
        JOIN coin c1 ON pool.coin1_id = c1.coin_id
        WHERE (pos.position_key = %s OR pos.id::text = %s)
          AND e.event_type IN ('create', 'add_liquidity', 'withdraw', 'delete', 'collect_claim')
        ORDER BY e.timestamp DESC
        """
        
        cur.execute(query, (position_key, position_key))
        rows = cur.fetchall()

        raw_history = []
        tx_groups = {}

        for r in rows:
            event = {
                "timestamp": r[0].isoformat(),
                "event_type": r[1],
                "amount0": float(r[2]) if r[2] else 0.0,
                "amount1": float(r[3]) if r[3] else 0.0,
                "tx_hash": r[4],
                "coin0": r[5],
                "coin1": r[6],
                "network": r[7]
            }
            raw_history.append(event)

            if event['tx_hash'] not in tx_groups:
                tx_groups[event['tx_hash']] = []
            tx_groups[event['tx_hash']].append(event)

        # A position NFT is minted exactly once. Older duplicate 'create'
        # events are backfill artifacts (txs that predate the NFT) — keep
        # only the latest create per position.
        create_events = [e for e in raw_history if e['event_type'] == 'create']
        if len(create_events) > 1:
            latest = max(create_events, key=lambda x: x['timestamp'])
            raw_history = [e for e in raw_history
                           if e['event_type'] != 'create'
                           or (e['timestamp'] == latest['timestamp'] and e['tx_hash'] == latest['tx_hash'])]

        history = []
        for e in raw_history:
            # 1. If Add Liquidity AND sibling Create exists -> Skip (merged into Create)
            if e['event_type'] == 'add_liquidity':
                siblings = tx_groups[e['tx_hash']]
                if any(s['event_type'] == 'create' for s in siblings):
                    continue

            # 2. If Create AND sibling Add Liquidity exists -> Merge amounts
            if e['event_type'] == 'create':
                siblings = tx_groups[e['tx_hash']]
                add_ev = next((s for s in siblings if s['event_type'] == 'add_liquidity'), None)
                if add_ev:
                    e['amount0'] = add_ev['amount0']
                    e['amount1'] = add_ev['amount1']
            
            history.append(e)

        cur.close()
        conn.close()
        return history
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pool/{identifier}", tags=["Liquidity Pools"],
         responses={
             200: {
                 "description": "Single pool as a JSON:API compound document",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "type": "pool",
                                 "id": 1,
                                 "attributes": {
                                     "pool_address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                                     "fee_bps": 500,
                                     "fee_tier": "0.05%",
                                     "protocol": "uniswap_v3",
                                     "chain": "Ethereum",
                                     "tvl_usd": 1234567.0,
                                 },
                                 "relationships": {
                                     "coin0": {"data": {"type": "coin", "id": 506}},
                                     "coin1": {"data": {"type": "coin", "id": 4}},
                                 },
                             },
                             "included": [
                                 {"type": "coin", "id": 506, "attributes": {"symbol": "WETH"}},
                                 {"type": "coin", "id": 4, "attributes": {"symbol": "USDC"}},
                             ],
                         }
                     }
                 }
             }
         })
async def get_pool(
    identifier: str = Path(..., description="Numeric liquidity_pool.id, a 42-char 0x V3 contract address, or a 66-char 0x V4 pool_id. Example: `1` or `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `coin0,coin1`. Example: `coin0.contracts,coin1`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `pool[pool_address,fee_bps,tvl_usd]`"),
    request: Request = None,
):
    """Get pool metadata, analytics, and external links as a JSON:API compound document.

    Accepts a liquidity_pool.id (integer), a V3 contract address (0x + 40 hex,
    42 chars), or a V4 pool_id (0x + 64 hex, 66 chars). ``data`` is the ``pool``
    resource (including ``links`` and 90-day ``history``); ``included`` holds
    the coins of the pair (default) plus any relatives you request via
    ``?include=``.

    Example:

        GET /api/pool/1?include=coin0,coin1
    """
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()

        # Determine what to search by.
        if identifier.isdigit():
            col_clause = "lp.id = %s"
            param = int(identifier)
        elif len(identifier) == 42 and identifier.startswith("0x"):
            col_clause = "LOWER(lp.pool_address) = LOWER(%s)"
            param = identifier
        elif len(identifier) == 66 and identifier.startswith("0x"):
            col_clause = "LOWER(lp.pool_id) = LOWER(%s) OR LOWER(lp.pool_address) = LOWER(%s)"
            param = (identifier, identifier)
        else:
            raise HTTPException(
                status_code=400,
                detail="Identifier must be a numeric id, a 42-char 0x contract address, or a 66-char 0x V4 pool_id",
            )

        # Fetch pool metadata.
        q = f"""
            SELECT lp.id, lp.pool_address, lp.pool_id, lp.fee_bps, lp.chain_id,
                   ch.name AS network, pr.name AS protocol,
                   c0.coin_id AS c0_id, c0.symbol AS c0_sym,
                   c1.coin_id AS c1_id, c1.symbol AS c1_sym,
                   lp.created_at
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            JOIN coin c0 ON lp.coin0_id = c0.coin_id
            JOIN coin c1 ON lp.coin1_id = c1.coin_id
            WHERE {col_clause}
            LIMIT 1
        """
        if isinstance(param, tuple):
            cur.execute(q, param)
        else:
            cur.execute(q, (param,))
        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()
            # Try id numeric match if the identifier is hex but no match yet.
            # This is just a last-resort  — we already checked above.
            raise HTTPException(status_code=404, detail="Pool not found")

        (
            pool_id, pool_address, v4_pool_id, fee_bps, chain_id,
            network, protocol,
            coin0_id, coin0_sym,
            coin1_id, coin1_sym,
            created_at
        ) = row

        is_v4 = "v4" in (protocol or "").lower()
        is_pancake_v4 = "pancakeswapv4" in (protocol or "").lower().replace(" ", "")

        # For V3 pools derive the canonical address for comparison.
        canonical_address = None
        fee_val = round(fee_bps) if fee_bps else None
        if not is_v4 and fee_bps is not None and coin0_id and coin1_id:
            try:
                cur.execute("""
                    SELECT cc.contract_address
                    FROM coin_contract cc
                    JOIN chain ch ON cc.chain_id = ch.id
                    WHERE cc.coin_id = %s AND LOWER(ch.name) = LOWER(%s)
                    LIMIT 1
                """, (coin0_id, network))
                r0 = cur.fetchone()
                cur.execute("""
                    SELECT cc.contract_address
                    FROM coin_contract cc
                    JOIN chain ch ON cc.chain_id = ch.id
                    WHERE cc.coin_id = %s AND LOWER(ch.name) = LOWER(%s)
                    LIMIT 1
                """, (coin1_id, network))
                r1 = cur.fetchone()
                if r0 and r1:
                    t0_bytes = bytes.fromhex(r0[0].lower().removeprefix("0x"))
                    t1_bytes = bytes.fromhex(r1[0].lower().removeprefix("0x"))
                    from config.dex_config import DEX_CONFIG  # noqa
                    proto_key = "pancakeswap_v3" if "pancake" in protocol.lower() else "uniswap_v3"
                    net_key = network.lower()
                    cfg = DEX_CONFIG.get(proto_key, {})
                    net_cfg = cfg.get(net_key) or (cfg.get("eth") if net_key == "ethereum" else None)
                    if net_cfg and "factory" in net_cfg:
                        canonical_address = _derive_address(
                            t0_bytes, t1_bytes, fee_val,
                            net_cfg["factory"], net_cfg["init_hash"], is_v2=False
                        )
            except Exception:
                pass  # non-fatal; canonical_address remains None

        # DeFiLlama UUID.
        addr_for_lookup = canonical_address or pool_address or v4_pool_id or ""
        defillama_uuid = get_defillama_pool_uuid(addr_for_lookup)

        # TVL and volume from liquidity_pool_daily_stats.
        cur.execute("""
            SELECT day, tvl_usd, volume_usd, tx_count
            FROM liquidity_pool_daily_stats
            WHERE pool_id = %s
              AND day >= NOW() - INTERVAL '90 days'
            ORDER BY day DESC
        """, (pool_id,))
        history_rows = cur.fetchall()

        latest_tvl = None
        latest_volume = None
        latest_tx_count = 0
        history = []
        for hr in history_rows:
            d, tvl, vol, txs = hr
            history.append({
                "date": d.isoformat(),
                "tvl_usd": float(tvl) if tvl else None,
                "volume_usd": float(vol) if vol else None,
                "tx_count": txs or 0,
            })
            if latest_tvl is None and tvl:
                latest_tvl = float(tvl)
            if latest_volume is None and vol:
                latest_volume = float(vol)
            if txs:
                latest_tx_count = txs or latest_tx_count

        # Fallback TVL from DeFiLlama if history had none.
        if latest_tvl is None:
            latest_tvl = get_defillama_pool_tvl(addr_for_lookup)

        cur.close()
        conn.close()

        links = build_pool_links(pool_address, v4_pool_id, protocol, network, defillama_uuid)

        pool_row = {
            "pool_id": pool_id,
            "pool_address": pool_address or v4_pool_id or "",
            "v4_pool_id": v4_pool_id or pool_address or "",
            "chain_id": chain_id,
            "protocol": protocol,
            "coin0_id": coin0_id,
            "coin1_id": coin1_id,
            "fee_bps": float(fee_bps) if fee_bps else None,
            "fee_tier": f"{fee_val / 100.0:.2f}%" if fee_val else ("Dynamic" if fee_bps is None else None),
            "canonical_address": canonical_address or "",
            "defillama_uuid": defillama_uuid,
            "tvl_usd": latest_tvl or None,
            "volume_usd_24h": latest_volume or None,
            "tx_count": latest_tx_count,
            "created_at": created_at.isoformat() if created_at else None,
            "links": links,
            "history": history,
        }

        include = include if include is not None else "coin0,coin1"
        return build_pool_documents(
            [pool_row], include_spec=include, fields_spec=fields,
            links={"self": str(request.url)},
            meta={
                "query": {
                    "identifier": identifier,
                    "chain": network,
                    "protocol": protocol,
                },
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pool/{identifier}/daily-stats", tags=["Liquidity Pools"],
         responses={
             200: {
                 "description": "Daily stats + bucket distribution for one pool",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": {
                                 "type": "pool",
                                 "id": 1,
                                 "relationships": {
                                     "daily_stats": {"data": [
                                         {"type": "pool_daily_stat", "id": "1:2026-08-13"}
                                     ]},
                                 },
                             },
                             "included": [
                                 {"type": "pool_daily_stat",
                                  "id": "1:2026-08-13",
                                  "attributes": {"day": "2026-08-13", "tx_count": 19,
                                                 "volume_usd": 1000.44, "tvl_usd": 5800000.0}},
                             ],
                         }
                     }
                 }
             }
         })
async def get_pool_daily_stats(
    identifier: str = Path(..., description="Numeric liquidity_pool.id, a 42-char 0x V3 contract address, or a 66-char 0x V4 pool_id. Example: `1` or `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `daily_stats,daily_stats_bucket`. Example: `daily_stats`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `pool_daily_stat[day,volume_usd]`"),
    days: Optional[float] = Query(None, description="Window lookback in days for `window_stats`/`apr` (defaults to the pool's full available range)"),
    start_date: Optional[str] = Query(None, description="ISO start date (YYYY-MM-DD) for `window_stats`/`apr`"),
    end_date: Optional[str] = Query(None, description="ISO end date (YYYY-MM-DD) for `window_stats`/`apr`"),
):
    """Return one pool's pre-aggregated daily stats as a JSON:API compound document.

    Served from `liquidity_pool_daily_stats` (per pool+day: volume, TVL, tx
    count) and `liquidity_pool_daily_stats_bucket` (per pool+day+bucket
    log-volume distribution), the same pre-aggregated read models used by the
    pool page's history chart.

    When a window is given (`days`, or `start_date`/`end_date`), the pool
    resource also carries `window_stats` (window sums: tx_count, volume_usd,
    tvl_usd, fees_usd) and an `apr` attribute computed with the same fee-rate /
    TVL-reliability math as `/api/routes/analyze`.

    Accepts a liquidity_pool.id (integer), a V3 contract address (0x + 40 hex,
    42 chars), or a V4 pool_id (0x + 64 hex, 66 chars).

    Examples:

        GET /api/pool/1/daily-stats
        GET /api/pool/1/daily-stats?include=daily_stats
        GET /api/pool/1/daily-stats?days=7
        GET /api/pool/1/daily-stats?fields=pool_daily_stat[day,volume_usd]
    """
    try:
        pool_row = await load_pool_row(identifier)
        include = include if include is not None else "daily_stats,daily_stats_bucket"
        from resources.graph import _fetch_pool_stats_range
        default_range = _fetch_pool_stats_range([pool_row['pool_id']])
        window = resolve_stats_window(days, start_date, end_date, default_range)
        return build_pool_documents(
            [pool_row], include_spec=include, fields_spec=fields,
            links={"self": f"/api/pool/{identifier}/daily-stats"},
            window=window,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[pool-daily-stats] error: {e}")
        raise HTTPException(status_code=500, detail=f"Error looking up pool daily stats: {e}")


@app.get("/api/coin/price-history", tags=["Coins"])
async def price_history(symbol: str, start: Optional[int] = None, end: Optional[int] = None):
    """Get historical daily prices for a coin from Postgres.

    If a date range is provided and the cached data has gaps, missing ranges are
    fetched on demand from DeFi Llama (resolved via coin_contract) and upserted
    into coin_price_history before being returned.
    """
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()

        # Resolve coin_id so we can check coverage and fetch gaps.
        cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", (symbol.upper(),))
        coin_row = cur.fetchone()
        coin_id = coin_row[0] if coin_row else None

        if coin_id is not None and start is not None and end is not None:
            start_s = start / 1000.0
            end_s = end / 1000.0

            # Check how much of the requested range is already cached.
            cur.execute(
                """
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM coin_price_history
                WHERE coin_id = %s
                  AND timestamp >= to_timestamp(%s)
                  AND timestamp <= to_timestamp(%s)
                """,
                (coin_id, start_s, end_s),
            )
            count, min_ts, max_ts = cur.fetchone()

            has_gap = (
                count == 0
                or (max_ts is not None and max_ts.timestamp() * 1000 < end)
                or (min_ts is not None and min_ts.timestamp() * 1000 > start)
            )

            if has_gap:
                # Resolve a chain + contract address for this coin.
                cur.execute(
                    """
                    SELECT ch.name, cc.contract_address
                    FROM coin_contract cc
                    JOIN chain ch ON ch.id = cc.chain_id
                    WHERE cc.coin_id = %s AND cc.contract_address IS NOT NULL
                    ORDER BY cc.chain_id
                    LIMIT 1
                    """,
                    (coin_id,),
                )
                contract_row = cur.fetchone()
                if contract_row:
                    chain_name, address = contract_row
                    from defillama_client import fetch_historical_prices
                    from psycopg2.extras import execute_values

                    # DeFi Llama rejects start+end together, so we walk forward from
                    # `start` in <=500-day batches. Overlap with cached data is
                    # deduplicated by the ON CONFLICT clause below.
                    fetch_start = int(start_s)
                    fetch_end = int(end_s)
                    MAX_BATCHES = 10
                    for _ in range(MAX_BATCHES):
                        if fetch_start >= fetch_end:
                            break
                        history = await asyncio.to_thread(
                            fetch_historical_prices,
                            address,
                            chain_name,
                            fetch_start,  # start_timestamp
                            None,          # end_timestamp must be None (API rejects both)
                            500,
                        )
                        if not history:
                            break
                        batch = [
                            (coin_id, datetime.fromtimestamp(p["timestamp"]), p["price"])
                            for p in history
                        ]
                        execute_values(
                            cur,
                            """
                            INSERT INTO coin_price_history (coin_id, timestamp, price)
                            VALUES %s
                            ON CONFLICT (coin_id, timestamp) DO UPDATE SET price = EXCLUDED.price
                            """,
                            batch,
                        )
                        conn.commit()

                        last_ts = history[-1]["timestamp"]
                        if last_ts + 1 >= fetch_end:
                            break
                        fetch_start = last_ts + 1

        # Return cached (now possibly filled) data for the requested range.
        query = """
        SELECT h.timestamp, h.price
        FROM coin_price_history h
        JOIN coin c ON h.coin_id = c.coin_id
        WHERE UPPER(c.symbol) = %s
        """
        params = [symbol.upper()]
        if start is not None:
            query += " AND h.timestamp >= to_timestamp(%s)"
            params.append(start / 1000.0)
        if end is not None:
            query += " AND h.timestamp <= to_timestamp(%s)"
            params.append(end / 1000.0)
        query += " ORDER BY h.timestamp ASC"

        cur.execute(query, params)
        rows = cur.fetchall()

        # Format as [ [unix_ms, price], ... ] for the frontend
        history = [[int(row[0].timestamp() * 1000), float(row[1])] for row in rows]

        cur.close()
        conn.close()

        return {
            "symbol": symbol.upper(),
            "data": history
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/coin/dag/coin-history-feeder")
async def trigger_history_feeder(payload: HistoryFeederRequest):
    """
    Trigger the defillama_global_coin_price_history DAG in Airflow.
    Handles Airflow 3 JWT-based authentication.
    """
    import requests
    from anyio import to_thread
    from datetime import datetime
    
    dag_id = "defillama_global_coin_price_history"
    
    # Airflow 3 Auth: Get JWT Token
    # The token endpoint is usually at /auth/token
    base_airflow_url = AIRFLOW_API_URL.split("/api/v2")[0]
    token_url = f"{base_airflow_url}/auth/token"
    
    try:
        # 1. Fetch Token
        token_response = await to_thread.run_sync(
            lambda: requests.post(
                token_url,
                json={"username": AIRFLOW_USER, "password": AIRFLOW_PASS},
                timeout=5.0
            )
        )
        if token_response.status_code != 201:
            raise HTTPException(
                status_code=502, 
                detail=f"Failed to authenticate with Airflow at {token_url}: {token_response.status_code} - {token_response.text}"
            )
        
        token = token_response.json().get("access_token")
        
        # 2. Trigger DAG with Bearer Token
        dag_run_url = f"{AIRFLOW_API_URL}/dags/{dag_id}/dagRuns"
        dag_conf = {
            "force_update": payload.force_update,
            "coin_symbols": payload.coin_symbols
        }
        
        # Airflow 3 requires logical_date in the payload
        payload_data = {
            "conf": dag_conf,
            "run_after": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        }
        
        response = await to_thread.run_sync(
            lambda: requests.post(
                dag_run_url,
                json=payload_data,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
        )
        
        if response.status_code >= 400:
            logging_err = f"Airflow API error: {response.status_code} - {response.text}"
            print(logging_err)
            raise HTTPException(status_code=502, detail=logging_err)
            
        data = response.json()
        return {
            "message": f"Successfully triggered {dag_id}",
            "dag_run_id": data.get("dag_run_id"),
            "state": data.get("state"),
            "conf": dag_conf
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Error communicating with Airflow: {exc}")

@app.get("/api/coin/dag/status/{dag_id}/{dag_run_id}")
async def get_dag_run_status(dag_id: str, dag_run_id: str):
    """
    Check the status of a specific Airflow DAG run.
    """
    import requests
    from anyio import to_thread
    
    base_airflow_url = AIRFLOW_API_URL.split("/api/v2")[0]
    token_url = f"{base_airflow_url}/auth/token"
    
    try:
        # 1. Fetch Token
        token_response = await to_thread.run_sync(
            lambda: requests.post(
                token_url,
                json={"username": AIRFLOW_USER, "password": AIRFLOW_PASS},
                timeout=5.0
            )
        )
        if token_response.status_code != 201:
            raise HTTPException(status_code=502, detail="Failed to authenticate with Airflow")
        
        token = token_response.json().get("access_token")
        
        # 2. Query Status
        url = f"{AIRFLOW_API_URL}/dags/{dag_id}/dagRuns/{dag_run_id}"
        
        response = await to_thread.run_sync(
            lambda: requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
        )
        
        if response.status_code >= 400:
            return {"status_code": response.status_code, "detail": response.text}
            
        data = response.json()
        return {
            "dag_id": data.get("dag_id"),
            "dag_run_id": data.get("dag_run_id"),
            "state": data.get("state"),
            "logical_date": data.get("logical_date"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date")
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Error communicating with Airflow: {exc}")

@app.get("/api/coins/list", tags=["Coins"])
async def get_coins():
    """Get list of active indexed coins for the backtester."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                query = """
                SELECT symbol, name, image_url as image, cmc_rank as market_cap_rank, slug
                FROM coin
                ORDER BY cmc_rank ASC NULLS LAST;
                """
                cur.execute(query)
                colnames = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                coins = [dict(zip(colnames, row)) for row in rows]
                return coins
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coin-families", tags=["Coins"],
         responses={
             200: {
                 "description": "List of coin families with their member coins",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": [
                                 {
                                     "type": "coin_family",
                                     "id": "BTC",
                                     "attributes": {"name": "BTC"},
                                     "relationships": {"members": {"data": [{"type": "coin", "id": 290}]}},
                                 }
                             ],
                             "included": [{"type": "coin", "id": 290, "attributes": {"symbol": "BTC", "name": "Bitcoin"}}],
                         }
                     }
                 }
             }
         })
async def get_coin_families(
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `members`. Example: `members`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `coin[symbol,name]`"),
):
    """List coin families as a JSON:API compound document.

    ``data`` is an array of ``coin_family`` resources and ``included`` holds
    the member ``coin`` resources (default). The response is intentionally
    slim so page loads stay fast.

    Example:

        GET /api/coin-families?include=members

        {
          "data": [
            { "type": "coin_family", "id": "BTC", "attributes": {"name": "BTC"},
              "relationships": { "members": { "data": [ {"type": "coin", "id": 290}, ... ] } } }
          ],
          "included": [
            { "type": "coin", "id": 290, "attributes": {"symbol": "BTC", "name": "Bitcoin"} },
            ...
          ]
        }

    Use ``data`` + ``included`` to rebuild the old ``families`` (family -> member
    symbols) and ``symbol_family_map`` (symbol -> family) maps client-side.
    """
    try:
        def _query():
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT UPPER(f.name) AS family
                    FROM coin_family f
                    ORDER BY family
                """)
                rows = cur.fetchall()
                cur.close()
                return [r[0] for r in rows]

        family_names = await asyncio.to_thread(_query)
        family_rows = [{"family": name} for name in family_names]

        include = include if include is not None else "members"
        return build_coin_family_documents(
            family_rows, include_spec=include, fields_spec=fields,
            links={"self": "/api/coin-families"},
            member_attrs=("symbol", "name"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coins/search-by-symbol", tags=["Coins"],
         responses={
             200: {
                 "description": "Coins matching a symbol with their contracts and families",
                 "content": {
                     "application/json": {
                         "example": {
                             "data": [
                                 {
                                     "type": "coin",
                                     "id": 290,
                                     "attributes": {"symbol": "BTC", "name": "Bitcoin", "price": 60000.0},
                                     "relationships": {
                                         "contracts": {"data": [{"type": "coin_contract", "id": "290:Ethereum"}]},
                                         "families": {"data": [{"type": "coin_family", "id": "BTC"}]},
                                     },
                                 }
                             ],
                             "included": [
                                 {"type": "coin_contract", "id": "290:Ethereum",
                                  "attributes": {"chain": "Ethereum", "contract_address": "0x...", "decimals": 8}},
                             ],
                         }
                     }
                 }
             }
         })
async def search_coins_by_symbol(
    symbol: str = Query(..., description="Coin symbol to search (case-insensitive). Example: `WETH`"),
    include_coin_families: bool = Query(True, description="Also return every coin in the same coin family. With this on, `BTC` matches all BTC-pegged coins (WBTC, KBTC, ...). Default: `true`"),
    include: Optional[str] = Query(None, description="Comma-separated dot-paths to embed. Default: `contracts,families`. Example: `contracts`"),
    fields: Optional[str] = Query(None, description="Sparse fieldsets, e.g. `coin[symbol,name]`"),
):
    """List coins matching a symbol as a JSON:API compound document.

    ``data`` is an array of ``coin`` resources and ``included`` holds the
    requested ``coin_contract`` / ``coin_family`` resources.

    Example — WETH with its on-chain contracts:

        GET /api/coins/search-by-symbol?symbol=WETH&include=contracts

    Example — BTC expanded to its whole family:

        GET /api/coins/search-by-symbol?symbol=BTC&include_coin_families=true&include=contracts,families

    When include_coin_families is true (default), the search expands to every
    coin that shares the queried symbol's coin family (e.g. `BTC` also returns
    WBTC, KBTC, TBTC, ...).
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = UPPER(%s)", (symbol.strip(),))
                matched_coin_ids = [row[0] for row in cur.fetchall()]
                if not matched_coin_ids:
                    raise HTTPException(status_code=404, detail=f"No coin found for symbol '{symbol}'")

                if include_coin_families:
                    # Only expand through the family whose name matches the
                    # queried symbol (e.g. `BTC` -> the "BTC" family with WBTC,
                    # KBTC, ...). A coin can also belong to broad meta-families
                    # like Tier1/STOCK which we must not expand through.
                    cur.execute("""
                        SELECT DISTINCT f.name
                        FROM coin_family f
                        JOIN coin c ON f.coin_id = c.coin_id
                        WHERE UPPER(f.name) = UPPER(%s)
                    """, (symbol.strip(),))
                    family_names = [row[0] for row in cur.fetchall()]
                    if family_names:
                        cur.execute("""
                            SELECT DISTINCT f.coin_id
                            FROM coin_family f
                            WHERE f.name = ANY(%s)
                        """, (family_names,))
                        matched_coin_ids = [row[0] for row in cur.fetchall()]

                cur.execute("""
                    SELECT c.coin_id, c.symbol, c.name, c.slug, c.hardness,
                           c.cmc_rank, c.cmc_id, c.first_historical_data,
                           c.image_url, c.price, c.price_timestamp, c.decimals,
                           c.percent_change_1h, c.percent_change_24h, c.percent_change_7d,
                           c.percent_change_30d, c.percent_change_60d, c.percent_change_90d,
                           c.market_cap, c.market_cap_dominance, c.fully_diluted_market_cap,
                           c.tvl, c.total_supply, c.circulating_supply, c.max_supply,
                           c.cmc_last_updated
                    FROM coin c
                    WHERE c.coin_id = ANY(%s)
                    ORDER BY c.coin_id
                """, (matched_coin_ids,))
                coins = cur.fetchall()

                coin_cols = [
                    "coin_id", "symbol", "name", "slug", "hardness",
                    "cmc_rank", "cmc_id", "first_historical_data",
                    "image_url", "price", "price_timestamp", "decimals",
                    "percent_change_1h", "percent_change_24h", "percent_change_7d",
                    "percent_change_30d", "percent_change_60d", "percent_change_90d",
                    "market_cap", "market_cap_dominance", "fully_diluted_market_cap",
                    "tvl", "total_supply", "circulating_supply", "max_supply",
                    "cmc_last_updated"
                ]

                result = []
                for row in coins:
                    coin = dict(zip(coin_cols, row))
                    coin["price"] = float(coin["price"]) if coin["price"] is not None else None
                    for col in ("percent_change_1h", "percent_change_24h", "percent_change_7d",
                                "percent_change_30d", "percent_change_60d", "percent_change_90d",
                                "market_cap", "market_cap_dominance", "fully_diluted_market_cap",
                                "tvl", "total_supply", "circulating_supply", "max_supply"):
                        if coin[col] is not None:
                            coin[col] = float(coin[col])
                    for col in ("first_historical_data", "price_timestamp", "cmc_last_updated"):
                        if coin[col] is not None:
                            coin[col] = coin[col].isoformat()
                    result.append(coin)

        include = include if include is not None else "contracts,families"
        return build_coin_documents(
            result, include_spec=include, fields_spec=fields,
            links={"self": "/api/coins/search-by-symbol"},
            meta={"query": symbol.strip(), "include_coin_families": include_coin_families, "n": len(result)},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid include/fields parameter: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pools", tags=["Liquidity Pools"])
async def list_pools(
    limit: int = Query(50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Number of rows to skip"),
):
    """List liquidity pools with latest stats, ordered by TVL descending."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                query = """
                SELECT
                    p.id, ch.name AS network, pr.name AS protocol, p.pool_name,
                    CASE WHEN p.fee_bps IS NULL THEN 'Dynamic' ELSE (p.fee_bps / 100.0)::text || '%%' END AS fee_tier,
                    p.pool_address,
                    h.tvl_usd, h.volume_usd, h.tx_count, c0.symbol, c1.symbol
                FROM liquidity_pool p
                JOIN chain ch ON p.chain_id = ch.id
                JOIN protocol pr ON p.protocol_id = pr.id
                JOIN coin c0 ON p.coin0_id = c0.coin_id
                JOIN coin c1 ON p.coin1_id = c1.coin_id
                LEFT JOIN (
                    SELECT DISTINCT ON (pool_id) pool_id, tvl_usd, volume_usd, tx_count
                    FROM liquidity_pool_daily_stats
                    ORDER BY pool_id, day DESC
                ) h ON p.id = h.pool_id
                WHERE p.reverted = FALSE OR pr.name IN ('Uniswap V3', 'Uniswap V4', 'PancakeSwap V3', 'PancakeSwap V4')
                ORDER BY h.tvl_usd DESC NULLS LAST
                LIMIT %s OFFSET %s
                """
                cur.execute(query, (limit, offset))
                rows = cur.fetchall()
                
                pools = []
                for r in rows:
                    pools.append({
                        "id": r[0],
                        "network": r[1],
                        "protocol": r[2],
                        "pool_name": r[3],
                        "fee_tier": r[4],
                        "pool_address": r[5],
                        "tvl_usd": float(r[6]) if r[6] else 0.0,
                        "volume_24h": float(r[7]) if r[7] else 0.0,
                        "tx_count": r[8] if r[8] else 0,
                        "tokens": [r[9], r[10]]
                    })
                    
                return pools
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pools/{pool_id}/leaderboard", tags=["Liquidity Pools"])
async def pool_leaderboard(pool_id: int):
    """Get the top LP providers for a specific pool."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # Robust query: Join on pool_id directly, fallback to snapshot balance if exists
        query = """
        SELECT 
            pos.wallet_address,
            COALESCE(SUM(s.balance_usd), 0) as total_balance_usd,
            COUNT(pos.id) as position_count,
            MAX(COALESCE(s.timestamp, pos.created_at)) as last_activity,
            COALESCE(SUM(s.coin0_amount), 0) as total_coin0,
            COALESCE(SUM(s.coin1_amount), 0) as total_coin1
        FROM liquidity_pool_position pos
        LEFT JOIN (
            SELECT DISTINCT ON (position_id) position_id, balance_usd, coin0_amount, coin1_amount, timestamp
            FROM liquidity_pool_position_snapshot
            ORDER BY position_id, timestamp DESC
        ) s ON pos.id = s.position_id
        WHERE pos.pool_id = %s
        GROUP BY pos.wallet_address
        ORDER BY total_balance_usd DESC, position_count DESC
        """
        cur.execute(query, (pool_id,))
        rows = cur.fetchall()
        
        # Calculate total pool balance for percentages (only count positive ones to avoid skewed shares)
        total_pos_usd = sum(float(r[1]) for r in rows if float(r[1]) > 0)
        
        leaderboard = []
        for r in rows:
            bal_usd = float(r[1]) if r[1] else 0.0
            if bal_usd <= 0: continue # Skip negative or empty balances for the leaderboard
            
            # share is relative to the total tracked POSITIVE liquidity
            share = (bal_usd / total_pos_usd * 100) if total_pos_usd > 0 else 0.0
            
            leaderboard.append({
                "wallet_address": r[0],
                "balance_usd": bal_usd,
                "position_count": r[2],
                "last_activity": r[3].isoformat() if r[3] else None,
                "share_percent": share,
                "assets": [
                    {"amount": float(r[4]) if r[4] else 0.0},
                    {"amount": float(r[5]) if r[5] else 0.0}
                ]
            })
            
        cur.close()
        conn.close()
        return leaderboard
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pools/{pool_id}/sync", tags=["Liquidity Pools"])
async def sync_pool(pool_id: int):
    """Trigger manual discovery and indexing for a specific pool."""
    try:
        from graph_discovery_client import fetch_positions_by_pool, resolve_pool_address
        
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # 1. Get pool details
        cur.execute("""
            SELECT ch.name AS network, lp.pool_address, pr.name AS protocol, c0.symbol, c1.symbol,
                   CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic' ELSE (lp.fee_bps / 100.0)::text || '%' END AS fee_tier
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            JOIN coin c0 ON lp.coin0_id = c0.coin_id
            JOIN coin c1 ON lp.coin1_id = c1.coin_id
            WHERE lp.id = %s
        """, (pool_id,))
        pool_res = cur.fetchone()
        if not pool_res:
            raise HTTPException(status_code=404, detail="Pool not found")
        
        network, pool_address, protocol, c0, c1, fee = pool_res
        
        # 2. Attempt to resolve address if missing
        if not pool_address:
            print(f"Pool address missing for ID {pool_id}. Attempting to resolve...")
            # Fetch token addresses for the pool's network
            cur.execute("""
                SELECT c.symbol, cc.contract_address 
                FROM coin_contract cc
                JOIN coin c ON cc.coin_id = c.coin_id
                WHERE c.symbol IN (%s, %s) AND cc.chain_id = (SELECT id FROM chain WHERE LOWER(name) = LOWER(%s))
            """, (c0, c1, network.lower()))
            coin_rows = cur.fetchall()
            coin_map = {row[0]: row[1] for row in coin_rows}
            
            if c0 in coin_map and c1 in coin_map:
                resolved_addr = await to_thread.run_sync(
                    lambda: resolve_pool_address(
                        coin_map[c0], coin_map[c1], fee, 
                        network=network, protocol=protocol
                    )
                )
                if resolved_addr:
                    print(f"Resolved address for pool {pool_id}: {resolved_addr}")
                    cur.execute("UPDATE liquidity_pool SET pool_address = %s WHERE id = %s", (resolved_addr, pool_id))
                    conn.commit()
                    pool_address = resolved_addr
                else:
                    raise HTTPException(status_code=400, detail=f"Could not resolve pool address on The Graph for {c0}-{c1} {fee}")
            else:
                raise HTTPException(status_code=400, detail=f"Token addresses missing in database for {c0} or {c1}")

        if not pool_address:
            raise HTTPException(status_code=400, detail="Pool address unknown for this entry")
            
        # 3. Fetch from Graph
        positions = await to_thread.run_sync(
            lambda: fetch_positions_by_pool(pool_address, network=network, protocol=protocol)
        )
        
        if not positions:
            return {"status": "success", "message": "Sync completed. No new positions found on Graph.", "count": 0}
            
        # 4. Trigger ingestion logic (standalone helpers)
        try:
            from graph_ingestion_helpers import (
                ingest_coins_data, ingest_pools_data, ingest_pool_stats,
                ingest_positions_data, ingest_snapshots_data
            )
            
            # Use same connection to ensure consistency
            ingest_coins_data(conn, positions)
            ingest_pools_data(conn, positions)
            ingest_pool_stats(conn, positions)
            ingest_positions_data(conn, positions)
            ingest_snapshots_data(conn, positions)
            
        except Exception as ingest_err:
            print(f"Ingestion error: {ingest_err}")
            # We still return success if discovery worked but ingestion was partial
            
        cur.close()
        conn.close()
        return {
            "status": "success", 
            "message": f"Successfully discovered and indexed {len(positions)} positions.",
            "count": len(positions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/assets/price-by-cmc-id", tags=["Coins"])
async def get_price_by_cmc_id(id: str = Query(..., description="Comma-separated CMC IDs (max 100)")):
    """
    Get coin price data by CoinMarketCap IDs.
    
    Similar to CMC's /v1/cryptocurrency/quotes/latest endpoint.
    Returns price, percent changes, market cap, and metadata for the requested coins.
    
    Example: ?id=1,1027,825
    """
    try:
        # Parse and validate IDs
        try:
            cmc_ids = [int(x.strip()) for x in id.split(',')]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid CMC ID format. IDs must be integers.")
        
        if len(cmc_ids) > 100:
            raise HTTPException(status_code=400, detail="Too many IDs requested. Maximum is 100.")
        
        if len(cmc_ids) == 0:
            raise HTTPException(status_code=400, detail="At least one CMC ID is required.")
        
        # Query database
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # Fetch coin details and map ethereum_address if it exists
        query = """
        SELECT 
            c.coin_id, c.cmc_id, c.symbol, c.name, c.price, c.price_timestamp,
            c.percent_change_1h, c.percent_change_24h, c.percent_change_7d,
            c.market_cap, c.tvl, c.cmc_rank, c.image_url,
            eth.contract_address AS ethereum_address
        FROM coin c
        LEFT JOIN coin_contract eth ON c.coin_id = eth.coin_id AND eth.chain_id = 1
        WHERE c.cmc_id = ANY(%s)
        """
        cur.execute(query, (cmc_ids,))
        rows = cur.fetchall()
        
        # Fetch all other contracts for these coins
        cur.execute("""
            SELECT cc.coin_id, 
                   CASE WHEN LOWER(ch.name) = 'bnb' THEN 'bsc' ELSE LOWER(ch.name) END AS chain, 
                   cc.contract_address, cc.decimals, cc.is_native
            FROM coin_contract cc
            JOIN coin c ON cc.coin_id = c.coin_id
            JOIN chain ch ON cc.chain_id = ch.id
            WHERE c.cmc_id = ANY(%s)
        """, (cmc_ids,))
        contract_rows = cur.fetchall()
        contracts_by_coin = {}
        for cid, chain, addr, dec, is_native in contract_rows:
            contracts_by_coin.setdefault(cid, {})[chain] = {
                "contract_address": addr,
                "decimals": dec,
                "is_native": is_native
            }
        
        # Build response keyed by CMC ID
        data = {}
        for row in rows:
            coin_id = row[0]
            cmc_id_val = row[1]
            data[str(cmc_id_val)] = {
                "cmc_id": cmc_id_val,
                "symbol": row[2],
                "name": row[3],
                "price": float(row[4]) if row[4] is not None else None,
                "price_timestamp": row[5].isoformat() if row[5] is not None else None,
                "percent_change_1h": float(row[6]) if row[6] is not None else None,
                "percent_change_24h": float(row[7]) if row[7] is not None else None,
                "percent_change_7d": float(row[8]) if row[8] is not None else None,
                "market_cap": float(row[9]) if row[9] is not None else None,
                "tvl": float(row[10]) if row[10] is not None else None,
                "cmc_rank": row[11],
                "image_url": row[12],
                "ethereum_address": row[13],
                "platforms": contracts_by_coin.get(coin_id, {})
            }
        
        cur.close()
        conn.close()
        
        return {
            "data": data,
            "status": {
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "error_code": 0,
                "error_message": None,
                "total_count": len(data)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sps/find", tags=["SPS"])
async def sps_find(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    families: Optional[str] = Query(None, description="Comma-separated family names (e.g. USD,EUR,ETH). Empty = all correlated."),
    cross_family: bool = Query(False, description="Include cross-family analysis (e.g. USD×EUR)"),
    min_volume: float = Query(10000, description="Minimum divertable volume (USD)"),
    tvl_targets: Optional[str] = Query(None, description="Comma-separated TVL targets for APR projection (e.g. 100000,500000,1000000)"),
):
    """Find stable-pair shortcut opportunities.

    Scans multi-hop routes between correlated token families and identifies
    where volume flows through volatile intermediaries like WETH. Returns
    ranked opportunities with projected revenue and APR.
    """
    try:
        from datetime import datetime as dt

        start_dt = dt.strptime(start_date, '%Y-%m-%d')
        end_dt = dt.strptime(end_date, '%Y-%m-%d')

        if start_dt >= end_dt:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")

        family_list = None
        if families:
            family_list = [f.strip() for f in families.split(',') if f.strip()]

        tvl_list = [100_000, 500_000, 1_000_000]
        if tvl_targets:
            try:
                tvl_list = [float(t.strip()) for t in tvl_targets.split(',') if t.strip()]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid tvl_targets format")

        def run_finder():
            finder = ShortcutFinder(
                families=family_list,
                cross_family=cross_family,
                min_volume=min_volume,
                tvl_targets=tvl_list,
                verbose=False,
            )
            opportunities = finder.find(start_dt, end_dt)
            period_days = (end_dt - start_dt).total_seconds() / 86400
            return finder.to_json(opportunities, period_days), period_days

        results, period_days = await to_thread.run_sync(run_finder)

        # Collect unique pools from opportunities paths to query stats and addresses
        pools_to_fetch = set()
        for opp in results:
            dom = opp.get('dominant_route')
            if dom and dom.get('path'):
                parts = dom['path'].split()
                for i in range(1, len(parts) - 1, 2):
                    pools_to_fetch.add((parts[i-1], parts[i+1], parts[i]))
            
            for r in opp.get('multihop_routes', []):
                if r.get('path'):
                    parts = r['path'].split()
                    for i in range(1, len(parts) - 1, 2):
                        pools_to_fetch.add((parts[i-1], parts[i+1], parts[i]))
            
            for r in opp.get('direct_routes', []):
                if r.get('path'):
                    parts = r['path'].split()
                    for i in range(1, len(parts) - 1, 2):
                        pools_to_fetch.add((parts[i-1], parts[i+1], parts[i]))

        pool_stats = {}
        if pools_to_fetch:
            fetcher = PostgresFetcher(verbose=False)
            latest_prices = fetcher.fetch_latest_prices()
            try:
                aprs = await to_thread.run_sync(
                    fetcher.fetch_pool_stats, list(pools_to_fetch), start_dt, end_dt,
                    prices=latest_prices, tvl_mode='avg', use_swaps_fallback=True,
                )
            except Exception as e:
                print(f"Error fetching pool stats in SPS: {e}")
                aprs = {}

            pool_addresses = {}
            token_symbols = set()
            needed_networks = set()
            for (t0, t1, fee) in pools_to_fetch:
                token_symbols.add(t0.upper())
                token_symbols.add(t1.upper())
                parts = str(fee).split('|')
                pool_network = parts[2].strip() if len(parts) >= 3 else "Ethereum"
                needed_networks.add(pool_network)

            token_addresses = {}
            for target_network in needed_networks:
                token_addresses[target_network] = {}
                if target_network not in TOKEN_ADDRESS_CACHE:
                    TOKEN_ADDRESS_CACHE[target_network] = {}
                for sym in token_symbols:
                    if sym in TOKEN_ADDRESS_CACHE[target_network]:
                        token_addresses[target_network][sym] = TOKEN_ADDRESS_CACHE[target_network][sym]
                
                missing_symbols = [sym for sym in token_symbols if sym not in token_addresses[target_network]]
                if missing_symbols:
                    try:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            db_chain = 'bsc' if target_network.lower() == 'bnb' else target_network.lower()
                            cur.execute("""
                                SELECT UPPER(c.symbol), cc.contract_address 
                                FROM coin_contract cc
                                JOIN coin c ON cc.coin_id = c.coin_id
                                JOIN chain ch ON cc.chain_id = ch.id
                                WHERE (LOWER(ch.name) = %s OR (LOWER(ch.name) = 'bnb' AND %s = 'bsc'))
                                  AND UPPER(c.symbol) = ANY(%s)
                            """, (db_chain, db_chain, missing_symbols))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[target_network][row[0]] = row[1]
                                    TOKEN_ADDRESS_CACHE[target_network][row[0]] = row[1]
                            cur.close()
                    except Exception as e:
                        print(f"Error fetching token addresses in SPS: {e}")

            jobs = []
            v4_keys = []
            for (t0, t1, fee) in pools_to_fetch:
                try:
                    t0_sym, t1_sym = t0.upper(), t1.upper()
                    parts = str(fee).split('|')
                    pool_network = parts[2].strip() if len(parts) >= 3 else "Ethereum"
                    proto_raw = parts[1].strip() if len(parts) >= 2 else "Uniswap V3"
                    proto_lower = proto_raw.lower()

                    if proto_lower in ('v4', 'uniswap v4', 'uniswap-v4', 'pancakeswap v4', 'pancake v4', 'pancakeswap-v4', 'pancake-v4'):
                        v4_proto = 'PancakeSwap V4' if 'pancake' in proto_lower else 'Uniswap V4'
                        fee_clean_v4 = parts[0].replace('%', '').strip()
                        fee_map_v4 = {'0.01': '100', '0.05': '500', '0.08': '800', '0.25': '2500', '0.3': '3000', '1.0': '10000'}
                        fee_norm = fee_map_v4.get(fee_clean_v4)
                        if not fee_norm:
                            try:
                                fv = float(fee_clean_v4)
                                fee_norm = str(int(fv * 10000)) if (0 < fv < 5) else str(int(fv))
                            except:
                                fee_norm = parts[0]
                        v4_keys.append(f"{t0_sym}-{t1_sym}-{fee_norm}|{v4_proto}|{pool_network}")
                        continue

                    if proto_lower in ('aerodrome',):
                        continue

                    is_v2 = (proto_lower in ('v2', 'uniswap v2', 'uniswap-v2'))
                    protocol = 'Uniswap V2' if is_v2 else ('Uniswap V3' if proto_lower in ('v3', 'uniswap v3', 'uniswap-v3') else proto_raw)

                    addr0 = token_addresses.get(pool_network, {}).get(t0_sym)
                    addr1 = token_addresses.get(pool_network, {}).get(t1_sym)
                    if not addr0 or not addr1:
                        continue

                    fee_clean = parts[0].replace('%', '').strip()
                    fee_map = {'0.01': 100, '0.05': 500, '0.08': 800, '0.3': 3000, '1.0': 10000}
                    fee_val = fee_map.get(fee_clean) or int(float(fee_clean) * 10000)

                    tokens = sorted([addr0.lower(), addr1.lower()])
                    t0_bytes = bytes.fromhex(tokens[0][2:])
                    t1_bytes = bytes.fromhex(tokens[1][2:])
                    key = f"{t0}-{t1}-{fee}"

                    net_map = {"BNB": "bsc", "ETH": "ethereum"}
                    cfg_network = net_map.get(pool_network, pool_network.lower())

                    fh_key = (protocol, cfg_network)
                    if fh_key in FACTORY_HASH_CACHE:
                        factory_hex, init_hash_hex = FACTORY_HASH_CACHE[fh_key]
                    else:
                        try:
                            factory_hex, init_hash_hex = get_factory_and_hash(protocol, cfg_network)
                            FACTORY_HASH_CACHE[fh_key] = (factory_hex, init_hash_hex)
                        except ValueError:
                            continue

                    pool_cache_key = (tokens[0], tokens[1], fee_val, protocol, pool_network)
                    jobs.append({
                        'key': key,
                        'pool_cache_key': pool_cache_key,
                        't0_bytes': t0_bytes,
                        't1_bytes': t1_bytes,
                        'fee_val': fee_val,
                        'factory_hex': factory_hex,
                        'init_hash_hex': init_hash_hex,
                        'is_v2': is_v2,
                    })
                except:
                    continue

            if jobs:
                def _derive_batch():
                    batch_results = {}
                    for j in jobs:
                        pk = j['pool_cache_key']
                        if pk in POOL_ADDRESS_CACHE:
                            batch_results[j['key']] = POOL_ADDRESS_CACHE[pk]
                        else:
                            try:
                                addr = _derive_address(
                                    j['t0_bytes'], j['t1_bytes'], j['fee_val'],
                                    j['factory_hex'], j['init_hash_hex'],
                                    is_v2=j.get('is_v2', False)
                                )
                                batch_results[j['key']] = addr
                                POOL_ADDRESS_CACHE[pk] = addr
                            except Exception as ex:
                                print(f"Error deriving address in SPS for {j['key']}: {ex}")
                    return batch_results

                batch = await to_thread.run_sync(_derive_batch)
                pool_addresses.update(batch)

            if v4_keys:
                def _lookup_v4_pool_ids():
                    v4_results = {}
                    try:
                        with get_conn() as conn:
                            cur = conn.cursor()
                            cur.execute("""
                                 SELECT ch.name AS network, pr.name AS protocol, 
                                        CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic' ELSE (lp.fee_bps / 100.0)::text || '%' END AS fee_tier, 
                                        lp.pool_id,
                                        UPPER(c0.symbol) AS s0,
                                        UPPER(c1.symbol) AS s1,
                                        cc0.contract_address AS t0_addr
                                 FROM liquidity_pool lp
                                 JOIN chain ch ON lp.chain_id = ch.id
                                 JOIN protocol pr ON lp.protocol_id = pr.id
                                 JOIN coin c0 ON lp.coin0_id = c0.coin_id
                                 JOIN coin c1 ON lp.coin1_id = c1.coin_id
                                 LEFT JOIN coin_contract cc0
                                     ON cc0.coin_id = lp.coin0_id
                                    AND cc0.chain_id = lp.chain_id
                                 WHERE (pr.name = 'Uniswap V4' AND lp.pool_id IS NOT NULL)
                                    OR pr.name = 'PancakeSwap V4'
                             """)
                            for net, proto, fee_tier, pid, sym0, sym1, t0_addr in cur.fetchall():
                                if proto == 'PancakeSwap V4':
                                    if pid and len(pid) == 66:
                                        value = pid
                                    elif t0_addr:
                                        value = t0_addr
                                    else:
                                        continue
                                else:
                                    if not pid:
                                        continue
                                    value = pid
                                fee_keys = {fee_tier}
                                if '%' in fee_tier:
                                    fee_keys.add(fee_tier.replace('%', '').strip())
                                else:
                                    try:
                                        val = int(fee_tier)
                                        pct = val / 10000
                                        pct_str = f'{pct:.6f}'.rstrip('0').rstrip('.')
                                        fee_keys.add(f'{pct_str}%')
                                        fee_keys.add(fee_tier)
                                        fee_keys.add(str(val))
                                    except ValueError:
                                        pass
                                for fk in fee_keys:
                                    if not fk:
                                        continue
                                    key_fwd = f"{sym0}-{sym1}-{fk}|{proto}|{net}"
                                    key_rev = f"{sym1}-{sym0}-{fk}|{proto}|{net}"
                                    v4_results[key_fwd] = value
                                    v4_results[key_rev] = value
                            cur.close()
                    except Exception as ex:
                        print(f"Error looking up V4 pool_ids in SPS: {ex}")
                    return v4_results

                v4_batch = await to_thread.run_sync(_lookup_v4_pool_ids)
                pool_addresses.update(v4_batch)

            for (t0, t1, fee) in pools_to_fetch:
                t0_norm = t0.upper()
                t1_norm = t1.upper()
                if 'v4' in fee.lower():
                    if t0_norm == 'ETH': t0_norm = 'WETH'
                    if t0_norm == 'BNB': t0_norm = 'WBNB'
                    if t1_norm == 'ETH': t1_norm = 'WETH'
                    if t1_norm == 'BNB': t1_norm = 'WBNB'

                key = f"{t0_norm}-{t1_norm}-{fee}"
                rev_key = f"{t1_norm}-{t0_norm}-{fee}"
                pool_addr = pool_addresses.get(key) or pool_addresses.get(rev_key)
                
                fee_parts = fee.split('|')
                pool_network = fee_parts[2].strip() if len(fee_parts) >= 3 else "Ethereum"
                
                enriched = await get_enriched_pool_stat(
                    key=key,
                    rev_key=rev_key,
                    aprs=aprs,
                    pool_addr=pool_addr,
                    pool_network=pool_network,
                    period_days=period_days,
                    fee_tier=fee
                )
                
                apr_val = enriched['apr']
                tvl_val = enriched['tvl']
                apr_str = format_apr(apr_val)
                defillama_uuid = get_defillama_pool_uuid(pool_addr)
                pool_protocol = fee_parts[1].strip() if len(fee_parts) >= 2 else "Uniswap V3"
                pool_stats[f"{t0}-{t1}-{fee}"] = {
                    'apr': apr_val if apr_val is not None else 0.0,
                    'apr_str': apr_str,
                    'pool_address': pool_addr,
                    'tvl': tvl_val,
                    'defillama_uuid': defillama_uuid,
                    'links': build_pool_links(pool_addr, None, pool_protocol, pool_network, defillama_uuid),
                }

        return {
            'period': {
                'start': start_date,
                'end': end_date,
                'days': period_days,
            },
            'config': {
                'families': family_list,
                'cross_family': cross_family,
                'min_volume': min_volume,
                'tvl_targets': tvl_list,
            },
            'opportunities': results,
            'pool_stats': pool_stats,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# UI Routes (Excluded from Swagger schema)
@app.get("/", include_in_schema=False)
async def read_index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

@app.get("/routing", include_in_schema=False)
async def read_routing():
    return FileResponse(os.path.join(STATIC_DIR, 'routing.html'))

_health_data_cache: Dict[int, tuple] = {}  # lookback_days -> (data, timestamp)
_health_data_lock = threading.Lock()
_HEALTH_DATA_TTL = 120  # seconds — health data changes slowly (per ingestion run)

def build_all_tables_health(lookback_days: int = 7):
    """Build the table-freshness report. Cached for _HEALTH_DATA_TTL seconds.

    Thread-safety: a non-blocking lock ensures only ONE caller runs the (slow)
    full-swap-scan build; concurrent callers serve the stale cache instead of
    all piling onto the same 45M-row scan and wedging the DB / event loop.
    Callers MUST run this off the event loop (asyncio.to_thread) — the swaps
    aggregation alone can take tens of seconds on a cold cache.
    """
    import time, urllib.parse
    from datetime import datetime, timezone, timedelta

    now_ts = time.time()
    cache_entry = _health_data_cache.get(lookback_days)
    cached = cache_entry[0] if cache_entry else None
    cached_ts = cache_entry[1] if cache_entry else 0
    if cached is not None and (now_ts - cached_ts) < _HEALTH_DATA_TTL:
        return cached

    # Block (up to 60s) waiting for an in-progress builder rather than piling
    # a second full-swap-scan onto the DB. The acquirer builds; waiters wake
    # and read the freshly-populated cache.
    acquired = _health_data_lock.acquire(blocking=True, timeout=60)
    if not acquired:
        # Timed out waiting for the builder — serve stale if we have it,
        # otherwise report degraded so the caller isn't wedged indefinitely.
        if cached is not None:
            return cached
        return (True, {"error": "health check build in progress"})
    try:
        # Double-check after acquiring — another builder may have just
        # finished and refreshed the cache while we waited.
        cache_entry = _health_data_cache.get(lookback_days)
        cached = cache_entry[0] if cache_entry else None
        cached_ts = cache_entry[1] if cache_entry else 0
        if cached is not None and (time.time() - cached_ts) < _HEALTH_DATA_TTL:
            _health_data_lock.release()
            return cached
    except Exception:
        _health_data_lock.release()
        raise
    # We hold the lock and the cache is stale/empty — build. The lock is
    # released at the end (and in the double-check's exception path above).

    now = datetime.now(timezone.utc)
    overall_degraded = False
    data_dict = {}

    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SET LOCAL statement_timeout = '600s'")

        # 1. swaps (from liquidity_pool_daily_stats — 28MB vs 19GB swaps partition)
        volume_thresholds = [0, 1000, 100000, 10000000]

        # Single scan of history: get per-chain/protocol cnt/min/max for each volume threshold
        filter_cols = []
        for min_vol in volume_thresholds:
            vol_filter = "" if min_vol == 0 else f"AND v.vol_7d >= {min_vol}"
            filter_cols.append(f"""
                COALESCE(SUM(lph.tx_count) FILTER (WHERE 1=1 {vol_filter}), 0) as cnt_{min_vol},
                MIN(lph.day) FILTER (WHERE 1=1 {vol_filter}) as min_date_{min_vol},
                MAX(lph.day) FILTER (WHERE 1=1 {vol_filter}) as max_date_{min_vol}
            """)
        filter_sql = ','.join(filter_cols)

        cur.execute(f"""
            SELECT ch.name, pr.name,
                   {filter_sql}
            FROM liquidity_pool_daily_stats lph
            JOIN liquidity_pool lp ON lph.pool_id = lp.id
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            LEFT JOIN (
                SELECT pool_id, COALESCE(SUM(volume_usd), 0) as vol_7d
                FROM liquidity_pool_daily_stats
                WHERE day >= CURRENT_DATE - INTERVAL '7 days' AND day < CURRENT_DATE
                GROUP BY pool_id
            ) v ON lp.id = v.pool_id
            WHERE lph.day >= CURRENT_DATE - INTERVAL '7 days'
              AND lph.day < CURRENT_DATE
            GROUP BY ch.name, pr.name
            ORDER BY ch.name, pr.name
        """)

        lph_rows = cur.fetchall()

        # Also get overall earliest swap data and total estimate
        cur.execute("SELECT MIN(day), MAX(day) FROM liquidity_pool_daily_stats")
        lph_earliest, lph_latest = cur.fetchone()
        # Route coverage is derived from the pre-aggregated route tables (the
        # raw swaps tables are no longer read by the API). Routed swap-log
        # volume comes from route_daily_stats; pending/unclassified txs come
        # from the classification queue.
        cur.execute("""
            SELECT COALESCE(SUM(swap_count), 0)::bigint
            FROM route_daily_stats
        """)
        routed_log_count = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM route_classification_queue
            WHERE status <> 'complete'
        """)
        unclassified_pending_count = cur.fetchone()[0]
        swaps_log_count = routed_log_count + unclassified_pending_count
        swaps_assigned_route_count = routed_log_count
        swaps_unassigned_route_count = unclassified_pending_count
        swaps_route_assignment_pct = round(
            swaps_assigned_route_count / swaps_log_count * 100, 2
        ) if swaps_log_count else 0
        swaps_total_estimate = routed_log_count

        swap_matrix_filters = {}
        for i, min_vol in enumerate(volume_thresholds):
            swap_chains = {}
            total_swaps = 0
            any_swap_stale = False
            swaps_daily_pass = True

            # Each threshold: 3 cols at positions 2 + i*3 in the row
            col_offset = 2 + i * 3

            for row in lph_rows:
                chain = row[0]
                protocol = row[1]
                count = row[col_offset] or 0
                earliest = row[col_offset + 1]
                latest = row[col_offset + 2]

                if chain not in swap_chains:
                    swap_chains[chain] = {"status": "fresh", "protocols": {}}

                stale = False
                if latest:
                    ft = datetime(latest.year, latest.month, latest.day, tzinfo=timezone.utc)
                    stale = ft < now - timedelta(hours=3)
                    if stale and min_vol == 0:
                        overall_degraded = True
                    any_swap_stale = stale or any_swap_stale
                    if ft < now - timedelta(days=2):
                        swaps_daily_pass = False
                else:
                    stale = True

                if stale:
                    swap_chains[chain]["status"] = "stale"

                swap_chains[chain]["protocols"][protocol] = {
                    "count": int(count),
                    "earliest": earliest.isoformat() if earliest else None,
                    "latest": latest.isoformat() if latest else None,
                    "checks": {
                        "has_data_every_day": "pass" if latest and ft >= now - timedelta(days=2) else "fail",
                        "is_fresher_than_3_hours": "pass" if not stale else "fail"
                    }
                }
                total_swaps += int(count)

            swap_matrix_filters[str(min_vol)] = {
                "freshness_requirement": "Latest pool history date for each protocol on a chain must be within the last 3 hours",
                "count": total_swaps,
                "chains": swap_chains,
                "checks": {
                    "has_data_every_day": "pass" if swaps_daily_pass else "fail",
                    "is_fresher_than_3_hours": "pass" if not any_swap_stale else "fail"
                }
            }

        data_dict["swaps"] = {k: v for k, v in swap_matrix_filters["0"].items()}
        data_dict["swaps"]["volume_filters"] = {k: {x: y for x, y in v.items()} for k, v in swap_matrix_filters.items()}
        data_dict["swaps"]["earliest_all_time"] = lph_earliest.isoformat() if lph_earliest else None
        data_dict["swaps"]["total_estimate"] = swaps_total_estimate
        data_dict["swaps"]["route_assignment"] = {
            "assigned_count": swaps_assigned_route_count,
            "unassigned_count": swaps_unassigned_route_count,
            "total_count": swaps_log_count,
            "assigned_percentage": swaps_route_assignment_pct
        }

        # 2. coin
        cur.execute("SELECT COUNT(*) FROM coin")
        total_coins = cur.fetchone()[0]

        # Calculate coverage percentages
        cur.execute("SELECT COUNT(DISTINCT coin_id) FROM coin_contract")
        coins_with_any_contract = cur.fetchone()[0]

        cur.execute("""
            SELECT ch.name, COUNT(DISTINCT cc.coin_id)
            FROM coin_contract cc
            JOIN chain ch ON cc.chain_id = ch.id
            WHERE ch.name IN ('Ethereum', 'BNB', 'Arbitrum', 'Base')
            GROUP BY ch.name
        """)
        chain_counts = {row[0]: row[1] for row in cur.fetchall()}

        pct_any = (coins_with_any_contract / total_coins * 100) if total_coins > 0 else 0
        pct_eth = (chain_counts.get('Ethereum', 0) / total_coins * 100) if total_coins > 0 else 0
        pct_bnb = (chain_counts.get('BNB', 0) / total_coins * 100) if total_coins > 0 else 0
        pct_arb = (chain_counts.get('Arbitrum', 0) / total_coins * 100) if total_coins > 0 else 0
        pct_base = (chain_counts.get('Base', 0) / total_coins * 100) if total_coins > 0 else 0

        coverage_data = {
            "any_chain_percentage": round(pct_any, 2),
            "ethereum_percentage": round(pct_eth, 2),
            "bnb_percentage": round(pct_bnb, 2),
            "arbitrum_percentage": round(pct_arb, 2),
            "base_percentage": round(pct_base, 2)
        }

        cur.execute("""
            SELECT symbol, cmc_last_updated FROM coin
            WHERE cmc_last_updated IS NOT NULL ORDER BY cmc_last_updated ASC
        """)
        coin_rows = cur.fetchall()
        if coin_rows:
            old_sym, old_ts = coin_rows[0]
            new_sym, new_ts = coin_rows[-1]
            ft = new_ts if new_ts.tzinfo else new_ts.replace(tzinfo=timezone.utc)
            coin_stale = ft < now - timedelta(days=2)
            if coin_stale: overall_degraded = True
            data_dict["coin"] = {
                "count": total_coins,
                "contract_coverage": coverage_data,
                "oldest": {"symbol": old_sym, "last_updated": old_ts.isoformat() if old_ts else None},
                "latest": {"symbol": new_sym, "last_updated": new_ts.isoformat() if new_ts else None},
                "checks": {
                    "is_fresher_than_2_days": "fail" if coin_stale else "pass"
                }
            }
        else:
            data_dict["coin"] = {
                "count": total_coins,
                "contract_coverage": coverage_data,
                "checks": {
                    "is_fresher_than_2_days": "fail"
                }
            }

        # 3. coin_price_history
        cur.execute("""
            SELECT 
                COUNT(DISTINCT coin_id),
                COUNT(DISTINCT CASE WHEN latest_ts >= (CURRENT_DATE - INTERVAL '1 day') THEN coin_id END)
            FROM (
                SELECT coin_id, MAX(timestamp) AS latest_ts FROM coin_price_history GROUP BY coin_id
            ) sub
        """)
        cph_covered_coins, cph_fresh_coins = cur.fetchone()
        cph_covered_pct = round(cph_covered_coins / total_coins * 100, 2) if total_coins > 0 else 0
        cph_fresh_pct = round(cph_fresh_coins / total_coins * 100, 2) if total_coins > 0 else 0

        cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM coin_price_history")
        ph_min, ph_max, ph_count = cur.fetchone()
        ph_stale = False
        if ph_max:
            ft = ph_max if ph_max.tzinfo else ph_max.replace(tzinfo=timezone.utc)
            ph_stale = ft < now - timedelta(days=2)
            if ph_stale: overall_degraded = True
        else:
            ph_stale = True
        data_dict["coin_price_history"] = {
            "freshness_requirement": "Latest price history timestamp must be within the last 2 days",
            "count": ph_count,
            "earliest": ph_min.isoformat() if ph_min else None,
            "latest": ph_max.isoformat() if ph_max else None,
            "covered_coins": {
                "count": cph_covered_coins,
                "percentage": cph_covered_pct,
                "fresh_count": cph_fresh_coins,
                "fresh_percentage": cph_fresh_pct
            },
            "checks": {
                "is_fresher_than_2_days": "fail" if ph_stale else "pass"
            }
        }

        # 4. liquidity_pool & volume filters matrix (0, 1k, 100k, 10M USD)
        volume_thresholds = [0, 1000, 100000, 10000000]
        matrix_filters = {}

        for min_vol in volume_thresholds:
            cur.execute("""
                SELECT ch.name, pr.name,
                       COUNT(lp.id) as total_pools,
                       COUNT(lp_stats.pool_id) as covered_pools,
                       COUNT(lp_stats.pool_id) FILTER (WHERE lp_stats.has_tvl = true) as tvl_covered_pools,
                       COUNT(lp_stats.pool_id) FILTER (WHERE lp_stats.last_history_date >= CURRENT_DATE - INTERVAL '2 days') as fresh_pools,
                       MAX(lp_stats.last_history_date) as latest_history_date
                FROM liquidity_pool lp
                JOIN chain ch ON lp.chain_id = ch.id
                JOIN protocol pr ON lp.protocol_id = pr.id
                LEFT JOIN (
                    SELECT pool_id, 
                           MAX(day) as last_history_date, 
                           bool_or(tvl_usd IS NOT NULL AND tvl_usd > 0) as has_tvl,
                           SUM(CASE WHEN day >= CURRENT_DATE - INTERVAL '7 days' THEN volume_usd ELSE 0 END) as vol_7d
                    FROM liquidity_pool_daily_stats
                    GROUP BY pool_id
                ) lp_stats ON lp.id = lp_stats.pool_id
                WHERE (%s = 0 OR COALESCE(lp_stats.vol_7d, 0) >= %s)
                GROUP BY ch.name, pr.name
                ORDER BY ch.name, pr.name
            """, (min_vol, min_vol))
            
            lp_chains = {}
            tot_pools = 0
            tot_covered = 0
            tot_tvl_covered = 0
            tot_fresh = 0
            for chain, protocol, count, covered, tvl_covered, fresh, latest_ts in cur.fetchall():
                if chain not in lp_chains:
                    lp_chains[chain] = {"protocols": {}}
                coverage_pct = round((covered / count * 100)) if count > 0 else 0
                tvl_coverage_pct = round((tvl_covered / count * 100)) if count > 0 else 0
                fresh_pct = round((fresh / count * 100)) if count > 0 else 0
                lp_chains[chain]["protocols"][protocol] = {
                    "count": count,
                    "covered_count": covered,
                    "tvl_covered_count": tvl_covered,
                    "fresh_count": fresh,
                    "coverage_percentage": coverage_pct,
                    "tvl_coverage_percentage": tvl_coverage_pct,
                    "fresh_percentage": fresh_pct,
                    "latest_history_date": latest_ts.isoformat() if latest_ts else None
                }
                tot_pools += count
                tot_covered += covered
                tot_tvl_covered += tvl_covered
                tot_fresh += fresh
            
            ov_coverage_pct = round((tot_covered / tot_pools * 100)) if tot_pools > 0 else 0
            ov_tvl_coverage_pct = round((tot_tvl_covered / tot_pools * 100)) if tot_pools > 0 else 0
            ov_fresh_pct = round((tot_fresh / tot_pools * 100)) if tot_pools > 0 else 0

            matrix_filters[str(min_vol)] = {
                "min_volume": min_vol,
                "count": tot_pools,
                "chains": lp_chains,
                "covered_pools": {
                    "count": tot_covered,
                    "percentage": ov_coverage_pct,
                    "tvl_covered_count": tot_tvl_covered,
                    "tvl_coverage_percentage": ov_tvl_coverage_pct,
                    "fresh_count": tot_fresh,
                    "fresh_percentage": ov_fresh_pct
                }
            }

        data_dict["liquidity_pool"] = {k: v for k, v in matrix_filters["0"].items()}
        data_dict["liquidity_pool"]["volume_filters"] = {k: {x: y for x, y in v.items()} for k, v in matrix_filters.items()}

        # 5. coin_contract
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE tracked = true) FROM coin_contract")
        cc_total, cc_tracked = cur.fetchone()

        cur.execute("""
            SELECT ch.name, COUNT(*), COUNT(*) FILTER (WHERE cc.tracked = true)
            FROM coin_contract cc
            JOIN chain ch ON cc.chain_id = ch.id
            GROUP BY ch.name
            ORDER BY ch.name
        """)
        cc_chains = {}
        for chain, chain_total, chain_tracked in cur.fetchall():
            cc_chains[chain] = {
                "count": chain_total,
                "tracked_count": chain_tracked,
                "untracked_count": chain_total - chain_tracked
            }

        data_dict["coin_contract"] = {
            "count": cc_total,
            "tracked_count": cc_tracked,
            "untracked_count": cc_total - cc_tracked,
            "chains": cc_chains
        }

        # 6. route taxonomy: endpoint pairs, routes, and daily route stats
        cur.execute("""
            SELECT ch.name, COUNT(*)
            FROM origin_destination_pair odp
            JOIN chain ch ON odp.chain_id = ch.id
            GROUP BY ch.name
        """)
        pair_chains = dict(cur.fetchall())
        cur.execute("""
            SELECT ch.name, COUNT(*)
            FROM route r
            JOIN chain ch ON r.chain_id = ch.id
            GROUP BY ch.name
        """)
        route_chains = dict(cur.fetchall())
        cur.execute("""
            SELECT ch.name, COUNT(DISTINCT r.route_id)
            FROM route_daily_stats rds
            JOIN route r ON r.route_id = rds.route_id
            JOIN chain ch ON r.chain_id = ch.id
            GROUP BY ch.name
        """)
        stats_chains = dict(cur.fetchall())
        cur.execute("""
            SELECT ch.name, COUNT(DISTINCT r.route_id)
            FROM route_daily_stats_bucket rdb
            JOIN route r ON r.route_id = rdb.route_id
            JOIN chain ch ON r.chain_id = ch.id
            GROUP BY ch.name
        """)
        bucket_chains = dict(cur.fetchall())
        cur.execute("""
            SELECT ch.name, COUNT(DISTINCT r.route_id)
            FROM route_hop rh
            JOIN route r ON r.route_id = rh.route_id
            JOIN chain ch ON r.chain_id = ch.id
            GROUP BY ch.name
        """)
        hop_chains = dict(cur.fetchall())
        taxonomy_chains = {
            chain: {
                "pairs": pair_chains.get(chain, 0),
                "routes": route_chains.get(chain, 0),
                "daily_stats": stats_chains.get(chain, 0),
                "route_daily_stats_bucket": bucket_chains.get(chain, 0),
                "route_hop": hop_chains.get(chain, 0)
            }
            for chain in sorted(set(pair_chains) | set(route_chains) | set(stats_chains) | set(bucket_chains) | set(hop_chains))
        }
        cur.execute("SELECT COUNT(*) FROM origin_destination_pair")
        pair_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM route")
        route_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT route_id) FROM route_daily_stats")
        daily_stats_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT route_id) FROM route_daily_stats_bucket")
        bucket_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT route_id) FROM route_hop")
        hop_count = cur.fetchone()[0]
        data_dict["route_taxonomy"] = {
            "count": route_count,
            "pairs_count": pair_count,
            "routes_count": route_count,
            "daily_stats_count": daily_stats_count,
            "route_daily_stats_bucket_count": bucket_count,
            "route_hop_count": hop_count,
            "chains": taxonomy_chains
        }

        # 7. coin_family
        cur.execute("SELECT COUNT(*) FROM coin_family")
        cf_count = cur.fetchone()[0]
        data_dict["coin_family"] = {
            "count": cf_count
        }

        # 9. chain
        cur.execute("SELECT name FROM chain ORDER BY id")
        chain_names = [r[0] for r in cur.fetchall()]
        data_dict["chain"] = {
            "count": len(chain_names),
            "chains": chain_names
        }

        # 10. protocol
        cur.execute("SELECT name FROM protocol ORDER BY id")
        proto_names = [r[0] for r in cur.fetchall()]
        data_dict["protocol"] = {
            "count": len(proto_names),
            "protocols": proto_names
        }

        # 11. liquidity_pool_daily_stats
        cur.execute("SELECT COUNT(*) FROM liquidity_pool")
        total_pools = cur.fetchone()[0]

        cur.execute("""
            SELECT 
                COUNT(DISTINCT pool_id),
                COUNT(DISTINCT CASE WHEN latest_date >= (CURRENT_DATE - INTERVAL '1 day') THEN pool_id END)
            FROM (
                SELECT pool_id, MAX(day) AS latest_date FROM liquidity_pool_daily_stats GROUP BY pool_id
            ) sub
        """)
        lph_covered_pools, lph_fresh_pools = cur.fetchone()
        lph_covered_pct = round(lph_covered_pools / total_pools * 100, 2) if total_pools > 0 else 0
        lph_fresh_pct = round(lph_fresh_pools / total_pools * 100, 2) if total_pools > 0 else 0

        cur.execute("SELECT MIN(day), MAX(day), COUNT(*) FROM liquidity_pool_daily_stats")
        lph_min, lph_max, lph_count = cur.fetchone()
        lph_stale = False
        if lph_max:
            if isinstance(lph_max, datetime):
                ft = lph_max if lph_max.tzinfo else lph_max.replace(tzinfo=timezone.utc)
                lph_stale = ft < now - timedelta(days=2)
            else:
                lph_stale = lph_max < (now.date() - timedelta(days=2))
            if lph_stale: overall_degraded = True
        else:
            lph_stale = True
        data_dict["liquidity_pool_daily_stats"] = {
            "freshness_requirement": "Latest pool history timestamp must be within the last 2 days",
            "count": lph_count,
            "earliest": lph_min.isoformat() if lph_min else None,
            "latest": lph_max.isoformat() if lph_max else None,
            "covered_pools": {
                "count": lph_covered_pools,
                "percentage": lph_covered_pct,
                "fresh_count": lph_fresh_pools,
                "fresh_percentage": lph_fresh_pct
            },
            "checks": {
                "is_fresher_than_2_days": "fail" if lph_stale else "pass"
            }
        }

        # Per-chain/protocol pool-level passing metric for liquidity_pool_daily_stats matrix
        # A pool passes if it has TVL > 0 for every day in the lookback window.
        # Dormant pools (0 tx, 0 vol) with healthy TVL are considered passing.
        lookback_interval = f'{lookback_days} days'
        cur.execute("""
            SELECT ch.name, pr.name,
                   COUNT(lp.id) as total_pools,
                   COUNT(*) FILTER (WHERE sub.valid_days >= %s) as passing_pools,
                   MIN(sub.earliest_valid) as earliest_valid,
                   MAX(sub.latest_valid) as latest_valid
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            LEFT JOIN (
                SELECT lph.pool_id,
                       COUNT(*) FILTER (WHERE lph.tvl_usd > 0) as valid_days,
                       MIN(lph.day) FILTER (WHERE lph.tvl_usd > 0) as earliest_valid,
                       MAX(lph.day) FILTER (WHERE lph.tvl_usd > 0) as latest_valid
                FROM liquidity_pool_daily_stats lph
                WHERE lph.day >= CURRENT_DATE - %s::interval
                  AND lph.day < CURRENT_DATE
                GROUP BY lph.pool_id
            ) sub ON lp.id = sub.pool_id
            GROUP BY ch.name, pr.name
            ORDER BY ch.name, pr.name
        """, (lookback_days, lookback_interval))

        lph_chains = {}
        for chain, protocol, total_pools, passing_pools, earliest, latest in cur.fetchall():
            if chain not in lph_chains:
                lph_chains[chain] = {"protocols": {}}

            is_fresh = False
            if latest:
                if isinstance(latest, datetime):
                    ft = latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
                    is_fresh = ft >= now - timedelta(days=2)
                else:
                    is_fresh = latest >= (now.date() - timedelta(days=2))

            passing_pct = round(passing_pools / total_pools * 100, 1) if total_pools > 0 else 0.0

            lph_chains[chain]["protocols"][protocol] = {
                "total_pools": total_pools,
                "passing_pools": passing_pools,
                "passing_pct": passing_pct,
                "lookback_days": lookback_days,
                "earliest": earliest.isoformat() if earliest else None,
                "latest": latest.isoformat() if latest else None,
                "checks": {
                    "is_fresher_than_2_days": "pass" if is_fresh else "fail"
                }
            }

        data_dict["liquidity_pool_daily_stats"]["lookback_days"] = lookback_days
        data_dict["liquidity_pool_daily_stats"]["chains"] = lph_chains

        # Per-volume-threshold passing metrics for liquidity_pool_daily_stats
        lph_volume_filters = {}
        for min_vol in volume_thresholds:
            cur.execute("""
                SELECT ch.name, pr.name,
                       COUNT(lp.id) as total_pools,
                       COUNT(*) FILTER (WHERE sub.valid_days >= %s) as passing_pools,
                       MIN(sub.earliest_valid) as earliest,
                       MAX(sub.latest_valid) as latest
                FROM liquidity_pool lp
                JOIN chain ch ON lp.chain_id = ch.id
                JOIN protocol pr ON lp.protocol_id = pr.id
                LEFT JOIN (
                    SELECT lph.pool_id,
                           COUNT(*) FILTER (WHERE lph.tvl_usd > 0) as valid_days,
MIN(lph.day) FILTER (WHERE lph.tvl_usd > 0) as earliest_valid,
                       MAX(lph.day) FILTER (WHERE lph.tvl_usd > 0) as latest_valid,
                            SUM(CASE WHEN day >= CURRENT_DATE - %s::interval THEN volume_usd ELSE 0 END) as vol_window
                    FROM liquidity_pool_daily_stats lph
                    WHERE lph.day >= CURRENT_DATE - %s::interval
                      AND lph.day < CURRENT_DATE
                    GROUP BY lph.pool_id
                ) sub ON lp.id = sub.pool_id
                WHERE (%s = 0 OR COALESCE(sub.vol_window, 0) >= %s)
                GROUP BY ch.name, pr.name
                ORDER BY ch.name, pr.name
            """, (lookback_days, lookback_interval, lookback_interval, min_vol, min_vol))

            lph_threshold_chains = {}
            tot_pools = 0
            tot_passing = 0
            for chain, protocol, total_pools, passing_pools, earliest, latest in cur.fetchall():
                if chain not in lph_threshold_chains:
                    lph_threshold_chains[chain] = {"protocols": {}}

                passing_pct = round(passing_pools / total_pools * 100, 1) if total_pools > 0 else 0.0

                lph_threshold_chains[chain]["protocols"][protocol] = {
                    "total_pools": total_pools,
                    "passing_pools": passing_pools,
                    "passing_pct": passing_pct,
                    "earliest": earliest.isoformat() if earliest else None,
                    "latest": latest.isoformat() if latest else None,
                }
                tot_pools += total_pools
                tot_passing += passing_pools

            lph_volume_filters[str(min_vol)] = {
                "chains": lph_threshold_chains,
                "count": tot_pools,
                "covered_pools": {
                    "count": tot_passing,
                    "percentage": round(tot_passing / tot_pools * 100, 2) if tot_pools > 0 else 0,
                }
            }

        data_dict["liquidity_pool_daily_stats"]["volume_filters"] = lph_volume_filters

        # 12. liquidity_pool_position
        cur.execute("SELECT COUNT(*) FROM liquidity_pool_position")
        lpp_count = cur.fetchone()[0]

        cur.execute("""
            SELECT 
                COUNT(DISTINCT position_id),
                COUNT(DISTINCT CASE WHEN latest_ts >= (CURRENT_DATE - INTERVAL '1 day') THEN position_id END)
            FROM (
                SELECT position_id, MAX(timestamp) AS latest_ts FROM liquidity_pool_position_snapshot GROUP BY position_id
            ) sub
        """)
        lpps_covered_pos, lpps_fresh_pos = cur.fetchone()
        lpps_covered_pct = round(lpps_covered_pos / lpp_count * 100, 2) if lpp_count > 0 else 0
        lpps_fresh_pct = round(lpps_fresh_pos / lpp_count * 100, 2) if lpp_count > 0 else 0

        data_dict["liquidity_pool_position"] = {
            "count": lpp_count,
            "snapshot_coverage": {
                "covered_positions_count": lpps_covered_pos,
                "covered_positions_percentage": lpps_covered_pct,
                "fresh_positions_count": lpps_fresh_pos,
                "fresh_positions_percentage": lpps_fresh_pct
            }
        }

        # 13. liquidity_pool_position_event
        cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM liquidity_pool_position_event")
        lppe_min, lppe_max, lppe_count = cur.fetchone()
        data_dict["liquidity_pool_position_event"] = {
            "count": lppe_count,
            "earliest": lppe_min.isoformat() if lppe_min else None,
            "latest": lppe_max.isoformat() if lppe_max else None
        }

        # 14. liquidity_pool_position_snapshot
        cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM liquidity_pool_position_snapshot")
        lpps_min, lpps_max, lpps_count = cur.fetchone()
        lpps_stale = False
        if lpps_max:
            ft = lpps_max if lpps_max.tzinfo else lpps_max.replace(tzinfo=timezone.utc)
            lpps_stale = ft < now - timedelta(days=2)
            if lpps_stale: overall_degraded = True
        else:
            lpps_stale = True
        data_dict["liquidity_pool_position_snapshot"] = {
            "freshness_requirement": "Latest pool position snapshot timestamp must be within the last 2 days",
            "count": lpps_count,
            "earliest": lpps_min.isoformat() if lpps_min else None,
            "latest": lpps_max.isoformat() if lpps_max else None,
            "covered_positions": {
                "count": lpps_covered_pos,
                "percentage": lpps_covered_pct,
                "fresh_count": lpps_fresh_pos,
                "fresh_percentage": lpps_fresh_pct
            },
            "checks": {
                "has_data_every_day": "pass" if lpps_max else "fail",
                "is_fresher_than_2_days": "fail" if lpps_stale else "pass"
            }
        }

        cur.close()
        conn.close()
    except Exception as e:
        overall_degraded = True
        data_dict["error"] = str(e)

    res_tuple = (overall_degraded, data_dict)
    _health_data_cache[lookback_days] = (res_tuple, time.time())

    if acquired:
        _health_data_lock.release()
    return res_tuple


def navigate_health_data(data_obj, path_str: str):
    import urllib.parse
    if not path_str or path_str.strip("/") == "":
        return data_obj

    parts = [p for p in path_str.strip("/").split("/") if p]
    current = data_obj

    for part in parts:
        part_unquoted = urllib.parse.unquote(part)
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                found = False
                for k, v in current.items():
                    if k.lower() == part.lower() or k.lower() == part_unquoted.lower():
                        current = v
                        found = True
                        break
                if not found:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Path segment '{part}' not found. Available keys: {list(current.keys())}"
                    )
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                if part_unquoted in current:
                    current = part_unquoted
                else:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Item '{part}' not found in list: {current}"
                    )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Cannot drill down further into '{part}' on non-container element"
            )

    return current


@app.get("/health", tags=["System"])
async def health_check(lph_lookback: int = Query(7, ge=1, le=365)):
    """Detailed health and data freshness report for database tables.
    - lph_lookback: number of days to check for complete pool history coverage (default 7)
    """
    from datetime import datetime, timezone
    overall_degraded, data = await asyncio.to_thread(build_all_tables_health, lph_lookback)
    response_data = {k: v for k, v in data.items() if k != "coins"}

    db_status = {
        "status": "connected" if "error" not in data else "error",
        "table": response_data
    }
    if "error" in data:
        db_status["error"] = data["error"]

    return {
        "status": "degraded" if overall_degraded else "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": db_status
    }


@app.get("/health/db/table", tags=["System"])
@app.get("/health/db/table/{subpath:path}", tags=["System"])
async def health_table_subpath(subpath: str = ""):
    """Drill down into table health data (e.g. /health/db/table/, /health/db/table/swaps, /health/db/table/swaps/chains/Arbitrum)."""
    overall_degraded, data = await asyncio.to_thread(build_all_tables_health)
    if "error" in data and not subpath:
        return data
    return navigate_health_data(data, subpath)


@app.get("/lp", include_in_schema=False)
async def read_lp():
    return FileResponse(os.path.join(STATIC_DIR, 'lp.html'))

@app.get("/pool", include_in_schema=False)
async def read_pool():
    return FileResponse(os.path.join(STATIC_DIR, 'pool.html'))

@app.get("/pool-arena", include_in_schema=False)
async def read_pool_arena():
    return FileResponse(os.path.join(STATIC_DIR, 'pool-arena.html'),
                        headers={"Cache-Control": "no-store"})

@app.get("/status", include_in_schema=False)
@app.get("/health-status", include_in_schema=False)
async def read_status():
    return FileResponse(os.path.join(STATIC_DIR, 'health.html'))

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return FileResponse(os.path.join(STATIC_DIR, 'api.html'))

@app.get("/swagger", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - API Specs",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_css_url="/static/swagger-custom.css?v=4.0",
        swagger_ui_parameters={"syntaxHighlight": {"theme": "monokai"}}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
