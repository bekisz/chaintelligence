import sys
import os
import base64
import secrets
import psycopg2
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.responses import FileResponse
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WEB_DIR = os.path.join(ROOT_DIR, 'web')
STATIC_DIR = os.path.join(WEB_DIR, 'static')
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# Import routing logic from chain-feeder
CHAIN_FEEDER_ROUTING = os.path.join(ROOT_DIR, 'chain-feeder', 'routing')
if CHAIN_FEEDER_ROUTING not in sys.path:
    sys.path.insert(0, CHAIN_FEEDER_ROUTING)

try:
    from postgres_fetcher import PostgresFetcher
    from route_analyzer import RouteAnalyzer
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
    exempt_paths = ["/api/coin/list", "/api/coin/price-history", "/backtester", "/favicon.ico", "/static"]
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
    end_date: Optional[str] = Query(None, description="ISO format end date")
):
    """Analyze swap routes between two tokens."""
    try:
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if end_date else now
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
        BATCH_DAYS = 5
        current_chunk_start = start_dt
        has_data = False
        
        while current_chunk_start < end_dt:
            # Calculate chunk end
            chunk_end = current_chunk_start + timedelta(days=BATCH_DAYS)
            if chunk_end > end_dt:
                chunk_end = end_dt
                
            # Fetch Batch
            print(f"[Anaylsis] Processing batch: {current_chunk_start} -> {chunk_end}")
            batch_swaps = fetcher.fetch_swaps(current_chunk_start, chunk_end)
            
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
            cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM uniswap_v3_swaps")
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
        if pools_to_fetch:
            aprs = fetcher.fetch_pool_stats(list(pools_to_fetch), start_dt, end_dt)
            
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
                        
                        # Normalize for lookup (matches PostgresFetcher.fetch_pool_stats)
                        t0_norm = t0.upper()
                        t1_norm = t1.upper()
                        key = f"{t0_norm}-{t1_norm}-{fee}"
                        apr_val = aprs.get(key)
                        
                        # Replace string fee with object
                        new_path.append({
                            'fee': fee,
                            'apr': apr_val if apr_val is not None else 0.0,
                            'apr_str': f"{apr_val:.1%}" if apr_val is not None else 'N/A'
                        })
                    else:
                        new_path.append(item)
                
                analysis['routes'][route_idx]['path_tokens'] = new_path

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
        cur.execute("SELECT MIN(timestamp)::date, MAX(timestamp)::date FROM uniswap_v3_swaps")
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
        
        # 1. Fetch latest state + metadata from view (or join)
        # We still use the view for the base data, but we'll enrich it with APRs
        query_latest = """
        SELECT 
            id, timestamp, address, protocol, network, position_label, balance_usd,
            assets, unclaimed, images, total_unclaimed_usd, position_key,
            token_id, tick_lower, tick_upper, current_tick,
            price_lower, price_upper, current_price, in_range, fee_tier
        FROM v_lp_snapshots_summary
        ORDER BY timestamp DESC
        """
        cur.execute(query_latest)
        all_rows = cur.fetchall()
        
        # Group by position_key to find the LATEST row for each position
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
            # ... (Existing extraction code)
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
        
        # We allow matching on symbol case-insensitively
        query = """
        SELECT timestamp, price FROM coin_price_history
        WHERE UPPER(symbol) = %s
        ORDER BY timestamp ASC
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


@app.get("/api/coin/list", tags=["Assets"])
async def get_coins():
    """Get list of active indexed coins for the backtester."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT symbol, name, image_url as image, cmc_rank as market_cap_rank
        FROM coin
        ORDER BY cmc_rank ASC NULLS LAST
        LIMIT 1000;
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


@app.get("/api/assets/price-by-cmc-id", tags=["Assets"])
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "error_code": 0,
                "error_message": None,
                "total_count": len(data)
            }
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
