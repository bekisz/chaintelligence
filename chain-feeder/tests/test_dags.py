import os
import time
import pytest
import requests
import subprocess
import yaml
import json
from verify_data import verify_coin_families, verify_coin_prices, verify_coin_mapping
from conftest import run_command, wait_for_dag, wait_for_dag_run, AIRFLOW_CONTAINER

FAMILY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../include/config/coin-families.yml")

@pytest.fixture
def custom_families():
    """Setup custom coin families for tiered testing."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(FAMILY_CONFIG_PATH), exist_ok=True)
    
    # Backup original if it exists
    original_content = None
    if os.path.exists(FAMILY_CONFIG_PATH):
        with open(FAMILY_CONFIG_PATH, 'r') as f:
            original_content = f.read()
            
    # Create test families as per requirements:
    # T1: BTC and ETH
    # T2: first 20-ranked coins
    # T3: first 35-ranked coins
    test_config = {
        "coin-families": [
            {
                "name": "T1",
                "coin-list": ["BTC", "ETH"]
            },
            {
                "name": "T2",
                "sql-rule": "cmc_rank <= 20"
            },
            {
                "name": "T3",
                "sql-rule": "cmc_rank <= 35"
            }
        ]
    }
    
    print(f"\n📝 Creating custom coin-families.yml at {FAMILY_CONFIG_PATH}...")
    with open(FAMILY_CONFIG_PATH, 'w') as f:
        yaml.dump(test_config, f)
        
    yield
    
    # Restore original
    if original_content:
        print(f"\nRestore original coin-families.yml...")
        with open(FAMILY_CONFIG_PATH, 'w') as f:
            f.write(original_content)

@pytest.mark.parametrize("max_rank", [25])
def test_coin_ingestion(max_rank, docker_env):
    """Test the coin_ingestion DAG using the live scheduler container."""
    print(f"\n🧪 Testing coin_ingestion DAG (max_rank={max_rank})...")
    
    import json
    conf = json.dumps({"max_rank": max_rank, "force_update": True})
    
    assert wait_for_dag("coin_ingestion"), "DAG not found"

    # Trigger instead of test
    cmd = f"docker exec {AIRFLOW_CONTAINER} airflow dags trigger coin_ingestion --conf '{conf}'"
    
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to trigger coin_ingestion: {result.stderr}"
    
    assert wait_for_dag_run("coin_ingestion"), "coin_ingestion DAG failed or timed out"
    print(f"✅ coin_ingestion DAG (max_rank={max_rank}) completed SUCCESSFULLY.")

def test_coin_family_ingestion(docker_env):
    """Test the coin_family_ingestion DAG using the live scheduler container."""
    print("\n🧪 Testing coin_family_ingestion DAG...")
    
    import json
    conf = json.dumps({"bypass_sensor": True, "force_coin_ingestion": True, "max_rank": 20})
    
    assert wait_for_dag("coin_family_ingestion"), "DAG not found"

    # Trigger instead of test
    cmd = f"docker exec {AIRFLOW_CONTAINER} airflow dags trigger coin_family_ingestion --conf '{conf}'"
    
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to trigger coin_family_ingestion: {result.stderr}"
    
    assert wait_for_dag_run("coin_family_ingestion", timeout=600), "coin_family_ingestion DAG failed or timed out"
    print("✅ coin_family_ingestion DAG test completed SUCCESSFULLY.")
    
@pytest.mark.parametrize("targets, test_name", [
    ("AAVE", "Single_AAVE"),
    ("7278, 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48, Stablecoins", "Mixed_ID_Address_Family")
], ids=["Single_AAVE", "Mixed_ID_Address_Family"])
def test_coin_price_ingestion(targets, test_name, docker_env):
    """Test coin_price_ingestion with different targets using parameterization."""
    print(f"\n🧪 Testing coin_price_ingestion ({test_name})...")
    
    import json
    conf = json.dumps({"targets": targets})
    
    assert wait_for_dag("coin_price_ingestion"), "DAG not found"

    cmd = f"docker exec {AIRFLOW_CONTAINER} airflow dags trigger coin_price_ingestion --conf '{conf}'"
    
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    assert result.returncode == 0, f"Failed to trigger: {result.stderr}"
    
    assert wait_for_dag_run("coin_price_ingestion", timeout=600), f"coin_price_ingestion ({test_name}) failed or timed out"
    print(f"✅ coin_price_ingestion ({test_name}) SUCCESSFUL.")

def test_tiered_coin_price_ingestion(custom_families, docker_env):
    """Test the tiered_coin_price_ingestion DAG using the test environment."""
    dag_id = "tiered_coin_price_ingestion"
    
    print(f"\n🧪 Testing {dag_id}...")
    
    # 1. Wait for all relevant DAGs to be available
    for d in [dag_id, "coin_family_ingestion", "coin_price_ingestion"]:
        assert wait_for_dag(d), f"DAG {d} not found"
    
    # 2. Trigger with custom family names
    conf = json.dumps({
        "tier_1_coin_family": "T1",
        "tier_2_coin_family": "T2",
        "tier_3_coin_family": "T3"
    })
    
    print(f"🚀 Triggering {dag_id} with conf: {conf}")
    cmd = f"docker exec {AIRFLOW_CONTAINER} airflow dags trigger {dag_id} --conf '{conf}'"
    subprocess.run(cmd, shell=True, check=True)
    
    # 3. Wait for the orchestrator to finish
    # It triggers: coin_family_ingestion -> (checks) -> coin_price_ingestion (for each tier)
    # This might take a while.
    print(f"⏳ Waiting for {dag_id} orchestrator (900s timeout)...")
    success = wait_for_dag_run(dag_id, timeout=900)
    
    if not success:
        print(f"❌ {dag_id} failed or timed out. Dumping logs:")
        subprocess.run(f"docker exec {AIRFLOW_CONTAINER} find /opt/airflow/logs -name '*.log' -exec tail -n 50 {{}} +", shell=True, check=False)
        pytest.fail(f"{dag_id} execution failed")
        
    print(f"✅ {dag_id} completed successfully!")
