"""
Unit tests for the Stable-Pair Shortcut Finder.

Tests the core logic with mock data — no database required.
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# Ensure routing modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock config before importing shortcut_finder
mock_config = MagicMock()
mock_config.DATA_WAREHOUSE_DB = 'dbname=test'
mock_config.TOKENS = {}
mock_config.ADDRESS_TO_SYMBOL = {}
sys.modules['config'] = mock_config

from shortcut_finder import ShortcutFinder, ShortcutOpportunity, STANDARD_FEE_TIERS


class TestShortcutOpportunity(unittest.TestCase):
    """Test the ShortcutOpportunity data class."""

    def test_pair_label(self):
        opp = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        self.assertEqual(opp.pair_label, 'USDC/DAI')

    def test_is_cross_family(self):
        opp_same = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        self.assertFalse(opp_same.is_cross_family)

        opp_cross = ShortcutOpportunity('USDC', 'EURC', 'USD', 'EUR')
        self.assertTrue(opp_cross.is_cross_family)

    def test_calculate_economics(self):
        opp = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        opp.total_divertable_volume = 1_000_000  # $1M over 30 days
        opp.proposed_shortcut_fee = 0.01  # 0.01%

        opp.calculate_economics([100_000, 500_000], 30)

        # Daily volume = $1M / 30 = $33,333
        # Daily revenue = $33,333 * 0.01% = $3.33
        self.assertAlmostEqual(opp.daily_revenue_by_tvl[100_000], 3.33, places=1)

        # APR @ $100K TVL = ($3.33 / $100K) * 365 = 1.22%
        self.assertAlmostEqual(opp.apr_by_tvl[100_000], 0.0122, places=3)

    def test_zero_volume_economics(self):
        opp = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        opp.total_divertable_volume = 0
        opp.proposed_shortcut_fee = 0.01

        opp.calculate_economics([100_000], 30)
        self.assertEqual(len(opp.daily_revenue_by_tvl), 0)


class TestShortcutFinderLogic(unittest.TestCase):
    """Test internal logic of ShortcutFinder without DB."""

    def setUp(self):
        # Patch DB-dependent init
        with patch('shortcut_finder.CoinFamilyResolver') as MockResolver, \
             patch('shortcut_finder.PostgresFetcher') as MockFetcher:

            mock_resolver = MockResolver.return_value
            mock_resolver.families = [
                {'name': 'USD', 'correlated': True, 'coin-list': ['USDC', 'DAI', 'USDT']},
                {'name': 'EUR', 'correlated': True, 'coin-list': ['EURC', 'EURI']},
                {'name': 'ETH', 'correlated': True, 'coin-list': ['WETH', 'STETH']},
            ]
            mock_resolver.resolve_family.side_effect = lambda name: {
                'USD': {'USDC', 'DAI', 'USDT'},
                'EUR': {'EURC', 'EURI'},
                'ETH': {'WETH', 'STETH'},
            }.get(name, set())

            mock_fetcher = MockFetcher.return_value
            mock_fetcher.fetch_latest_prices.return_value = {
                'USDC': 1.0, 'DAI': 1.0, 'USDT': 1.0,
                'WETH': 2500.0, 'STETH': 2500.0,
                'EURC': 1.05, 'EURI': 1.05,
            }
            mock_fetcher.fetch_swaps.return_value = []

            self.finder = ShortcutFinder(verbose=False)
            self.finder.family_resolver = mock_resolver
            self.finder.fetcher = mock_fetcher

    def test_parse_fee_pct_percentage(self):
        self.assertAlmostEqual(self.finder._parse_fee_pct('0.05%|v3'), 0.05)
        self.assertAlmostEqual(self.finder._parse_fee_pct('0.3%'), 0.3)
        self.assertAlmostEqual(self.finder._parse_fee_pct('1.0%'), 1.0)

    def test_parse_fee_pct_bips(self):
        self.assertAlmostEqual(self.finder._parse_fee_pct('500'), 0.05)
        self.assertAlmostEqual(self.finder._parse_fee_pct('3000'), 0.3)
        self.assertAlmostEqual(self.finder._parse_fee_pct('10000'), 1.0)

    def test_parse_fee_pct_raw(self):
        self.assertAlmostEqual(self.finder._parse_fee_pct('0.05'), 0.05)
        self.assertAlmostEqual(self.finder._parse_fee_pct('0.3'), 0.3)

    def test_parse_fee_pct_invalid(self):
        self.assertEqual(self.finder._parse_fee_pct('abc'), 0.0)
        self.assertEqual(self.finder._parse_fee_pct(''), 0.0)

    def test_pick_shortcut_fee(self):
        # Cumulative 0.35% -> best undercut is 0.30%
        self.assertEqual(self.finder._pick_shortcut_fee(0.35), 0.30)

        # Cumulative 0.6% -> best undercut is 0.30%
        self.assertEqual(self.finder._pick_shortcut_fee(0.6), 0.30)

        # Cumulative 0.06% -> best undercut is 0.05%
        self.assertEqual(self.finder._pick_shortcut_fee(0.06), 0.05)

        # Cumulative 0.02% -> best undercut is 0.01%
        self.assertEqual(self.finder._pick_shortcut_fee(0.02), 0.01)

        # Cumulative 0.005% -> still 0.01% (minimum)
        self.assertEqual(self.finder._pick_shortcut_fee(0.005), 0.01)

        # Cumulative 1.5% -> best undercut is 1.0%
        self.assertEqual(self.finder._pick_shortcut_fee(1.5), 1.0)

    def test_is_volatile_intermediary(self):
        family_tokens = {'USDC', 'DAI', 'USDT'}
        self.assertTrue(self.finder._is_volatile_intermediary('WETH', family_tokens))
        self.assertTrue(self.finder._is_volatile_intermediary('WBTC', family_tokens))
        self.assertFalse(self.finder._is_volatile_intermediary('USDC', family_tokens))
        self.assertFalse(self.finder._is_volatile_intermediary('DAI', family_tokens))

    def test_generate_intra_family_pairs(self):
        self.finder.families = None
        self.finder.cross_family = False

        candidates = self.finder._generate_candidate_pairs()

        # USD: C(3,2)=3 pairs, EUR: C(2,2)=1, ETH: C(2,2)=1 => 5 total
        self.assertEqual(len(candidates), 5)

        # Verify some specific pairs exist
        pair_sets = [frozenset([c[0], c[1]]) for c in candidates]
        self.assertIn(frozenset(['USDC', 'DAI']), pair_sets)
        self.assertIn(frozenset(['USDC', 'USDT']), pair_sets)
        self.assertIn(frozenset(['EURC', 'EURI']), pair_sets)
        self.assertIn(frozenset(['WETH', 'STETH']), pair_sets)

    def test_generate_cross_family_pairs(self):
        self.finder.families = ['USD', 'EUR']
        self.finder.cross_family = True

        candidates = self.finder._generate_candidate_pairs()

        # USD intra: 3, EUR intra: 1, USD×EUR cross: 3*2=6 => 10 total
        self.assertEqual(len(candidates), 10)

        pair_sets = [frozenset([c[0], c[1]]) for c in candidates]
        self.assertIn(frozenset(['USDC', 'EURC']), pair_sets)
        self.assertIn(frozenset(['DAI', 'EURI']), pair_sets)

    def test_analyze_pair_with_multihop_via_volatile(self):
        """Test that a multi-hop route USDC -> WETH -> DAI is detected."""
        swaps = [
            # Transaction: USDC -> WETH (first hop)
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'USDC',
                'token1_symbol': 'WETH',
                'amount0': 10000,   # User sold 10K USDC
                'amount1': -4,      # Got 4 WETH
                'amountUSD': 10000,
                'fee_tier': '0.05%|v3',
                'protocol': 'v3',
            },
            # Transaction: WETH -> DAI (second hop)
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'WETH',
                'token1_symbol': 'DAI',
                'amount0': 4,       # User sold 4 WETH
                'amount1': -9950,   # Got ~9950 DAI
                'amountUSD': 10000,
                'fee_tier': '0.3%|v3',
                'protocol': 'v3',
            },
        ]

        with patch.object(self.finder, '_check_existing_pool', return_value=(None, None)):
            opp = self.finder._analyze_pair(
                'USDC', 'DAI', 'USD', 'USD',
                swaps, period_days=30
            )

        self.assertIsNotNone(opp)
        self.assertEqual(opp.token_a, 'USDC')
        self.assertEqual(opp.token_b, 'DAI')
        self.assertEqual(len(opp.multihop_routes), 1)
        self.assertEqual(opp.multihop_routes[0]['intermediaries'], ['WETH'])
        self.assertGreater(opp.total_divertable_volume, 0)
        self.assertAlmostEqual(opp.multihop_routes[0]['cumulative_fee_pct'], 0.35)
        # Shortcut fee should undercut 0.35%
        self.assertEqual(opp.proposed_shortcut_fee, 0.30)

    def test_analyze_pair_direct_only_skipped(self):
        """Test that a direct route (1 hop) is NOT flagged as a shortcut opportunity."""
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'USDC',
                'token1_symbol': 'DAI',
                'amount0': 10000,
                'amount1': -9999,
                'amountUSD': 10000,
                'fee_tier': '0.01%|v3',
                'protocol': 'v3',
            },
        ]

        with patch.object(self.finder, '_check_existing_pool', return_value=(None, None)):
            opp = self.finder._analyze_pair(
                'USDC', 'DAI', 'USD', 'USD',
                swaps, period_days=30
            )

        # Should be None because there's no divertable multi-hop volume
        self.assertIsNone(opp)

    def test_analyze_pair_multihop_within_family_skipped(self):
        """Multi-hop within family (USDC -> USDT -> DAI) should NOT be flagged
        because USDT is in the family."""
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'USDC',
                'token1_symbol': 'USDT',
                'amount0': 10000,
                'amount1': -9999,
                'amountUSD': 10000,
                'fee_tier': '0.01%|v3',
                'protocol': 'v3',
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'USDT',
                'token1_symbol': 'DAI',
                'amount0': 9999,
                'amount1': -9998,
                'amountUSD': 10000,
                'fee_tier': '0.01%|v3',
                'protocol': 'v3',
            },
        ]

        with patch.object(self.finder, '_check_existing_pool', return_value=(None, None)):
            opp = self.finder._analyze_pair(
                'USDC', 'DAI', 'USD', 'USD',
                swaps, period_days=30
            )

        # USDT is in USD family, so it's not volatile — no shortcut opportunity
        self.assertIsNone(opp)

    def test_analyze_pair_below_threshold(self):
        """Volume below min_volume should be filtered out."""
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'USDC',
                'token1_symbol': 'WETH',
                'amount0': 100,   # Only $100
                'amount1': -0.04,
                'amountUSD': 100,
                'fee_tier': '0.05%|v3',
                'protocol': 'v3',
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'WETH',
                'token1_symbol': 'DAI',
                'amount0': 0.04,
                'amount1': -99,
                'amountUSD': 100,
                'fee_tier': '0.3%|v3',
                'protocol': 'v3',
            },
        ]

        self.finder.min_volume = 10_000  # $10K min

        with patch.object(self.finder, '_check_existing_pool', return_value=(None, None)):
            opp = self.finder._analyze_pair(
                'USDC', 'DAI', 'USD', 'USD',
                swaps, period_days=30
            )

        self.assertIsNone(opp)


class TestOutputFormatting(unittest.TestCase):
    """Test output formatting."""

    def setUp(self):
        with patch('shortcut_finder.CoinFamilyResolver'), \
             patch('shortcut_finder.PostgresFetcher'):
            self.finder = ShortcutFinder(verbose=False)

    def test_format_results_empty(self):
        result = self.finder.format_results([], 30)
        self.assertIn("No stable-pair shortcut opportunities found", result)

    def test_format_results_with_opportunity(self):
        opp = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        opp.total_divertable_volume = 1_000_000
        opp.total_divertable_txns = 100
        opp.current_cumulative_fee = 0.35
        opp.proposed_shortcut_fee = 0.30
        opp.dominant_route = {
            'path': 'USDC -- 0.05%|v3 --> WETH -- 0.3%|v3 --> DAI',
            'intermediaries': ['WETH'],
            'cumulative_fee_pct': 0.35,
            'individual_fees': [0.05, 0.30],
            'volume': 800_000,
            'count': 80,
        }
        opp.calculate_economics([100_000], 30)

        result = self.finder.format_results([opp], 30)

        self.assertIn('USDC/DAI', result)
        self.assertIn('WETH', result)
        self.assertIn('0.35%', result)
        self.assertIn('0.30%', result)

    def test_to_json(self):
        opp = ShortcutOpportunity('USDC', 'DAI', 'USD', 'USD')
        opp.total_divertable_volume = 500_000
        opp.total_divertable_txns = 50
        opp.current_cumulative_fee = 0.35
        opp.proposed_shortcut_fee = 0.30
        opp.dominant_route = {
            'path': 'USDC -- 0.05%|v3 --> WETH -- 0.3%|v3 --> DAI',
            'intermediaries': ['WETH'],
            'cumulative_fee_pct': 0.35,
            'volume': 500_000,
            'count': 50,
        }
        opp.calculate_economics([100_000], 30)

        result = self.finder.to_json([opp], 30)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['pair'], 'USDC/DAI')
        self.assertEqual(result[0]['token_a'], 'USDC')
        self.assertFalse(result[0]['is_cross_family'])
        self.assertAlmostEqual(result[0]['fee_saving_pct'], 0.05)


if __name__ == '__main__':
    unittest.main()
