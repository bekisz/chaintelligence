"""Asynchronous route classification worker.

Swap ingestion only enqueues transaction hashes. This DAG drains the queue in
batches so route reconstruction and route-dimension writes do not block Graph
ingestion or compete with a historical backfill callback.
"""

from datetime import timedelta
import logging

import pendulum
from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook

from include.route_classifier import (
    classify_tx_hashes,
    recompute_daily_stats,
    recompute_distribution_buckets,
    recompute_pool_distribution_buckets,
    RAW_SWAP_TABLE,
)

BATCH_SIZE = 5000
MAX_BATCHES_PER_RUN = 100
STALE_AFTER_MINUTES = 30


def _claim_batch(conn):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE route_classification_queue
            SET status = 'pending', claimed_at = NULL, updated_at = NOW(),
                last_error = COALESCE(last_error, 'stale claim recovered')
            WHERE status = 'processing'
              AND claimed_at < NOW() - (%s || ' minutes')::interval
        """, (STALE_AFTER_MINUTES,))
        cur.execute("""
            SELECT tx_hash
            FROM route_classification_queue
            WHERE status = 'pending' AND available_at <= NOW()
            ORDER BY available_at, tx_hash
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (BATCH_SIZE,))
        tx_hashes = [row[0] for row in cur.fetchall()]
        if tx_hashes:
            cur.execute("""
                UPDATE route_classification_queue
                SET status = 'processing', claimed_at = NOW(), updated_at = NOW(),
                    attempts = attempts + 1
                WHERE tx_hash = ANY(%s)
            """, (tx_hashes,))
    conn.commit()
    return tx_hashes


with DAG(
    'route_classification_queue',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Drain asynchronous swap route-classification work queue',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['routes', 'swaps', 'classification'],
) as dag:

    @task
    def classify_queue():
        hook = PostgresHook(postgres_conn_id='postgres_default')
        conn = hook.get_conn()
        affected_days = set()
        completed = 0
        failed = 0
        try:
            for _ in range(MAX_BATCHES_PER_RUN):
                tx_hashes = _claim_batch(conn)
                if not tx_hashes:
                    break

                try:
                    with conn.cursor() as cur:
                        _, days = classify_tx_hashes(cur, tx_hashes, table_name=RAW_SWAP_TABLE)
                    conn.commit()
                    affected_days.update(days)

                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE route_classification_queue
                            SET status = 'complete', claimed_at = NULL,
                                updated_at = NOW(), last_error = NULL
                            WHERE tx_hash = ANY(%s)
                        """, (tx_hashes,))
                    conn.commit()
                    completed += len(tx_hashes)
                except Exception as exc:
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE route_classification_queue
                            SET status = 'pending', claimed_at = NULL,
                                available_at = NOW() + INTERVAL '5 minutes',
                                updated_at = NOW(), last_error = LEFT(%s, 2000)
                            WHERE tx_hash = ANY(%s)
                        """, (str(exc), tx_hashes))
                    conn.commit()
                    failed += len(tx_hashes)
                    logging.exception('Route classification batch failed')

            if affected_days:
                with conn.cursor() as cur:
                    recompute_daily_stats(cur, sorted(affected_days), table_name=RAW_SWAP_TABLE)
                    recompute_distribution_buckets(cur, sorted(affected_days), table_name=RAW_SWAP_TABLE)
                    recompute_pool_distribution_buckets(cur, sorted(affected_days), table_name=RAW_SWAP_TABLE)
                conn.commit()
            logging.info('Route queue run complete: %d completed, %d failed, %d days rolled up',
                         completed, failed, len(affected_days))
        finally:
            conn.close()

    classify_queue()
