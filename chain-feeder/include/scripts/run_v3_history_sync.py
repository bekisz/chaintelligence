import sys
import os
import logging

# Ensure Airflow is loaded properly if needed
os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")

from uniswap_v3_history_sync import sync_pools_from_swaps, build_daily_history, sync_tvl_from_graph

logging.basicConfig(level=logging.INFO)

def main():
    logging.info("Running sync_pools_from_swaps...")
    sync_pools_from_swaps.function()

    logging.info("Running build_daily_history...")
    build_daily_history.function()
    
    logging.info("Running sync_tvl_from_graph...")
    sync_tvl_from_graph.function()
    
    logging.info("DONE!")

if __name__ == "__main__":
    main()
