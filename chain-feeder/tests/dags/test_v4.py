import os
import sys
import logging

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from uniswap_v4_range_fetcher import fetch_v4_position_range_data

logging.basicConfig(level=logging.DEBUG)

def test():
    key = os.environ.get("GRAPH_API_KEY")
    if not key:
        print("ERROR: No GRAPH_API_KEY found in environment.")
        # Try to hardcode one if known, or fail
        return

    print(f"Testing V4 Fetch with Key: {key[:4]}***")
    
    # Test ID 124668 (Should be EURCV-EURC)
    label = "Test Position (Token ID: 124668)"
    res = fetch_v4_position_range_data(label, "Ethereum", key)
    
    if res:
        print("SUCCESS:")
        print(res)
    else:
        print("FAILURE: Returned None")

if __name__ == "__main__":
    test()
