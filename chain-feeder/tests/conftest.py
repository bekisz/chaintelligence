import os
import subprocess
import time
import pytest
import requests

# Configuration
DOCKER_COMPOSE_FILE = "docker-compose.test.yaml"
AIRFLOW_URL = "http://localhost:8085"
AIRFLOW_CONTAINER = "airflow-scheduler-test"

# When running from host, we use these to connect to the exposed ports
os.environ["DB_HOST"] = "localhost"
os.environ["DB_PORT"] = "5435"

def run_command(cmd, shell=True, check=True):
    """Utility to run shell commands and return output."""
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        result.check_returncode()
    return result

@pytest.fixture(scope="function")
def docker_env():
    """Fixture to manage Docker environment for each test."""
    print("\n🏗️  Starting test environment...")
    run_command(f"docker compose -f {DOCKER_COMPOSE_FILE} down -v", check=False)
    run_command(f"docker compose -f {DOCKER_COMPOSE_FILE} up -d", check=False)
    
    # Wait for Airflow
    print("⏳ Waiting for Airflow Webserver health...")
    success = False
    for i in range(90): # Increase to 7.5 mins
        try:
            response = requests.get(f"{AIRFLOW_URL}/api/v2/monitor/health", timeout=2)
            if response.status_code == 200:
                print("✅ Airflow is up!")
                success = True
                break
        except:
            pass
        time.sleep(5)
    
    if not success:
        run_command(f"docker logs airflow-webserver-test | tail -n 50")
        pytest.fail("Airflow failed to start")

    yield
    
    # Only teardown if explicitly requested or if test passed (optional logic)
    if os.getenv("CLEANUP", "true").lower() == "true":
        print("\n🧹 Tearing down test environment...")
        run_command(f"docker compose -f {DOCKER_COMPOSE_FILE} down -v")
    else:
        print("\n🚧 CLEANUP skipped. Environment left running for debugging.")

def wait_for_dag(dag_id):
    """Helper to wait for a DAG to be available in Airflow."""
    print(f"⏳ Waiting for DAG '{dag_id}' to be available...")
    for i in range(120): # Increased timeout for DAG processing
        result = run_command(f"docker exec {AIRFLOW_CONTAINER} airflow dags unpause {dag_id}", check=False)
        if result.returncode == 0:
            # Also verify it shows up in dags list
            check_list = run_command(f"docker exec {AIRFLOW_CONTAINER} airflow dags list", check=False)
            if dag_id in check_list.stdout:
                print(f"✅ DAG '{dag_id}' is available and unpaused!")
                time.sleep(5) # Extra buffer for Airflow to settle
                return True
        time.sleep(5)
    
    print(f"❌ DAG '{dag_id}' not found after timeout. Listing all DAGs:")
    run_command(f"docker exec {AIRFLOW_CONTAINER} airflow dags list")
    print("📜 Dumping Task Logs for all DAGs:")
    run_command(f"docker exec {AIRFLOW_CONTAINER} find /opt/airflow/logs -name '*.log' -exec tail -n 20 {{}} +", check=False)
    return False

def wait_for_dag_run(dag_id, timeout=300):
    """Helper to wait for a DAG run to complete and assert success."""
    import json
    print(f"⏳ Waiting for {dag_id} to finish...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Airflow 3 uses 'airflow dags list-runs <dag_id>'
        cmd = f"docker exec {AIRFLOW_CONTAINER} airflow dags list-runs {dag_id} --output json"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            try:
                runs = json.loads(result.stdout)
                if runs:
                    # Filter for manual runs first
                    manual_runs = [r for r in runs if r.get('run_id', '').startswith('manual__')]
                    
                    if manual_runs:
                        # Sort manual runs by run_after
                        latest_run = sorted(manual_runs, key=lambda x: x.get('run_after', ''), reverse=True)[0]
                    else:
                        # Fallback to any run
                        latest_run = sorted(runs, key=lambda x: x.get('run_after', ''), reverse=True)[0]
                        
                    state = latest_run.get('state')
                    print(f"   Current state of {dag_id} ({latest_run.get('run_id')}): {state}")
                    if state == 'success':
                        return True
                    if state in ['failed', 'failed-manual']:
                        return False
            except Exception as e:
                print(f"   Error parsing dag-runs: {e}")
        
        time.sleep(10)
    return False
