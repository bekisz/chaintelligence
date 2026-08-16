"""Pure-Python unit tests for the O&D goal-state matching/specificity logic.

These tests exercise the DB-free parts of include/od_retention: window parsing,
pure side/pair matching, specificity ranking and effective-window resolution.
Requirements are constructed with pre-resolved ``_origin``/``_dest`` sides so no
database is required.
"""
import sys
import os
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'include'))

from od_retention import (
    parse_window,
    window_resolve,
    side_matches,
    rule_matches_pair,
    rule_specificity,
    effective_window,
)


def resolved_side(coin_ids=None, symbols=None, addresses=None, wild=False, spec=0):
    return {'wild': wild, 'coin_ids': coin_ids or [], 'symbols': symbols or [],
            'addresses': addresses or [], 'kind': 'test', 'spec': spec}


def req(name, origin, dest, direction='both', chains_all=True, chains=None,
        layers=None, idx=0):
    return {
        'name': name, 'origin': origin, 'dest': dest, 'direction': direction,
        'chains_all': chains_all, 'chains': set(chains) if chains else set(),
        'layers': layers or {}, 'idx': idx,
        '_origin': origin, '_dest': dest,
    }


def pair(oid=1, chain='Ethereum', o_sym='WBTC', d_sym='USDC',
         o_cid=11, d_cid=22, o_contract='0xaaa', d_contract='0xbbb'):
    return {
        'pair_id': oid, 'chain': chain,
        'origin_contract': o_contract, 'dest_contract': d_contract,
        'origin_coin_id': o_cid, 'dest_coin_id': d_cid,
        'origin_symbol': o_sym, 'dest_symbol': d_sym,
    }


class TestWindows(unittest.TestCase):
    def test_rolling_window_inclusive(self):
        win = parse_window({'last_days': 3}, 'x')
        start, end = window_resolve(win, date(2026, 8, 16))
        self.assertEqual((start, end), (date(2026, 8, 13), date(2026, 8, 16)))

    def test_since_resolves_to_today(self):
        win = parse_window({'since': '2026-04-01'}, 'x')
        start, end = window_resolve(win, date(2026, 8, 16))
        self.assertEqual((start, end), (date(2026, 4, 1), date(2026, 8, 16)))

    def test_explicit_bounded(self):
        win = parse_window({'from': '2026-08-13', 'to': '2026-08-15'}, 'x')
        start, end = window_resolve(win, date(2026, 8, 16))
        self.assertEqual((start, end), (date(2026, 8, 13), date(2026, 8, 15)))

    def test_none_means_no_floor(self):
        self.assertIsNone(parse_window(None, 'x'))
        self.assertIsNone(parse_window({}, 'x'))

    def test_invalid_windows_raise(self):
        with self.assertRaises(ValueError):
            parse_window({'last_days': 0}, 'x')
        with self.assertRaises(ValueError):
            parse_window({'from': '2026-08-15', 'to': '2026-08-13'}, 'x')
        with self.assertRaises(ValueError):
            parse_window({'bogus': 1}, 'x')


class TestMatching(unittest.TestCase):
    def test_side_matches_symbol_additive(self):
        res = resolved_side(coin_ids=[11], symbols=['WBTC'])
        self.assertTrue(side_matches(res, '0xaaa', 'WBTC', 11))
        # address side doesn't match, but symbol does
        self.assertTrue(side_matches(res, '0xzzz', 'WBTC', None))
        self.assertFalse(side_matches(res, '0xzzz', 'USDC', None))

    def test_side_matches_address(self):
        res = resolved_side(addresses=['0xaaa'])
        self.assertTrue(side_matches(res, '0xAAA', 'WBTC', 11))
        self.assertFalse(side_matches(res, '0xccc', 'WBTC', 11))

    def test_family_resolves_to_members(self):
        # BTC family resolved to {WBTC, cbBTC}
        res = resolved_side(coin_ids=[11, 12], symbols=['WBTC', 'cbBTC'], spec=2)
        self.assertTrue(side_matches(res, '0x1', 'cbBTC', None))

    def test_wildcard_matches_anything(self):
        res = resolved_side(wild=True)
        self.assertTrue(side_matches(res, '0x1', 'X', 1))

    def test_forward_only_orientation(self):
        r = req('fwd', resolved_side(symbols=['WBTC'], spec=2),
                resolved_side(symbols=['USDC'], spec=2), direction='forward')
        self.assertTrue(rule_matches_pair(r, pair()))                 # WBTC->USDC
        self.assertFalse(rule_matches_pair(r, pair(o_sym='USDC', d_sym='WETH')))

    def test_both_direction_matches_reversed(self):
        r = req('both', resolved_side(symbols=['WBTC'], spec=2),
                resolved_side(symbols=['USDC'], spec=2), direction='both')
        self.assertTrue(rule_matches_pair(r, pair()))                       # WBTC->USDC
        self.assertTrue(rule_matches_pair(r, pair(o_sym='USDC', d_sym='WBTC')))
        self.assertFalse(rule_matches_pair(r, pair(o_sym='WETH', d_sym='DAI')))

    def test_chain_filter(self):
        r = req('eth-only', resolved_side(wild=True, spec=0),
                resolved_side(wild=True, spec=0), chains_all=False, chains=['ethereum'])
        self.assertTrue(rule_matches_pair(r, pair(chain='Ethereum')))
        self.assertFalse(rule_matches_pair(r, pair(chain='Base')))


class TestSpecificity(unittest.TestCase):
    def goal(self, *rules):
        return {'defaults': {}, 'per_chain': [], 'requirements': list(rules)}

    def test_family_pair_overrides_wild_all(self):
        wild = req('*-*', resolved_side(wild=True, spec=0), resolved_side(wild=True, spec=0),
                   layers={'swaps': parse_window({'last_days': 3}, 's')})
        fam = req('btc-usd', resolved_side(symbols=['WBTC', 'cbBTC'], spec=2),
                  resolved_side(symbols=['USDC', 'USDT', 'DAI'], spec=2),
                  layers={'swaps': parse_window({'last_days': 40}, 's')}, idx=1)
        p = pair()
        g = self.goal(wild, fam)
        start, _ = effective_window(p, 'swaps', g, date(2026, 8, 16))
        self.assertEqual(start, date(2026, 8, 16) - timedelta(days=40))
        # a non-BTC-USD pair keeps the wildcard floor
        p2 = pair(oid=2, o_sym='WETH', d_sym='DAI')
        start2, _ = effective_window(p2, 'swaps', g, date(2026, 8, 16))
        self.assertEqual(start2, date(2026, 8, 16) - timedelta(days=3))

    def test_contract_is_more_specific_than_symbol(self):
        sym = req('sym', resolved_side(symbols=['WBTC'], spec=2),
                  resolved_side(symbols=['USDC'], spec=2),
                  layers={'swaps': parse_window({'last_days': 30}, 's')})
        ctr = req('ctr', resolved_side(addresses=['0xaaa'], spec=3),
                  resolved_side(symbols=['USDC'], spec=2),
                  layers={'swaps': parse_window({'last_days': 90}, 's')}, idx=1)
        p = pair()
        start, _ = effective_window(p, 'swaps', self.goal(sym, ctr), date(2026, 8, 16))
        self.assertEqual(start, date(2026, 8, 16) - timedelta(days=90))

    def test_later_rule_wins_on_tie(self):
        a = req('a', resolved_side(symbols=['WBTC'], spec=2),
                resolved_side(symbols=['USDC'], spec=2),
                layers={'swaps': parse_window({'last_days': 10}, 's')}, idx=0)
        b = req('b', resolved_side(symbols=['WBTC'], spec=2),
                resolved_side(symbols=['USDC'], spec=2),
                layers={'swaps': parse_window({'last_days': 60}, 's')}, idx=1)
        start, _ = effective_window(pair(), 'swaps', self.goal(a, b), date(2026, 8, 16))
        self.assertEqual(start, date(2026, 8, 16) - timedelta(days=60))

    def test_layers_independent(self):
        r = req('multi', resolved_side(symbols=['WBTC'], spec=2), resolved_side(symbols=['USDC'], spec=2),
                layers={'route_daily_stats': parse_window({'since': '2026-04-01'}, 'r'),
                        'swaps': parse_window({'last_days': 7}, 's')})
        g = self.goal(r)
        ds = effective_window(pair(), 'route_daily_stats', g, date(2026, 8, 16))
        sw = effective_window(pair(), 'swaps', g, date(2026, 8, 16))
        self.assertEqual(ds[0], date(2026, 4, 1))
        self.assertEqual(sw[0], date(2026, 8, 16) - timedelta(days=7))
        # element not in any requirement + no default floor -> everything deletable
        self.assertEqual(effective_window(pair(), 'route_daily_stats_bucket', g, date(2026, 8, 16)),
                         (None, None))

    def test_default_floor_applies_when_no_requirement(self):
        wild = req('*-*', resolved_side(wild=True, spec=0), resolved_side(wild=True, spec=0),
                   layers={'swaps': parse_window({'last_days': 3}, 's')})
        r = req('btc-usd', resolved_side(symbols=['WBTC'], spec=2), resolved_side(symbols=['USDC'], spec=2),
                layers={'route_daily_stats': parse_window({'since': '2026-04-01'}, 'r')}, idx=1)
        g = {'defaults': {}, 'per_chain': [],
             'requirements': [wild, r]}
        p = pair()
        # BTC-USD has no swaps requirement -> falls to the wildcard rule for swaps
        start, _ = effective_window(p, 'swaps', g, date(2026, 8, 16))
        self.assertEqual(start, date(2026, 8, 16) - timedelta(days=3))
        # and its daily_stats is governed by the specific requirement
        ds = effective_window(p, 'route_daily_stats', g, date(2026, 8, 16))
        self.assertEqual(ds[0], date(2026, 4, 1))


class TestRanking(unittest.TestCase):
    def test_specificity_order(self):
        a = req('a', resolved_side(wild=True, spec=0), resolved_side(wild=True, spec=0))
        b = req('b', resolved_side(spec=2), resolved_side(spec=2))
        c = req('c', resolved_side(addresses=['0x1'], spec=3), resolved_side(spec=2))
        self.assertLess(rule_specificity(a, 'swaps'), rule_specificity(b, 'swaps'))
        self.assertLess(rule_specificity(b, 'swaps'), rule_specificity(c, 'swaps'))
        # explicit window beats rolling at equal token/chain specificity
        r1 = req('r1', resolved_side(spec=2), resolved_side(spec=2),
                 layers={'swaps': parse_window({'last_days': 90}, 's')})
        r2 = req('r2', resolved_side(spec=2), resolved_side(spec=2),
                 layers={'swaps': parse_window({'from': '2026-01-01', 'to': '2026-08-16'}, 's')})
        self.assertLess(rule_specificity(r1, 'swaps'), rule_specificity(r2, 'swaps'))


if __name__ == '__main__':
    unittest.main()