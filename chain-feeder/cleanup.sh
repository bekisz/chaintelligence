#!/bin/bash

# Cleanup script for chain-feeder
# This script stops all containers, removes volumes, and clears logs/temp files

set -e

# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "Stopping and removing containers, networks, and volumes..."
docker compose down -v --remove-orphans

echo "Cleaning up local logs and temporary files..."
rm -rf logs/*
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

echo "Cleanup complete!"
