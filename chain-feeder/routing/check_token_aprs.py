import psycopg2
from datetime import datetime, timedelta
from tabulate import tabulate
import sys
import os

# Ensure we can import from the current directory if needed
sys.path.insert(0, os.path.dirname(__file__))

DB_CONFIG = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5433')

def format_usd(amount):
    return f"${amount:,.2f}"

def format_pct(amount):
    return f"{amount:.2%}"

def get_token_aprs(symbols):
    try:
        conn = psycopg2.connect(DB_CONFIG)
        cur = conn.cursor()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return []
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    
    results = []
    
    for symbol in symbols:
        # 1. Find all pools for this token (from metadata table)
        query_pools = """
        SELECT id, coin0_symbol, coin1_symbol, fee_tier
        FROM liquidity_pool
        WHERE UPPER(coin0_symbol) = %s OR UPPER(coin1_symbol) = %s
        """
        cur.execute(query_pools, (symbol.upper(), symbol.upper()))
        pools = cur.fetchall()
        
        token_pools_stats = []
        for pool_id, c0, c1, fee_tier in pools:
            # 2. Get history stats
            cur.execute("""
                SELECT SUM(volume_usd), AVG(tvl_usd), COUNT(*)
                FROM liquidity_pool_history
                WHERE pool_id = %s AND date >= %s::date AND date <= %s::date
            """, (pool_id, start_date, end_date))
            hist_row = cur.fetchone()
            
            hist_vol = float(hist_row[0]) if hist_row[0] else 0.0
            hist_tvl = float(hist_row[1]) if hist_row[1] else 0.0
            days_count = hist_row[2] if hist_row[2] else 0
            
            # 3. Fallback volume from swaps table
            if hist_vol == 0:
                fee_map = {
                    '100': '0.01%',
                    '500': '0.05%',
                    '3000': '0.3%',
                    '10000': '1.0%'
                }
                fee_pct_str = fee_map.get(str(fee_tier), f"{float(fee_tier)/10000}%")
                
                cur.execute("""
                    SELECT SUM(amount_usd)
                    FROM uniswap_v3_swaps
                    WHERE timestamp >= %s AND timestamp <= %s
                    AND (
                        (token0_symbol = %s AND token1_symbol = %s)
                        OR 
                        (token0_symbol = %s AND token1_symbol = %s)
                    )
                    AND fee_tier = %s
                """, (start_date, end_date, c0, c1, c1, c0, fee_pct_str))
                swap_row = cur.fetchone()
                if swap_row and swap_row[0]:
                    hist_vol = float(swap_row[0])

            apr = 0.0
            if hist_tvl > 0:
                fee_rate = float(fee_tier) / 1000000.0
                fees_earned = hist_vol * fee_rate
                # Use actual days if available, otherwise assume 7.0 for 7d volume
                lookback_days = float(days_count) if days_count > 0 else 7.0
                apr = (fees_earned / hist_tvl) * (365.0 / lookback_days)
            
            other_token = c1 if c0.upper() == symbol.upper() else c0
            token_pools_stats.append({
                'pair': f"{symbol.upper()}-{other_token.upper()}",
                'fee': f"{float(fee_tier)/10000:.2f}%",
                'volume_7d': hist_vol,
                'tvl_avg': hist_tvl if hist_tvl > 0 else None,
                'apr': apr if hist_tvl > 0 else None
            })
        
        # Filter to pools with either volume or TVL
        token_pools_stats = [p for p in token_pools_stats if p['volume_7d'] > 0 or p['tvl_avg'] is not None]

        if token_pools_stats:
            # Aggregate for the token (weighted by TVL where available)
            pools_with_tvl = [p for p in token_pools_stats if p['tvl_avg'] is not None]
            if pools_with_tvl:
                total_tvl = sum(p['tvl_avg'] for p in pools_with_tvl)
                weighted_apr = sum(p['apr'] * p['tvl_avg'] for p in pools_with_tvl) / total_tvl
            else:
                total_tvl = 0
                weighted_apr = 0
            
            # Sort individual pools by volume (since TVL might be N/A)
            token_pools_stats.sort(key=lambda x: x['volume_7d'], reverse=True)
            
            results.append({
                'token': symbol.upper(),
                'avg_apr': weighted_apr,
                'total_tvl': total_tvl,
                'pools': token_pools_stats
            })
        else:
            results.append({
                'token': symbol.upper(),
                'avg_apr': 0,
                'total_tvl': 0,
                'pools': []
            })
            
    cur.close()
    conn.close()
    return results

def main():
    tokens = ['EURC', 'EURCV', 'SPYon', 'SLVon', 'PAXG', 'xAUt', 'GHO']
    if len(sys.argv) > 1:
        tokens = sys.argv[1:]
        
    print(f"\n{'='*100}")
    print(f"Average APR Analysis for Routing Tokens (7-day window)")
    print(f"Target Tokens: {', '.join(tokens)}")
    print(f"{'='*100}\n")
    
    results = get_token_aprs(tokens)
    
    summary_table = []
    for r in results:
        summary_table.append([
            r['token'],
            format_pct(r['avg_apr']) if r['total_tvl'] > 0 else "N/A",
            format_usd(r['total_tvl']) if r['total_tvl'] > 0 else "N/A",
            len(r['pools'])
        ])
    
    print("SUMMARY")
    print(tabulate(summary_table, headers=['Token', 'Weighted Avg APR', 'Total TVL', 'Active Pools'], tablefmt='grid'))
    print("\n")
    
    for r in results:
        if not r['pools']:
            continue
            
        print(f"DEEP DIVE: {r['token']}")
        print("-" * 50)
        
        pool_table = []
        for p in r['pools']:
            pool_table.append([
                p['pair'],
                p['fee'],
                format_usd(p['volume_7d']),
                format_usd(p['tvl_avg']) if p['tvl_avg'] else 'N/A',
                format_pct(p['apr']) if p['apr'] is not None else 'N/A'
            ])
            
        print(tabulate(pool_table, headers=['Pair', 'Fee', '7d Volume', 'Avg TVL', 'APR'], tablefmt='simple'))
        print("\n")

if __name__ == '__main__':
    main()
