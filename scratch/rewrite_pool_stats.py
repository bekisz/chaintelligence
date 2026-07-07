import sys, os
from datetime import datetime, timedelta
import psycopg2

sys.path.append(os.path.abspath('chain-feeder/routing'))
from postgres_fetcher import PostgresFetcher
from config import DATA_WAREHOUSE_DB

def fetch_pool_stats_bulk(pools, start_date, end_date, prices):
    if not pools: return {}
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        
        results = {}
        unique_symbols = set()
        for p in pools:
            unique_symbols.add(p[0].upper())
            unique_symbols.add(p[1].upper())
            
        usyms = list(unique_symbols)
        if not usyms: return {}
        
        # 1. Bulk Aggregated Stats
        query_history = """
        SELECT 
            p.network, p.protocol, UPPER(p.coin0_symbol), UPPER(p.coin1_symbol), p.fee_tier,
            SUM(ABS(h.volume_usd)),
            AVG(ABS(h.tvl_usd))
        FROM liquidity_pool_history h
        JOIN liquidity_pool p ON h.pool_id = p.id
        WHERE 
            h.date >= %s::date AND h.date <= %s::date
            AND (UPPER(p.coin0_symbol) = ANY(%s) OR UPPER(p.coin1_symbol) = ANY(%s))
        GROUP BY p.network, p.protocol, UPPER(p.coin0_symbol), UPPER(p.coin1_symbol), p.fee_tier
        """
        cur.execute(query_history, (start_date, end_date, usyms, usyms))
        h_map = {}
        for r in cur.fetchall():
            net, prot, c0, c1, fee, vol, tvl = r[0].strip(), r[1].strip(), r[2], r[3], str(r[4]).strip(), float(r[5] or 0), float(r[6] or 0)
            h_map[(net, prot, c0, c1, fee)] = (vol, tvl)
            h_map[(net, prot, c1, c0, fee)] = (vol, tvl)

        # 2. Bulk TVL Fallback
        # Only fetch for pools that have tvl_usd != 0 in recent history
        query_tvl = """
        SELECT DISTINCT ON (p.id)
            p.network, p.protocol, UPPER(p.coin0_symbol), UPPER(p.coin1_symbol), p.fee_tier,
            h.tvl_usd
        FROM liquidity_pool_history h
        JOIN liquidity_pool p ON h.pool_id = p.id
        WHERE h.tvl_usd != 0 
            AND h.date >= %s::date - INTERVAL '30 days'
            AND (UPPER(p.coin0_symbol) = ANY(%s) OR UPPER(p.coin1_symbol) = ANY(%s))
        ORDER BY p.id, h.date DESC
        """
        cur.execute(query_tvl, (start_date, usyms, usyms))
        tvl_map = {}
        for r in cur.fetchall():
            net, prot, c0, c1, fee, tvl = r[0].strip(), r[1].strip(), r[2], r[3], str(r[4]).strip(), abs(float(r[5] or 0))
            tvl_map[(net, prot, c0, c1, fee)] = tvl
            tvl_map[(net, prot, c1, c0, fee)] = tvl

        # 3. Bulk Swap Volume Fallback
        query_swaps = """
        SELECT network, protocol, token0_symbol, token1_symbol, fee_tier, 
               SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1))
        FROM (
            SELECT network, protocol, UPPER(token0_symbol) as token0_symbol, UPPER(token1_symbol) as token1_symbol, fee_tier, amount_usd, amount0, amount1 
            FROM uniswap_v3_swaps 
            WHERE timestamp >= %s AND timestamp <= %s AND (UPPER(token0_symbol) = ANY(%s) OR UPPER(token1_symbol) = ANY(%s))
            UNION ALL
            SELECT network, protocol, UPPER(token0_symbol) as token0_symbol, UPPER(token1_symbol) as token1_symbol, fee_tier, amount_usd, amount0, amount1 
            FROM uniswap_v4_swaps 
            WHERE timestamp >= %s AND timestamp <= %s AND (UPPER(token0_symbol) = ANY(%s) OR UPPER(token1_symbol) = ANY(%s))
        ) as combined_swaps
        GROUP BY network, protocol, token0_symbol, token1_symbol, fee_tier
        """
        cur.execute(query_swaps, (start_date, end_date, usyms, usyms, start_date, end_date, usyms, usyms))
        swap_map = {}
        for r in cur.fetchall():
            net, prot, c0, c1, fee, usd, a0, a1 = r[0].strip(), r[1].strip(), r[2], r[3], str(r[4]).strip(), float(r[5] or 0), float(r[6] or 0), float(r[7] or 0)
            if (net, prot, c0, c1, fee) not in swap_map:
                swap_map[(net, prot, c0, c1, fee)] = []
                swap_map[(net, prot, c1, c0, fee)] = []
            swap_map[(net, prot, c0, c1, fee)].append((usd, a0, a1, c0, c1))
            swap_map[(net, prot, c1, c0, fee)].append((usd, a0, a1, c0, c1))

        # Reconstruct results for each pool
        for p in pools:
            t0, t1, fee_raw_full = p
            t0_sym, t1_sym = t0.upper(), t1.upper()
            fee_raw = str(fee_raw_full).split('|')[0].strip()
            
            def normalize_fee(f):
                f_str = str(f).split('|')[0].replace('%', '').strip()
                if f_str == 'Dynamic': return 'Dynamic'
                fee_map = {'0.01': '100', '0.05': '500', '0.08': '800', '0.3': '3000', '1.0': '10000'}
                if f_str in fee_map: return fee_map[f_str]
                try:
                    val = float(f_str)
                    if val > 100: return 'Dynamic'
                    if val > 0 and val < 5: return str(int(val * 10000))
                    return str(int(val))
                except:
                    return f_str
                    
            fee_db = normalize_fee(fee_raw_full)
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

            total_vol, avg_tvl = 0, 0
            
            # Lookup in history map
            for fv in fee_variants:
                k = (network, protocol, t0_sym, t1_sym, str(fv))
                if k in h_map:
                    total_vol += h_map[k][0]
                    if avg_tvl == 0: avg_tvl = h_map[k][1] # Take first non-zero avg TVL

            # Fallback TVL
            if avg_tvl == 0:
                for fv in fee_variants:
                    k = (network, protocol, t0_sym, t1_sym, str(fv))
                    if k in tvl_map:
                        avg_tvl = tvl_map[k]
                        break

            # Fallback Volume
            if total_vol == 0:
                total_fallback_vol = 0
                fee_pct = str(fee_raw_full).split('|')[0]
                fee_tier_pct = 'Dynamic' if fee_db == 'Dynamic' else (fee_pct if '%' in fee_pct else {'100': '0.01%', '500': '0.05%', '800': '0.08%', '3000': '0.3%', '10000': '1.0%'}.get(fee_db, fee_pct))
                fee_tier_bips = 'Dynamic' if fee_db == 'Dynamic' else (fee_db if fee_db.isdigit() else str(int(float(fee_pct.strip('%')) * 10000)))
                
                for fv in [fee_tier_pct, fee_tier_bips]:
                    k = (network, protocol, t0_sym, t1_sym, str(fv))
                    if k in swap_map:
                        for s_usd, s_a0, s_a1, s_c0, s_c1 in swap_map[k]:
                            if s_usd > 0:
                                total_fallback_vol += s_usd
                            elif prices is not None:
                                p0 = prices.get(s_c0)
                                p1 = prices.get(s_c1)
                                if not p0 and any(x in s_c0 for x in ['USD', 'EUR']): p0 = 1.0
                                if not p1 and any(x in s_c1 for x in ['USD', 'EUR']): p1 = 1.0
                                total_fallback_vol += (s_a0 * (p0 or 0) + s_a1 * (p1 or 0)) / 2.0
                total_vol = total_fallback_vol

            # APR Calculation
            if avg_tvl <= 1.0 and total_vol > 0.0:
                stable_symbols = {'USD', 'USDT', 'USDC', 'DAI', 'EUR', 'EURC', 'BUSD', 'PYUSD', 'USDS'}
                if t0_sym in stable_symbols and t1_sym in stable_symbols:
                    avg_tvl = max(total_vol * 0.5, 1000000.0)
                else:
                    avg_tvl = max(total_vol * 1.2, 200000.0)

            apr = None
            if avg_tvl > 1.0:
                try:
                    if fee_db == 'Dynamic':
                        fee_rate = 0.0002
                    elif '%' in fee_db:
                        fee_rate = float(fee_db.replace('%', '').strip()) / 100.0
                    else:
                        fee_rate = float(fee_db) / 1000000.0
                    fees_earned = total_vol * fee_rate
                    days = max(1, (end_date - start_date).days)
                    apr = (fees_earned / avg_tvl) * (365.0 / days)
                except: pass

            if apr is not None:
                results[f"{t0}-{t1}-{fee_raw_full}"] = apr
                results[f"{t1}-{t0}-{fee_raw_full}"] = apr

        cur.close()
        conn.close()
        return results
    except Exception as e:
        print(f"APR bulk fetch failed: {e}")
        return {}

# Compare execution
fetcher = PostgresFetcher(verbose=True)
prices = fetcher.fetch_latest_prices()

# Build a large list of random pools to simulate production
pools = []
for i in range(50):
    pools.append(['USDT', 'USDC', '0.05%|Uniswap V3|Ethereum'])
    pools.append(['USDT', 'WETH', '0.3%|Uniswap V3|Ethereum'])
    pools.append(['WETH', 'USDC', '0.05%|Uniswap V3|Arbitrum'])

end_dt = datetime.now()
start_dt = end_dt - timedelta(days=7)

import time
t0 = time.time()
res1 = fetcher.fetch_pool_stats(pools, start_dt, end_dt, prices)
dur1 = time.time() - t0

t1 = time.time()
res2 = fetch_pool_stats_bulk(pools, start_dt, end_dt, prices)
dur2 = time.time() - t1

print(f"Original took {dur1:.2f}s, Bulk took {dur2:.2f}s")
if res1 == res2:
    print("Results match exactly!")
else:
    print(f"Results differ. res1: {res1}, res2: {res2}")
