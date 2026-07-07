"""
Unit test for Aerodrome (Base, Slipstream) swap ingestion.

The Aerodrome Slipstream subgraph is Uniswap-V3-schema-identical, so
UniswapV3Fetcher(network='Base', protocol='Aerodrome') is reused unchanged
apart from the subgraph URL. This test verifies:

  1. The fetcher points at the verified Aerodrome deployment ID.
  2. A V3-shape swap response (from scratch/probe_aerodrome_subgraph.py) is
     normalized into the dict shape PostgresStorage.save_swaps expects.
  3. save_swaps is invoked with network='Base', protocol='Aerodrome'.

Run (plain runner, per repo convention):
  cd chain-feeder/routing && python test_aerodrome_fetcher.py
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# chain-feeder/dags must be on the path so `from common.utils...` resolves.
_DAGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dags'))
if _DAGS_DIR not in sys.path:
    sys.path.insert(0, _DAGS_DIR)

# Ensure a key is present so __init__ builds the authenticated URL shape.
os.environ.setdefault('GRAPH_API_KEY', 'test_key_for_unit_test')

from common.utils import uniswap_utils  # noqa: E402
from common.utils.uniswap_utils import UniswapV3Fetcher  # noqa: E402

AERO_SUBGRAPH_ID = "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"

# Sample swap shape taken verbatim from the probe output (WETH/USDS pool).
SAMPLE_SWAP = {
    "id": "0xee20a44f5e8b0d0755dbcad85f4af05b3c1ec86366a5204d0f7b2eb5421d4e16#1619614",
    "timestamp": "1783409893",
    "transaction": {"id": "0xee20a44f5e8b0d0755dbcad85f4af05b3c1ec86366a5204d0f7b2eb5421d4e16"},
    "token0": {"id": "0x4200000000000000000000000000000000000006", "symbol": "WETH"},
    "token1": {"id": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "symbol": "USDC"},
    "amount0": "0.000705088639025545",
    "amount1": "-1.250348742102622329",
    "amountUSD": "1.249029431169885976400210955436194",
    "pool": {"feeTier": "500"},
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestAerodromeFetcher(unittest.TestCase):
    def test_subgraph_url_points_at_aerodrome_deployment(self):
        fetcher = UniswapV3Fetcher(network="Base", protocol="Aerodrome")
        self.assertIn(AERO_SUBGRAPH_ID, fetcher.subgraph_url)
        self.assertIn("gateway-arbitrum.network.thegraph.com", fetcher.subgraph_url)

    @patch.object(uniswap_utils, 'SYMBOL_TO_COIN_ID', {'WETH': 1, 'USDC': 2})
    @patch.object(uniswap_utils, 'PostgresStorage')
    def test_fetch_normalizes_and_stores_with_aerodrome_protocol(self, mock_storage_cls):
        # First call returns one batch; second call (next page) returns empty.
        responses = [
            _FakeResponse({"data": {"swaps": [SAMPLE_SWAP]}}),
            _FakeResponse({"data": {"swaps": []}}),
        ]
        mock_storage = mock_storage_cls.return_value

        fetcher = UniswapV3Fetcher(network="Base", protocol="Aerodrome")
        fetcher.session = MagicMock()
        fetcher.session.post.side_effect = responses

        # Tiny range around the sample timestamp so the query window covers it.
        from datetime import datetime, timezone, timedelta
        end = datetime.fromtimestamp(1783409893, timezone.utc) + timedelta(seconds=1)
        start = datetime.fromtimestamp(1783409893, timezone.utc) - timedelta(seconds=1)

        fetcher.fetch_swaps(
            start_date=start, end_date=end,
            on_batch_callback=lambda b: mock_storage.save_swaps(b, network='Base', protocol='Aerodrome'),
            collect_results=False,
        )

        # save_swaps must have been called with the Aerodrome protocol label.
        self.assertTrue(mock_storage.save_swaps.called)
        args, kwargs = mock_storage.save_swaps.call_args
        batch = args[0] if args else kwargs.get('swaps')
        self.assertEqual(kwargs.get('network'), 'Base')
        self.assertEqual(kwargs.get('protocol'), 'Aerodrome')
        self.assertEqual(len(batch), 1)

        s = batch[0]
        # Normalized dict shape expected by save_swaps.
        self.assertEqual(s['tx_hash'], "0xee20a44f5e8b0d0755dbcad85f4af05b3c1ec86366a5204d0f7b2eb5421d4e16")
        self.assertEqual(s['token0_symbol'], 'WETH')
        self.assertEqual(s['token1_symbol'], 'USDC')
        self.assertAlmostEqual(s['amount0'], 0.000705088639025545)
        self.assertAlmostEqual(s['amountUSD'], 1.249029431169885976400210955436194)
        # feeTier 500 → 0.05%
        self.assertEqual(s['fee_tier'], '0.05%')


if __name__ == '__main__':
    unittest.main()
