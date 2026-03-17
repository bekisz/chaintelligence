import sys
import os
import logging

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")

from uniswap_v3_history_sync import sync_pools_from_swaps, build_daily_history, sync_tvl_from_graph

logging.basicConfig(level=logging.INFO)

def main():
    try:
        logging.info("Running sync_pools_from_swaps...")
        sync_pools_from_swaps.function()
    except Exception as e:
        logging.error(f"Error in sync_pools: {e}")

    try:
        logging.info("Running build_daily_history...")
        build_daily_history.function()
    except Exception as e:
        logging.error(f"Error in build_daily: {e}")
        
    try:
        logging.info("Running sync_tvl_from_graph...")
        sync_tvl_from_graph.function()
    except Exception as e:
        logging.error(f"Error in sync_tvl: {e}")
    
    logging.info("DONE!")

if __name__ == "__main__":
    main()
