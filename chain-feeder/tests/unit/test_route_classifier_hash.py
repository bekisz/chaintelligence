import unittest
from route_classifier import compute_pair_id, compute_route_id, canonical_key, chains_in_tx


class TestHashIDs(unittest.TestCase):
    def test_compute_pair_id_deterministic(self):
        pid1 = compute_pair_id(1, "0x123", "0x456")
        pid2 = compute_pair_id(1, "0X123", "0X456")
        self.assertEqual(pid1, pid2)
        self.assertIsInstance(pid1, int)

    def test_compute_route_id_deterministic(self):
        pid = compute_pair_id(1, "0x123", "0x456")
        rid1 = compute_route_id(pid, [100, 200])
        rid2 = compute_route_id(pid, [100, 200])
        self.assertEqual(rid1, rid2)
        self.assertIsInstance(rid1, int)

    def test_distinct_routes_distinct_ids(self):
        pid = compute_pair_id(1, "0x123", "0x456")
        rid1 = compute_route_id(pid, [100, 200])
        rid2 = compute_route_id(pid, [100, 201])
        self.assertNotEqual(rid1, rid2)

    def test_chains_in_tx(self):
        legs = [
            {'log_index': 0, 'amount0': 100, 'token0': 'A', 'token1': 'B', 'pool_id': 1},
            {'log_index': 1, 'amount0': 100, 'token0': 'B', 'token1': 'C', 'pool_id': 2},
        ]
        chains = chains_in_tx(legs)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]['tokens'], ['A', 'B', 'C'])
        self.assertEqual(chains[0]['pools'], [1, 2])


if __name__ == '__main__':
    unittest.main()
