#!/usr/bin/env python3
"""One-shot backfill for the route taxonomy tables.

Sweeps `swaps` partition-by-partition, classifies every transaction's legs into
(origin_destination_pair, route, route_hop), sets swaps.route_id, and finally
recomputes route_daily_stats for all affected days. Idempotent — safe to re-run.

Usage:
    python3 backfill_route_tables.py                      # everything
    python3 backfill_route_tables.py --limit-days 30      # only last 30 days
    python3 backfill_route_tables.py --table swaps_default # specific partition
"""

import os
import sys
import argparse
import logging
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))

import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
from include.route_classifier import (
    classify_tx_hashes,
    recompute_daily_stats,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('backfill_route_tables')

TX_BATCH = 2000


def iter_swap_tables(conn, limit_days: int | None):
    """Yield (child_table_name, ts_min, ts_max) for swap partitions to sweep."""
    cur = conn.cursor()
    cur.execute("""
        SELECT child.relname AS child,
               pg_get_expr(child.relpartbound, child.oid) AS bound
        FROM pg_inherits i
        JOIN pg_class parent ON parent.oid = i.inhparent
        JOIN pg_class child ON child.oid = i.inhrelid
        WHERE parent.relname = 'swaps'
        ORDER BY child.relname
    """)
    rows = cur.fetchall()
    cur.close()

    for child, bound in rows:
        # Parse bound like FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
        lo = hi = None
        if bound:
            try:
                parts = [p.strip().strip("'") for p in bound.replace('FOR VALUES FROM', '')
                         .replace('TO', '|').split('|')]
                lo, hi = parts[0], parts[1]
            except Exception:
                lo = hi = None
        # Swap partition tables are monthly.
        part_month = (lo or '')[:7] or child.replace('swaps_', '').split('_')[0]
        yield child, lo, hi


def classify_partition(conn, table_name: str, lo: str | None, hi: str | None,
                       limit_days: int | None, dry_run: bool = False) -> int:
    """Classify every distinct tx hash in one swap partition. Returns tx count."""
    cur = conn.cursor()

    # Optional ts filter for --limit-days
    ts_where = ""
    params: list = []
    if limit_days:
        ts_where = "AND ts >= now() - (%s || ' days')::interval"
        params.append(str(limit_days))

    # Distinct tx hashes in batches
    total = 0
    cur.execute(f"""
        SELECT DISTINCT tx_hash FROM {table_name}
        WHERE 1=1 {ts_where}
        ORDER BY tx_hash
    """, params)
    txs = [r[0] for r in cur.fetchall()]
    log.info("Partition %s: %d tx hashes to classify", table_name, len(txs))
    for i in range(0, len(txs), TX_BATCH):
        batch = txs[i:i + TX_BATCH]
        if dry_run:
            total += len(batch)
            continue
        legs = _classify_batch(conn, batch)
        total += len(batch)
        log.info("  classified %d txs (%d/%d)", len(batch), min(i + TX_BATCH, len(txs)), len(txs))
    cur.close()
    return total


def _classify_batch(conn, txs):
    from include.route_classifier import classify_tx_hashes, recompute_daily_stats
    with conn.cursor() as cur:
        classify_tx_hashes(cur, txs)
    conn.commit()
    days = _days_for_txs(conn, txs)
    if days:
        with conn.cursor() as cur:
            recompute_daily_stats(cur, sorted(days))
        conn.commit()


def _days_for_txs(conn, txs):
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT s.ts::date::text
        FROM swaps s WHERE s.tx_hash = ANY(%s)
    """, (txs,))
    days = [r[0] for r in cur.fetchall()]
    cur.close()
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit-days', type=int, default=None,
                    help='Only classify swaps in the last N days (dry: quick).')
    ap.add_argument('--table', default=None,
                    help='Single swap partition table to sweep (e.g. swaps_default).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Count only, do not write.')
    args = ap.parse_args()

    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    try:
        if args.table:
            tables = [(args.table, None, None)]
        else:
            tables = list(iter_swap_tables(conn, args.limit_days))
        grand = 0
        for child, lo, hi in tables:
            n = classify_partition(conn, child, lo, hi, args.limit_days, args.dry_run)
            grand += n
        log.info("Done. classified %d tx hashes across %d partitions", grand, len(tables))
    finally:
        conn.close()


if __name__ == '__main__':
    main()