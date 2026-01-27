import sys
import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add routing directory to path to import fetcher and analyzer
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'routing')))

try:
    from postgres_fetcher import PostgresFetcher
    from route_analyzer import RouteAnalyzer
    from config import DATA_WAREHOUSE_DB
except ImportError as e:
    print(f"Error importing routing modules: {e}")
    # Fallback or alternative import logic if needed
    sys.exit(1)

app = FastAPI(title="Uniswap V3 Route Analysis API")

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

class AnalysisRequest(BaseModel):
    start_token: str
    end_token: str
    days: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

@app.get("/api/analyze")
async def analyze(
    start_token: str,
    end_token: str,
    days: Optional[float] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    try:
        # Determine date range
        now = datetime.now()
        if days is not None:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
        elif start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if end_date else now
        else:
            # Default to 1 day if nothing specified
            end_dt = now
            start_dt = end_dt - timedelta(days=1)

        fetcher = PostgresFetcher(verbose=True)
        # Fetch swaps (we fetch all to captured hops, but PostgresFetcher.fetch_swaps can filter)
        # However, for route analysis, we need ALL swaps in the period to reconstruct paths properly
        # if they involve intermediate tokens not in our primary tokens list.
        # But PostgresFetcher.fetch_swaps uses TOKEN_ADDRESSES from config by default.
        
        # For simple routes (A->B), we need swaps for both.
        # Let's fetch all swaps in the interval for any token in config if no filter passed.
        swaps = fetcher.fetch_swaps(start_dt, end_dt)
        
        if not swaps:
            return {"routes": [], "total_tx": 0, "total_volume": 0}

        analyzer = RouteAnalyzer(verbose=True)
        analysis = analyzer.analyze_routes(swaps, start_token, end_token)
        
        return analysis

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/lp-summary")
async def lp_summary():
    try:
        import psycopg2
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        query = """
        SELECT 
            id, timestamp, address, protocol, network, position_label, balance_usd,
            asset0_symbol, asset0_balance, asset0_usd,
            asset1_symbol, asset1_balance, asset1_usd,
            unclaimed_asset0_balance, unclaimed_asset1_balance,
            total_unclaimed_usd,
            images
        FROM v_lp_snapshots_summary
        ORDER BY timestamp DESC
        LIMIT 50
        """
        
        cur.execute(query)
        rows = cur.fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "timestamp": row[1].isoformat(),
                "address": row[2],
                "protocol": row[3],
                "network": row[4],
                "position_label": row[5],
                "balance_usd": float(row[6]) if row[6] else 0,
                "assets": [
                    {"symbol": row[7], "balance": float(row[8]) if row[8] else 0, "usd": float(row[9]) if row[9] else 0},
                    {"symbol": row[10], "balance": float(row[11]) if row[11] else 0, "usd": float(row[12]) if row[12] else 0}
                ],
                "unclaimed": {
                    row[7]: float(row[13]) if row[13] else 0,
                    row[10]: float(row[14]) if row[14] else 0
                },
                "total_unclaimed_usd": float(row[15]) if row[15] else 0,
                "images": row[16]
            })
            
        cur.close()
        conn.close()
        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def read_index():
    from fastapi.responses import FileResponse
    return FileResponse('static/index.html')

@app.get("/lp")
async def read_lp():
    from fastapi.responses import FileResponse
    return FileResponse('static/lp.html')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
