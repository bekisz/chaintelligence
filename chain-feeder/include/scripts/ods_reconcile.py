#!/usr/bin/env python3
"""O&D reconciliation planning CLI (declarative control plane).

Compile config/ods-goal-state.yaml into O&D sets + products, load the coverage
ledger, and print the reconciliation plan per (set, product, chain, day):

    python3 ods_reconcile.py plan                 # per-set/per-product action counts
    python3 ods_reconcile.py plan --by-day        # detailed rows
    python3 ods_reconcile.py plan --json          # machine-readable

The plan is the input to workers (source fetch, classifier, materializer); it
is what reconciles the warehouse toward the declared goal state.
"""
import os
import sys
import json
import argparse
import logging
from datetime import date, datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'include'))

import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
try:
    from od_catalog import compile_catalog
    from reconcile import load_coverage_state, plan_requirement, summarize_rows
except ImportError:
    from include.od_catalog import compile_catalog
    from include.reconcile import load_coverage_state, plan_requirement, summarize_rows

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def get_db_dsn() -> str:
    dsn = os.getenv('DATA_WAREHOUSE_DB', DATA_WAREHOUSE_DB)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


def cmd_plan(args):
    conn = psycopg2.connect(get_db_dsn())
    try:
        state = load_coverage_state(conn)
        cat = compile_catalog(args.config)
        today = args.today or date.today()
        rows = []
        for s in cat['sets']:
            rows.extend(plan_requirement(s, state, today))
    finally:
        conn.close()

    if args.json:
        print(json.dumps({
            'as_of': today.isoformat(),
            'raw_cells': len(state.raw_present),
            'classified_cells': len(state.classified),
            'rows': rows,
        }, default=str))
        return

    for s in sorted({r['set_id'] for r in rows}):
        srows = [r for r in rows if r['set_id'] == s]
        print(json.dumps({s: {'total': len(srows), 'actions': summarize_rows(srows)}})) if not args.by_day else None
    if args.by_day:
        for r in sorted(rows, key=lambda x: (x['set_id'], x['chain'], x['product'], x['day'])):
            print(f"{r['set_id']:10} {r['chain']:10} {r['product']:26} {r['day']}  {r['action']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', help='path to ods-goal-state.yaml (default: auto)')
    parser.add_argument('--today', type=lambda s: date.fromisoformat(s),
                        help='override "today" for rolling windows (YYYY-MM-DD)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='print the reconciliation plan')
    p.add_argument('--by-day', action='store_true', help='dump per-day rows')
    p.add_argument('--json', action='store_true', help='machine-readable JSON')
    p.set_defaults(fn=cmd_plan)
    args = parser.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()