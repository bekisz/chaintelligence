#!/usr/bin/env python3
"""
purge_old_non_eth_swaps.py  – Delete non-Ethereum swaps older than N days.

The swaps table is range-partitioned by `ts` (TIMESTAMPTZ).
Ethereum is chain_id = 1 (see normalize_protocols_chains.sql).

Rows where chain_id != 1 AND ts < NOW() - INTERVAL '<days> days' are deleted
in partition-aware batches so each DELETE is cheap and vacuum-friendly.

Usage (inside the container):
    python3 purge_old_non_eth_swaps.py               # dry-run, 3 days cutoff
    python3 purge_old_non_eth_swaps.py --execute      # actually delete
    python3 purge_old_non_eth_swaps.py --days 7 --execute
    python3 purge_old_non_eth_swaps.py --table swaps_2026_07 --execute

Env:
    DATA_WAREHOUSE_DB   – PostgreSQL DSN (falls back to default from config)
"""

import os
import sys
import time
import logging
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT  = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))

import psycopg2

try:
    from common.utils.config import DATA_WAREHOUSE_DB as _DEFAULT_DSN
except ImportError:
    _DEFAULT_DSN = ''

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('purge_non_eth_swaps')

ETHEREUM_CHAIN_ID = 1
BATCH_SIZE = 50_000   # rows per DELETE statement; keeps lock time short


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def get_dsn() -> str:
    dsn = os.getenv('DATA_WAREHOUSE_DB', _DEFAULT_DSN)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


def list_partitions(cur, parent: str = 'swaps') -> list:
    """Return all child partition table names for the given parent table."""
    cur.execute("""
        SELECT inhrelid::regclass::text
        FROM   pg_inherits
        WHERE  inhparent = %s::regclass
        ORDER  BY 1
    """, (parent,))
    return [row[0] for row in cur.fetchall()]


def count_non_eth_old(cur, table: str, cutoff_str: str) -> int:
    """Count non-ETH rows older than cutoff via JOIN to liquidity_pool."""
    cur.execute("""
        SELECT COUNT(*)
        FROM   {table} s
        JOIN   liquidity_pool lp ON lp.id = s.pool_id
        WHERE  lp.chain_id != %s
          AND  s.ts < %s
    """.format(table=table), (ETHEREUM_CHAIN_ID, cutoff_str))
    return cur.fetchone()[0]


def delete_batch(cur, table: str, cutoff_str: str, batch: int) -> int:
    """Delete up to `batch` non-ETH rows older than cutoff_str. Returns deleted count."""
    cur.execute("""
        DELETE FROM {table}
        WHERE  (ts, tx_hash, log_index) IN (
            SELECT s.ts, s.tx_hash, s.log_index
            FROM   {table} s
            JOIN   liquidity_pool lp ON lp.id = s.pool_id
            WHERE  lp.chain_id != %s
              AND  s.ts < %s
            LIMIT  %s
        )
    """.format(table=table), (ETHEREUM_CHAIN_ID, cutoff_str, batch))
    return cur.rowcount


# ---------------------------------------------------------------------------
# main logic
# ---------------------------------------------------------------------------

def purge(conn, tables, cutoff_str: str, dry_run: bool) -> int:
    grand_total = 0

    for tbl in tables:
        with conn.cursor() as cur:
            n = count_non_eth_old(cur, tbl, cutoff_str)
        conn.rollback()

        if n == 0:
            log.info("%-30s  0 rows to delete – skipping.", tbl)
            continue

        log.info("%-30s  %d rows to delete%s", tbl, n,
                 ' (dry-run, skipping)' if dry_run else '')

        if dry_run:
            grand_total += n
            continue

        deleted = 0
        while True:
            with conn.cursor() as cur:
                batch_deleted = delete_batch(cur, tbl, cutoff_str, BATCH_SIZE)
            conn.commit()
            deleted += batch_deleted
            log.info("  %s – deleted batch of %d (total so far: %d / %d)",
                     tbl, batch_deleted, deleted, n)
            if batch_deleted < BATCH_SIZE:
                break
            time.sleep(0.05)   # brief yield to avoid starving other writers

        log.info("  %s – done. Total deleted: %d", tbl, deleted)
        grand_total += deleted

    return grand_total


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Purge non-Ethereum swaps older than N days.')
    ap.add_argument('--days', type=int, default=3,
                    help='Delete non-ETH rows older than this many days (default: 3).')
    ap.add_argument('--table', default=None,
                    help='Restrict to a single partition, e.g. swaps_2026_07.'
                         ' Default: all partitions of swaps.')
    ap.add_argument('--execute', action='store_true',
                    help='Actually perform the DELETE (default is dry-run).')
    args = ap.parse_args()

    dry_run = not args.execute
    cutoff_expr  = "NOW() - INTERVAL '%d days'" % args.days

    log.info("=== purge_old_non_eth_swaps ===")
    log.info("Cutoff  : %s days back", args.days)
    log.info("Mode    : %s", 'DRY-RUN' if dry_run else 'EXECUTE')

    dsn  = get_dsn()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False

    # Resolve cutoff to an actual timestamp for logging clarity & consistent DELETE
    with conn.cursor() as cur:
        cur.execute("SELECT " + cutoff_expr)
        cutoff_ts_actual = cur.fetchone()[0]
    conn.rollback()
    cutoff_str = cutoff_ts_actual.isoformat()
    log.info("Cutoff ts (resolved): %s", cutoff_str)

    if args.table:
        tables = [args.table]
        log.info("Tables  : %s (single)", args.table)
    else:
        with conn.cursor() as cur:
            tables = list_partitions(cur)
        conn.rollback()
        log.info("Tables  : %d partition(s) discovered", len(tables))
        for t in tables:
            log.info("          %s", t)

    if not tables:
        log.warning("No partitions found – nothing to do.")
        conn.close()
        return

    total = purge(conn, tables, cutoff_str, dry_run)

    conn.close()

    if dry_run:
        log.info("=== DRY-RUN complete: %d row(s) would be deleted. "
                 "Pass --execute to delete. ===", total)
    else:
        log.info("=== Done. Total rows deleted: %d ===", total)


if __name__ == '__main__':
    main()
