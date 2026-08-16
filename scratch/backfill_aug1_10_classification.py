"""Classify the Aug 1-10 2026 gap in legacy `swaps` and rebuild route_daily_stats.

The 2026-08-01..08-10 swap rows exist in the legacy `swaps` table but were never
route-classified (route_id IS NULL), so route_daily_stats has a gap for those
days. swaps_staging does not contain the rows at all (they predate the
switchover), so the normal route_classification_queue DAG cannot help.

This script:
  1. Collects distinct unclassified tx_hashes in the window from legacy `swaps`.
  2. Classifies them (upsert pair/route/route_hop, set swaps.route_id) in batches.
  3. Recomputes route_daily_stats, route_daily_stats_bucket, and pool buckets for
     the affected days from legacy `swaps`.

Run from the chaintelligence-server container:
    python scratch/backfill_aug1_10_classification.py
"""

import sys
import time
from datetime import date, timedelta

sys.path.insert(0, '/app/chain-feeder')
sys.path.insert(0, '/app/chain-feeder/include')
sys.path.insert(0, '/app/chain-feeder/dags')
sys.path.insert(0, '/app/api/routing')

import psycopg2

from include.route_classifier import (
    classify_tx_hashes,
    recompute_daily_stats,
    recompute_distribution_buckets,
    recompute_pool_distribution_buckets,
)

WINDOW_START = date(2026, 8, 1)
WINDOW_END = date(2026, 8, 10)
BATCH = 5000


def main():
    conn = psycopg2.connect(dbname='chaintelligence', user='airflow', password='airflow',
                            host='postgres', port=5432)
    cur = conn.cursor()

    t0 = time.time()
    cur.execute("""
        SELECT DISTINCT tx_hash
        FROM swaps
        WHERE ts >= %s AND ts < %s AND route_id IS NULL
    """, (WINDOW_START, WINDOW_END))
    txs = [r[0] for r in cur.fetchall()]
    cur.close()
    print(f"Fetched {len(txs)} unclassified tx hashes in {time.time()-t0:.1f}s", flush=True)

    affected_days = set()
    done = 0
    cur = conn.cursor()
    for i in range(0, len(txs), BATCH):
        batch = txs[i:i + BATCH]
        n, days = classify_tx_hashes(cur, batch, table_name='swaps')
        conn.commit()
        affected_days.update(days)
        done += n
        if (i // BATCH) % 20 == 0:
            print(f"  classified {done}/{len(txs)} txs ({(i+BATCH)/len(txs)*100:.0f}%)", flush=True)
    print(f"Classified {done} txs; affected days: {sorted(affected_days)}", flush=True)

    days = sorted(affected_days)
    if days:
        n1 = recompute_daily_stats(cur, days, table_name='swaps')
        conn.commit()
        print(f"recompute_daily_stats: {n1} rows", flush=True)
        n2 = recompute_distribution_buckets(cur, days, table_name='swaps')
        conn.commit()
        print(f"recompute_distribution_buckets: {n2} rows", flush=True)
        n3 = recompute_pool_distribution_buckets(cur, days, table_name='swaps')
        conn.commit()
        print(f"recompute_pool_distribution_buckets: {n3} rows", flush=True)

    cur.close()
    conn.close()
    print(f"DONE in {time.time()-t0:.1f}s", flush=True)


if __name__ == '__main__':
    main()
