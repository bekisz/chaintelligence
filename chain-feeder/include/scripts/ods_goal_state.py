#!/usr/bin/env python3
"""O&D goal-state retention command-line tool.

Evaluates `config/ods-goal-state.yaml` against the warehouse and (optionally)
prunes data that falls outside every requirement's keep-window.

Usage:
    python3 ods_goal_state.py show-rules                      # resolved requirements
    python3 ods_goal_state.py check                           # coverage report (table)
    python3 ods_goal_state.py check --json                    # machine-readable report
    python3 ods_goal_state.py gaps                            # contiguous missing ranges
    python3 ods_goal_state.py prune                           # dry-run delete estimate
    python3 ods_goal_state.py prune --apply --batch 20000     # actually delete rows
    python3 ods_goal_state.py backfill                        # recompute missing daily stats
    python3 ods_goal_state.py --config /path/to/ods-goal-state.yaml check --json
"""

import os
import sys
import json
import argparse
import logging
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))

import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
from include.od_retention import (
    load_goal_state,
    run_checks,
    export_gaps,
    prune,
    backfill_missing_daily_stats,
    effective_window,
    window_resolve,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('ods_goal_state')


def get_db_dsn() -> str:
    dsn = os.getenv('DATA_WAREHOUSE_DB', DATA_WAREHOUSE_DB)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


def connect():
    conn = psycopg2.connect(get_db_dsn())
    conn.autocommit = False
    return conn


def cmd_check(args, goal):
    report = run_checks(connect(), goal, today=args.today)
    if args.json:
        print(json.dumps(report, indent=2))
        return
    if not report:
        print("No requirements declared in the goal-state config.")
        return
    for r in report:
        print(f"{r['status']:9} {r['layer']:26} {str(r['pairs']):4} pairs "
              f"{r['window']['start']}..{r['window']['end']} "
              f"{r['present_days']}/{r['expected_days']} days "
              f"(missing {len(r['missing_days'])})  {r['name']} "
              f"{r['origin']}->{r['dest']} chains={r['chains']}")
    bad = [r for r in report if r['status'] != 'ok']
    print(f"\n{len(report)} checks, {len(bad)} not ok")


def cmd_gaps(args, goal):
    report = run_checks(connect(), goal, today=args.today)
    gaps = export_gaps(report)
    if args.json:
        print(json.dumps(gaps, indent=2))
        return
    if not gaps:
        print("No gaps.")
        return
    for g in gaps:
        print(f"{g['from']} .. {g['to']} ({g['days']} days)  layer={g['layer']:26} "
              f"chains={g['chain']}  {g['name']}")


def cmd_prune(args, goal):
    conn = connect()
    try:
        result = prune(conn, goal, today=args.today, dry_run=not args.apply, batch=args.batch)
    finally:
        conn.close()
    counts = result['rows']
    total = sum(counts.values())
    mode = "DRY-RUN estimate" if result['dry_run'] else "DELETED"
    print(f"{mode}:")
    for layer, n in counts.items():
        print(f"  {layer:26} {n}")
    print(f"  TOTAL                  {total}")
    if result.get('vacuum'):
        vc = connect()
        vc.autocommit = True
        try:
            with vc.cursor() as cur:
                for layer, table in (('swaps', 'swaps'),
                                     ('route_daily_stats', 'route_daily_stats'),
                                     ('route_daily_stats_bucket', 'route_daily_stats_bucket'),
                                     ('liquidity_pool', 'liquidity_pool_daily_stats')):
                    if counts.get(layer, 0) > 0:
                        log.info("vacuuming %s", table)
                        cur.execute(f"VACUUM {table}")
        finally:
            vc.close()


def cmd_backfill(args, goal):
    report = run_checks(connect(), goal, today=args.today)
    n = backfill_missing_daily_stats(connect(), report, chunk_days=args.chunk_days)
    print(f"Recomputed {n} days of route daily stats/buckets (0 = nothing missing).")


def cmd_show_rules(args, goal):
    conn = connect()
    try:
        today = args.today or date.today()
        print("defaults:")
        for layer, win in goal['defaults'].items():
            s, e = window_resolve(win, today) or (None, None)
            print(f"  {layer:26} {_win_label(s, e)}")
        print("defaults.per_chain:")
        for row in goal.get('per_chain', []):
            for layer, win in row['layers'].items():
                s, e = window_resolve(win, today) or (None, None)
                print(f"  {row['chain']:20} {layer:26} {_win_label(s, e)}")
        for req in goal['requirements']:
            print(f"requirement: {req['name']}")
            print(f"  origin={req['origin']!r} dest={req['dest']!r} "
                  f"direction={req['direction']} chains={'*' if req['chains_all'] else sorted(req['chains'])}")
            for layer in ('swaps', 'route_daily_stats', 'route_daily_stats_bucket', 'liquidity_pool'):
                win = req['layers'].get(layer)
                if win is None:
                    continue
                s, e = window_resolve(win, today) or (None, None)
                print(f"  {layer:26} {_win_label(s, e)}")
    finally:
        conn.close()


def _win_label(start, end):
    if start is None and end is None:
        return "(no floor — deleteable everywhere)"
    if start is None:
        return f"<= {end}"
    if end is None:
        return f">= {start}"
    return f"{start} .. {end}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', help='path to ods-goal-state.yaml (default: auto-discovered)')
    parser.add_argument('--today', type=lambda s: date.fromisoformat(s),
                        help='override "today" for rolling windows (YYYY-MM-DD)')
    sub = parser.add_subparsers(dest='command', required=True)

    for name in ('show-rules',):
        p = sub.add_parser(name)
        p.set_defaults(fn=cmd_show_rules)

    for name in ('check',):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
        p.set_defaults(fn=cmd_check)

    for name in ('gaps',):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
        p.set_defaults(fn=cmd_gaps)

    p = sub.add_parser('prune')
    p.add_argument('--apply', action='store_true', help='actually delete (default: dry-run estimate)')
    p.add_argument('--batch', type=int, default=10000)
    p.set_defaults(fn=cmd_prune)

    p = sub.add_parser('backfill')
    p.add_argument('--chunk-days', type=int, default=7)
    p.set_defaults(fn=cmd_backfill)

    args = parser.parse_args()
    goal = load_goal_state(config_path=args.config)
    if args.config is None and goal.get('config_path'):
        log.info("using config: %s", goal['config_path'])
    elif args.config is None and not goal.get('config_path'):
        log.warning("no config file found; built-in defaults only (no requirements)")
    args.fn(args, goal)


if __name__ == '__main__':
    main()