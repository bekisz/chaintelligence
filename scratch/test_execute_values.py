import sys, os
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(os.path.abspath('chain-feeder/routing'))
from postgres_fetcher import PostgresFetcher
from config import DATA_WAREHOUSE_DB

def fetch_pool_stats_fast(pools, start_date, end_date):
    if not pools: return {}
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    
    # Prepare values
    values = []
    for p in pools:
        t0, t1, fee_raw_full = p
        t0_sym, t1_sym = t0.upper(), t1.upper()
        fee_raw = str(fee_raw_full).split('|')[0].strip()
        f_clean = fee_raw.replace('%', '').strip()
        
        network = "Ethereum"
        parts = str(fee_raw_full).split('|')
        if len(parts) >= 3: network = parts[2].strip()

        protocol = "Uniswap V3"
        if len(parts) >= 2:
            proto_raw = parts[1].strip()
            if proto_raw.lower() in ('v3', 'uniswap v3'): protocol = 'Uniswap V3'
            elif proto_raw.lower() in ('v4', 'uniswap v4'): protocol = 'Uniswap V4'
            else: protocol = proto_raw
            
        # We don't know exact fee variant in DB, so we can try the percentage format and basis points format
        fee_pct = f"{float(f_clean):g}%" if f_clean.replace('.','').isdigit() else f_clean
        fee_bips = str(int(float(f_clean) * 10000)) if f_clean.replace('.','').isdigit() else f_clean
        
        values.append((network, protocol, t0_sym, t1_sym, fee_pct))
        values.append((network, protocol, t0_sym, t1_sym, fee_bips))
        
    query = """
    SELECT v.network, v.protocol, v.coin0, v.coin1, v.fee,
           SUM(ABS(h.volume_usd)), AVG(ABS(h.tvl_usd))
    FROM (VALUES %s) AS v(network, protocol, coin0, coin1, fee)
    JOIN liquidity_pool p ON 
        p.network = v.network AND p.protocol = v.protocol AND p.fee_tier = v.fee
        AND (
            (UPPER(p.coin0_symbol) = v.coin0 AND UPPER(p.coin1_symbol) = v.coin1) OR
            (UPPER(p.coin0_symbol) = v.coin1 AND UPPER(p.coin1_symbol) = v.coin0)
        )
    LEFT JOIN liquidity_pool_history h ON h.pool_id = p.id AND h.date >= %s::date AND h.date <= %s::date
    GROUP BY v.network, v.protocol, v.coin0, v.coin1, v.fee
    """
    execute_values(cur, query, values, template=None, page_size=100)
    # Actually wait, execute_values doesn't easily let me append the %s arguments for dates.
    # It's easier to just format the query:
    pass

