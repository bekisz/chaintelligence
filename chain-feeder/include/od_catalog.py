"""Declarative O&D catalog.

This is the v2 front-end of the goal state: ``config/ods-goal-state.yaml`` is
*compiled* into a catalog of O&D sets and the data products each set requests,
rather than being treated only as a retention/pruning rule set.

The catalog is intentionally DB-free (pure Python) so the set/product semantics
are unit-testable, mirroring ``od_retention``.

Product catalog (product_id -> spec). Adding a new data product later means
adding a row here plus a materializer, not changing the O&D engine.

Reuses the selectors/window grammar from ``od_retention`` (``parse_window``,
``_parse_chains``) so the YAML stays familiar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

import yaml

try:
    # when imported as `include.od_catalog`
    from .od_retention import _normalize_requirement, load_goal_state, parse_window
except ImportError:
    # when included directly with PATH=<repo>/chain-feeder/include
    from od_retention import _normalize_requirement, load_goal_state, parse_window

CONFIG_BASENAME = 'ods-goal-state.yaml'


def _config_candidates() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(here, '..', '..', 'config', CONFIG_BASENAME),
        os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'config', CONFIG_BASENAME),
        os.path.join('/app', 'config', CONFIG_BASENAME),
    ]


# ---------------------------------------------------------------------------
# Product registry
# ---------------------------------------------------------------------------
# grain:              the physical grain the materializer produces
# requires_classification: whether route assignment must happen first
# durable:            whether rows survive raw-staging purge
# coverage_rule:      how to test "is this product present for a day"

@dataclass(frozen=True)
class ProductDef:
    product_id: str
    grain: str
    requires_classification: bool
    durable: bool
    physical_table: str
    coverage_rule: str = 'present'  # 'present' | 'classified' | 'raw'

    def __post_init__(self):
        if self.coverage_rule not in ('present', 'classified', 'raw'):
            raise ValueError(f"unknown coverage_rule {self.coverage_rule!r} for {self.product_id}")


PRODUCTS: Dict[str, ProductDef] = {
    'route.swap_logs': ProductDef(
        'route.swap_logs', 'event', requires_classification=True, durable=True,
        physical_table='classified_swap_event', coverage_rule='classified'),
    'route.daily_stats': ProductDef(
        'route.daily_stats', 'route_day', requires_classification=True, durable=True,
        physical_table='route_daily_stats', coverage_rule='present'),
    'route.daily_stats_buckets': ProductDef(
        'route.daily_stats_buckets', 'route_day_bucket', requires_classification=True,
        durable=True, physical_table='route_daily_stats_bucket', coverage_rule='present'),
    'pool.daily_stats': ProductDef(
        'pool.daily_stats', 'pool_day', requires_classification=True, durable=True,
        physical_table='liquidity_pool_daily_stats', coverage_rule='present'),
    'pool.daily_stats_buckets': ProductDef(
        'pool.daily_stats_buckets', 'pool_day_bucket', requires_classification=True,
        durable=True, physical_table='liquidity_pool_daily_stats_bucket', coverage_rule='present'),
    'pool.position_snapshots': ProductDef(
        'pool.position_snapshots', 'pool_day', requires_classification=True, durable=True,
        physical_table='liquidity_pool_position_snapshot', coverage_rule='present'),
}


# ---------------------------------------------------------------------------
# Catalog model
# ---------------------------------------------------------------------------

@dataclass
class ProductRequirement:
    product_id: str
    window: Optional[Dict[str, Any]]  # parsed window spec (od_retention grammar)


@dataclass
class SetCatalog:
    """A declared O&D set with a selector and the products it requests."""
    id: str
    name: str
    origin: str
    dest: str
    bidirectional: bool
    chains_all: bool
    chains: set
    products: List[ProductRequirement]  # only products actually requested
    # legacy requirement retained for backward-compatible window semantics
    requirement: Optional[Dict[str, Any]] = None

    def product_ids(self) -> List[str]:
        return [p.product_id for p in self.products if p.product_id in PRODUCTS]


def _window_for(cat: Dict[str, Any], product_id: str) -> Optional[Dict[str, Any]]:
    """Extract a product's window from the YAML (returns parsed window spec)."""
    from od_retention import parse_window
    raw = cat.get(product_id)
    if raw is None:
        return None
    if isinstance(raw, dict) and 'window' in raw:
        raw = raw['window']
    if isinstance(raw, dict) and any(k in raw for k in ('last_days', 'since', 'from', 'to')):
        return parse_window(raw, f"product {product_id}")
    if isinstance(raw, (int,)) and raw >= 1:
        # shorthand: route.daily_stats: 30  => last_days 30
        return {'kind': 'rolling', 'days': raw}
    return None


def _parse_selector(term) -> str:
    """A selector side may be a scalar or a small map; return the canonical term."""
    if isinstance(term, str):
        return term
    if isinstance(term, dict):
        for k in ('family', 'symbol', 'address', 'contract', 'any', 'all'):
            v = term.get(k)
            if v:
                return str(v)
        if term.get('wild'):
            return '*'
    return '*'


def _split_chains(chains) -> tuple:
    """Return (chains_all, set_of_chain_names) from a chain selector."""
    if chains is None:
        return True, set()
    if isinstance(chains, str):
        text = chains.strip()
        if not text or text.lower() in ('all', '*'):
            return True, set()
        return False, {c.strip().lower() for c in text.split(',') if c.strip()}
    if isinstance(chains, list):
        names = [str(c).strip().lower() for c in chains if str(c).strip()]
        return (not names), set(names)
    return True, set()


def compile_catalog(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Compile ``config/ods-goal-state.yaml`` (or a v2 sh) into catalog records.

    Returns ``{'version', 'sets': [SetDef...], 'products': PRODUCTS}``.
    """
    path = config_path
    if path is None:
        for c in _config_candidates():
            if os.path.exists(c):
                path = c
                break
    if path is None or not os.path.exists(path):
        return {'path': None, 'sets': [], 'products': PRODUCTS}

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    version = raw.get('version', 1)
    cated: List[SetCatalog] = []

    if version == 2 and isinstance(raw.get('sets'), list):
        cated = _compile_v2(raw['sets'])
    else:
        # legacy: compile the `requirements` list into sets
        cated = _compile_v1(raw.get('requirements') or [])

    return {'version': version, 'path': path, 'sets': cated, 'products': PRODUCTS}


def _compile_v2(sets: List[Dict[str, Any]]) -> List[SetCatalog]:
    out: List[SetCatalog] = []
    for row in sets:
        pid = str(row.get('id') or row.get('name') or 'set')
        sel = row.get('selector') or row
        origin = _parse_selector(sel.get('origin', '*'))
        dest = _parse_selector(sel.get('destination', sel.get('dest', '*')))
        chains_all, chains = _split_chains(sel.get('chains'))
        products: List[ProductRequirement] = []
        for product_id, pcfg in (row.get('products') or {}).items():
            win = None
            if isinstance(pcfg, dict) and 'window' in pcfg:
                win = parse_window(pcfg['window'], f"product {product_id}.window")
            elif isinstance(pcfg, dict) and any(k in pcfg for k in ('last_days', 'since', 'from', 'to')):
                win = parse_window(pcfg, f"product {product_id}")
            elif isinstance(pcfg, int) and pcfg >= 1:
                win = {'kind': 'rolling', 'days': pcfg}
            products.append(ProductRequirement(product_id, win))
        out.append(SetCatalog(
            id=pid, name=str(row.get('name') or pid),
            origin=origin, dest=dest, bidirectional=bool(sel.get('bidirectional', True)),
            chains_all=chains_all, chains=chains, products=products,
        ))
    return out


def _compile_v1(requirements: List[Dict[str, Any]]) -> List[SetCatalog]:
    out: List[SetCatalog] = []
    for idx, row in enumerate(requirements):
        # reuse od_retention normalization for the requirement semantics
        req = _normalize_requirement(row, idx)
        products: List[ProductRequirement] = []
        layer_to_product = {
            'swaps': 'route.swap_logs',
            'route_daily_stats': 'route.daily_stats',
            'route_daily_stats_bucket': 'route.daily_stats_buckets',
            'liquidity_pool': 'pool.position_snapshots',
            'liquidity_pool_daily_stats': 'pool.daily_stats',
            'liquidity_pool_daily_stats_bucket': 'pool.daily_stats_buckets',
        }
        for layer, win in (req.get('layers') or {}).items():
            pid = layer_to_product.get(layer)
            if pid:
                products.append(ProductRequirement(pid, win))
        out.append(SetCatalog(
            id=f"req-{idx+1}", name=str(req['name']),
            origin=str(req['origin']), dest=str(req['dest']),
            bidirectional=bool(req.get('bidirectional', True)),
            chains_all=bool(req['chains_all']), chains=set(req['chains']),
            products=products,
        ))
    return out