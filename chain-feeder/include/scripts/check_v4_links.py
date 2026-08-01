import sys, os
sys.path.insert(0, '/app/chain-feeder/dags')
from common.utils.uniswap_utils import UniswapV4Fetcher
import psycopg2

pids = {205: ('USDT', 'USDC'), 225: ('USDS', 'USDT'), 206: ('USDT', 'USDC'), 167792: ('USDE', 'USDC')}
fetcher = UniswapV4Fetcher(verbose=True, network='Ethereum')

for pid, (sym0, sym1) in pids.items():
    print(f'\n=== Pool {pid} ({sym0}/{sym1}) ===')
    conn = psycopg2.connect('dbname=chaintelligence user=airflow password=airflow host=postgres')
    cur = conn.cursor()
    cur.execute('SELECT pool_id, pool_address, coin0_id, coin1_id, fee_bps FROM liquidity_pool WHERE id = %s', (pid,))
    db_pool_id, db_pool_addr, c0, c1, fee = cur.fetchone()
    conn.close()

    print(f'DB pool_id:      {db_pool_id[:42]}...')
    print(f'DB pool_address: {db_pool_addr[:42]}...')

    # Query subgraph by pool_id
    q1 = '{ pools(where: {id: "' + db_pool_id + '"}) { id } }'
    result = fetcher._execute_query(q1)
    if result and result.get('data', {}).get('pools'):
        print(f'  pool_id MATCHES subgraph')
    else:
        print(f'  pool_id NOT in subgraph')

    # Query subgraph by pool_address
    q2 = '{ pools(where: {id: "' + db_pool_addr + '"}) { id } }'
    result = fetcher._execute_query(q2)
    if result and result.get('data', {}).get('pools'):
        print(f'  pool_address MATCHES subgraph')
    else:
        print(f'  pool_address NOT in subgraph')

    # Try to find by token pair and fee
    conn2 = psycopg2.connect('dbname=chaintelligence user=airflow password=airflow host=postgres')
    cur2 = conn2.cursor()
    cur2.execute('''SELECT c.symbol, cc.contract_address FROM coin c
        JOIN coin_contract cc ON cc.coin_id = c.coin_id
        JOIN chain ch ON cc.chain_id = ch.id
        WHERE c.coin_id = %s AND ch.name = 'Ethereum' ''', (c0,))
    r0 = cur2.fetchone()
    cur2.execute('''SELECT c.symbol, cc.contract_address FROM coin c
        JOIN coin_contract cc ON cc.coin_id = c.coin_id
        JOIN chain ch ON cc.chain_id = ch.id
        WHERE c.coin_id = %s AND ch.name = 'Ethereum' ''', (c1,))
    r1 = cur2.fetchone()
    conn2.close()

    if r0 and r1:
        a0, a1 = r0[1].lower(), r1[1].lower()
        t0, t1 = sorted([a0, a1])
        fee_bips = int(float(fee) * 100)
        print(f'  Sorted tokens: {t0[:20]}..., {t1[:20]}...')
        print(f'  Fee bips: {fee_bips}')

        q3 = '{ pools(where: {token0: "' + t0 + '", token1: "' + t1 + '", feeTier: "' + str(fee_bips) + '"}) { id token0 {id} token1 {id} feeTier } }'
        result = fetcher._execute_query(q3)
        if result and result.get('data', {}).get('pools'):
            for p in result['data']['pools']:
                print(f'  Subgraph has pool: id={p["id"][:42]}... feeTier={p["feeTier"]}')
        else:
            print(f'  No pool found for this pair/fee')
