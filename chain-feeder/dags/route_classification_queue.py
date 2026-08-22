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
MAX_BATCHES_PER_RUN = 300
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
            SELECT tx_hash, generation
            FROM route_classification_queue
            WHERE status = 'pending' AND available_at <= NOW()
            ORDER BY available_at DESC, tx_hash DESC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (BATCH_SIZE,))
        rows = [(row[0], row[1]) for row in cur.fetchall()]
        if rows:
            tx_hashes = [r[0] for r in rows]
            # claim_token records the generation at claim time so completion can
            # be made conditional (no overwriting a newer requeue).
            cur.execute("""
                UPDATE route_classification_queue
                SET status = 'processing', claimed_at = NOW(), updated_at = NOW(),
                    attempts = attempts + 1
                WHERE tx_hash = ANY(%s)
            """, (tx_hashes,))
            cur.execute("""
                UPDATE route_classification_queue
                SET claim_token = generation
                WHERE tx_hash = ANY(%s)
            """, (tx_hashes,))
    conn.commit()
    return rows


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
        # Share pair/route resolution caches across the whole run: the same
        # (chain, origin, dest) pairs and (pair, pools) routes repeat constantly,
        # and without these caches every tx re-resolves them from the DB (the
        # dominant cost of classification). Populated per-batch, reused all run.
        pair_cache = {}
        route_cache = {}
        try:
            for _ in range(MAX_BATCHES_PER_RUN):
                claimed = _claim_batch(conn)
                if not claimed:
                    break
                tx_hashes = [c[0] for c in claimed]
                gen_by_hash = {c[0]: c[1] for c in claimed}

                try:
                    with conn.cursor() as cur:
                        _, days = classify_tx_hashes(cur, tx_hashes,
                                                     pair_cache=pair_cache,
                                                     route_cache=route_cache,
                                                     table_name=RAW_SWAP_TABLE)
                    conn.commit()
                    affected_days.update(days)

                    # Conditional completion: only mark complete if the row's
                    # generation still equals the claimed token. A producer
                    # requeue (new leg) bumps generation, so a worker that
                    # classified an older value must NOT overwrite the requeue.
                    with conn.cursor() as cur:
                        for th, gen in claimed:
                            cur.execute("""
                                UPDATE route_classification_queue
                                SET status = 'complete', claimed_at = NULL,
                                    updated_at = NOW(), last_error = NULL
                                WHERE tx_hash = %s AND claim_token = %s
                                      AND generation = %s
                            """, (th, gen, gen))
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
