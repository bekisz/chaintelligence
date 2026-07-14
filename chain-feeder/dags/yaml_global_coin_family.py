from airflow.sdk import task, dag, Param
from airflow.models import Variable
from airflow.sdk.bases.sensor import PokeReturnValue
from airflow.providers.postgres.hooks.postgres import PostgresHook
import os
import logging
import pendulum
from datetime import timedelta

@dag(
    'yaml_global_coin_family',
    schedule='@weekly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=5,
    tags=['config', 'coin_family', 'ingestion'],
    params={
        'bypass_sensor': Param(False, type='boolean', description='Bypass file change sensor and force update'),
        'force_coin_ingestion': Param(True, type='boolean', description='Force coin_ingestion DAG run'),
        'max_rank': Param(200, type='integer', description='Max rank for coin_ingestion'),
    },
    default_args={
        'owner': 'airflow',
        'retries': 0,
    }
)
def yaml_global_coin_family_dag():
    
    @task.sensor(poke_interval=30, timeout=3600, mode='reschedule', soft_fail=True)
    def wait_for_config_change(bypass_sensor: bool = False):
        """
        Pokes the file modification time and compares it with the last recorded mtime.
        If bypass_sensor is True, it returns immediately.
        """
        config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
        logging.info(f"Checking config at {config_path}. Raw bypass_sensor: {bypass_sensor} ({type(bypass_sensor)})")
        
        if not os.path.exists(config_path):
            logging.warning(f"Config file NOT found: {config_path}")
            return PokeReturnValue(is_done=False)
            
        current_mtime = str(os.path.getmtime(config_path))
        
        # Handle param string -> bool
        if isinstance(bypass_sensor, str):
            bypass_sensor = bypass_sensor.lower() in ('true', '1', 'yes')
            logging.info(f"Normalized bypass_sensor (from str): {bypass_sensor}")
            
        if bypass_sensor:
            logging.info(f"🚀 Sensor bypassed! Force updating. Current mtime: {current_mtime}")
            return PokeReturnValue(is_done=True, xcom_value=current_mtime)

        last_mtime = Variable.get("coin_family_yml_mtime", default_var="0")
        
        if current_mtime != last_mtime:
            logging.info(f"Change detected! New mtime: {current_mtime}, previous: {last_mtime}")
            return PokeReturnValue(is_done=True, xcom_value=current_mtime)
        
        return PokeReturnValue(is_done=False)

    @task.sensor(poke_interval=60, timeout=3600, mode='reschedule')
    def wait_for_coin_table():
        """
        Waits until the coin table has been populated by the CMC mapper.
        """
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        try:
            # Check if there are any coins with CMC rank or mapping data
            count = pg_hook.get_first("SELECT COUNT(*) FROM coin WHERE cmc_id IS NOT NULL")[0]
            if count > 0:
                logging.info(f"Coin table is populated with {count} CMC-mapped coins.")
                return PokeReturnValue(is_done=True)
        except Exception as e:
            logging.error(f"Error checking coin table: {e}")
            
        logging.info("Coin table is empty or not yet mapped by CMC, waiting...")
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

    from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator

    trigger_ingestion = TriggerDagRunOperator(
        task_id="trigger_coin_ingestion",
        trigger_dag_id="cmc_global_coin_metadata",
        conf={
            "force_update": "{{ params.force_coin_ingestion }}",
            "max_rank": "{{ params.max_rank }}"
        },
        wait_for_completion=True,
        poke_interval=30,
        reset_dag_run=True,
        deferrable=False
    )

    # Pass the DAG parameter to the sensor task
    mtime = wait_for_config_change(bypass_sensor="{{ params.bypass_sensor }}")
    
    mtime >> trigger_ingestion >> wait_for_coin_table() >> update_coin_families(mtime)

# Initialize the DAG and assign to variable for Airflow discovery
dag = yaml_global_coin_family_dag()
