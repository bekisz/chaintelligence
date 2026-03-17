# Function to setup environment
setup_env() {
    echo "🏗️  Setting up test environment..."
    docker compose -f docker-compose.test.yaml up -d || echo "⚠️  Docker Compose reported an issue, but proceeding to health check..."
    
    echo "⏳ Waiting for Airflow Webserver health..."
    for i in {1..60}; do
        if curl -s http://localhost:8085/health > /dev/null; then
            echo "✅ Airflow is up!"
            return 0
        fi
        sleep 5
    done
    echo "❌ Airflow failed to start"
    echo "📜 Webserver Logs:"
    docker logs airflow-webserver-test 2>&1 | tail -n 20
    echo "📜 Scheduler Logs:"
    docker logs airflow-scheduler-test 2>&1 | tail -n 20
    exit 1
}

# Function to teardown environment
teardown_env() {
    echo "🧹 Tearing down test environment..."
    docker compose -f docker-compose.test.yaml down -v
}

# Helper to wait for DAG to be available
wait_for_dag() {
    local dag_id=$1
    echo "⏳ Waiting for DAG '$dag_id' to be available..."
    for i in {1..60}; do
        if docker exec airflow-scheduler-test airflow dags unpause "$dag_id" > /dev/null 2>&1; then
            echo "✅ DAG '$dag_id' is available and unpaused!"
            return 0
        fi
        sleep 2
    done
    echo "❌ DAG '$dag_id' not found after 120s"
    echo "📜 Scheduler Logs:"
    docker logs airflow-scheduler-test 2>&1 | tail -n 20
    echo "📜 DAG Processor Logs:"
    docker logs airflow-dag-processor-test 2>&1 | tail -n 20
    echo "📊 Container Status:"
    docker ps -a
    docker stats --no-stream
    return 1
}

# Function to run Coin Family Updater Test
test_family_updater() {
    echo "=============================================="
    echo "🧪 TESTING COIN FAMILY UPDATER"
    echo "=============================================="
    setup_env
    
    if ! wait_for_dag "coin_family_updater"; then
        teardown_env
        exit 1
    fi

    echo "🔄 Triggering Coin Family Updater..."
    docker exec airflow-scheduler-test airflow dags trigger coin_family_updater -c '{"bypass_sensor": true}'
    
    echo "⏳ Waiting for DAG execution (90s)..."
    sleep 90
    
    echo "🔍 Verifying Coin Families..."
    docker cp verify_data.py airflow-scheduler-test:/opt/airflow/verify_data.py
    if docker exec airflow-scheduler-test python /opt/airflow/verify_data.py families; then
        echo "✅ Coin Family Updater Test PASSED"
    else
        echo "❌ Coin Family Updater Test FAILED"
        teardown_env
        exit 1
    fi
    
    teardown_env
}

# Function to run Actual Coin Price Ingestion Test
test_price_ingestion() {
    echo "=============================================="
    echo "🧪 TESTING ACTUAL COIN PRICE INGESTION"
    echo "=============================================="
    setup_env
    
    # We need families for prices? Actually target resolution needs coin table, which is inited by DB script.
    # But does it need families? resolve_targets might use families.
    # If we run price ingestion targeting 'ETH', it just needs coin table.
    
    if ! wait_for_dag "actual_coin_price_ingestion"; then
        teardown_env
        exit 1
    fi

    echo "💰 Triggering Price Ingestion (ETH)..."
    docker exec airflow-scheduler-test airflow dags trigger actual_coin_price_ingestion -c '{"targets": "ETH", "force_cmc_mapping": false}'
    
    echo "⏳ Waiting for DAG execution (120s)..."
    sleep 120
    
    echo "🔍 Verifying Coin Prices..."
    docker cp verify_data.py airflow-scheduler-test:/opt/airflow/verify_data.py
    if docker exec airflow-scheduler-test python /opt/airflow/verify_data.py prices; then
        echo "✅ Price Ingestion Test PASSED"
    else
        echo "❌ Price Ingestion Test FAILED"
        teardown_env
        exit 1
    fi
    
    teardown_env
}

# Main execution
# Ensure clean slate
teardown_env

test_family_updater
test_price_ingestion

echo "✅ ALL TESTS SUITES COMPLETED SUCCESSFULLY!"
exit 0
