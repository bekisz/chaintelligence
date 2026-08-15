"""Unit tests for api/resources/serializer.py (no server, no DB needed).

Run with:  cd api/resources && python test_serializer.py
"""

import unittest

from serializer import (
    build_document, include_matches_any, make_resource, parse_fields,
    parse_include, rel_data, rel_data_many, relationship_target,
    resource_types,
)


class TestParseInclude(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(parse_include(None, 'od'), [])
        self.assertEqual(parse_include('', 'od'), [])

    def test_single_path(self):
        self.assertEqual(
            parse_include('routes.hops.pool', 'od'),
            [['routes', 'hops', 'pool']]
        )

    def test_multiple_paths(self):
        paths = parse_include('routes.hops.pool,routes.hops.pool.coin0', 'od')
        self.assertEqual(paths, [
            ['routes', 'hops', 'pool'],
            ['routes', 'hops', 'pool', 'coin0'],
        ])

    def test_comma_spaces_ignored(self):
        self.assertEqual(
            parse_include(' routes . hops , routes.hops.pool ', 'od'),
            [['routes', 'hops'], ['routes', 'hops', 'pool']]
        )

    def test_unknown_segment_raises(self):
        with self.assertRaises(ValueError):
            parse_include('routes.bogus', 'od')
        with self.assertRaises(ValueError):
            parse_include('coins.hops', 'od')


class TestIncludeMatches(unittest.TestCase):
    def setUp(self):
        self.paths = parse_include('routes.hops.pool,routes.hops.pool.coin0', 'od')

    def test_prefix_match(self):
        self.assertTrue(include_matches_any(['routes'], self.paths))
        self.assertTrue(include_matches_any(['routes', 'hops'], self.paths))
        self.assertTrue(include_matches_any(['routes', 'hops', 'pool'], self.paths))
        self.assertTrue(include_matches_any(['routes', 'hops', 'pool', 'coin0'], self.paths))

    def test_no_match(self):
        self.assertFalse(include_matches_any(['routes', 'hops', 'pool', 'coin1'], self.paths))
        self.assertFalse(include_matches_any(['routes', 'hops', 'pool', 'coin0', 'contracts'], self.paths))


class TestParseFields(unittest.TestCase):
    def test_shorthand(self):
        fields = parse_fields('od[chain,origin_symbol],pool[fee_bps]')
        self.assertEqual(fields['od'], {'chain', 'origin_symbol'})
        self.assertEqual(fields['pool'], {'fee_bps'})

    def test_empty(self):
        self.assertEqual(parse_fields(None), {})
        self.assertEqual(parse_fields(''), {})


class TestRelationshipTarget(unittest.TestCase):
    def test_known(self):
        t = relationship_target('od', 'routes')
        self.assertEqual(t, {'type': 'route', 'many': True})

    def test_unknown(self):
        self.assertIsNone(relationship_target('od', 'bogus'))


class TestBuildDocument(unittest.TestCase):
    def test_single_resource(self):
        node = make_resource('od', '2ac53c78a580597e',
                             attributes={'chain': 'BNB'})
        doc = build_document(node)
        self.assertEqual(doc['data']['type'], 'od')
        self.assertEqual(doc['data']['id'], '2ac53c78a580597e')
        self.assertNotIn('included', doc)

    def test_included_dedupes_by_type_id(self):
        dup = [make_resource('coin', 4), make_resource('coin', 4)]
        doc = build_document(make_resource('od', 'x'), included=dup)
        self.assertEqual(len(doc['included']), 1)

    def test_relationships(self):
        rels = {
            'origin_coin': rel_data('coin', 4),
            'routes': rel_data_many([('route', 'abc'), ('route', 'def')]),
        }
        node = make_resource('od', 'x', relationships=rels)
        self.assertEqual(node['relationships']['origin_coin'], {'data': {'type': 'coin', 'id': 4}})
        self.assertEqual(len(node['relationships']['routes']['data']), 2)

    def test_null_relationship(self):
        self.assertEqual(rel_data('coin', None), {'data': None})

    def test_resource_types_loaded(self):
        types = set(resource_types())
        self.assertIn('od', types)
        self.assertIn('route', types)
        self.assertIn('hop', types)
        self.assertIn('pool', types)
        self.assertIn('coin', types)
        self.assertIn('coin_family', types)
        self.assertIn('chain', types)


if __name__ == '__main__':
    unittest.main()
