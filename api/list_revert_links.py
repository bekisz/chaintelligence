import sys
import os
import asyncio
import json

ROOT_DIR = '/app'
sys.path.insert(0, os.path.join(ROOT_DIR, 'api'))
sys.path.insert(1, os.path.join(ROOT_DIR, 'chain-feeder'))
sys.path.insert(2, os.path.join(ROOT_DIR, 'chain-feeder', 'routing'))
sys.path.insert(3, os.path.join(ROOT_DIR, 'chain-feeder', 'include'))

import main
analyze = main.analyze

async def run_test():
    response_stream = await analyze(
        start_token='ETH',
        end_token='USDC',
        days=10.0,
        network='Arbitrum'
    )
    
    async for chunk in response_stream.body_iterator:
        if '"type": "result"' in chunk:
            data = json.loads(chunk)
            routes = data['data']['routes']
            for r in routes:
                path_str = r['path']
                if 'Uniswap V4' in path_str and ' -- ' in path_str and path_str.count(' -- ') == 1:
                    # single hop only
                    for hop in r['path_tokens']:
                        if isinstance(hop, dict):
                            fee_clean = hop['fee'].split('|')[0].strip()
                            pid = hop['pool_address']
                            print(f"Tier {fee_clean}: https://revert.finance/#/pool/arbitrum/uniswapv4/{pid}")
                        
if __name__ == '__main__':
    asyncio.run(run_test())
