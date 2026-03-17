
import psycopg2
import os
from datetime import datetime, timedelta

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def test_apr_calc():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Pool 1748: EURC-EURCV 0.01% V3
    t0, t1, fee = 'EURC', 'EURCV', '0.01%|v3'
    t0_sym, t1_sym = t0.upper(), t1.upper()
    fee_db = '100'
    
    start_date = datetime.now() - timedelta(days=30)
    end_date = datetime.now()
    
    # 1. Fetch aggregated stats
    query = """
    SELECT 
        SUM(h.volume_usd),
        AVG(h.tvl_usd)
    FROM liquidity_pool_history h
    JOIN liquidity_pool p ON h.pool_id = p.id
    WHERE 
        h.date >= %s::date AND h.date <= %s::date
        AND p.fee_tier = %s
        AND p.protocol = 'Uniswap V3'
        AND (
            (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
            OR 
            (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
        )
    """
    cur.execute(query, (start_date, end_date, fee_db, t0_sym, t1_sym, t1_sym, t0_sym))
    row = cur.fetchone()
    total_vol = float(row[0]) if row and row[0] else 0
    avg_tvl = float(row[1]) if row and row[1] else 0
    
    print(f"Stats from history: Vol={total_vol}, TVL={avg_tvl}")
    
    if total_vol == 0 and avg_tvl > 0:
        print("Entering fallback volume calculation...")
        # Fallback Volume Calc
        swap_query = """
            SELECT token0_symbol, token1_symbol, SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1)) FROM (
                SELECT amount_usd, amount0, amount1, timestamp, token0_symbol, token1_symbol, fee_tier, 'Uniswap V3' as protocol FROM uniswap_v3_swaps
                UNION ALL
                SELECT amount_usd, amount0, amount1, timestamp, token0_symbol, token1_symbol, fee_tier, 'Uniswap V4' as protocol FROM uniswap_v4_swaps
            ) as all_swaps
            WHERE timestamp >= %s AND timestamp <= %s
            AND protocol = 'Uniswap V3'
            AND (
                (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
                OR 
                (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
            )
            AND fee_tier = '0.01%%'
            GROUP BY token0_symbol, token1_symbol
        """
        params = [start_date, end_date, t0_sym, t1_sym, t1_sym, t0_sym]
        cur.execute(swap_query, params)
        
        total_fallback_vol = 0
        prices = {} # empty as in main.py
        
        for sw_row in cur.fetchall():
            print(f"Found swap row: {sw_row}")
            t0_row_sym = sw_row[0]
            t1_row_sym = sw_row[1]
            usd_sum = float(sw_row[2]) if sw_row[2] else 0
            amt0_sum = float(sw_row[3]) if sw_row[3] else 0
            amt1_sum = float(sw_row[4]) if sw_row[4] else 0
            
            if usd_sum > 0:
                total_fallback_vol += usd_sum
                print(f"Adding USD sum: {usd_sum}")
            else:
                p0 = prices.get(t0_row_sym)
                p1 = prices.get(t1_row_sym)
                if p0 is None and any(x in t0_row_sym.upper() for x in ['USD', 'EUR']): p0 = 1.0
                if p1 is None and any(x in t1_row_sym.upper() for x in ['USD', 'EUR']): p1 = 1.0
                p0 = p0 or 0
                p1 = p1 or 0
                calc_vol = (amt0_sum * p0 + amt1_sum * p1) / 2.0
                total_fallback_vol += calc_vol
                print(f"Fallback heuristic for {t0_row_sym}-{t1_row_sym}: P0={p0}, P1={p1}, Vol={calc_vol}")
        
        total_vol = total_fallback_vol
        print(f"Total Fallback Vol: {total_vol}")
    
    if avg_tvl > 0:
        fee_rate = float(fee_db) / 1000000.0 
        fees_earned = total_vol * fee_rate
        days = (end_date - start_date).days
        if days < 1: days = 1
        apr = (fees_earned / avg_tvl) * (365.0 / days)
        print(f"Fees Earned: {fees_earned}")
        print(f"APR: {apr:.4%} ( {apr*100:.2f}% )")
    else:
        print("No TVL, cannot calc APR")

    cur.close()
    conn.close()

if __name__ == "__main__":
    test_apr_calc()
