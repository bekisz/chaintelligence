import sys, os
from datetime import datetime, timedelta
import psycopg2
import time

sys.path.append(os.path.abspath('chain-feeder/routing'))
from postgres_fetcher import PostgresFetcher
from config import DATA_WAREHOUSE_DB

def fetch_pool_stats_union(pools, start_date, end_date, prices=None):
    if not pools: return {}
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        results = {}
        
        # 1. Prepare parameters
        pool_queries_history = []
        params_history = []
        
        pool_meta = {} # key -> dict of meta
        
        for p in pools:
            t0, t1, fee_raw_full = p
            t0_sym, t1_sym = t0.upper(), t1.upper()
            fee_raw = str(fee_raw_full).split('|')[0].strip()
            
            f_str = fee_raw.replace('%', '').strip()
            fee_db = 'Dynamic' if (f_str == 'Dynamic' or (f_str.replace('.','').isdigit() and float(f_str) > 100)) else f_str
            if fee_db != 'Dynamic':
                fee_map = {'0.01': '100', '0.05': '500', '0.08': '800', '0.3': '3000', '1.0': '10000'}
                fee_db = fee_map.get(fee_db, fee_db)
                if fee_db.isdigit() and 0 < float(fee_db) < 5: fee_db = str(int(float(fee_db)*10000))
                elif not fee_db.isdigit(): fee_db = str(int(float(fee_db))) if fee_db.replace('.','').isdigit() else fee_db

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

            fee_variants = [fee_db, f_clean, fee_raw]
            try:
                if fee_db.isdigit():
                    val = float(fee_db) / 10000.0
                    fee_variants.append(f"{val:g}%")
                    fee_variants.append(f"{val:g}")
            except: pass
            fee_variants = list(set([v for v in fee_variants if v]))
            
            key = f"{t0}-{t1}-{fee_raw_full}"
            pool_meta[key] = {
                't0_sym': t0_sym, 't1_sym': t1_sym, 'fee_db': fee_db, 'network': network, 'protocol': protocol, 'fee_variants': fee_variants, 'fee_raw_full': fee_raw_full
            }
            
            pool_queries_history.append("""
            SELECT %s, SUM(ABS(h.volume_usd)), AVG(ABS(h.tvl_usd))
            FROM liquidity_pool_history h
            JOIN liquidity_pool p ON h.pool_id = p.id
            WHERE h.date >= %s::date AND h.date <= %s::date
            AND p.fee_tier = ANY(%s) AND p.network = %s AND p.protocol = %s
            AND ((UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s) OR (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s))
            """)
            params_history.extend([key, start_date, end_date, fee_variants, network, protocol, t0_sym, t1_sym, t1_sym, t0_sym])
            
        # Execute history
        if pool_queries_history:
            cur.execute(" UNION ALL ".join(pool_queries_history), tuple(params_history))
            for row in cur.fetchall():
                k = row[0]
                pool_meta[k]['total_vol'] = float(row[1] or 0)
                pool_meta[k]['avg_tvl'] = float(row[2] or 0)
        
        # 2. TVL Fallback
        pool_queries_tvl = []
        params_tvl = []
        for k, meta in pool_meta.items():
            if meta.get('avg_tvl', 0) == 0:
                pool_queries_tvl.append("""
                SELECT %s, h.tvl_usd
                FROM liquidity_pool_history h
                JOIN liquidity_pool p ON h.pool_id = p.id
                WHERE p.fee_tier = ANY(%s) AND p.network = %s AND p.protocol = %s AND h.tvl_usd != 0
                AND ((UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s) OR (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s))
                ORDER BY h.date DESC LIMIT 1
                """)
                params_tvl.extend([k, meta['fee_variants'], meta['network'], meta['protocol'], meta['t0_sym'], meta['t1_sym'], meta['t1_sym'], meta['t0_sym']])
        if pool_queries_tvl:
            # We cannot UNION ALL queries containing ORDER BY / LIMIT without wrapping them in parens
            wrapped = ["(" + q + ")" for q in pool_queries_tvl]
            cur.execute(" UNION ALL ".join(wrapped), tuple(params_tvl))
            for row in cur.fetchall():
                k = row[0]
                pool_meta[k]['avg_tvl'] = abs(float(row[1] or 0))
                
        # 3. Volume Fallback
        pool_queries_swaps = []
        params_swaps = []
        for k, meta in pool_meta.items():
            if meta.get('total_vol', 0) == 0:
                fee_pct = str(meta['fee_raw_full']).split('|')[0]
                fee_db = meta['fee_db']
                if fee_db == 'Dynamic':
                    fee_tier_pct, fee_tier_bips = 'Dynamic', 'Dynamic'
                else:
                    fee_tier_pct = fee_pct if '%' in fee_pct else {'100': '0.01%', '500': '0.05%', '800': '0.08%', '3000': '0.3%', '10000': '1.0%'}.get(fee_db, fee_pct)
                    fee_tier_bips = fee_db if fee_db.isdigit() else str(int(float(fee_pct.strip('%')) * 10000))
                
                target_table = "uniswap_v4_swaps" if '|v4' in str(meta['fee_raw_full']).lower() else "uniswap_v3_swaps"
                pool_queries_swaps.append(f"""
                SELECT %s, token0_symbol, token1_symbol, SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1))
                FROM {target_table}
                WHERE timestamp >= %s AND timestamp <= %s AND network = %s AND protocol = %s
                AND ((token0_symbol = %s AND token1_symbol = %s) OR (token0_symbol = %s AND token1_symbol = %s))
                AND (fee_tier = %s OR fee_tier = %s)
                GROUP BY token0_symbol, token1_symbol
                """)
                params_swaps.extend([k, start_date, end_date, meta['network'], meta['protocol'], meta['t0_sym'], meta['t1_sym'], meta['t1_sym'], meta['t0_sym'], fee_tier_pct, fee_tier_bips])
                
        if pool_queries_swaps:
            cur.execute(" UNION ALL ".join(pool_queries_swaps), tuple(params_swaps))
            for row in cur.fetchall():
                k = row[0]
                usd_sum = float(row[3] or 0)
                if usd_sum > 0:
                    pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + usd_sum
                elif prices is not None:
                    p0 = prices.get(row[1]) or (1.0 if any(x in row[1].upper() for x in ['USD','EUR']) else 0)
                    p1 = prices.get(row[2]) or (1.0 if any(x in row[2].upper() for x in ['USD','EUR']) else 0)
                    pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + (float(row[4] or 0)*p0 + float(row[5] or 0)*p1)/2.0

        # Calculate APR
        for k, meta in pool_meta.items():
            avg_tvl = meta.get('avg_tvl', 0)
            total_vol = meta.get('total_vol', 0)
            t0_sym, t1_sym = meta['t0_sym'], meta['t1_sym']
            
            if avg_tvl <= 1.0 and total_vol > 0.0:
                stable_symbols = {'USD', 'USDT', 'USDC', 'DAI', 'EUR', 'EURC', 'BUSD', 'PYUSD', 'USDS'}
                if t0_sym in stable_symbols and t1_sym in stable_symbols: avg_tvl = max(total_vol * 0.5, 1000000.0)
                else: avg_tvl = max(total_vol * 1.2, 200000.0)
                
            apr = None
            if avg_tvl > 1.0:
                try:
                    fee_db = meta['fee_db']
                    if fee_db == 'Dynamic': fee_rate = 0.0002
                    elif '%' in fee_db: fee_rate = float(fee_db.replace('%', '').strip()) / 100.0
                    else: fee_rate = float(fee_db) / 1000000.0
                    
                    fees_earned = total_vol * fee_rate
                    days = max(1, (end_date - start_date).days)
                    apr = (fees_earned / avg_tvl) * (365.0 / days)
                except: pass
                
            if apr is not None:
                results[k] = apr
                t0, t1, f = k.split('-')
                results[f"{t1}-{t0}-{f}"] = apr
                
        cur.close()
        conn.close()
        return results
    except Exception as e:
        print(f"UNION ALL failed: {e}")
        return {}

pools = []
for i in range(50):
    pools.append(['USDT', 'USDC', '0.05%|Uniswap V3|Ethereum'])
    pools.append(['USDT', 'WETH', '0.3%|Uniswap V3|Ethereum'])
    pools.append(['WETH', 'USDC', '0.05%|Uniswap V3|Arbitrum'])
end_dt = datetime.now()
start_dt = end_dt - timedelta(days=7)

fetcher = PostgresFetcher(verbose=False)
prices = fetcher.fetch_latest_prices()

t0 = time.time()
res_orig = fetcher.fetch_pool_stats(pools, start_dt, end_dt, prices)
dur_orig = time.time() - t0

t1 = time.time()
res_union = fetch_pool_stats_union(pools, start_dt, end_dt, prices)
dur_union = time.time() - t1

print(f"Orig: {dur_orig:.2f}s, Union: {dur_union:.2f}s")
if res_orig == res_union:
    print("Match!")
else:
    print("Mismatch!")
