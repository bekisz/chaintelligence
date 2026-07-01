import sys
import os
import base64
import secrets
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

# Airflow API Configuration
AIRFLOW_API_URL = os.getenv("AIRFLOW_API_URL", "http://airflow-webserver:8080/api/v2")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "airflow")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "airflow")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WEB_DIR = os.path.join(ROOT_DIR, 'web')
STATIC_DIR = os.path.join(WEB_DIR, 'static')
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# Import routing logic from chain-feeder
CHAIN_FEEDER_ROUTING = os.path.join(ROOT_DIR, 'chain-feeder', 'routing')
if CHAIN_FEEDER_ROUTING not in sys.path:
    sys.path.insert(0, CHAIN_FEEDER_ROUTING)

# Import graph discovery client
GRAPH_CLIENT_DIR = os.path.join(ROOT_DIR, 'chain-feeder')
if GRAPH_CLIENT_DIR not in sys.path:
    sys.path.insert(0, GRAPH_CLIENT_DIR)
if os.path.join(GRAPH_CLIENT_DIR, 'include') not in sys.path:
    sys.path.insert(0, os.path.join(GRAPH_CLIENT_DIR, 'include'))

try:
    from postgres_fetcher import PostgresFetcher
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
    exempt_paths = ["/api/coin/list", "/api/coin/price-history", "/backtester", "/pool", "/favicon.ico", "/static", "/api/sps", "/sps", "/api/lp"]
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
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        # Check if it's a family
        # We search case-insensitive for family name in the official coin_family table
        cur.execute("""
            SELECT symbol FROM coin_family
            WHERE UPPER(name) = %s
        """, (input_str.upper(),))
        rows = cur.fetchall()
        
        cur.close()
        conn.close()
        
        if rows:
            return [row[0] for row in rows]
        
        # Not a family, assume single token
        return [input_str]
        
    except Exception as e:
        print(f"Error resolving token family: {e}")
        return [input_str]

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
        else:
            end_dt = now
            start_dt = end_dt - timedelta(days=1)

        # Resolve tokens/families FIRST so we can use them in processing
        start_tokens_list = resolve_token_input(start_token)
        end_tokens_list = resolve_token_input(end_token)
        
        if not start_tokens_list: start_tokens_list = [start_token]
        if not end_tokens_list: end_tokens_list = [end_token]

        fetcher = PostgresFetcher(verbose=True)
        # Fetch prices for volume fallback
        latest_prices = fetcher.fetch_latest_prices()
        analyzer = RouteAnalyzer(verbose=True, prices=latest_prices)
        
        # Batched Processing Configuration
        BATCH_DAYS = 1
        current_chunk_start = start_dt
        has_data = False
        
        # Build token_filter to prevent fetching millions of irrelevant rows
        token_filter = []
        if "*" not in start_tokens_list:
            token_filter.extend(start_tokens_list)
        if "*" not in end_tokens_list:
            token_filter.extend(end_tokens_list)
        if not token_filter:
            token_filter = None # Fallback if both are wildcards (should not happen from UI)
            
        while current_chunk_start < end_dt:
            # Calculate chunk end
            chunk_end = current_chunk_start + timedelta(days=BATCH_DAYS)
            if chunk_end > end_dt:
                chunk_end = end_dt
                
            # Fetch Batch
            print(f"[Anaylsis] Processing batch: {current_chunk_start} -> {chunk_end}")
            batch_swaps = fetcher.fetch_swaps(current_chunk_start, chunk_end, token_filter=token_filter, network=network)
            
            if batch_swaps:
                has_data = True
                analyzer.process_batch(batch_swaps, start_tokens_list, end_tokens_list)
                
            # Cleanup
            batch_swaps = []
            
            # Move to next batch (microsecond offset to avoid overlap with <= logic)
            current_chunk_start = chunk_end + timedelta(microseconds=1)
            
        if not has_data:
            # Fetch min/max from DB to show user available range
            import psycopg2
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
            cur = conn.cursor()
            cur.execute("""
                SELECT MIN(timestamp), MAX(timestamp) FROM (
                    SELECT timestamp FROM uniswap_v3_swaps
                    UNION ALL
                    SELECT timestamp FROM uniswap_v4_swaps
                ) as all_swaps
            """)
            row = cur.fetchone()
            db_min = row[0].isoformat() if row[0] else None
            db_max = row[1].isoformat() if row[1] else None
            cur.close()
            conn.close()
            
            return {
                "routes": [], 
                "total_tx": 0, 
                "total_volume": 0,
                "db_range": {"min": db_min, "max": db_max}
            }

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
            try:
                aprs = fetcher.fetch_pool_stats(list(pools_to_fetch), start_dt, end_dt, prices=latest_prices)
            except Exception as e:
                print(f"Error fetching pool stats: {e}")

        # 2b. Compute pool addresses deterministically using Create2/Keccak-256
        pool_addresses = {}
        if pools_to_fetch:
            token_symbols = set()
            for (t0, t1, fee) in pools_to_fetch:
                token_symbols.add(t0.upper())
                token_symbols.add(t1.upper())
                
            token_addresses = {}
            if token_symbols:
                # Core verified on-chain token addresses by network to ensure correct Create2 calculations
                NETWORK_TOKEN_MAPS = {
                    "Ethereum": {
                        "USDC": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eB48",
                        "USDT": "0xdAC17F958D2ee523a2206206994597c13d831ec7",
                        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
                        "ETH":  "0x0000000000000000000000000000000000000000",
                    },
                    "BNB": {
                        "WBNB": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
                        "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
                        "USDT": "0x55d398326f99059ff775485246999027b3197955",
                        "BTCB": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
                        "ETH":  "0x0000000000000000000000000000000000000000",
                    },
                    "Arbitrum": {
                        "ETH":    "0x0000000000000000000000000000000000000000",
                        "USDC.e": "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
                        "USDC":   "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                        "WETH":   "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                        "USDT":   "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
                        "WBTC":   "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
                        "DAI":    "0xda10009c55681e77d502082691d29f8fb095569f",
                        "LINK":   "0xf97f4df75117a78c1a5a0dbb814af92458539fb4",
                        "GMX":    "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a",
                        "AAVE":   "0xba5ddd1f9d7f570dc94a51479a000e3bce967196",
                        "ZRO":    "0x6985884c4392d348587b19cb9eaaf157f13271cd",
                    },
                    "Base": {
                        "ETH":    "0x0000000000000000000000000000000000000000",
                        "WETH":   "0x4200000000000000000000000000000000000006",
                        "USDC":   "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "USDbC":  "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
                        "cbBTC":  "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
                    }
                }
                
                target_network = network or "Ethereum"
                net_map = NETWORK_TOKEN_MAPS.get(target_network, {})
                
                # Pre-populate symbols we have in core mapping
                for sym in token_symbols:
                    if sym in net_map:
                        token_addresses[sym] = net_map[sym]
                        
                # Database lookup only for any remaining missing symbols (e.g. custom test tokens)
                missing_symbols = [sym for sym in token_symbols if sym not in token_addresses]
                if missing_symbols:
                    try:
                        import psycopg2
                        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
                        cur = conn.cursor()
                        
                        # 1. Fetch addresses from V3 swaps table for this specific network
                        cur.execute("""
                            SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                            UNION
                            SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                        """, (target_network, missing_symbols, target_network, missing_symbols))
                        for row in cur.fetchall():
                            if row[1]:
                                token_addresses[row[0]] = row[1]
                                
                        # 2. Fetch from V4 swaps table for any missing symbols
                        missing_tokens_v4 = [sym for sym in missing_symbols if sym not in token_addresses]
                        if missing_tokens_v4:
                            cur.execute("""
                                SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                                UNION
                                SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                            """, (target_network, missing_tokens_v4, target_network, missing_tokens_v4))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[row[0]] = row[1]
                                    
                        # 3. Fallback to general coin table for any remaining ones
                        still_missing = [sym for sym in missing_symbols if sym not in token_addresses]
                        if still_missing:
                            cur.execute("SELECT UPPER(symbol), ethereum_address FROM coin WHERE UPPER(symbol) = ANY(%s)", (still_missing,))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[row[0]] = row[1]
                                    
                        cur.close()
                        conn.close()
                    except Exception as e:
                        print(f"Error fetching token addresses for network {network}: {e}")
            
            try:
                from Crypto.Hash import keccak
                for (t0, t1, fee) in pools_to_fetch:
                    t0_sym, t1_sym = t0.upper(), t1.upper()
                    addr0 = token_addresses.get(t0_sym)
                    addr1 = token_addresses.get(t1_sym)
                    if not addr0 or not addr1:
                        continue
                        
                    fee_raw = str(fee).split('|')[0].strip()
                    f_clean = fee_raw.replace('%', '').strip()
                    
                    network = "Ethereum"
                    parts = str(fee).split('|')
                    if len(parts) >= 3:
                        network = parts[2].strip()
                        
                    protocol = "Uniswap V3"
                    if len(parts) >= 2:
                        proto_raw = parts[1].strip()
                        if proto_raw.lower() in ('v3', 'uniswap v3', 'uniswap-v3'):
                            protocol = 'Uniswap V3'
                        elif proto_raw.lower() in ('v4', 'uniswap v4', 'uniswap-v4'):
                            protocol = 'Uniswap V4'
                        else:
                            protocol = proto_raw
                            
                    try:
                        fee_map = {'0.01': 100, '0.05': 500, '0.08': 800, '0.3': 3000, '1.0': 10000}
                        if f_clean in fee_map:
                            fee_val = fee_map[f_clean]
                        else:
                            fee_val = int(float(f_clean) * 10000)
                    except:
                        continue
                        
                    # Sort token addresses numerically
                    t0_hex = addr0.lower()
                    t1_hex = addr1.lower()
                    tokens = sorted([t0_hex, t1_hex])
                    t0_bytes = bytes.fromhex(tokens[0][2:])
                    t1_bytes = bytes.fromhex(tokens[1][2:])
                    
                    if protocol == 'Uniswap V4':
                        # Uniswap V4 Pool ID derivation:
                        # keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))
                        tick_spacing = 10 if fee_val <= 500 else 60
                        payload = (
                            b'\x00'*12 + t0_bytes +
                            b'\x00'*12 + t1_bytes +
                            b'\x00'*29 + fee_val.to_bytes(3, 'big') +
                            b'\x00'*29 + tick_spacing.to_bytes(3, 'big') +
                            b'\x00'*32 # Hooks address is 0x0000... padded to 32 bytes
                        )
                        pool_addr = '0x' + keccak.new(digest_bits=256, data=payload).hexdigest()
                    else:
                        # V3 style Create2 address derivation
                        if 'pancake' in protocol.lower():
                            # PancakeSwap V3 uses a separate Pool Deployer for CREATE2 (not the Factory)
                            factory_hex = '0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9'
                            init_hash_hex = '0x6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2'
                        else:
                            factory_hex = '0x1F98431c8aD98523631AE4a59f267346ea31F984'
                            init_hash_hex = '0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54'
                            
                        payload = (
                            b'\x00'*12 + t0_bytes +
                            b'\x00'*12 + t1_bytes +
                            b'\x00'*29 + fee_val.to_bytes(3, 'big')
                        )
                        salt = keccak.new(digest_bits=256, data=payload).digest()
                        factory = bytes.fromhex(factory_hex[2:])
                        init_hash = bytes.fromhex(init_hash_hex[2:])
                        
                        create2_input = b'\xff' + factory + salt + init_hash
                        pool_addr = '0x' + keccak.new(digest_bits=256, data=create2_input).digest()[-20:].hex()
                        
                    pool_addresses[f"{t0}-{t1}-{fee}"] = pool_addr
            except Exception as e:
                print(f"Error computing pool addresses dynamically: {e}")
            
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
                    
                    key = f"{t0}-{t1}-{fee}"
                    apr_val = aprs.get(key)
                    
                    # Also try reversed key just in case
                    if apr_val is None:
                        apr_val = aprs.get(f"{t1}-{t0}-{fee}")
                    
                    pool_addr = pool_addresses.get(key) or pool_addresses.get(f"{t1}-{t0}-{fee}")
                    
                    # Replace string fee with object
                    new_path.append({
                        'fee': fee,
                        'apr': apr_val if apr_val is not None else 0.0,
                        'apr_str': f"{apr_val:.2%}" if apr_val is not None else "N/A",
                        'pool_address': pool_addr
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
                route_apr = leg_aprs[0] if leg_aprs else 0.0
                apr_str = f"{route_apr:.2%}" if route_apr > 0 else "0.0%"
            
            # Determine route-level network from path fee node
            route_network = "Ethereum"
            for p in pool_nodes:
                if 'fee' in p:
                    fee_parts = p['fee'].split('|')
                    if len(fee_parts) >= 3:
                        route_network = fee_parts[2]
                        break
            
            analysis['routes'][route_idx]['path_tokens'] = new_path
            analysis['routes'][route_idx]['apr'] = route_apr
            analysis['routes'][route_idx]['apr_str'] = apr_str
            analysis['routes'][route_idx]['network'] = route_network

        return analysis
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/routes/date-range", tags=["Route Analytics"])
async def get_date_range():
    """Get the available date range from the swap data."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(timestamp)::date, MAX(timestamp)::date FROM (
                SELECT timestamp FROM uniswap_v3_swaps
                UNION ALL
                SELECT timestamp FROM uniswap_v4_swaps
            ) as all_swaps
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        
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
                price_lower, price_upper, current_price, in_range, fee_tier, NULL as pool_id
            FROM v_lp_snapshots_summary
            WHERE LOWER(address) IN ({addr_placeholders})
            ORDER BY timestamp DESC
            """
            cur.execute(query_latest, target_addresses)
        else:
            query_latest = """
            SELECT 
                id, timestamp, address, protocol, network, position_label, balance_usd,
                assets, unclaimed, images, total_unclaimed_usd, position_key,
                token_id, tick_lower, tick_upper, current_tick,
                price_lower, price_upper, current_price, in_range, fee_tier, NULL as pool_id
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
                        lp.id AS pool_id
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
        JOIN coin c0 ON pool.coin0_symbol = c0.symbol
        JOIN coin c1 ON pool.coin1_symbol = c1.symbol
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
            pool.coin0_symbol,
            pool.coin1_symbol,
            pool.network
        FROM liquidity_pool_position_event e
        JOIN liquidity_pool_position pos ON e.position_id = pos.id
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
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
        
        # We join with coin table because history is now linked via address
        query = """
        SELECT h.timestamp, h.price 
        FROM coin_price_history h
        JOIN coin c ON h.address = c.ethereum_address
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
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT symbol, name, image_url as image, cmc_rank as market_cap_rank
        FROM coin
        ORDER BY cmc_rank ASC NULLS LAST;
        """
        cur.execute(query)
        colnames = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        
        coins = [dict(zip(colnames, row)) for row in rows]
        
        cur.close()
        conn.close()
        return coins
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pools", tags=["Liquidity Pools"])
async def list_pools():
    """List all available liquidity pools with latest stats."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT 
            p.id, p.network, p.protocol, p.pool_name, p.fee_tier, p.pool_address,
            h.tvl_usd, h.volume_usd, h.tx_count, p.coin0_symbol, p.coin1_symbol
        FROM liquidity_pool p
        LEFT JOIN (
            SELECT DISTINCT ON (pool_id) pool_id, tvl_usd, volume_usd, tx_count
            FROM liquidity_pool_history
            ORDER BY pool_id, date DESC
        ) h ON p.id = h.pool_id
        WHERE p.reverted = FALSE OR p.protocol IN ('Uniswap V3', 'Uniswap V4', 'PancakeSwap V3') -- Show all V3/V4 pools even if reverted, to avoid gaps
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
            
        cur.close()
        conn.close()
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
        cur.execute("SELECT network, pool_address, protocol, coin0_symbol, coin1_symbol, fee_tier FROM liquidity_pool WHERE id = %s", (pool_id,))
        pool_res = cur.fetchone()
        if not pool_res:
            raise HTTPException(status_code=404, detail="Pool not found")
        
        network, pool_address, protocol, c0, c1, fee = pool_res
        
        # 2. Attempt to resolve address if missing
        if not pool_address:
            print(f"Pool address missing for ID {pool_id}. Attempting to resolve...")
            # Fetch token addresses
            cur.execute("SELECT symbol, ethereum_address FROM coin WHERE symbol IN (%s, %s)", (c0, c1))
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
        
        query = """
        SELECT 
            cmc_id, symbol, name, price, price_timestamp,
            percent_change_1h, percent_change_24h, percent_change_7d,
            market_cap, tvl, cmc_rank, image_url, ethereum_address
        FROM coin
        WHERE cmc_id = ANY(%s)
        """
        cur.execute(query, (cmc_ids,))
        rows = cur.fetchall()
        
        # Build response keyed by CMC ID
        data = {}
        for row in rows:
            cmc_id_val = row[0]
            data[str(cmc_id_val)] = {
                "cmc_id": cmc_id_val,
                "symbol": row[1],
                "name": row[2],
                "price": float(row[3]) if row[3] is not None else None,
                "price_timestamp": row[4].isoformat() if row[4] is not None else None,
                "percent_change_1h": float(row[5]) if row[5] is not None else None,
                "percent_change_24h": float(row[6]) if row[6] is not None else None,
                "percent_change_7d": float(row[7]) if row[7] is not None else None,
                "market_cap": float(row[8]) if row[8] is not None else None,
                "tvl": float(row[9]) if row[9] is not None else None,
                "cmc_rank": row[10],
                "image_url": row[11],
                "ethereum_address": row[12]
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
