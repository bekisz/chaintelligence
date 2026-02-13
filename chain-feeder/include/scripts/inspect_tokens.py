
import logging
import os
import requests
from web3 import Web3

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

RPC_URL = "https://cloudflare-eth.com" # Assuming Ethereum-like chain
# If it fails, we might be on a different chain (Unichain?)

TOKENS = [
    "0xbbbb2d4d765c1e455e4896a64ba3883e914abbbb",
    "0xbbbba1ee822c9b8fc134dea6adfc26603a9cbbbb"
]

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

def check_tokens():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error("Failed to connect to RPC")
        return

    for t in TOKENS:
        addr = w3.to_checksum_address(t)
        try:
            contract = w3.eth.contract(address=addr, abi=ERC20_ABI)
            symbol = contract.functions.symbol().call()
            decimals = contract.functions.decimals().call()
            logger.info(f"Address: {t} | Symbol: {symbol} | Decimals: {decimals}")
        except Exception as e:
            logger.info(f"Address: {t} | Failed to fetch details: {e}")

if __name__ == "__main__":
    check_tokens()
