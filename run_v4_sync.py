import sys
import os
os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")
import logging
logging.basicConfig(level=logging.INFO)

from uniswap_v4_history_sync import build_daily_history, sync_tvl_from_graph
build_daily_history.function()
sync_tvl_from_graph.function()
print("DONE!")
