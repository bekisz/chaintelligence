"""Route daily stats rollup DAG.

Materializes `route_daily_stats` (route_id, day, tx_count, swap_count,
volume_usd) from the classified `swaps` table. The table is derived, so each
run deletes and recomputes the recent window (never increments), which keeps it
consistent even when a late mixed-protocol leg reclassifies a tx's route.
"""
from airflow import DAG
from airflow.sdk import task, Param
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import timedelta
import logging

from include.route_classifier import recompute_daily_stats, recompute_distribution_buckets

LOOKBACK_DAYS = 3


def _rollup_days(pg_hook, days: list):
    conn = pg_hook.get_conn()
    try:
        with conn.cursor() as cur:
            recompute_daily_stats(cur, days)
            recompute_distribution_buckets(cur, days)
        conn.commit()
    finally:
        conn.close()


with DAG(
    'route_daily_stats_rollup',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Recompute route_daily_stats (fast-path route analytics)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['routes', 'swaps', 'analytics', 'rollup'],
    params={
        'backfill_days': Param(
            default=3,
            type='integer',
            description='Recompute this many past days (default 3).',
        ),
    },
) as dag:
    @task
    def rollup(**context):
        conf = context.get('dag_run') and context['dag_run'].conf or {}
        lookback = conf.get('backfill_days', LOOKBACK_DAYS)
        if not isinstance(lookback, int) or lookback < 1:
            lookback = LOOKBACK_DAYS

        # Build ISO day list for the window [today - lookback, today].
        days = []
        from datetime import date, timedelta as td
        for i in range(lookback, -1, -1):
            days.append((date.today() - td(days=i)).isoformat())
        logging.info(f"Recomputing route_daily_stats for: {days}")

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        _rollup_days(pg_hook, sorted(days))

    rollup()
