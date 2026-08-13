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
from concurrent.futures import ProcessPoolExecutor, as_completed

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

TX_BATCH = 10000

_WORKER_CONN = None
_WORKER_PAIR_CACHE = None
_WORKER_ROUTE_CACHE = None


def get_db_dsn() -> str:
    """Return PostgreSQL connection DSN, auto-adjusting for local execution outside Docker."""
    dsn = os.getenv('DATA_WAREHOUSE_DB', DATA_WAREHOUSE_DB)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


def ensure_indexes(conn, table_name: str):
    """Ensure partial indexes match hash discovery and assignment updates."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_unclassified
                ON {table_name} (tx_hash) WHERE route_id IS NULL
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_unclassified_assign
                ON {table_name} (tx_hash, log_index, ts)
                WHERE route_id IS NULL
            """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.debug("Index note for %s: %s", table_name, e)


def _init_worker():
    """Open one persistent read connection per worker process."""
    global _WORKER_CONN, _WORKER_PAIR_CACHE, _WORKER_ROUTE_CACHE
    _WORKER_CONN = psycopg2.connect(get_db_dsn())
    _WORKER_CONN.autocommit = False
    _WORKER_PAIR_CACHE = {}
    _WORKER_ROUTE_CACHE = {}
    log.info("Worker initialized")


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
        yield child, lo, hi


def _worker_classify_batch(args_tuple):
    """Worker process task: read and reconstruct one batch without writes."""
    table_name, batch, pair_cache, route_cache, dry_run = args_tuple
    if dry_run:
        return [], [], []

    from include.route_classifier import collect_route_staging

    # Workers only read and reconstruct. Route identity merges remain in the
    # parent process so workers cannot contend on the global route tables.
    conn = _WORKER_CONN
    owns_conn = conn is None
    if owns_conn:
        conn = psycopg2.connect(get_db_dsn())
    try:
        with conn.cursor() as cur:
            candidates, assignments, days = collect_route_staging(cur, batch, table_name=table_name)
        conn.commit()
        return candidates, assignments, days
    finally:
        if owns_conn:
            conn.close()


def classify_partition(conn, table_name: str, lo: str | None, hi: str | None,
                       limit_days: int | None, dry_run: bool = False,
                       pair_cache: dict = None, route_cache: dict = None,
                       unclassified_only: bool = False,
                       batch_size: int = TX_BATCH,
                       workers: int = 1,
                       executor: ProcessPoolExecutor | None = None,
                        chunk_limit: int = 20000) -> tuple[int, set]:
    """Classify unclassified tx hashes in a swap partition in streaming chunks."""
    if unclassified_only and not dry_run:
        ensure_indexes(conn, table_name)

    cur = conn.cursor()

    if unclassified_only and not dry_run:
        cur.execute(f"SELECT 1 FROM {table_name} WHERE route_id IS NULL LIMIT 1")
        if not cur.fetchone():
            log.info("Partition %s: 0 unclassified tx hashes (skipping).", table_name)
            cur.close()
            return 0, set()

    total_partition_txs = 0
    affected_days = set()
    start_time = time.time()

    where_clauses = ["1=1"]
    params: list = []
    if limit_days:
        where_clauses.append("ts >= now() - (%s || ' days')::interval")
        params.append(str(limit_days))
    if unclassified_only:
        where_clauses.append("route_id IS NULL")

    where_str = " AND ".join(where_clauses)
    chunk_num = 1

    if pair_cache is None:
        pair_cache = {}
    if route_cache is None:
        route_cache = {}

    log.info("Scanning partition %s for tx hashes in streaming chunks... (workers=%d, batch_size=%d)", table_name, workers, batch_size)

    while True:
        limit_clause = f" LIMIT {chunk_limit}" if unclassified_only else ""
        cur.execute(f"""
            SELECT DISTINCT tx_hash FROM {table_name}
            WHERE {where_str}
            {limit_clause}
        """, params)
        txs = [r[0] for r in cur.fetchall()]
        # Release the snapshot held by the hash-discovery SELECT before the
        # workers and merge transaction begin. This avoids keeping old row
        # versions alive during a long-running partition backfill.
        conn.commit()

        # Debug: number of tx hashes retrieved for this chunk
        log.info("  [%s] fetched %d tx hashes to classify", table_name, len(txs))

        if not txs:
            break


        total_partition_txs += len(txs)
        batches = [txs[i:i + batch_size] for i in range(0, len(txs), batch_size)]

        if dry_run:
            log.info("  [%s] dry-run chunk %d: %d tx hashes found", table_name, chunk_num, len(txs))
            break

        chunk_classified = 0
        if workers > 1 and executor is not None:
            worker_args = [
                (table_name, b, {}, {}, dry_run)
                for b in batches
            ]
            future_to_batch = {executor.submit(_worker_classify_batch, arg): arg for arg in worker_args}
            completed_batches = 0
            chunk_candidate_map = {}
            chunk_assignments = []
            for future in as_completed(future_to_batch):
                candidates, assignments, days = future.result()
                for candidate in candidates:
                    key = candidate['candidate_key']
                    existing = chunk_candidate_map.get(key)
                    if existing is None:
                        chunk_candidate_map[key] = candidate
                    else:
                        if candidate['first_seen'] and candidate['first_seen'] < existing['first_seen']:
                            existing['first_seen'] = candidate['first_seen']
                        if candidate['last_seen'] and candidate['last_seen'] > existing['last_seen']:
                            existing['last_seen'] = candidate['last_seen']
                chunk_assignments.extend(assignments)
                affected_days.update(days)
                completed_batches += 1
                if completed_batches % 5 == 0 or completed_batches == len(batches):
                    log.info("  [%s] chunk %d progress: %d/%d batches complete (%d txs)",
                             table_name, chunk_num, completed_batches, len(batches), len(chunk_assignments))
            chunk_classified = _merge_staging_with_retry(
                conn, list(chunk_candidate_map.values()), chunk_assignments, table_name
            )
        else:
            for batch in batches:
                days = _classify_batch(conn, batch, pair_cache, route_cache, table_name)
                chunk_classified += len(batch)
                affected_days.update(days)

        elapsed = time.time() - start_time
        speed = total_partition_txs / elapsed if elapsed > 0 else 0
        log.info("  [%s] chunk %d: classified %d txs (%d total so far, %.0f tx/s)",
                 table_name, chunk_num, chunk_classified, total_partition_txs, speed)

        chunk_num += 1
        if not unclassified_only or len(txs) < chunk_limit:
            break

    cur.close()
    log.info("Partition %s classification complete (%d total txs, %d affected days).",
             table_name, total_partition_txs, len(affected_days))
    return total_partition_txs, affected_days


def _classify_batch(conn, txs, pair_cache, route_cache, table_name):
    import random
    from include.route_classifier import collect_route_staging, merge_route_staging
    max_retries = 10
    for attempt in range(max_retries):
        try:
            with conn.cursor() as cur:
                candidates, assignments, days = collect_route_staging(cur, txs, table_name=table_name)
                merge_route_staging(cur, candidates, assignments, table_name=table_name)
            conn.commit()
            return days
        except Exception as err:
            conn.rollback()
            if attempt == max_retries - 1:
                log.error("Batch classification failed permanently after %d attempts: %s", max_retries, err)
                raise
            jitter = random.uniform(0.1, 0.5)
            time.sleep((0.5 * (2 ** attempt)) + jitter)
    return []


def _merge_staging_with_retry(conn, candidates, assignments, table_name, max_retries=8):
    """Merge one staged batch, retrying the complete transaction on deadlock."""
    from include.route_classifier import merge_route_staging
    import random

    for attempt in range(max_retries):
        try:
            with conn.cursor() as cur:
                updated = merge_route_staging(cur, candidates, assignments, table_name=table_name)
            conn.commit()
            return updated
        except psycopg2.errors.DeadlockDetected:
            conn.rollback()
            if attempt == max_retries - 1:
                raise
            delay = min(10.0, 0.5 * (2 ** attempt)) + random.uniform(0.1, 0.5)
            log.warning("Route merge deadlock; retrying batch in %.1fs (%d/%d)",
                        delay, attempt + 1, max_retries - 1)
            time.sleep(delay)
        except Exception:
            conn.rollback()
            raise
    return 0


def main():
    default_workers = max(1, (os.cpu_count() or 4) // 2)
    ap = argparse.ArgumentParser(description="Parallel backfill for route taxonomy tables.")
    ap.add_argument('--limit-days', type=int, default=None,
                    help='Only classify swaps in the last N days.')
    ap.add_argument('--table', default=None,
                    help='Single swap partition table to sweep (e.g. swaps_default).')
    ap.add_argument('--batch-size', type=int, default=TX_BATCH,
                    help=f'Transaction batch size per update chunk (default: {TX_BATCH}).')
    ap.add_argument('--chunk-limit', type=int, default=20000,
                    help='Transaction hashes fetched per scan chunk (default: 20000).')
    ap.add_argument('--workers', '-j', type=int, default=default_workers,
                    help=f'Number of parallel worker processes (default: {default_workers}).')
    ap.add_argument('--seed-top', type=int, default=500,
                    help='Number of top pairs/routes to seed top-down before bottom-up sweep (default: 500, set 0 to disable).')
    ap.add_argument('--unclassified-only', action='store_true', default=True,
                    help='Only classify transactions where route_id IS NULL (default: True).')
    ap.add_argument('--reclassify-all', action='store_true',
                    help='Force re-classification of all transactions, even if already classified.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Count only, do not write.')
    args = ap.parse_args()

    unclassified_only = not args.reclassify_all if args.reclassify_all else args.unclassified_only

    conn = psycopg2.connect(get_db_dsn())
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))",
                    ("chaintelligence.route-backfill",))
        if not cur.fetchone()[0]:
            log.error("Another route backfill is already running; exiting.")
            conn.close()
            raise SystemExit(2)
    pair_cache = {}
    route_cache = {}

    all_affected_days = set()

    # Phase 1: Top-down seed ingestion (if requested and not dry run)
    if args.seed_top > 0 and not args.dry_run:
        try:
            from include.route_classifier import seed_top_routes_and_attribute
            with conn.cursor() as cur:
                target_tbl = args.table if args.table else 'swaps'
                seeded_swaps, seeded_days = seed_top_routes_and_attribute(cur, table_name=target_tbl, top_n_pairs=args.seed_top)
                all_affected_days.update(seeded_days)
            conn.commit()
            log.info("Top-down seeding phase complete: %d swaps attributed.", seeded_swaps)
        except Exception as e:
            conn.rollback()
            log.warning("Top-down seeding pass encountered an error (continuing with bottom-up): %s", e)

    if not args.dry_run:
        try:
            from include.route_classifier import load_classifier_caches
            with conn.cursor() as cur:
                pair_cache, route_cache = load_classifier_caches(cur)
        except Exception as e:
            log.warning("Could not pre-load classifier cache: %s", e)

    executor = None
    try:
        if args.table:
            tables = [(args.table, None, None)]
        else:
            tables = list(iter_swap_tables(conn, args.limit_days))

        grand_txs = 0
        processed_partitions = 0

        log.info("Starting classification sweep across %d partition(s) with %d workers (unclassified_only=%s)...",
                 len(tables), args.workers, unclassified_only)
        executor = ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=_init_worker,
        ) if (args.workers > 1 and not args.dry_run) else None
        try:
            for child, lo, hi in tables:
                n_txs, affected_days = classify_partition(
                    conn, child, lo, hi, args.limit_days, args.dry_run,
                    pair_cache=pair_cache, route_cache=route_cache,
                    unclassified_only=unclassified_only,
                    batch_size=args.batch_size,
                    workers=args.workers,
                    executor=executor,
                    chunk_limit=args.chunk_limit
                )
                grand_txs += n_txs
                all_affected_days.update(affected_days)
                processed_partitions += 1
                progress_pct = (processed_partitions / len(tables)) * 100 if len(tables) > 0 else 0
                log.info("Processed %d/%d partitions (%.1f%%)", processed_partitions, len(tables), progress_pct)
        finally:
            if executor is not None:
                log.info("Closing parallel worker pool (%d workers)...", args.workers)
                executor.shutdown(wait=False, cancel_futures=True)
                log.info("Worker pool closed successfully.")
                executor = None

        if all_affected_days and not args.dry_run:
            log.info("Starting daily stats recomputation across %d unique affected days...", len(all_affected_days))
            with conn.cursor() as cur:
                source_table = args.table if args.table else 'swaps'
                recompute_daily_stats(cur, sorted(all_affected_days), table_name=source_table)
            log.info("Committing daily stats transaction to database...")
            conn.commit()
            log.info("Daily stats committed successfully.")

        log.info("Done. Classified %d tx hashes across %d partitions using %d workers.",
                 grand_txs, len(tables), args.workers)
    except KeyboardInterrupt:
        log.warning("\nInterrupted by user (Ctrl+C). Terminating workers...")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(130)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
