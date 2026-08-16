"""Last-7-days daily stats + bucket distribution for all BTC->USD routes on Ethereum.

"BTC" and "USD" are token families (BTC ~22 wrapped variants, USD ~200 stablecoins),
so this resolves both families via coin_family, matches every origin_destination_pair
on the Ethereum chain whose two sides fall into the two families (both directions),
then pulls route_daily_stats + route_daily_stats_bucket for the last 7 days for every
route of those pairs — the same data the /api/routes/{hash}/daily-stats endpoint serves,
just aggregated across an entire family-to-family query instead of one route at a time.

Usage:
    python scratch/btc_usd_eth_daily_stats.py > scratch/btc_usd_eth_daily_stats.json
"""

import json
import os
import sys

import psycopg2

DSN = os.environ.get(
    "DATA_WAREHOUSE_DB",
    "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433",
)


def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    # Resolve family symbols.
    cur.execute("""
        SELECT f.name, c.symbol
        FROM coin_family f
        JOIN coin c ON f.coin_id = c.coin_id
        WHERE f.name IN ('BTC', 'USD')
    """)
    family_symbols = {}
    for fam, sym in cur.fetchall():
        family_symbols.setdefault(fam, []).append(sym)
    btc_syms = sorted(family_symbols.get('BTC', []))
    usd_syms = sorted(family_symbols.get('USD', []))

    # Matched pairs on Ethereum (both directions).
    cur.execute("""
        SELECT pair.id, pair.origin_symbol, pair.dest_symbol
        FROM origin_destination_pair pair
        JOIN chain ch ON pair.chain_id = ch.id
        WHERE LOWER(ch.name) = 'ethereum'
          AND (
                (UPPER(pair.origin_symbol) IN %s AND UPPER(pair.dest_symbol) IN %s)
             OR (UPPER(pair.origin_symbol) IN %s AND UPPER(pair.dest_symbol) IN %s)
          )
    """, (tuple(btc_syms), tuple(usd_syms), tuple(usd_syms), tuple(btc_syms)))
    pairs = cur.fetchall()

    # Routes for those pairs.
    pair_ids = [p[0] for p in pairs]
    cur.execute("""
        SELECT r.route_id, r.pair_id, r.hops
        FROM route r
        WHERE r.pair_id = ANY(%s)
    """, (pair_ids,))
    route_rows = cur.fetchall()
    routes_by_pair = {}
    for route_id, pair_id, hops in route_rows:
        routes_by_pair.setdefault(pair_id, []).append({'route_id': route_id, 'hops': hops})
    route_ids = [r[0] for r in route_rows]

    # Daily stats + buckets for the last 7 days.
    cur.execute("""
        SELECT route_id, day, tx_count, swap_count, volume_usd, fees_usd
        FROM route_daily_stats
        WHERE route_id = ANY(%s) AND day >= CURRENT_DATE - 7 AND day <= CURRENT_DATE
        ORDER BY route_id, day
    """, (route_ids,))
    daily_by_route = {}
    for route_id, day, tx, swaps, vol, fees in cur.fetchall():
        daily_by_route.setdefault(route_id, []).append({
            'day': str(day), 'tx_count': tx or 0, 'swap_count': swaps or 0,
            'volume_usd': float(vol or 0), 'fees_usd': float(fees or 0),
        })

    cur.execute("""
        SELECT route_id, day, bucket_index, tx_count, sample_count,
               volume_usd, fees_usd, log_sum, log_sum2
        FROM route_daily_stats_bucket
        WHERE route_id = ANY(%s) AND day >= CURRENT_DATE - 7 AND day <= CURRENT_DATE
        ORDER BY route_id, day, bucket_index
    """, (route_ids,))
    buckets_by_route = {}
    for (route_id, day, bidx, tx, samples, vol, fees, ls, ls2) in cur.fetchall():
        buckets_by_route.setdefault(route_id, []).append({
            'day': str(day), 'bucket_index': int(bidx), 'tx_count': tx or 0,
            'sample_count': samples or 0, 'volume_usd': float(vol or 0),
            'fees_usd': float(fees or 0), 'log_sum': float(ls) if ls is not None else None,
            'log_sum2': float(ls2) if ls2 is not None else None,
        })

    # Assemble the report.
    report = {
        'window': {'start': 'CURRENT_DATE - 7', 'end': 'CURRENT_DATE'},
        'network': 'Ethereum',
        'families': {'BTC': btc_syms, 'USD': usd_syms},
        'pairs': [],
    }
    pair_by_id = {p[0]: p for p in pairs}
    for pair_id, origin, dest in pairs:
        pair_routes = routes_by_pair.get(pair_id, [])
        route_entries = []
        for r in pair_routes:
            rid = r['route_id']
            # hex hash rendering (mirrors route_hash_hex in api/main.py)
            r_hash = format((rid & ((1 << 64) - 1)), '016x')
            daily = daily_by_route.get(rid, [])
            buckets = buckets_by_route.get(rid, [])
            if not daily and not buckets:
                continue
            route_entries.append({
                'route_id': r_hash,
                'hops': r['hops'],
                'daily_stats': daily,
                'daily_stats_bucket': buckets,
            })
        report['pairs'].append({
            'pair_id': pair_id,
            'origin_symbol': origin,
            'dest_symbol': dest,
            'routes': route_entries,
        })

    report['summary'] = {
        'pair_count': len(report['pairs']),
        'route_count': sum(len(p['routes']) for p in report['pairs']),
        'daily_stat_rows': sum(len(r['daily_stats']) for p in report['pairs'] for r in p['routes']),
        'bucket_rows': sum(len(r['daily_stats_bucket']) for p in report['pairs'] for r in p['routes']),
    }

    cur.close()
    conn.close()
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    sys.exit(main())
