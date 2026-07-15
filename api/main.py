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
from fastapi import FastAPI, HTTPException, Query, Request, Response, Body
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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

# Import routing logic from chain-feeder

# Load DEX configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'dex_config.yaml')
with open(CONFIG_PATH, 'r') as f:
    DEX_CONFIG = yaml.safe_load(f)


CHAIN_FEEDER_ROUTING = os.path.join(ROOT_DIR, 'chain-feeder', 'routing')
if CHAIN_FEEDER_ROUTING not in sys.path:
    sys.path.insert(0, CHAIN_FEEDER_ROUTING)

# Import graph discovery client


GRAPH_CLIENT_DIR = os.path.join(ROOT_DIR, 'chain-feeder')
if GRAPH_CLIENT_DIR not in sys.path:
    sys.path.insert(0, GRAPH_CLIENT_DIR)
if os.path.join(GRAPH_CLIENT_DIR, 'include') not in sys.path:
    sys.path.insert(0, os.path.join(GRAPH_CLIENT_DIR, 'include'))

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


def format_apr(apr_val):
    if apr_val is None:
        return "N/A"
    pct = apr_val * 100
    rounded = round(pct + 1e-9, 1)
    if rounded == 0.0:
        return "0%"
    if rounded == int(rounded):
        return f"{int(rounded)}%"
    return f"{rounded}%"


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


async def get_enriched_pool_stat(key: str, rev_key: str, aprs: dict, pool_addr: str, pool_network: str, period_days: float, fee_tier: str) -> dict:
    pool_stat = aprs.get(key) or aprs.get(rev_key)
    if pool_stat is None:
        pool_stat = {'apr': None, 'tvl': 0.0, 'volume': 0.0}
        
    tvl_val = pool_stat.get('tvl') or 0.0
    vol_val = pool_stat.get('volume') or 0.0
    apr_val = pool_stat.get('apr')
    
    is_unreliable = tvl_val <= 1.0 or (vol_val > 0.0 and tvl_val < (vol_val / period_days) * 0.05)
    
    if is_unreliable and pool_addr:
        ds_tvl = await asyncio.to_thread(fetch_dexscreener_tvl, pool_network, pool_addr)
        
        # Fallback to DeFi Llama TVL if DexScreener fails
        if not ds_tvl or ds_tvl <= 1.0:
            dl_tvl = get_defillama_pool_tvl(pool_addr)
            if dl_tvl and dl_tvl > 1.0:
                ds_tvl = dl_tvl
                
        if ds_tvl and ds_tvl > 1.0:
            tvl_val = ds_tvl
            fee_rate = parse_fee_rate(fee_tier)
            if fee_rate is not None:
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
    assuming hooks = address(0). currency0 < currency1 (sorted)."""
    a = bytes.fromhex(c0_hex.lower().removeprefix('0x').rjust(40, '0'))
    b = bytes.fromhex(c1_hex.lower().removeprefix('0x').rjust(40, '0'))
    if b < a:
        a, b = b, a
    hooks = b'\x00' * 32
    enc = (a.rjust(32, b'\x00') + b.rjust(32, b'\x00') +
           fee.to_bytes(32, 'big') + tick_spacing.to_bytes(32, 'big', signed=True) + hooks)
    return '0x' + keccak(enc).hex()


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

        # Uniswap V4: derive the poolId (what Chaintelligence carries) and map
        # it to the UUID. Only non-ETH, standard-fee, no-hook pools — ETH pools
        # use native 0x0 on DeFi Llama but WETH in Chaintelligence (different
        # poolIds), and non-standard fees have unknown tickSpacing/hooks.
        if project == 'uniswap-v4':
            if any(t.lower() == _NATIVE_ZERO for t in tokens):
                continue
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


def get_defillama_index() -> Dict[str, dict]:
    """Return the cached pool_address->UUID index, rebuilding if stale.
    Thread-safe; safe to call from asyncio.to_thread on first use."""
    global DEFILLAMA_INDEX, DEFILLAMA_INDEX_BUILT_AT
    now = time.time()
    if DEFILLAMA_INDEX and (now - DEFILLAMA_INDEX_BUILT_AT < DEFILLAMA_INDEX_TTL):
        return DEFILLAMA_INDEX
    with _DEFILLAMA_LOCK:
        if DEFILLAMA_INDEX and (time.time() - DEFILLAMA_INDEX_BUILT_AT < DEFILLAMA_INDEX_TTL):
            return DEFILLAMA_INDEX
        try:
            idx = _build_defillama_index()
            if idx:
                DEFILLAMA_INDEX = idx
                DEFILLAMA_INDEX_BUILT_AT = time.time()
                print(f"[DeFiLlama] yields index built: {len(idx)} pools")
        except Exception as e:
            print(f"[DeFiLlama] yields index build failed: {e}")
    return DEFILLAMA_INDEX


def get_defillama_pool_uuid(pool_addr: Optional[str]) -> Optional[str]:
    if not pool_addr:
        return None
    return get_defillama_index().get(pool_addr.lower(), {}).get('uuid')


def get_defillama_pool_tvl(pool_addr: Optional[str]) -> Optional[float]:
    if not pool_addr:
        return None
    return get_defillama_index().get(pool_addr.lower(), {}).get('tvl')


try:
    from postgres_fetcher import PostgresFetcher, get_conn
    from route_analyzer import RouteAnalyzer
    from shortcut_finder import ShortcutFinder
    from config import DATA_WAREHOUSE_DB
except ImportError as e:
    print(f"Error importing routing modules from {CHAIN_FEEDER_ROUTING}: {e}")
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
    exempt_paths = ["/api/coin/list", "/api/coin/price-history", "/api/routes/date-range", "/api/routes/analyze", "/backtester", "/pool", "/favicon.ico", "/static", "/api/sps", "/sps", "/api/lp", "/routing", "/lp"]
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

@app.get("/backtester/config.js", include_in_schema=False)
async def get_backtester_config():
    """Dynamically serve CryptoCompare API key to the backtester."""
    api_key = os.getenv("CRYPTOCOMPARE_API_KEY", "")
    content = f"const CONFIG = {{ CRYPTOCOMPARE_API_KEY: '{api_key}' }};\n"
    if "typeof module !== 'undefined'" not in content: # Just to be safe with format
         content += "if (typeof module !== 'undefined') { module.exports = CONFIG; }\n"
    return Response(content=content, media_type="application/javascript")

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

# Global memory cache for resolved token symbols to contract addresses per network to prevent repetitive slow DB queries
TOKEN_ADDRESS_CACHE = {}

@app.get("/api/routes/analyze", tags=["Route Analytics"])
async def analyze(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None, description="Lookback period in days"),
    start_date: Optional[str] = Query(None, description="ISO format start date"),
    end_date: Optional[str] = Query(None, description="ISO format end date"),
    network: Optional[str] = Query(None, description="Filter swaps by network")
):
    """Analyze swap routes between two tokens."""
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            if end_date:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                # If it's a date-only input (midnight), extend to the end of that day
                if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0 and end_dt.microsecond == 0:
                    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                end_dt = now
            # No cap — the streaming + day‑chunking approach handles
# large ranges efficiently, one day at a time.
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

        # Fetch prices (scoped to the involved tokens) for volume fallback
        latest_prices = fetcher.fetch_latest_prices(token_filter)
        analyzer = RouteAnalyzer(verbose=True, prices=latest_prices)

        from fastapi.responses import StreamingResponse
        import json
        import asyncio
        import psycopg2

        async def generate():
            c_chunk_start = start_dt
            has_data = False
            total_seconds = (end_dt - start_dt).total_seconds()
            
            while c_chunk_start < end_dt:
                c_chunk_end = min(c_chunk_start + timedelta(days=1), end_dt)
                progress_sec = (c_chunk_start - start_dt).total_seconds()
                # Fetching takes 0% to 75% of the total progress
                pct = (progress_sec / total_seconds) * 75 if total_seconds > 0 else 0
                yield json.dumps({"type": "progress", "pct": round(pct, 1), "message": f"Fetching swaps for {c_chunk_start.strftime('%Y-%m-%d')} → {c_chunk_end.strftime('%Y-%m-%d')}..."}) + "\n"
                await asyncio.sleep(0.01)

                print(f"[Anaylsis] Processing batch: {c_chunk_start} -> {c_chunk_end}")
                batch_swaps = await asyncio.to_thread(
                    fetcher.fetch_swaps, c_chunk_start, c_chunk_end, token_filter, network, start_tokens_list, end_tokens_list
                )

                if batch_swaps:
                    has_data = True
                    await asyncio.to_thread(
                        analyzer.process_batch, batch_swaps, start_tokens_list, end_tokens_list
                    )

                batch_swaps = []
                c_chunk_start = c_chunk_end
                
            yield json.dumps({"type": "progress", "pct": 75.0, "message": "Building routing path graph..."}) + "\n"
            await asyncio.sleep(0.01)
            
            if not has_data:
                conn = psycopg2.connect(DATA_WAREHOUSE_DB)
                cur = conn.cursor()
                cur.execute("""
                    SELECT MIN(ts), MAX(ts) FROM swaps
                """)
                row = cur.fetchone()
                db_min = row[0].isoformat() if row[0] else None
                db_max = row[1].isoformat() if row[1] else None
                cur.close()
                conn.close()
                
                yield json.dumps({"type": "result", "data": {"routes": [], "total_tx": 0, "total_volume": 0, "db_range": {"min": db_min, "max": db_max}}}) + "\n"
                return

            analysis = analyzer.get_results()
        
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
                        fetcher.fetch_pool_stats, list(pools_to_fetch), start_dt, end_dt, latest_prices
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
                                        WHERE LOWER(cc.chain) = %s AND UPPER(c.symbol) = ANY(%s)
                                    """, (db_chain, missing_symbols))
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
                                    print(f"  Error deriving address for {j['key']}: {ex}")
                        return batch_results

                    batch = await asyncio.to_thread(_derive_batch)
                    pool_addresses.update(batch)

                if v4_keys:
                    def _lookup_v4_pool_ids():
                        """Fetch V4 pool identifiers from DB in one query."""
                        v4_results = {}
                        try:
                            with get_conn() as conn:
                                cur = conn.cursor()
                                cur.execute("""
                                    SELECT lp.network, lp.protocol, lp.fee_tier, lp.pool_id,
                                           UPPER(c0.symbol) AS s0,
                                           UPPER(c1.symbol) AS s1,
                                           cc0.contract_address AS t0_addr
                                    FROM liquidity_pool lp
                                    JOIN coin c0 ON lp.coin0_id = c0.coin_id
                                    JOIN coin c1 ON lp.coin1_id = c1.coin_id
                                    LEFT JOIN coin_contract cc0
                                        ON cc0.coin_id = lp.coin0_id
                                       AND LOWER(cc0.chain) =
                                           CASE WHEN lp.network = 'BNB' THEN 'bsc'
                                                ELSE LOWER(lp.network) END
                                    WHERE (lp.protocol = 'Uniswap V4' AND lp.pool_id IS NOT NULL)
                                       OR lp.protocol = 'PancakeSwap V4'
                                    ORDER BY
                                        CASE WHEN c0.symbol IN ('WETH', 'WBNB') OR c1.symbol IN ('WETH', 'WBNB') THEN 1 ELSE 0 END ASC
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
                                    
                                    # Normalize symbols for key mapping (so native ETH/BNB maps to the wrapped WETH/WBNB pool)
                                    s0_norm = 'WETH' if sym0 == 'ETH' else ('WBNB' if sym0 == 'BNB' else sym0)
                                    s1_norm = 'WETH' if sym1 == 'ETH' else ('WBNB' if sym1 == 'BNB' else sym1)

                                    # Build ALL possible fee format keys
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
                                        key_fwd = f"{s0_norm}-{s1_norm}-{fk}|{proto}|{net}"
                                        key_rev = f"{s1_norm}-{s0_norm}-{fk}|{proto}|{net}"
                                        v4_results[key_fwd] = value
                                        v4_results[key_rev] = value
                        except Exception as ex:
                            print(f"  Error looking up V4 pool_ids: {ex}")
                        return v4_results

                    v4_batch = await asyncio.to_thread(_lookup_v4_pool_ids)
                    pool_addresses.update(v4_batch)

            # Warm the DeFi Llama yields index off the event loop (one-time per TTL).
            if not DEFILLAMA_INDEX or (time.time() - DEFILLAMA_INDEX_BUILT_AT > DEFILLAMA_INDEX_TTL):
                await asyncio.to_thread(get_defillama_index)

            # 3. Inject into routes
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
                        days = max(1, (end_dt - start_dt).days)
                        
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
                        new_path.append({
                            'fee': fee,
                            'apr': apr_val if apr_val is not None else 0.0,
                            'apr_str': format_apr(apr_val),
                            'pool_address': pool_addr,
                            'tvl': tvl_val,
                            'defillama_uuid': get_defillama_pool_uuid(pool_addr)
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
            
                analysis['routes'][route_idx]['path_tokens'] = new_path
                analysis['routes'][route_idx]['apr'] = route_apr
                analysis['routes'][route_idx]['apr_str'] = apr_str
                analysis['routes'][route_idx]['network'] = route_network

            yield json.dumps({"type": "progress", "pct": 98.0, "message": "Formatting routing path data..."}) + "\n"
            await asyncio.sleep(0.01)
            
            yield json.dumps({"type": "progress", "pct": 100.0, "message": "Analysis complete!"}) + "\n"
            await asyncio.sleep(0.01)

            yield json.dumps({"type": "result", "data": analysis}) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/routes/date-range", tags=["Route Analytics"])
async def get_date_range(network: Optional[str] = Query(None, description="Filter by network")):
    """Get the available date range from the swap data, optionally scoped to a network."""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            if network and network.lower() != 'all':
                # Per-network: return the exact date range for that network
                cur.execute("""
                    SELECT MIN(ts)::date, MAX(ts)::date
                    FROM swaps
                    WHERE network = %s
                """, (network,))
            else:
                # "All" mode: return the tightest range that has data for every network
                cur.execute("""
                    SELECT MAX(min_date)::date, MAX(max_date)::date FROM (
                        SELECT network, MIN(ts) as min_date, MAX(ts) as max_date
                        FROM swaps
                        GROUP BY network
                    ) as per_network
                """)
            row = cur.fetchone()
            cur.close()

        if row and row[0] and row[1]:
            return {
                "min_date": row[0].isoformat(),
                "max_date": row[1].isoformat()
            }
        else:
            return {"min_date": None, "max_date": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/lp/position-summary", tags=["Liquidity Pools"])
async def lp_summary():
    """Get the latest summary of LP snapshots with APR calculations."""
    try:
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
                        lp.price_lower, lp.price_upper, lp.current_price, lp.in_range, lp.fee_tier,
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
                "pool_id": latest[21],
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
@app.get("/api/lp/history", tags=["Liquidity Pools"])
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
            pool.network
        FROM liquidity_pool_position_event e
        JOIN liquidity_pool_position pos ON e.position_id = pos.id
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
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

@app.get("/api/coin/price-history", tags=["Assets"])
async def price_history(symbol: str):
    """Get historical daily prices for a coin from Postgres."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # We join with coin table via coin_id
        query = """
        SELECT h.timestamp, h.price 
        FROM coin_price_history h
        JOIN coin c ON h.coin_id = c.coin_id
        WHERE UPPER(c.symbol) = %s
        ORDER BY h.timestamp ASC
        """
        cur.execute(query, (symbol.upper(),))
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
    Trigger the coin_price_history_feeder DAG in Airflow.
    Handles Airflow 3 JWT-based authentication.
    """
    import requests
    from anyio import to_thread
    from datetime import datetime
    
    dag_id = "coin_price_history_feeder"
    
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

@app.get("/api/coin/list", tags=["Assets"])
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


@app.get("/api/pools", tags=["Liquidity Pools"])
async def list_pools():
    """List all available liquidity pools with latest stats."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                query = """
                SELECT
                    p.id, p.network, p.protocol, p.pool_name, p.fee_tier, p.pool_address,
                    h.tvl_usd, h.volume_usd, h.tx_count, c0.symbol, c1.symbol
                FROM liquidity_pool p
                JOIN coin c0 ON p.coin0_id = c0.coin_id
                JOIN coin c1 ON p.coin1_id = c1.coin_id
                LEFT JOIN (
                    SELECT DISTINCT ON (pool_id) pool_id, tvl_usd, volume_usd, tx_count
                    FROM liquidity_pool_history
                    ORDER BY pool_id, date DESC
                ) h ON p.id = h.pool_id
                WHERE p.reverted = FALSE OR p.protocol IN ('Uniswap V3', 'Uniswap V4', 'PancakeSwap V3', 'PancakeSwap V4') -- Show all V3/V4 pools even if reverted, to avoid gaps
                ORDER BY h.tvl_usd DESC NULLS LAST
                """
                cur.execute(query)
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
            SELECT lp.network, lp.pool_address, lp.protocol, c0.symbol, c1.symbol, lp.fee_tier 
            FROM liquidity_pool lp
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
                WHERE c.symbol IN (%s, %s) AND LOWER(cc.chain) = %s
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
        LEFT JOIN coin_contract eth ON c.coin_id = eth.coin_id AND eth.chain = 'ethereum'
        WHERE c.cmc_id = ANY(%s)
        """
        cur.execute(query, (cmc_ids,))
        rows = cur.fetchall()
        
        # Fetch all other contracts for these coins
        cur.execute("""
            SELECT cc.coin_id, cc.chain, cc.contract_address, cc.decimals, cc.is_native
            FROM coin_contract cc
            JOIN coin c ON cc.coin_id = c.coin_id
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
                    fetcher.fetch_pool_stats, list(pools_to_fetch), start_dt, end_dt, latest_prices
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
                                WHERE LOWER(cc.chain) = %s AND UPPER(c.symbol) = ANY(%s)
                            """, (db_chain, missing_symbols))
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
                                SELECT lp.network, lp.protocol, lp.fee_tier, lp.pool_id,
                                       UPPER(c0.symbol) AS s0,
                                       UPPER(c1.symbol) AS s1,
                                       cc0.contract_address AS t0_addr
                                FROM liquidity_pool lp
                                JOIN coin c0 ON lp.coin0_id = c0.coin_id
                                JOIN coin c1 ON lp.coin1_id = c1.coin_id
                                LEFT JOIN coin_contract cc0
                                    ON cc0.coin_id = lp.coin0_id
                                   AND LOWER(cc0.chain) =
                                       CASE WHEN lp.network = 'BNB' THEN 'bsc'
                                            ELSE LOWER(lp.network) END
                                WHERE (lp.protocol = 'Uniswap V4' AND lp.pool_id IS NOT NULL)
                                   OR lp.protocol = 'PancakeSwap V4'
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
                pool_stats[f"{t0}-{t1}-{fee}"] = {
                    'apr': apr_val if apr_val is not None else 0.0,
                    'apr_str': apr_str,
                    'pool_address': pool_addr,
                    'tvl': tvl_val,
                    'defillama_uuid': get_defillama_pool_uuid(pool_addr)
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

@app.get("/lp", include_in_schema=False)
async def read_lp():
    return FileResponse(os.path.join(STATIC_DIR, 'lp.html'))

@app.get("/pool", include_in_schema=False)
async def read_pool():
    return FileResponse(os.path.join(STATIC_DIR, 'pool.html'))

@app.get("/sps", include_in_schema=False)
async def read_sps():
    return FileResponse(os.path.join(STATIC_DIR, 'sps.html'))

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return FileResponse(os.path.join(STATIC_DIR, 'api.html'))

@app.get("/swagger", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - API Specs",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_css_url="/static/swagger-custom.css"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
