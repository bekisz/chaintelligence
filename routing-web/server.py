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
    exempt_paths = ["/api/coin/list", "/api/coin/price-history", "/backtester"]
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
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse('static/favicon.png')

@app.get("/backtester/config.js", include_in_schema=False)
async def get_backtester_config():
    """Dynamically serve CryptoCompare API key to the backtester."""
    api_key = os.getenv("CRYPTOCOMPARE_API_KEY", "")
    content = f"const CONFIG = {{ CRYPTOCOMPARE_API_KEY: '{api_key}' }};\n"
    if "typeof module !== 'undefined'" not in content: # Just to be safe with format
         content += "if (typeof module !== 'undefined') { module.exports = CONFIG; }\n"
    return Response(content=content, media_type="application/javascript")

# Serve LP Backtester as a separate static site
BACKTESTER_DIR = os.path.join(ROOT_DIR, 'lp-backtester')
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
    """Get the latest summary of LP snapshots."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT 
            id, timestamp, address, protocol, network, position_label, balance_usd,
            assets, unclaimed, images, total_unclaimed_usd, position_key,
            token_id, tick_lower, tick_upper, current_tick,
            price_lower, price_upper, current_price, in_range, fee_tier
        FROM v_lp_snapshots_summary
        ORDER BY timestamp DESC
        LIMIT 200
        """
        cur.execute(query)
        rows = cur.fetchall()
        
        pos_history = {}
        for row in rows:
            key = row[11] if row[11] else f"{row[3]}-{row[5]}-{row[4]}"
            if key not in pos_history:
                pos_history[key] = []
            pos_history[key].append(row)

        results = []
        for key, snapshots in pos_history.items():
            latest = snapshots[0]
            assets = latest[7] if latest[7] else []
            unclaimed = latest[8] if latest[8] else []
            
            delta_usd = 0
            if len(snapshots) > 1:
                previous = snapshots[1]
                prev_unclaimed_usd = float(previous[10]) if previous[10] else 0
                latest_unclaimed_usd = float(latest[10]) if latest[10] else 0
                delta_usd = latest_unclaimed_usd - prev_unclaimed_usd

            results.append({
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
                "reward_delta_usd": delta_usd,
                "images": latest[9],
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
                } if latest[12] else None  # Only include if token_id exists
            })
            
        results.sort(key=lambda x: x["balance_usd"], reverse=True)
        cur.close()
        conn.close()
        return results
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


# UI Routes (Excluded from Swagger schema)
@app.get("/", include_in_schema=False)
async def read_index():
    return FileResponse('static/index.html')

@app.get("/routing", include_in_schema=False)
async def read_routing():
    return FileResponse('static/routing.html')

@app.get("/lp", include_in_schema=False)
async def read_lp():
    return FileResponse('static/lp.html')

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return FileResponse('static/api.html')

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
