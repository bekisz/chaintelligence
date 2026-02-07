import sys
import os
from datetime import datetime, timedelta

# Add paths to sys.path
ROOT_DIR = '/app'
sys.path.insert(0, os.path.join(ROOT_DIR, 'chain-feeder', 'dags'))

from common.utils.uniswap_utils import UniswapV3Fetcher
from dotenv import load_dotenv

load_dotenv()

def test_graph_tvl():
    fetcher = UniswapV3Fetcher(verbose=True)
    
    # SLVON and XAUT
    SLVON = "0xF3e4872e6a4cF365888D93b6146a2bAA7348F1A4"
    XAUT = "0x68749665ff8d2d112fa859aa293f07a622782f38"
    
    pairs = [
        (SLVON, XAUT, 10000, "SLVON-XAUT 1.0%")
    ]
    
    start_date = datetime.now() - timedelta(days=7)
    
    for addr0, addr1, fee, label in pairs:
        print(f"\nChecking {label}...")
        try:
            data = fetcher.fetch_pool_daily_data(addr0, addr1, fee, start_date)
            if data:
                print(f"  Found {len(data)} records")
                for d in data[:2]:
                    print(f"    Date: {d['date']}, TVL: ${d['tvl_usd']:,.2f}, Vol: ${d['volume_usd']:,.2f}")
            else:
                print("  No data found in graph.")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    test_graph_tvl()
