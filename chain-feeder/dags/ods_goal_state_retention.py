"""Daily O&D goal-state retention DAG.

Reads ``config/ods-goal-state.yaml`` and, for every declared requirement +
layer combination, reports coverage and (when ``dry_run=false``) prunes rows
that fall outside the effective keep-window. Also derives the chain-level floor
for unclassified raw swaps (replacing the old ``config_global_swap_retention``
network/protocol mechanism).

Param ``dry_run`` (default true, the safe setting) only reports; set it to
``false`` in the UI / CLI to actually delete. Param ``backfill`` (default true)
recomputes route_daily_stats/-buckets for any days reported as missing first.
"""
from airflow import DAG
from airflow.sdk import task, Param
import pendulum
import psycopg2
import logging
from datetime import timedelta

from common.utils.config import DATA_WAREHOUSE_DB
from include.od_retention import (
    load_goal_state,
    run_checks,
    export_gaps,
    prune,
    backfill_missing_daily_stats,
    is_floor_requirement,
)

BATCH_SIZE = 10000


def connect():
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    conn.autocommit = False
    return conn


@task
def run_goal_state_checks(**context):
    params = context.get('params', {})
    dry_run = params.get('dry_run', True)
    backfill = params.get('backfill', True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ('true', '1', 'yes')

    goal = load_goal_state()
    logging.info("Loaded goal-state: %s requirements (%s base floors, %s O&D sets)",
                 len(goal['requirements']),
                 sum(1 for r in goal['requirements'] if is_floor_requirement(r)),
                 sum(1 for r in goal['requirements'] if not is_floor_requirement(r)))

    conn = connect()
    try:
        report = run_checks(conn, goal)
    finally:
        conn.close()

    for row in report:
        logging.info("%-8s %-26s %4d pairs %s..%s %d/%d days (missing %d)  %s %s->%s chains=%s",
                     row['status'], row['layer'], row['pairs'],
                     row['window']['start'], row['window']['end'],
                     row['present_days'], row['expected_days'],
                     len(row['missing_days']), row['name'],
                     row['origin'], row['dest'], row['chains'])

    bad = [r for r in report if r['status'] != 'ok']
    logging.info("Goal-state coverage: %d checks, %d not ok", len(report), len(bad))

    gaps = export_gaps(report)
    for g in gaps:
        logging.info("gap: %s .. %s (%d days) layer=%s chains=%s %s",
                     g['from'], g['to'], g['days'], g['layer'], g['chain'], g['name'])

    context['task_instance'].xcom_push(key='report', value=report)
    context['task_instance'].xcom_push(key='gaps', value=gaps)

    if backfill and report:
        conn = connect()
        try:
            n = backfill_missing_daily_stats(conn, report)
        finally:
            conn.close()
        logging.info("Backfilled/recomputed route daily stats for %d missing days", n)

    if not dry_run and bad:
        raise Exception(f"{len(bad)} goal-state checks not ok; refusing to prune data under an unmet goal state")


@task
def enforce_goal_state(**context):
    params = context.get('params', {})
    dry_run = params.get('dry_run', True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ('true', '1', 'yes')
    batch = params.get('batch', BATCH_SIZE)

    goal = load_goal_state()
    conn = connect()
    try:
        def progress(msg):
            logging.info(msg)
        result = prune(conn, goal, dry_run=dry_run, batch=batch, progress=progress)
    finally:
        conn.close()

    counts = result['rows']
    logging.info("%s rows per layer: %s", 'DRY-RUN estimate' if dry_run else 'DELETED', counts)

    # Reclaim space after real deletes.
    if not dry_run and sum(counts.values()) > 0:
        conn = connect()
        conn.autocommit = True
        cur = conn.cursor()
        try:
            for table in ('swaps', 'route_daily_stats', 'route_daily_stats_bucket',
                          'liquidity_pool_daily_stats', 'liquidity_pool_position_snapshot'):
                cur.execute(f"VACUUM ANALYZE {table}")
        finally:
            cur.close()
            conn.close()


with DAG(
    'ods_goal_state_retention',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': True,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Enforce O&D goal-state data retention (replaces config_global_swap_retention)',
    schedule='0 3 * * *',  # daily at 3 AM
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['retention', 'ods', 'goal-state'],
    params={
        'dry_run': Param(
            default=True,
            type='boolean',
            description='If true, only report coverage / estimate deletions (safe). Set false to actually prune data.'
        ),
        'backfill': Param(
            default=True,
            type='boolean',
            description='Recompute route_daily_stats/-buckets for missing days before pruning.'
        ),
        'batch': Param(
            default=BATCH_SIZE,
            type='integer',
            description='Rows processed per batched DELETE.'
        ),
    },
) as dag:

    checks = run_goal_state_checks()
    checks >> enforce_goal_state()