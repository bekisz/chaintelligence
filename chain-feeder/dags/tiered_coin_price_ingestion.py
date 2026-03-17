from airflow import DAG
from airflow.sdk import task, Param
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.task.trigger_rule import TriggerRule
import pendulum
from datetime import timedelta
import logging
import os

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=3),
}

def get_stale_count_for_family(family_name, interval_minutes):
    """Helper to check if a coin family has stale prices."""
    if not family_name:
        return 0
        
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    cutoff = now.subtract(minutes=interval_minutes)
    
    # Query coins belonging to the family via the coin_family table
    query = """
        SELECT COUNT(*) 
        FROM coin c
        JOIN coin_family cf ON c.symbol = cf.symbol
        WHERE LOWER(cf.name) = LOWER(%s)
        AND (c.price_timestamp IS NULL OR c.price_timestamp < %s)
    """
    count = pg_hook.get_first(query, parameters=(family_name, cutoff))[0]
    return count

@task.branch
def check_tier1_condition(**context):
    """Check if Tier 1 family needs update"""
    params = context['params']
    family = params.get('tier_1_coin_family')
    # Tier 1 is very frequent (e.g., 5 mins)
    count = get_stale_count_for_family(family, 5)
    
    if count > 0:
        logging.info(f"✅ Tier 1 ({family}): {count} coins stale - triggering update")
        return 'trigger_tier1_update'
    
    logging.info(f"⏭️  Tier 1 ({family}): All fresh - skipping")
    return None

@task.branch
def check_tier2_condition(**context):
    """Check if Tier 2 family needs update"""
    params = context['params']
    family = params.get('tier_2_coin_family')
    interval = int(os.getenv('CMC_TIER2_INTERVAL_MINUTES', '30'))
    count = get_stale_count_for_family(family, interval)
    
    if count > 0:
        logging.info(f"✅ Tier 2 ({family}): {count} coins stale - triggering update")
        return 'trigger_tier2_update'
        
    logging.info(f"⏭️  Tier 2 ({family}): Fresh - skipping")
    return None

@task.branch
def check_tier3_condition(**context):
    """Check if Tier 3 family needs update"""
    params = context['params']
    family = params.get('tier_3_coin_family')
    interval = int(os.getenv('CMC_TIER3_INTERVAL_MINUTES', '60'))
    count = get_stale_count_for_family(family, interval)
    
    if count > 0:
        logging.info(f"✅ Tier 3 ({family}): {count} coins stale - triggering update")
        return 'trigger_tier3_update'
        
    logging.info(f"⏭️  Tier 3 ({family}): Fresh - skipping")
    return None

with DAG(
    'tiered_coin_price_ingestion',
    default_args=default_args,
    description='Orchestrator for tiered coin price updates',
    schedule='*/15 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=2,
    tags=['prices', 'orchestrator'],
    params={
        "tier_1_coin_family": Param("T1", type="string", description="Family name for Tier 1 coins"),
        "tier_2_coin_family": Param("T2", type="string", description="Family name for Tier 2 coins"),
        "tier_3_coin_family": Param("T3", type="string", description="Family name for Tier 3 coins"),
    }
) as dag:

    # 1. Update Coin Families
    trigger_family_update = TriggerDagRunOperator(
        task_id='trigger_family_update',
        trigger_dag_id='coin_family_ingestion',
        conf={'bypass_sensor': True, 'force_coin_ingestion': True},
        wait_for_completion=True,
        poke_interval=20,
        allowed_states=['success'],
        failed_states=['failed'],
        deferrable=False
    )

    # 2. Check Conditions
    t1_check = check_tier1_condition()
    t2_check = check_tier2_condition()
    t3_check = check_tier3_condition()
    
    # 3. Trigger Workers (using the main coin_price_ingestion DAG)
    trigger_t1 = TriggerDagRunOperator(
        task_id='trigger_tier1_update',
        trigger_dag_id='coin_price_ingestion',
        conf={
            'targets': "{{ params.tier_1_coin_family }}",
        },
        wait_for_completion=True,
        deferrable=False
    )

    trigger_t2 = TriggerDagRunOperator(
        task_id='trigger_tier2_update',
        trigger_dag_id='coin_price_ingestion',
        conf={
            'targets': "{{ params.tier_2_coin_family }}",
        },
        wait_for_completion=True,
        deferrable=False
    )

    trigger_t3 = TriggerDagRunOperator(
        task_id='trigger_tier3_update',
        trigger_dag_id='coin_price_ingestion',
        conf={
            'targets': "{{ params.tier_3_coin_family }}",
        },
        wait_for_completion=True,
        deferrable=False
    )

    # Dependencies
    trigger_family_update >> [t1_check, t2_check, t3_check]
    
    t1_check >> trigger_t1
    t2_check >> trigger_t2
    t3_check >> trigger_t3
