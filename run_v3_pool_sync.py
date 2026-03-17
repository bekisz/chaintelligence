import sys
import os
import logging
os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")
logging.basicConfig(level=logging.INFO)
from uniswap_v3_history_sync import sync_pools_from_swaps
print("Running sync pools...")
sync_pools_from_swaps.function()
print("Done")
