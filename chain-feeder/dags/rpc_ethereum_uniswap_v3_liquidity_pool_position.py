from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
import pendulum
from datetime import timedelta
import logging
import os
from airflow.models import Variable

# Import Engine
from include.rpc_discovery_engine import RpcDiscoveryEngine

logger = logging.getLogger(__name__)

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}


# from airflow.decorators import task # Deprecated
from airflow.sdk import task

@task
def discover_positions_task(**context):
    target_address = os.getenv("TARGET_ADDRESS")
    
    # Check for manual run params
    force_date = context["params"].get("force_start_date")
    batch_size_param = context["params"].get("batch_size")
    
    # networks = ["Arbitrum", "Base", "Ethereum"]
    networks = ["Ethereum"]
    
    for net in networks:
        try:
            logger.info(f"Starting Discovery for {net}")
            engine = RpcDiscoveryEngine(net, target_address, batch_size=batch_size_param)
            engine.discover_positions(force_start_date=force_date)
        except Exception as e:
            logger.error(f"Discovery failed for {net}: {e}")

@task
def enrich_positions_task():
    target_address = os.getenv("TARGET_ADDRESS")
    
    networks = ["Arbitrum", "Base", "Ethereum"]
    
    for net in networks:
        try:
            logger.info(f"Enriching positions for {net}")
            # Use default batch size for enrichment or pass param if needed? 
            # Enrichment uses Multicall batching which is separate constant (50), maybe kept fixed.
            engine = RpcDiscoveryEngine(net, target_address) 
            engine.enrich_positions()
        except Exception as e:
            logger.error(f"Enrichment failed for {net}: {e}")

from airflow.sdk import Param

with DAG(
    'rpc_ethereum_uniswap_v3_liquidity_pool_position',
    default_args=default_args,
    description='Discover and Ingest LP Positions via RPC Logs',
    schedule=timedelta(hours=1),
    start_date=pendulum.today('UTC').add(days=-1),
    catchup=False,
    max_active_runs=1,
    params={
        "force_start_date": Param(
            default=None, 
            type=["null", "string"], 
            description="Force start scan from date (YYYY-MM-DD). Leave empty for incremental scan."
        ),
        "batch_size": Param(
            default=None,
            type=["null", "integer"],
            description="Override RPC Log Batch Size (default: env RPC_LOG_BATCH_SIZE or 2000)"
        )
    }
) as dag:

    # Define tasks
    t1 = discover_positions_task()
    t2 = enrich_positions_task()

    # Define dependencies
    t1 >> t2
