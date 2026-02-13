import sys
import os
import logging
import time

# Add parent dir to path to import dags
sys.path.append(os.path.join(os.path.dirname(__file__), '../../dags'))
import backfill_claims_rpc
from backfill_claims_rpc import scan_batch

logging.basicConfig(level=logging.INFO)

def test_batch_logic():
    # Setup mock position for Token 103718 (Pos 1)
    # Known Claim at Block 24207528
    
    pos = {
        "id": 1, 
        "token_id": 103718, 
        "pool_addr": "0x00b9edc1583bf6ef09ff3a09f6c23ecb57fd7d0bb75625717ec81eed181e22d7",
        "last_scan": 24207520, # Start just before claim
        "c0_addr": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", # WETH
        "c1_addr": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", # USDC
        "d0": 18,
        "d1": 6,
        "name": "Debug Pool"
    }
    
    positions = [pos]
    
    network = "Ethereum"
    protocol = "Uniswap V4"
    rpc = backfill_claims_rpc.get_rpc(network)
    
    print(f"Testing Batch Scan on RPC: {rpc}")
    
    # We want to scan a small range around the event
    # BUT scan_batch logic calculates 'current_block' from RPC.
    # And scans from min_start (position last_scan) to current_block.
    # So if I set last_scan = 24207520, it will scan from 24207521 to current.
    # This covers the event.
    
    claims, current = scan_batch(rpc, network, protocol, positions)
    
    print(f"Claims Result: {claims}")
    
    if len(claims.get(1, [])) > 0:
        print("SUCCESS: Claim found!")
    else:
        print("FAILURE: No claim found.")

if __name__ == "__main__":
    test_batch_logic()
