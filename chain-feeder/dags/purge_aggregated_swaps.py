"""Opt-in cleanup for raw swap rows covered by route aggregates.

This is deliberately disabled unless RAW_SWAP_PURGE_ENABLED=true. The current
API still has raw-swap fallbacks for unconfigured routes, so enabling cleanup is
an operational decision after the aggregate coverage has been verified.

Cleans up:
  - `swaps_staging` (short-lived raw store). Rows older than
    RAW_SWAP_RETENTION_DAYS that are covered by route_daily_stats (and by
    route_distribution_bucket where that route is configured) are deleted, and
    whole monthly partitions older than the retention window may be dropped.
  - legacy `swaps`, only as long as SWAP_LEGACY_MIRROR is on; once the mirror is
    off the permanent table should be retired via DROP.
"""

import logging
import os
from datetime import datetime, timedelta

import pendulum
from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook


def _purge_table(cur, target_table, retention_days, batch_size):
    cur.execute(
        f"""
        DELETE FROM {target_table} s
        WHERE s.ctid IN (
            SELECT s2.ctid
            FROM {target_table} s2
            WHERE s2.ts < CURRENT_DATE - ({retention_days} * INTERVAL '1 day')
              AND s2.route_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM route_daily_stats rds
                  WHERE rds.route_id = s2.route_id
                    AND rds.day = s2.ts::date
              )
              AND (
                  NOT EXISTS (
                      SELECT 1
                      FROM route_distribution_config dc
                      WHERE dc.route_id = s2.route_id AND dc.enabled
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM route_distribution_bucket db
                      WHERE db.route_id = s2.route_id
                        AND db.day = s2.ts::date
                  )
              )
            ORDER BY s2.ts
            LIMIT {batch_size}
        )
        """,
    )
    return cur.rowcount


@task
def purge() -> int:
    if os.getenv('RAW_SWAP_PURGE_ENABLED', 'false').lower() not in ('1', 'true', 'yes'):
        logging.info('Raw swap purge is disabled (set RAW_SWAP_PURGE_ENABLED=true to enable).')
        return 0

    retention_days = max(1, int(os.getenv('RAW_SWAP_RETENTION_DAYS', '3')))
    batch_size = max(1000, int(os.getenv('RAW_SWAP_PURGE_BATCH_SIZE', '50000')))
    raw_table = os.getenv('SWAP_RAW_TABLE', 'swaps_staging').strip()
    mirror_legacy = os.getenv('SWAP_LEGACY_MIRROR', 'true').strip().lower() in ('1', 'true', 'yes')

    hook = PostgresHook(postgres_conn_id='postgres_default')
    conn = hook.get_conn()
    deleted = 0
    targets = [raw_table]
    if mirror_legacy:
        targets.append('swaps')
    try:
        with conn.cursor() as cur:
            for target in targets:
                while True:
                    count = _purge_table(cur, target, retention_days, batch_size)
                    conn.commit()
                    deleted += count
                    if count == 0:
                        break
                    logging.info('Deleted %d raw swap rows from %s so far.', deleted, target)
        # Drop empty monthly staging partitions fully older than the retention
        # window; they can never be needed again once aggregates have been built.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT child.relname
                FROM pg_inherits i
                JOIN pg_class parent ON parent.oid = i.inhparent
                JOIN pg_class child ON child.oid = i.inhrelid
                WHERE parent.relname = %s
                  AND child.relname ~ E'\\mswaps_staging_[0-9]{4}_[0-9]{2}\\M'
                """,
                (raw_table,),
            )
            partition_names = [r[0] for r in cur.fetchall()]
            for partition_name in partition_names:
                month_str = partition_name.rsplit('_', 2)[-2:]
                try:
                    month_start = datetime.strptime('_'.join(month_str), '%Y_%m')
                except ValueError:
                    continue
                # Drop only partitions wholly older than the retention window.
                if (month_start + timedelta(days=31)) < (datetime.now() - timedelta(days=retention_days)):
                    cur.execute(f"SELECT 1 FROM {partition_name} LIMIT 1")
                    if cur.fetchone() is None:
                        cur.execute(f"DROP TABLE {partition_name}")
                        logging.info('Dropped empty staging partition %s', partition_name)
                        deleted += 1
            conn.commit()
    finally:
        conn.close()
    logging.info('Raw swap purge complete: %d rows deleted.', deleted)
    return deleted


with DAG(
    'purge_aggregated_swaps',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Opt-in purge of raw swaps covered by route daily aggregates',
    schedule='@daily',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['swaps', 'retention', 'aggregates'],
) as dag:
    purge()
