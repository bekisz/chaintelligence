"""Daily swap retention DAG.

Reads swap_retention.yaml and deletes swap rows older than the configured
retention period per (network, protocol). Deletes in batches to avoid
long-running transactions and MVCC bloat.
"""
from airflow import DAG
from airflow.sdk import task, Param
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import datetime, timezone, timedelta
import logging
import os
import yaml
import time
from typing import List, Dict, Tuple

CONFIG_PATH = os.path.join(
    os.environ.get('AIRFLOW_HOME', '/opt/airflow'),
    'include/config/swap_retention.yaml'
)

BATCH_SIZE = 10000
SLEEP_BETWEEN_BATCHES = 1  # seconds


def load_rules() -> Tuple[int, List[Dict]]:
    """Load retention rules from YAML config.

    Returns (default_days, list_of_rule_dicts).
    """
    if not os.path.exists(CONFIG_PATH):
        logging.warning(f"Config not found at {CONFIG_PATH}, using defaults")
        return 90, []

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f) or {}

    default_days = cfg.get('default_days', 90)
    rules = cfg.get('rules', [])
    return default_days, rules


def build_rule_list(default_days: int, rules: List[Dict]) -> List[Tuple[str, str, int]]:
    """Build (network, protocol, retention_days) from rules + default.

    Known (network, protocol) pairs from the swaps table are listed inline
    rather than queried via SELECT DISTINCT (which would scan all partitions).
    """
    # Known pairs from the swaps table (avoids full-table DISTINCT scan)
    known_pairs = [
        ("Arbitrum", "Uniswap V3"),
        ("Arbitrum", "Uniswap V4"),
        ("Base", "Aerodrome"),
        ("Base", "Uniswap V3"),
        ("BNB", "PancakeSwap V3"),
        ("BNB", "PancakeSwap V4"),
        ("BNB", "Uniswap V3"),
        ("BNB", "Uniswap V4"),
        ("Ethereum", "Uniswap V2"),
        ("Ethereum", "Uniswap V3"),
        ("Ethereum", "Uniswap V4"),
    ]

    explicit = {}
    for r in rules:
        net = r.get('network')
        proto = r.get('protocol')
        days = r.get('days')
        if net and proto and days:
            explicit[(net, proto)] = days

    result = []
    for network, protocol in known_pairs:
        days = explicit.get((network, protocol), default_days)
        result.append((network, protocol, days))

    return sorted(result, key=lambda x: (x[0], x[1]))


@task
def enforce_retention(**context):
    dry_run = context.get('params', {}).get('dry_run', True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ('true', '1', 'yes')
    """Delete swap rows older than the retention period for each (network, protocol)."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')

    default_days, rules = load_rules()
    rule_list = build_rule_list(default_days, rules)

    logging.info(f"Loaded {len(rules)} explicit rules + default={default_days}d")
    logging.info(f"Total (network, protocol) combos to process: {len(rule_list)}")

    total_deleted = 0

    for network, protocol, days in rule_list:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        logging.info(f"  {network} / {protocol}: retention={days}d, cutoff={cutoff.date()}")

        # Resolve pool_ids for this network / protocol combo
        with pg_hook.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT lp.id 
                    FROM liquidity_pool lp
                    JOIN chain ch ON lp.chain_id = ch.id
                    JOIN protocol pr ON lp.protocol_id = pr.id
                    WHERE LOWER(ch.name) = LOWER(%s) AND LOWER(pr.name) = LOWER(%s)
                """, (network, protocol))
                pool_ids = [row[0] for row in cur.fetchall()]

        if not pool_ids:
            logging.info(f"    -> 0 pools found for {network} / {protocol}")
            continue

        # Count what would be deleted
        count_row = pg_hook.get_first(
            "SELECT COUNT(*) FROM swaps WHERE pool_id = ANY(%s) AND ts < %s",
            parameters=(pool_ids, cutoff)
        )
        to_delete = count_row[0] if count_row else 0
        logging.info(f"    -> {to_delete} rows to delete")

        if to_delete == 0:
            continue

        if dry_run:
            logging.info(f"    (dry-run, skipping delete)")
            continue

        # Delete in batches — reuse the same connection across all batches
        # to avoid exhausting the Postgres connection pool.
        conn = pg_hook.get_conn()
        cur = conn.cursor()
        try:
            deleted = 0
            while True:
                try:
                    cur.execute("""
                        DELETE FROM swaps
                        WHERE ctid IN (
                            SELECT ctid FROM swaps
                            WHERE pool_id = ANY(%s) AND ts < %s
                            LIMIT %s
                        )
                    """, (pool_ids, cutoff, BATCH_SIZE))
                    batch = cur.rowcount
                    conn.commit()
                    deleted += batch
                    total_deleted += batch
                    if batch > 0:
                        logging.info(f"    deleted {deleted}/{to_delete} rows")
                    if batch < BATCH_SIZE:
                        break
                except Exception as e:
                    logging.error(f"    batch delete failed: {e}")
                    conn.rollback()
                    break

                if SLEEP_BETWEEN_BATCHES > 0:
                    time.sleep(SLEEP_BETWEEN_BATCHES)
        finally:
            cur.close()
            conn.close()

        logging.info(f"    finished: deleted {deleted} rows for {network} / {protocol}")

    if dry_run:
        logging.info(f"DRY-RUN complete. See individual results above — no rows were actually deleted.")
    else:
        logging.info(f"Retention enforcement complete. Deleted {total_deleted} rows total.")

        # VACUUM the swaps table to reclaim space
        if total_deleted > 0:
            logging.info("Running VACUUM on swaps table...")
            conn = pg_hook.get_conn()
            conn.autocommit = True
            cur = conn.cursor()
            try:
                cur.execute("VACUUM ANALYZE swaps")
                logging.info("VACUUM ANALYZE swaps complete")
            except Exception as e:
                logging.warning(f"VACUUM failed (non-critical): {e}")
            finally:
                cur.close()
                conn.close()


with DAG(
    'config_global_swap_retention',
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Enforce swap retention policy per network/protocol',
    schedule='0 3 * * *',  # daily at 3 AM
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['retention', 'config', 'swaps'],
    params={
        'dry_run': Param(
            default=True,
            type='boolean',
            description='If true, only count rows without deleting (safe default). Set false to actually delete.'
        ),
    },
) as dag:

    enforce_retention()