import subprocess
try:
    print("--- Docker PS ---")
    print(subprocess.check_output(["docker", "ps", "-a"], text=True))
    print("--- Postgres Logs ---")
    print(subprocess.check_output(["docker", "logs", "--tail", "20", "chain-feeder-postgres-1"], text=True))
except subprocess.CalledProcessError as e:
    print(f"Error: {e}")
    print(e.output)
except Exception as e:
    print(f"Exception: {e}")
