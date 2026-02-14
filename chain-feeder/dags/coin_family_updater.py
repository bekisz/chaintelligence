from airflow.sdk import task, dag
from airflow.models import Variable
from airflow.sdk.bases.sensor import PokeReturnValue
from airflow.providers.postgres.hooks.postgres import PostgresHook
import os
import logging
import pendulum
from datetime import timedelta

@dag(
    'coin_family_updater',
    schedule='* * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['config', 'coin_family'],
    default_args={
        'owner': 'airflow',
        'retries': 0,
    }
)
def coin_family_updater_dag():
    
    @task.sensor(poke_interval=30, timeout=3600, mode='reschedule', soft_fail=True)
    def wait_for_config_change():
        """
        Pokes the file modification time and compares it with the last recorded mtime.
        """
        config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
        
        if not os.path.exists(config_path):
            logging.warning(f"Config file not found: {config_path}")
            return PokeReturnValue(is_done=False)
            
        current_mtime = str(os.path.getmtime(config_path))
        last_mtime = Variable.get("coin_family_yml_mtime", default_var="0")
        
        if current_mtime != last_mtime:
            logging.info(f"Change detected! New mtime: {current_mtime}, previous: {last_mtime}")
            return PokeReturnValue(is_done=True, xcom_value=current_mtime)
        
        return PokeReturnValue(is_done=False)

    @task
    def update_coin_families(new_mtime: str):
        """
        Syncs the coin families to the database and updates the recorded mtime.
        """
        from include.coin_family_resolver import CoinFamilyResolver
        
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        conn_uri = pg_hook.get_uri()
        config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
        
        logging.info(f"Syncing families from {config_path}")
        resolver = CoinFamilyResolver(config_path, conn_uri)
        resolver.sync_to_db()
        
        Variable.set("coin_family_yml_mtime", new_mtime)
        logging.info(f"Successfully synced families and updated mtime Variable to {new_mtime}")

    mtime = wait_for_config_change()
    update_coin_families(mtime)

# Initialize the DAG
coin_family_updater_dag()
