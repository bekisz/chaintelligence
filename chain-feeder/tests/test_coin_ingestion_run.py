import os
import subprocess
import json
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_coin_ingestion_test(max_rank=20):
    """
    Run the coin_ingestion DAG as a test with specific parameters.
    """
    logging.info(f"🧪 Starting coin_ingestion DAG test (max_rank={max_rank})...")
    
    # Airflow CLI command to test a DAG
    # Using 'airflow dags test' which runs the DAG tasks linearly in the current process
    conf = json.dumps({"max_rank": max_rank, "force_update": True})
    
    command = [
        "docker", "exec", "chaintelligence-airflow-scheduler-1",
        "airflow", "dags", "test", "coin_ingestion",
        "--conf", conf
    ]
    
    logging.info(f"Executing: {' '.join(command)}")
    
    try:
        # We use Popen to stream the output in real-time
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            
        process.wait()
        
        if process.returncode == 0:
            logging.info("✅ coin_ingestion DAG test completed SUCCESSFULLY.")
            return True
        else:
            logging.error(f"❌ coin_ingestion DAG test FAILED with exit code {process.returncode}")
            return False
            
    except Exception as e:
        logging.error(f"💥 An error occurred while running the test: {e}")
        return False

if __name__ == "__main__":
    success = run_coin_ingestion_test(max_rank=20)
    if not success:
        sys.exit(1)
