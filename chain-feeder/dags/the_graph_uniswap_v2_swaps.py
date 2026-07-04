from airflow import DAG
from airflow.sdk import task, Asset
import pendulum
from datetime import datetime, timedelta, timezone
import logging
import requests
import os
import time
import psycopg2
from typing import List, Dict, Optional, Callable

from airflow.providers.postgres.hooks.postgres import PostgresHook

# Asset for metadata tracking
uniswap_v2_swaps_asset = Asset("postgres://postgres/chaintelligence/public/uniswap_v2_swaps")

# Uniswap V2 subgraph ID (Ethereum only)
UNISWAP_V2_SUBGRAPH_ID = "EYCKATKZKLutcRcYvmignYqH3dU3GtD5xLQq2Gf2W3Zx"

MAX_RESULTS_PER_QUERY = 1000
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# ---------------------------------------------------------------------------
# V2 subgraph fetcher — the V2 swap entity schema differs from V3/V4
# ---------------------------------------------------------------------------

class UniswapV2Fetcher:
    """Minimal fetcher for the Uniswap V2 subgraph (Ethereum only)."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
        self.network = "Ethereum"
        self.protocol = "Uniswap V2"

        GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')
        if not GRAPH_API_KEY or GRAPH_API_KEY == 'YOUR_GRAPH_API_KEY':
            self.subgraph_url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{UNISWAP_V2_SUBGRAPH_ID}'
        else:
            self.subgraph_url = f'https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{UNISWAP_V2_SUBGRAPH_ID}'

    def _log(self, message: str):
        if self.verbose:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}")

    def _build_swap_query(self, start_timestamp: int, end_timestamp: int, token_addresses: List[str]) -> str:
        """Build a GraphQL query for V2 swaps.

        V2 swap entity fields:
          id, transaction { id }, timestamp, pair { token0 { id symbol }, token1 { id symbol } },
          amount0In, amount0Out, amount1In, amount1Out, amountUSD, sender, to
        """
        addr_list = str(token_addresses).replace("'", '"')
        return f"""
        {{
          swaps(
            first: {MAX_RESULTS_PER_QUERY}
            orderBy: timestamp
            orderDirection: asc
            where: {{
              timestamp_gte: {start_timestamp}
              timestamp_lte: {end_timestamp}
              pair_: {{ token0_in: {addr_list}, token1_in: {addr_list} }}
            }}
          ) {{
            id
            transaction {{ id }}
            timestamp
            pair {{
              token0 {{ id symbol }}
              token1 {{ id symbol }}
            }}
            amount0In
            amount0Out
            amount1In
            amount1Out
            amountUSD
          }}
        }}
        """

    def _execute_query(self, query: str) -> Optional[Dict]:
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    self.subgraph_url,
                    json={'query': query},
                    timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 200:
                    data = response.json()
                    if 'errors' in data:
                        logging.error(f"GraphQL errors: {data['errors']}")
                        return None
                    return data
                else:
                    logging.warning(f"HTTP {response.status_code}, attempt {attempt + 1}")
            except Exception as e:
                logging.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
        return None

    def fetch_swaps(
        self,
        start_date: datetime,
        end_date: datetime,
        on_batch_callback: Optional[Callable] = None,
        collect_results: bool = True,
    ) -> List[Dict]:
        """Fetch V2 swaps in hourly chunks and return (or callback) normalized swap dicts."""
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())

        # Key tracked token addresses (lowercase) for USDC, USDT, WETH, WBTC, DAI
        tracked_addresses = [
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
            "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
            "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
            "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
        ]

        all_swaps = []
        current_start = start_ts

        while current_start < end_ts:
            current_end = min(current_start + 3600, end_ts)  # 1-hour chunks
            query = self._build_swap_query(current_start, current_end, tracked_addresses)
            result = self._execute_query(query)

            if result and result.get('data', {}).get('swaps'):
                batch = result['data']['swaps']
                normalized = self._normalize_swaps(batch)
                if normalized:
                    if on_batch_callback:
                        on_batch_callback(normalized)
                    if collect_results:
                        all_swaps.extend(normalized)
                    self._log(f"  Fetched {len(normalized)} V2 swaps [{current_start} → {current_end}]")
            else:
                self._log(f"  No V2 swaps [{current_start} → {current_end}]")

            current_start = current_end
            time.sleep(0.3)  # rate limit

        return all_swaps

    def _normalize_swaps(self, raw_swaps: List[Dict]) -> List[Dict]:
        """Convert V2 subgraph swap format to the unified schema used by the routing layer.

        V2 swap events have amount0In/amount0Out/amount1In/amount1Out instead of
        a single amount0/amount1. We reconstruct the net flow per token.
        """
        normalized = []
        for s in raw_swaps:
            try:
                t0_addr = s['pair']['token0']['id'].lower()
                t1_addr = s['pair']['token1']['id'].lower()
                t0_sym = s['pair']['token0']['symbol']
                t1_sym = s['pair']['token1']['symbol']

                ts = int(s['timestamp'])
                tx_hash = s['transaction']['id']
                swap_id = s['id']
                amount_usd = float(s.get('amountUSD', 0))

                if amount_usd < 10.0:
                    continue

                # V2: reconstruct net amounts
                amount0_in = float(s.get('amount0In', 0))
                amount0_out = float(s.get('amount0Out', 0))
                amount1_in = float(s.get('amount1In', 0))
                amount1_out = float(s.get('amount1Out', 0))

                # Net flow: amount0 = amount0In - amount0Out (negative = token0 is output)
                amount0 = amount0_in - amount0_out
                amount1 = amount1_in - amount1_out

                normalized.append({
                    'id': f"v2-{swap_id}",
                    'timestamp': ts,
                    'tx_hash': tx_hash,
                    'token0_address': t0_addr,
                    'token1_address': t1_addr,
                    'token0_symbol': t0_sym,
                    'token1_symbol': t1_sym,
                    'amount0': amount0,
                    'amount1': amount1,
                    'amountUSD': amount_usd,
                    'fee_tier': '0.30%',  # V2 has a single fee tier
                })
            except Exception as e:
                logging.warning(f"Error normalizing V2 swap: {e}")
                continue

        return normalized


# ---------------------------------------------------------------------------
# V2 storage
# ---------------------------------------------------------------------------

class PostgresStorageV2:
    def __init__(self):
        self.conn_str = os.getenv('DATA_WAREHOUSE_DB', '')

    def save_swaps(self, swaps: List[Dict], network: str = "Ethereum", protocol: str = "Uniswap V2"):
        if not swaps:
            return

        conn_str = self.conn_str or os.getenv('DATA_WAREHOUSE_DB', '')
        if not conn_str:
            # Fallback: build from Airflow connection
            from airflow.providers.postgres.hooks.postgres import PostgresHook
            pg_hook = PostgresHook(postgres_conn_id='postgres_default')
            conn_str = pg_hook.get_uri()

        with psycopg2.connect(conn_str) as conn:
            with conn.cursor() as cur:
                insert_query = """
                INSERT INTO uniswap_v2_swaps (
                    id, timestamp, tx_hash, token0_address, token1_address,
                    token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier, network, protocol
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
                """
                data = [
                    (
                        s['id'],
                        datetime.fromtimestamp(s['timestamp'], timezone.utc),
                        s['tx_hash'],
                        s['token0_address'],
                        s['token1_address'],
                        s['token0_symbol'],
                        s['token1_symbol'],
                        s['amount0'],
                        s['amount1'],
                        s['amountUSD'],
                        s['fee_tier'],
                        network,
                        protocol
                    ) for s in swaps
                ]
                cur.executemany(insert_query, data)
            conn.commit()


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

with DAG(
    'the_graph_uniswap_v2_swaps',
    default_args=default_args,
    description='Fetch Uniswap V2 swaps for tracked tokens on Ethereum (Airflow 3)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=7),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi', 'v2'],
) as dag:

    @task(outlets=[uniswap_v2_swaps_asset])
    def fetch_and_store_v2_swaps(**context):
        """Fetch Uniswap V2 swaps for Ethereum."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Ethereum', 30)
        force_backfill = 'backfill_days' in conf

        network = 'Ethereum'
        protocol = 'Uniswap V2'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v2_swaps WHERE network = %s AND protocol = %s",
            parameters=(network, protocol)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} {protocol} swaps from {start_date} to {end_date}")
        fetcher = UniswapV2Fetcher(verbose=True)
        storage = PostgresStorageV2()

        def save_batch(batch):
            storage.save_swaps(batch, network=network, protocol=protocol)

        num_swaps = fetcher.fetch_swaps(
            start_date=start_date,
            end_date=end_date,
            on_batch_callback=save_batch,
            collect_results=False
        )
        logging.info(f"Fetched and saved {num_swaps} unique swaps for {network} {protocol}")

    fetch_and_store_v2_swaps()