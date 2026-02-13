
import logging
import os
import requests
import json
from web3 import Web3

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

RPC_URL = os.getenv("RPC_URL")
if not RPC_URL:
    RPC_URL = "https://cloudflare-eth.com"
    
TX_HASH = "0x9383d291fe4bbdee39637a1198ced618f7c525095aa2a790a57a631278c6a7f5" # The one from logs

def make_rpc_request(method, params):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    try:
        resp = requests.post(RPC_URL, json=payload, timeout=10)
        # logger.info(f"Raw Response: {resp.text[:200]}")
        return resp.json()
    except Exception as e:
        logger.error(f"RPC Error: {e}")
        return None

def inspect_tx():
    logger.info(f"Inspecting TX: {TX_HASH}")
    
    # 1. Get Transaction
    tx_data = make_rpc_request("eth_getTransactionByHash", [TX_HASH])
    if not tx_data or 'result' not in tx_data:
        logger.error("Failed to get transaction")
        return

    tx = tx_data['result']
    value = int(tx['value'], 16)
    logger.info(f"Transaction Value (Native ETH): {value} ({value/1e18} ETH)")
    logger.info(f"To: {tx['to']}") # Expecting PositionManager?
    
    # 2. Get Receipt (Logs)
    receipt_data = make_rpc_request("eth_getTransactionReceipt", [TX_HASH])
    if not receipt_data or 'result' not in receipt_data:
        logger.error("Failed to get receipt")
        return

    logs = receipt_data['result']['logs']
    logger.info(f"Found {len(logs)} logs")
    
    for l in logs:
        addr = l['address'].lower()
        topics = l['topics']
        data = l['data']
        
        # Log everything for now
        logger.info(f"Log: {addr} | Topics: {topics} | Data: {data}")

if __name__ == "__main__":
    inspect_tx()
