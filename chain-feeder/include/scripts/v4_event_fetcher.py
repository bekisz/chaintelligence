
import requests
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class V4EventFetcher:
    def __init__(self, rpc_url):
        self.rpc_url = rpc_url
        # Known V4 Managers to filter against for "Internal" accounting
        self.MANAGERS = [
            "0x000000000004444c5dc75cb358380d2e3de08a90", # Core
            "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e", # PM
        ]

    def _rpc_call(self, method, params):
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        try:
            resp = requests.post(self.rpc_url, json=payload, timeout=20)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"RPC Error {method}: {e}")
        return None

    def find_creation_tx(self, token_id, start_block="earliest"):
        """
        Finds the creation transaction (NFT Mint) for a given Token ID.
        Returns: (tx_hash, block_number, timestamp, is_batch, manager_address)
        """
        tid_hex = hex(int(token_id))[2:] # Remove 0x
        topic3 = "0x" + tid_hex.zfill(64)
        
        # Search for Transfer(0x0, to, tokenId) -> Mint
        # Topic0: Transfer
        # Topic1: 0x0...0 (From)
        # Topic2: Any (To)
        # Topic3: TokenID
        
        # Default start block for V4 (Ethereum Mainnet approx 21M+)
        # If "earliest" is passed, override with safe V4 default to avoid 2018 false positives
        if start_block == "earliest":
             start_block = "0x1406F40" # ~Block 21,000,000 (Late 2024 / Early 2025)
             
        params = [{
            "fromBlock": start_block,
            "toBlock": "latest",
            "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", # Transfer
                "0x0000000000000000000000000000000000000000000000000000000000000000", # From Mint
                None,
                topic3
            ]
        }]
        
        data = self._rpc_call("eth_getLogs", params)
        if data and "result" in data and data["result"]:
            log = data["result"][0]
            tx_hash = log["transactionHash"]
            block_num = int(log["blockNumber"], 16)
            manager_address = log["address"]
            
            # Get Timestamp
            ts_data = self._rpc_call("eth_getBlockByNumber", [log["blockNumber"], False])
            ts = int(ts_data["result"]["timestamp"], 16) if ts_data and "result" in ts_data else 0
            
            # Check if Batch (Multiple Mints in same TX?)
            # We can do this lazily or in parsing.
            is_batch = False # Default
            
            return tx_hash, block_num, ts, is_batch, manager_address
            
        return None, None, None, False, None

    def get_position_tokens(self, token_id, pm_address):
        """Authoritative on-chain token0/token1 for a V4 position via the
        PositionManager's getPoolAndPositionInfo(uint256). These always match
        the deposit tx (unlike coin_contract addresses, which can be stale).
        Returns (token0, token1) or (None, None) if the position is gone / call fails.
        Mirrors verify_v4_position_rpc in graph_discovery_client.py.
        """
        if not pm_address:
            return None, None
        selector = "0x7ba03aad"  # getPoolAndPositionInfo(uint256)
        calldata = selector + format(int(token_id), '064x')
        data = self._rpc_call("eth_call", [{"to": pm_address, "data": calldata}, "latest"])
        if not data or "result" not in data or not data["result"] or data["result"] == "0x":
            return None, None
        raw = data["result"][2:]
        if len(raw) < 128:  # need at least 2 words (token0, token1)
            return None, None
        words = [raw[i:i + 64] for i in range(0, len(raw), 64)]
        return "0x" + words[0][-40:], "0x" + words[1][-40:]

    def _get_decimals(self, token_addr):
        """On-chain ERC20 decimals() for token_addr; returns int or None."""
        if not token_addr:
            return None
        data = self._rpc_call("eth_call", [{"to": token_addr, "data": "0x313ce567"}, "latest"])
        if not data or "result" not in data or not data["result"] or data["result"] == "0x":
            return None
        try:
            return int(data["result"], 16)
        except (ValueError, TypeError):
            return None

    def get_token_amounts_from_tx(self, tx_hash, token0, token1, decimals0, decimals1, extra_manager=None):
        """
        Parses a transaction to find net inputs for token0 and token1.
        Returns: (amount0, amount1, status_msg)
        """
        # Get Receipt to see all logs
        logs = []
        data = self._rpc_call("eth_getTransactionReceipt", [tx_hash])
        
        if data and "result" in data and data["result"]:
            logs = data["result"]["logs"]
        else:
             # Fallback: getTransaction -> Block -> getLogs
             logger.warning(f"Receipt failed for {tx_hash}. Trying fallback...")
             tx_data = self._rpc_call("eth_getTransactionByHash", [tx_hash])
             if tx_data and "result" in tx_data:
                 block = tx_data["result"]["blockNumber"]
                 # Get all logs for block
                 l_data = self._rpc_call("eth_getLogs", [{"fromBlock": block, "toBlock": block}])
                 if l_data and "result" in l_data:
                     # Filter for this TX
                     logs = [l for l in l_data["result"] if l["transactionHash"] == tx_hash]
        
        if not logs:
             return 0, 0, "No Logs Found"
        
        # 1. Detect Batch: Count standard NFT transfers (ERC721 Transfer topic)
        # If > 1 Transfer with different Topic3, it's a batch.
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        # We assume standard NFT transfer has 4 topics.
        nft_transfers = [l for l in logs if len(l['topics']) == 4 and l['topics'][0] == transfer_topic]
        
        # Verify if they are indeed NFT transfers (mint from 0x0)
        mints = [l for l in nft_transfers if len(l['topics']) > 1 and int(l['topics'][1], 16) == 0]
        
        if len(mints) > 1:
            return 0, 0, f"Batch Transaction ({len(mints)} mints). Amounts ambiguous."

        # 2. Sum ERC20 Transfers involving Managers
        net0 = 0
        net1 = 0
        
        t0 = token0.lower()
        t1 = token1.lower()
        managers = [m.lower() for m in self.MANAGERS]
        
        if extra_manager:
            managers.append(extra_manager.lower())
        
        for l in logs:
            # ERC20 Transfer: Topic0=Transfer, 2 Indexed (From, To), Data=Value
            if l['topics'][0] == transfer_topic and len(l['topics']) == 3:
                # Use robust slicing for 32->20 bytes address extraction
                # topic is 32 bytes (66 chars with 0x), address is last 20 bytes (40 chars)
                try:
                    src = '0x' + l['topics'][1][-40:].lower()
                    dst = '0x' + l['topics'][2][-40:].lower()
                    token = l['address'].lower()
                    
                    try:
                        val = int(l['data'], 16)
                    except:
                        val = 0 # Skip empty data
                    
                    # Input: User -> Manager (Positive)
                    if dst in managers:
                        if token == t0: net0 += val
                        elif token == t1: net1 += val
                    
                    # Output: Manager -> User (Refund/Negative)
                    if src in managers:
                        if token == t0: net0 -= val
                        elif token == t1: net1 -= val
                except IndexError:
                    continue

        # Convert
        a0 = net0 / (10**decimals0) if decimals0 else net0
        a1 = net1 / (10**decimals1) if decimals1 else net1
        
        return a0, a1, "Success"

