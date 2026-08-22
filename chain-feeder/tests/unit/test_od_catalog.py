"""Pure-Python unit tests for the declarative O&D catalog and reconciliation planner.

No database required: the catalog compiler is exercised against a minimal
in-memory YAML and the planner against a CoverageState lookup struct.
"""
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'include'))

from od_retention import parse_window
from od_catalog import compile_catalog
from reconcile import plan_requirement, CoverageState, summarize_rows


V2_YAML = """
version: 2
sets:
  - id: btc-usd-eth
    name: BTC/USD Ethereum
    selector:
      origin: { family: BTC }
      destination: { family: USD }
      chains: [Ethereum]
      bidirectional: true
    products:
      route.swap_logs: { window: { last_days: 3 } }
      route.daily_stats: { window: { since: "2026-07-01" } }
      pool.daily_stats: { window: { last_days: 60 } }
"""


class TestCatalogCompile(unittest.TestCase):
    def test_v2_products(self):
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
            f.write(V2_YAML)
            path = f.name
        try:
            cat = compile_catalog(path)
            self.assertEqual(cat['version'], 2)
            self.assertEqual(len(cat['sets']), 1)
            s = cat['sets'][0]
            self.assertEqual(s.id, 'btc-usd-eth')
            self.assertFalse(s.chains_all)
            self.assertIn('ethereum', s.chains)
            ids = [p.product_id for p in s.products]
            self.assertIn('route.swap_logs', ids)
            self.assertIn('route.daily_stats', ids)
            self.assertIn('pool.daily_stats', ids)
        finally:
            os.unlink(path)

    def test_v1_shorthand_window(self):
        # shorthand integer product window compiles to rolling
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
            f.write("version: 2\nsets:\n  - id: x\n    selector: { origin: '*', dest: '*' }\n    products:\n      route.daily_stats: 30\n")
            path = f.name
        try:
            cat = compile_catalog(path)
            p = cat['sets'][0].products[0]
            self.assertEqual(p.window, {'kind': 'rolling', 'days': 30})
        finally:
            os.unlink(path)


class TestCoveragePlanner(unittest.TestCase):
    def _set(self, products=None, origin='WBTC', dest='USDC'):
        from od_catalog import SetCatalog, ProductRequirement
        prods = products or [
            ('route.swap_logs', {'kind': 'rolling', 'days': 2}),
            ('route.daily_stats', {'kind': 'rolling', 'days': 2}),
            ('pool.daily_stats', {'kind': 'rolling', 'days': 2}),
        ]
        return SetCatalog(
            id='s1', name='s1', origin=origin, dest=dest, bidirectional=False,
            chains_all=False, chains={'ethereum'},
            products=[ProductRequirement(pid, win) for pid, win in prods])

    def test_fetch_when_raw_missing(self):
        s = self._set()
        today = date(2026, 8, 16)
        st = CoverageState(raw_present=set(), classified=set(), product_present={})
        rows = plan_requirement(s, st, today)
        self.assertTrue(all(r['action'] == 'FETCH' for r in rows if r['product'] == 'route.swap_logs'))

    def test_classify_when_raw_present(self):
        s = self._set()
        today = date(2026, 8, 16)
        raw = {('ethereum', date(2026, 8, 14))}
        st = CoverageState(raw_present=raw, classified=set(), product_present={})
        plan = plan_requirement(s, st, today)
        classify = [r for r in plan if r['product'] == 'route.swap_logs' and r['action'] == 'CLASSIFY']
        self.assertTrue(classify)

    def test_satisfied_when_all_present(self):
        s = self._set()
        today = date(2026, 8, 16)
        raw = {('ethereum', date(2026, 8, 14)), ('ethereum', date(2026, 8, 15)),
               ('ethereum', date(2026, 8, 16))}
        st = CoverageState(raw_present=set(raw), classified=set(raw), product_present={
            'route.swap_logs': raw,
            'route.daily_stats': raw,
            'pool.daily_stats': raw,
        })
        plan = plan_requirement(s, st, today)
        self.assertTrue(all(r['action'] == 'RESOLVE' for r in plan))


if __name__ == '__main__':
    unittest.main()