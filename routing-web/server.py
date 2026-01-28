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
        
        # Query to get snapshots grouped by position to calculate deltas
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
        
        # Organize by position to find latest two for delta
        pos_history = {}
        for row in rows:
            key = row[11] if row[11] else f"{row[3]}-{row[5]}-{row[4]}" # Use position_key, fallback to old key
            if key not in pos_history:
                pos_history[key] = []
            pos_history[key].append(row)

        results = []
        # Keep only the latest snapshot for each position for the response, but include delta
        for key, snapshots in pos_history.items():
            latest = snapshots[0]
            
            # Use raw assets/unclaimed
            assets = latest[7] if latest[7] else []
            unclaimed = latest[8] if latest[8] else []
            
            # Get delta from previous snapshot if it exists
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
            
        # Re-sort results by balance_usd descending
        results.sort(key=lambda x: x["balance_usd"], reverse=True)

            
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
