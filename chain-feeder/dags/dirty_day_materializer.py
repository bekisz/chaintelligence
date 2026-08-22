"""Dirty-day fact materializer.

Consumes the fine-grained dirty work rows written by the route classifier
(``dirty_route_day`` / ``dirty_pool_day``) and recomputes exactly those days,
incrementally and idempotently — instead of the old broad contiguous-range
recompute that the classifier used to run at end of run.

Per run:
  1. Collect the distinct ``day`` values from dirty_route_day UNION dirty_pool_day.
  2. Delete those days from the dirty tables (mark as claimed).
  3. Recompute route_daily_stats, route_daily_stats_bucket, and
     liquidity_pool_daily_stats_bucket for exactly those days (chunked).
  4. Any dirty row added concurrently during recompute survives (it was deleted
     before the delete, then re-inserted) and is picked up by the next run, so
     nothing is lost (at-least-once).

This runs often but with a bounded, exact day set, so steady-state cost is low.
"""
import logging
from datetime import timedelta

from airflow import DAG
from airflow.sdk import task, Param
import pendulum
import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
from include.route_classifier import (
    recompute_daily_stats,
    recompute_distribution_buckets,
    recompute_pool_distribution_buckets,
    RAW_SWAP_TABLE,
)

CHUNK_DAYS = 7
MAX_DAYS_PER_RUN = 90  # guard against an enormous backlog retrying forever


def connect():
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    conn.autocommit = False
    return conn


@task
def materialize_dirty_days():
    conn = connect()
    processed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT day FROM (
                    SELECT day FROM dirty_route_day
                    UNION
                    SELECT day FROM dirty_pool_day
                ) d
                ORDER BY day
                LIMIT %s
            """, (MAX_DAYS_PER_RUN,))
            days = [str(r[0]) for r in cur.fetchall()]

            if not days:
                logging.info("no dirty days to materialize")
                return 0

            # Claim: remove these days from the dirty tables before recompute.
            cur.execute("DELETE FROM dirty_route_day WHERE day = ANY(%s::date[])", (days,))
            cur.execute("DELETE FROM dirty_pool_day WHERE day = ANY(%s::date[])", (days,))
            conn.commit()

            # Recompute exact days (chunked internally).
            recompute_daily_stats(cur, days, chunk_days=CHUNK_DAYS, table_name=RAW_SWAP_TABLE)
            recompute_distribution_buckets(cur, days, chunk_days=CHUNK_DAYS, table_name=RAW_SWAP_TABLE)
            recompute_pool_distribution_buckets(cur, days, chunk_days=CHUNK_DAYS, table_name=RAW_SWAP_TABLE)
            conn.commit()

            processed = len(days)
            logging.info("Materialized %d dirty days: %s .. %s",
                         processed, days[0], days[-1])
    finally:
        conn.close()
    return processed


with DAG(
    'dirty_day_materializer',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': True,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Recompute facts for dirty route/pool days recorded by the classifier',
    schedule='*/20 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['routes', 'materialization', 'rollup'],
    params={
        'max_days_per_run': Param(
            default=MAX_DAYS_PER_RUN, type='integer',
            description='Max distinct days to materialize per run (backlog guard).'),
    },
) as dag:
    materialize_dirty_days()