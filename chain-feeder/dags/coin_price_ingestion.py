from airflow import DAG
from airflow.sdk import task, Param
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import timedelta
import logging
import pendulum
import os

# Import the CoinMarketCap client
from include.coinmarketcap_client import fetch_crypto_quotes_by_id

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=3),
}

@task
def resolve_targets(target_list=None):
    """
    Resolves the list of target coins (as CMC IDs) based on a single mixed input list.
    
    Args:
        target_list (str): Comma-separated list of targets (IDs, symbols, families). 
                           e.g., "1027, ETH, USD.*"
        
    Returns:
        list: List of CMC IDs to update
    """
    if not target_list:
        logging.warning("No targets provided.")
        return []
        
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    # Parse input list
    raw_items = [s.strip() for s in target_list.split(',') if s.strip()]
    
    cmc_ids = []
    symbols = []
    families = []
    addresses = []
    
    for item in raw_items:
        # Check if it's a number -> CMC ID
        if item.isdigit():
            cmc_ids.append(int(item))
        # Check if it's an address (starts with 0x)
        elif item.lower().startswith('0x'):
            addresses.append(item.lower())
        # Check if it's a family/regex (contains * or . or %)
        elif '*' in item or '%' in item or item.endswith('.') or 'family' in item.lower():
            families.append(item)
        # Otherwise treat as symbol
        else:
            symbols.append(item.upper())
            
    resolved_ids = set()
    
    # 1. CMC IDs (Direct)
    if cmc_ids:
        resolved_ids.update(cmc_ids)
        logging.info(f"Added {len(cmc_ids)} direct CMC IDs: {cmc_ids}")
        
    # 2. Addresses
    if addresses:
        val_list = ",".join([f"'{a}'" for a in addresses])
        sql = f"SELECT cmc_id, ethereum_address FROM coin WHERE ethereum_address IN ({val_list}) AND cmc_id IS NOT NULL"
        rows = pg_hook.get_records(sql)
        for r in rows:
            resolved_ids.add(r[0])
        found_addrs = [r[1] for r in rows]
        missing = set(addresses) - set(found_addrs)
        logging.info(f"Resolved {len(rows)} addresses to IDs. Found: {len(found_addrs)}. Missing: {missing}")

    # 3. Symbols
    if symbols:
        # Escape single quotes in symbols to prevent SQL injection issues though parameterized is better
        val_list = ",".join([f"'{s}'" for s in symbols])
        sql = f"SELECT cmc_id, symbol FROM coin WHERE symbol IN ({val_list}) AND cmc_id IS NOT NULL"
        rows = pg_hook.get_records(sql)
        for r in rows:
            resolved_ids.add(r[0])
        found_syms = [r[1] for r in rows]
        missing = set(symbols) - set(found_syms)
        logging.info(f"Resolved {len(rows)} symbols to IDs. Found: {found_syms}. Missing: {missing}")
        
    # 3. Families (Regex match)
    if families:
        for fam in families:
            # Normalize pattern: replace * with %, ensure . matches correctly if regex intent
            pattern = fam.replace('*', '%')
            if pattern.endswith('.'): pattern += '%'
            
            sql = "SELECT cmc_id, symbol FROM coin WHERE symbol LIKE %s AND cmc_id IS NOT NULL"
            rows = pg_hook.get_records(sql, (pattern,))
            for r in rows:
                resolved_ids.add(r[0])
            logging.info(f"Family pattern '{fam}' (limit '{pattern}') matched {len(rows)} coins: {[r[1] for r in rows]}")

    # 4. Resolve families from coin_family table
    if symbols or families:
        # Check if any original symbols are actually family names in coin_family table
        search_terms = [s.lower() for s in symbols] + [f.replace('*', '%').replace('%', '').lower() for f in families]
        if search_terms:
            val_list = ",".join([f"'{s}'" for s in search_terms])
            sql = f"""
                SELECT c.cmc_id, c.symbol, cf.name 
                FROM coin c
                JOIN coin_family cf ON c.symbol = cf.symbol
                WHERE LOWER(cf.name) IN ({val_list}) AND c.cmc_id IS NOT NULL
            """
            rows = pg_hook.get_records(sql)
            for r in rows:
                resolved_ids.add(r[0])
            if rows:
                logging.info(f"Resolved {len(rows)} coins from coin_family table (targets: {search_terms})")

    if not resolved_ids:
        logging.warning("No targets resolved! Nothing to update.")
        return []
        
    return list(resolved_ids)

@task
def fetch_and_update_prices(target_ids):
    """
    Fetches prices from CMC for the resolved IDs and updates the database.
    """
    if not target_ids:
        logging.info("No target IDs provided. Skipping update.")
        return 0
        
    logging.info(f"Updating prices for {len(target_ids)} coins")
    
    all_metrics = fetch_crypto_quotes_by_id(target_ids)
    
    if not all_metrics:
        logging.warning("CMC API returned no data.")
        return 0

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    updated_count = 0
    
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    try:
        # Build ID->Symbol map
        if target_ids:
            s_sql = f"SELECT cmc_id, symbol FROM coin WHERE cmc_id IN ({','.join(map(str, target_ids))})"
            rows = pg_hook.get_records(s_sql)
            id_to_symbol = {r[0]: r[1] for r in rows}
        else:
            id_to_symbol = {}

        for cmc_id, metrics in all_metrics.items():
            sym = id_to_symbol.get(cmc_id)
            if not sym:
                logging.warning(f"Got price for CMC ID {cmc_id} but no symbol found in DB. Skipping.")
                continue
                
            if metrics.get('price') is not None:
                try:
                    cur.execute("""
                        UPDATE coin 
                        SET price = %s, price_timestamp = %s,
                            percent_change_1h = %s, percent_change_24h = %s, percent_change_7d = %s,
                            percent_change_30d = %s, percent_change_60d = %s, percent_change_90d = %s,
                            market_cap = %s, market_cap_dominance = %s, fully_diluted_market_cap = %s,
                            tvl = %s, total_supply = %s, circulating_supply = %s, max_supply = %s,
                            cmc_last_updated = %s
                        WHERE symbol = %s
                    """, (
                        metrics.get('price'), now,
                        metrics.get('percent_change_1h'), metrics.get('percent_change_24h'), metrics.get('percent_change_7d'),
                        metrics.get('percent_change_30d'), metrics.get('percent_change_60d'), metrics.get('percent_change_90d'),
                        metrics.get('market_cap'), metrics.get('market_cap_dominance'), metrics.get('fully_diluted_market_cap'),
                        metrics.get('tvl'), metrics.get('total_supply'), metrics.get('circulating_supply'), metrics.get('max_supply'),
                        metrics.get('last_updated'), sym
                    ))
                    updated_count += 1
                except Exception as e:
                    logging.error(f"Failed to update price for {sym}: {e}")
                    conn.rollback()
        conn.commit()
    finally:
        cur.close()
        conn.close()

    logging.info(f"✅ Updated {updated_count}/{len(target_ids)} coins")
    return updated_count


# Price ingestion tasks

def create_price_ingestion_dag(
    dag_id,
    schedule=None,
    default_targets="",
    description=""
):
    """
    Factory function to create coin price ingestion DAGs with specific configurations.
    """
    with DAG(
        dag_id,
        default_args=default_args,
        description=description,
        schedule=schedule,
        start_date=pendulum.now().subtract(days=1),
        catchup=False,
        max_active_runs=2,
        tags=['prices', 'coinmarketcap', 'factory'],
        params={
            'targets': Param(default_targets, type='string', description='Comma-separated targets (e.g. "ETH, 0x123..., 1027, USD.*")'),
        },
    ) as dag:
        
        from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator

        # Always trigger, the mapping DAG itself handles freshness check
        mapping_step = TriggerDagRunOperator(
            task_id="trigger_coin_family_ingestion",
            trigger_dag_id="coin_family_ingestion",
            conf={"bypass_sensor": True},
            wait_for_completion=True,
            poke_interval=30,
            reset_dag_run=True,
            deferrable=False
        )
        
        # We pass the targets from params. If default_targets is set in factory, it acts as default in UI too.
        resolved_ids = resolve_targets(target_list="{{ params.targets }}")
        
        update_task = fetch_and_update_prices(resolved_ids)
        
        mapping_step >> resolved_ids >> update_task
        
    return dag

# 1. Standard On-Demand DAG (No schedule, no default targets)
dag_manual = create_price_ingestion_dag(
    dag_id='coin_price_ingestion',
    schedule=None,
    description='Update specific coin prices on-demand (with auto mapping sync)',
    default_targets=""
)

# Example of how to add a scheduled version:
# dag_daily_eth = create_price_ingestion_dag(
#     dag_id='actual_coin_price_ingestion_daily_eth',
#     schedule='@daily',
#     default_targets="ETH",
#     description='Daily update for ETH price'
# )
