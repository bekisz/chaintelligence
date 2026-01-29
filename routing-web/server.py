import sys
import os
import base64
import secrets
import psycopg2
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

# Add routing directory to path to import fetcher and analyzer
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(ROOT_DIR, 'routing'))

try:
    from postgres_fetcher import PostgresFetcher
    from route_analyzer import RouteAnalyzer
    from config import DATA_WAREHOUSE_DB
except ImportError as e:
    print(f"Error importing routing modules: {e}")
    sys.exit(1)

app = FastAPI(
    title="Chaintelligence Portal API",
    description="Secure API for Chaintelligence DeFi analytics platform.",
    version="1.1.0"
)

# --- Authentication Middleware ---
PORTAL_USER = os.getenv("PORTAL_USERNAME", "admin")
PORTAL_PASS = os.getenv("PORTAL_PASSWORD", "chaintelligence")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
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

@app.get("/api/analyze", tags=["Analytics"])
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

        fetcher = PostgresFetcher(verbose=True)
        swaps = fetcher.fetch_swaps(start_dt, end_dt)
        if not swaps:
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

        analyzer = RouteAnalyzer(verbose=True)
        analysis = analyzer.analyze_routes(swaps, start_token, end_token)
        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/lp-summary", tags=["Portfolio"])
async def lp_summary():
    """Get the latest summary of LP snapshots."""
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT 
            id, timestamp, address, protocol, network, position_label, balance_usd,
            assets, unclaimed, images, total_unclaimed_usd, position_key
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
                "images": latest[9]
            })
            
        results.sort(key=lambda x: x["balance_usd"], reverse=True)
        cur.close()
        conn.close()
        return results
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
