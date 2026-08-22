#!/usr/bin/env python3
"""Materialize set-level LP daily aggregates (product lp.set.daily_stats).

For each declared O&D set, for the requested (pool, day) window, copy the
universal pool daily stats through the od_set_pool_member bridge into
od_set_pool_daily_stats. A pool shared by several routes is counted once.

Usage:
  python3 ods_lp_set_materializer.py  # process all active sets over their windows
  python3 ods_lp_set_materializer.py --set-id btc-usd-eth --days 30
"""
import os, sys, json, argparse, logging
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
for p in (os.path.join(REPO_ROOT,'chain-feeder'), os.path.join(REPO_ROOT,'chain-feeder','dags'), os.path.join(REPO_ROOT,'chain-feeder','include')):
    sys.path.insert(0, p)

import psycopg2
from common.utils.config import DATA_WAREHOUSE_DB
from od_catalog import compile_catalog

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('lp_set_materializer')


def get_db_dsn() -> str:
    dsn = os.getenv('DATA_WAREHOUSE_DB', DATA_WAREHOUSE_DB)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


def materialize_set(conn, set_id: str, window_days: int) -> int:
    """Copy universal pool facts for a set's member pools into the set table."""
    since = (datetime.now(timezone_utc()) - timedelta(days=window_days)).date()
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM od_set_pool_daily_stats WHERE set_id = %s AND day >= %s
        """, (set_id, since))
        cur.execute("""
            INSERT INTO od_set_pool_daily_stats (set_id, pool_id, day, tx_count, volume_usd, tvl_usd)
            SELECT %s, m.pool_id, l.day,
                   COALESCE(MAX(l.tx_count), 0),
                   COALESCE(SUM(l.volume_usd), 0),
                   COALESCE(MAX(l.tvl_usd), 0)
            FROM od_set_pool_member m
            JOIN liquidity_pool_daily_stats l ON l.pool_id = m.pool_id
            WHERE m.set_id = %s AND l.day >= %s
            GROUP BY m.pool_id, l.day
        """, (set_id, set_id, since))
        n = cur.rowcount
    conn.commit()
    return n


def timezone_utc():
    from datetime import timezone
    return timezone.utc


def cmd_main(args):
    cat = compile_catalog(args.config)
    conn = psycopg2.connect(get_db_dsn())
    total = 0
    try:
        set_ids = {s.id for s in cat['sets']}
        if args.set_id:
            if args.set_id not in set_ids:
                log.error("set %s not in catalog", args.set_id)
                return 1
            set_ids = {args.set_id}
        days = args.days if args.days else 30
        for sid in set_ids:
            n = materialize_set(conn, sid, days)
            log.info("set %s: materialized %d (set,pool,day) rows", sid, n)
            total += n
    finally:
        conn.close()
    print(f"total rows: {total}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', help='path to ods-goal-state.yaml')
    parser.add_argument('--set-id', help='materialize only this set id')
    parser.add_argument('--days', type=int, help='window days to (re)materialize (default 30)')
    args = parser.parse_args()
    sys.exit(cmd_main(args))


if __name__ == '__main__':
    main()
