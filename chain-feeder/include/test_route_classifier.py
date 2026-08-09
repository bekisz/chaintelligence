"""Unit tests for the pure topology derivation in route_classifier.

Mirrors the fixture shape from api/routing/test_route_analyzer.py: legs carry
pool_id, amount0/amount1 (positive = spent token), log_index, and token
contract addresses. These tests exercise only the SQL-free pure functions.
"""
import unittest

from route_classifier import (
    chains_in_tx,
    canonical_key,
    route_volume,
    _input_flow,
)


def leg(tx, idx, pool, t0, t1, a0, a1, usd=1000.0):
    return {
        'tx_hash': tx, 'log_index': idx, 'pool_id': pool,
        'token0': t0, 'token1': t1,
        'amount0': a0, 'amount1': a1, 'amount_usd': usd,
        'chain_id': 1, 'ts': None,
    }


class TestChainsInTx(unittest.TestCase):
    def test_single_hop_chain(self):
        legs = [leg('tx1', 1, 101, 'A', 'B', 100, -50)]
        chains = chains_in_tx(legs)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]['tokens'], ['A', 'B'])
        self.assertEqual(chains[0]['pools'], [101])
        self.assertEqual(chains[0]['legs'], [legs[0]])

    def test_multi_hop_chain(self):
        legs = [
            leg('tx1', 1, 101, 'A', 'B', 100, -50),
            leg('tx1', 2, 102, 'B', 'C', 50, -25),
            leg('tx1', 3, 103, 'C', 'D', 25, -12),
        ]
        chains = chains_in_tx(legs)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]['tokens'], ['A', 'B', 'C', 'D'])
        self.assertEqual(chains[0]['pools'], [101, 102, 103])

    def test_disjoint_swaps_split_into_chains(self):
        legs = [
            leg('tx2', 1, 101, 'WETH', 'USDC', 100, -50),
            leg('tx2', 2, 202, 'AAVE', 'USDT', 80, -40),
        ]
        chains = chains_in_tx(legs)
        self.assertEqual(len(chains), 2)
        self.assertEqual(chains[0]['tokens'], ['WETH', 'USDC'])
        self.assertEqual(chains[1]['tokens'], ['AAVE', 'USDT'])

    def test_round_trip_stays_one_chain(self):
        legs = [
            leg('tx1', 1, 101, 'ETH', 'USDC', 10, -1000),
            leg('tx1', 2, 102, 'USDC', 'ETH', 1000, -10),
        ]
        chains = chains_in_tx(legs)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]['tokens'], ['ETH', 'USDC', 'ETH'])

    def test_input_flow_uses_positive_amount(self):
        self.assertEqual(_input_flow(leg('x', 1, 101, 'A', 'B', 0, -5)), ('B', 'A'))
        self.assertEqual(_input_flow(leg('x', 1, 101, 'A', 'B', 5, -3)), ('A', 'B'))


class TestRouteVolume(unittest.TestCase):
    def test_single_leg_always_counts(self):
        chain = {
            'legs': [leg('tx1', 1, 101, '0xa', '0xb', 100, -50, usd=500.0)],
        }
        self.assertEqual(route_volume(chain, '0xa'), 500.0)

    def test_multi_hop_counts_only_origin_inputs(self):
        # The classifier lowercases contract addresses before comparing, mirror
        # that in the fixtures.
        chain = {
            'legs': [
                leg('tx1', 1, 101, '0xa', '0xb', 100, -50, usd=1000.0),
                leg('tx1', 2, 102, '0xb', '0xc', 50, -50, usd=1000.0),
            ]
        }
        # Only hop 1 consumes the origin input token.
        self.assertEqual(route_volume(chain, '0xa'), 1000.0)
        # A round-trip A->B->A consumes the origin only on the first hop.
        chain2 = {
            'legs': [
                leg('tx1', 1, 101, '0xa', '0xb', 100, -50, usd=1000.0),
                leg('tx1', 2, 102, '0xb', '0xa', 50, -1000, usd=1200.0),
            ]
        }
        self.assertEqual(route_volume(chain2, '0xa'), 1000.0)
        # A three-hop A->B->A->C consumes the origin on hop1 and hop3.
        chain3 = {
            'legs': [
                leg('tx1', 1, 101, '0xa', '0xb', 100, -50, usd=1000.0),
                leg('tx1', 2, 102, '0xb', '0xa', 50, -1000, usd=1200.0),
                leg('tx1', 3, 103, '0xa', '0xc', 30, -100, usd=500.0),
            ]
        }
        self.assertEqual(route_volume(chain3, '0xa'), 1500.0)

    def test_missing_usd_is_ignored(self):
        legx = leg('tx1', 1, 101, '0xa', '0xb', 100, -50, usd=None)
        chain = {'legs': [legx]}
        self.assertEqual(route_volume(chain, '0xa'), 0.0)


class TestCanonicalKey(unittest.TestCase):
    def test_key_encodes_pair_and_pools(self):
        self.assertEqual(canonical_key(7, [101, 102]),
                         "7:101:102")
        self.assertEqual(canonical_key(7, [101, 22]),
                         "7:101:22")
        self.assertNotEqual(canonical_key(7, [101, 102]),
                            canonical_key(7, [102, 101]))
        self.assertNotEqual(canonical_key(7, [101]),
                            canonical_key(8, [101]))


if __name__ == '__main__':
    unittest.main()