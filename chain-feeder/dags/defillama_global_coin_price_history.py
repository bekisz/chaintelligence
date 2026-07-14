from airflow import DAG
from airflow.sdk import task, Asset
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param
import pendulum
from datetime import timedelta
import logging
import os
from typing import List, Dict

asset_coin_price_history = Asset("postgres://postgres:5432/chaintelligence/public/coin_price_history")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

@task
def identify_coins_to_sync(target_symbols: list = None) -> List[Dict]:
    """Identify coins to sync by symbol or by active LP positions."""
    from include.coin_family_resolver import CoinFamilyResolver
    import json

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn_uri = pg_hook.get_uri()
    config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
    resolver = CoinFamilyResolver(config_path, conn_uri)

    resolved_symbols = []

    if isinstance(target_symbols, str):
        target_symbols = target_symbols.strip()
        if target_symbols in ('', '[]', 'None'):
            target_symbols = None
        else:
            try:
                target_symbols = json.loads(target_symbols.replace("'", '"'))
            except Exception:
                target_symbols = [s.strip() for s in target_symbols.split(',')]

    if target_symbols and len(target_symbols) > 0:
        logging.info(f"Target symbols/families provided: {target_symbols}. Resolving...")
        if not isinstance(target_symbols, list):
            target_symbols = [target_symbols]
        resolved_symbols = resolver.resolve_target_symbols(target_symbols)
        logging.info(f"Resolved to {len(resolved_symbols)} symbols: {resolved_symbols[:10]}...")
    else:
        logging.info("No target symbols provided. Identifying Tier 1 coins (active LP positions)...")
        tier1_query = """
            SELECT DISTINCT c.symbol
            FROM coin c
            JOIN (
                SELECT DISTINCT c0.symbol as symbol FROM liquidity_pool p
                JOIN coin c0 ON p.coin0_id = c0.coin_id
                JOIN liquidity_pool_position pos ON p.id = pos.pool_id
                UNION
                SELECT DISTINCT c1.symbol as symbol FROM liquidity_pool p
                JOIN coin c1 ON p.coin1_id = c1.coin_id
                JOIN liquidity_pool_position pos ON p.id = pos.pool_id
            ) active_coins ON c.symbol = active_coins.symbol
            WHERE EXISTS (
                SELECT 1 FROM coin_contract cc WHERE cc.coin_id = c.coin_id
            );
        """
        resolved_symbols = [r[0] for r in pg_hook.get_records(tier1_query)]

    if not resolved_symbols:
        logging.warning("No symbols identified to sync.")
        return []

    return [{"symbol": s} for s in resolved_symbols]

@task(outlets=[asset_coin_price_history])
def fetch_and_store_history(coin_list: List[Dict], force_update: bool = False):
    """Fetch historical prices from DeFi Llama and store in coin_price_history by coin_id."""
    from include.defillama_client import fetch_historical_prices

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')

    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')

    total_inserted = 0

    for coin in coin_list:
        symbol = coin["symbol"]

        coin_id_row = pg_hook.get_first(
            "SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", parameters=(symbol.upper(),)
        )
        if not coin_id_row:
            logging.warning(f"  No coin_id found for {symbol}, skipping")
            continue
        coin_id = coin_id_row[0]

        # Get the first contract address for this coin (DeFi Llama uses ETH address)
        addr_row = pg_hook.get_first(
            "SELECT contract_address FROM coin_contract WHERE coin_id = %s AND contract_address IS NOT NULL LIMIT 1",
            parameters=(coin_id,)
        )
        if not addr_row:
            logging.warning(f"  No contract address found for {symbol}, skipping")
            continue
        address = addr_row[0]

        logging.info(f"Processing history for {symbol} (coin_id={coin_id})")

        current_start_ts = None
        current_end_ts = None
        loop_direction = "backward"

        if not force_update:
            latest_ts_res = pg_hook.get_first(
                "SELECT MAX(timestamp) FROM coin_price_history WHERE coin_id = %s",
                parameters=(coin_id,)
            )
            if latest_ts_res and latest_ts_res[0]:
                current_start_ts = int(latest_ts_res[0].timestamp()) + 1
                loop_direction = "forward"
                logging.info(f"  Existing data found up to {latest_ts_res[0]}. Syncing forward...")
        else:
            logging.info("  Force update enabled. Syncing backward from now...")

        batch_count = 0
        MAX_BATCHES = 10

        while batch_count < MAX_BATCHES:
            batch_count += 1
            history = fetch_historical_prices(
                address,
                start_timestamp=current_start_ts,
                end_timestamp=current_end_ts,
                points=500
            )

            if not history:
                logging.info(f"  No more history found for {symbol}")
                break

            logging.info(f"  Batch {batch_count}: Fetched {len(history)} data points")

            conn = pg_hook.get_conn()
            cur = conn.cursor()
            try:
                batch_data = [(coin_id, pendulum.from_timestamp(p["timestamp"]), p["price"]) for p in history]
                from psycopg2.extras import execute_values
                execute_values(cur, """
                    INSERT INTO coin_price_history (coin_id, timestamp, price)
                    VALUES %s ON CONFLICT (coin_id, timestamp) DO UPDATE SET price = EXCLUDED.price
                """, batch_data)
                conn.commit()
                total_inserted += len(history)
            except Exception as e:
                logging.error(f"  Failed to save batch for {symbol}: {e}")
                conn.rollback()
                break
            finally:
                cur.close()
                conn.close()

            if loop_direction == "forward":
                current_start_ts = history[-1]["timestamp"] + 1
            else:
                current_end_ts = history[0]["timestamp"] - 1

            if len(history) < 1000:
                break

    logging.info(f"History sync complete. Total points processed: {total_inserted}")
    return {"total_coins": len(coin_list), "total_points": total_inserted}

with DAG(
    'defillama_global_coin_price_history',
    default_args=default_args,
    description='Sync historical coin prices from DeFi Llama',
    schedule='0 1 * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['prices', 'history', 'defillama'],
    params={
        'force_update': Param(
            default=False,
            type='boolean',
            description='Force full re-sync of available history.'
        ),
        'coin_symbols': Param(
            default=[],
            type='array',
            items={'type': 'string'},
            description='Optional: List of coin symbols to sync (e.g. ["ETH", "USDC"]). If empty, syncs all Tier 1 coins.'
        )
    },
) as dag:

    coins = identify_coins_to_sync(target_symbols="{{ params.coin_symbols }}")

    sync_job = fetch_and_store_history(
        coin_list=coins,
        force_update="{{ params.force_update }}"
    )

    coins >> sync_job