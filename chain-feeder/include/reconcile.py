"""O&D reconciliation planner.

The planner answers, for each declared O&D set product and each required day,
*which worker must act*:

- ``FETCH``         raw source swaps are absent for that (chain, protocol, utc_day)
- ``CLASSIFY``      raw swaps present but not yet route-classified
- ``MATERIALIZE``   classified but the requested product fact is missing
- ``RESOLVE``       no action needed this pass (product satisfied / coverage met)
- ``UNAVAILABLE``   source cannot replay the requested history

This decouples the four states the legacy engine conflated (raw present vs
classified vs product present), so a classified-coverage gap never triggers a
Graph re-fetch.

It is DB-light and pure-Python: coverage *state* is provided in as a lookup
struct (mirroring ``od_retention`` tests), so the decision logic is unit
testable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set

from od_retention import window_resolve
from od_catalog import PRODUCTS, SetCatalog


WORKERS = ('FETCH', 'CLASSIFY', 'MATERIALIZE', 'RESOLVE', 'UNAVAILABLE')


@dataclass
class CoverageState:
    """Read-model of what exists, keyed to drive the planner.

    ``raw_present``     set of (chain_key, utc_day) where raw source rows exist
    ``classified``      set of (chain_key, utc_day) where classification is done
    ``product_present`` map product_id -> set of (chain_key, utc_day) that is materialized
    """
    raw_present: Set[tuple] = field(default_factory=set)
    classified: Set[tuple] = field(default_factory=set)
    product_present: Dict[str, Set[tuple]] = field(default_factory=dict)

    def __post_init__(self):
        self.raw_present = set(self.raw_present)
        self.classified = set(self.classified)
        self.product_present = {k: set(v) for k, v in self.product_present.items()}


def _chain_set(s: SetCatalog) -> Set[str]:
    if s.chains_all:
        return {'ethereum', 'arbitrum', 'base', 'bnb'}
    return {c.lower() for c in s.chains}


def utc_days(start: date, end: date) -> List[date]:
    if start is None or end is None:
        return []
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def plan_requirement(cat_set: SetCatalog, state: CoverageState,
                     today: date) -> List[Dict[str, Any]]:
    """Return a list of plan rows (dicts) for a set.

    Each row: ``set_id``, ``chain``, ``utc``, ``product``, and ``action``.
    """
    rows: List[Dict[str, Any]] = []
    chains = _chain_set(cat_set)
    for req in cat_set.products:
        prod = PRODUCTS.get(req.product_id)
        if prod is None:
            continue
        if req.window is None:
            continue
        start, end = window_resolve(req.window, today)
        if start is None or end is None:
            continue
        for chain in chains:
            for day in utc_days(start, end):
                key = (chain, day)
                rows.append({
                    'set_id': cat_set.id,
                    'product': req.product_id,
                    'chain': chain,
                    'day': day.isoformat(),
                    'action': _single_action(prod, key, state),
                })
    return rows


def load_coverage_state(conn) -> CoverageState:
    """Build a CoverageState from the control-plane coverage ledger.

    Falls back to computing raw/classified/product presence from the live swap
    tables when the ledger is empty (so the planner works before the new
    coverage tables are populated).
    """
    state = CoverageState()
    ledger_populated = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT chain, utc_day FROM source_day_coverage WHERE status='INGESTED'
            """)
            state.raw_present = {(r[0].lower() if isinstance(r[0], str) else r[0], r[1]) for r in cur.fetchall()}
            cur.execute("""
                SELECT chain_id, utc_day FROM classification_day_coverage WHERE status='DONE'
            """)
            state.classified = {(str(r[0]).lower(), r[1]) for r in cur.fetchall()}
            cur.execute("""
                SELECT product_id, chain_id, utc_day FROM product_day_coverage WHERE status='MATERIALIZED'
            """)
            state.product_present = {}
            for pid, chain, day in cur.fetchall():
                state.product_present.setdefault(pid, set()).add((str(chain).lower(), day))
            ledger_populated = bool(state.raw_present) or bool(state.classified) or bool(state.product_present)
    except Exception:
        ledger_populated = False
    if not ledger_populated:
        # Control plane not yet populated -> derive from live swap tables.
        _load_from_swaps(conn, state)
    return state


def _load_from_swaps(conn, state: CoverageState) -> None:
    """Best-effort: derive coverage from the live swaps/route tables.

    Used when the control-plane coverage ledger is empty (pre-population).
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT LOWER(ch.name), s.ts::date
                FROM swaps s
                JOIN liquidity_pool lp ON s.pool_id = lp.id
                JOIN chain ch ON lp.chain_id = ch.id
                WHERE s.ts >= NOW() - INTERVAL '30 days'
                GROUP BY 1,2
            """)
            state.raw_present = {(r[0], r[1]) for r in cur.fetchall()}
            cur.execute("""
                SELECT LOWER(ch.name), s.ts::date
                FROM swaps s
                JOIN liquidity_pool lp ON s.pool_id = lp.id
                JOIN chain ch ON lp.chain_id = ch.id
                WHERE s.route_id IS NOT NULL AND s.ts >= NOW() - INTERVAL '90 days'
                GROUP BY 1,2
            """)
            state.classified = {(r[0], r[1]) for r in cur.fetchall()}
    except Exception:
        pass


def _single_action(prod, key: tuple, state: CoverageState) -> str:
    """Decide one worker for one (set product, chain, day)."""
    # Surrogate keys for state lookups
    raw_key = key
    if prod.coverage_rule == 'raw':
        # product fact == raw presence? Not a fact we track separately.
        if raw_key in state.raw_present:
            return 'RESOLVE'
        return 'FETCH'
    if prod.requires_classification:
        if raw_key not in state.raw_present:
            return 'FETCH'           # no raw source -> fetch first
        if raw_key not in state.classified:
            return 'CLASSIFY'        # raw present but not classified
        # classified: is the fact materialized?
        facts = state.product_present.get(prod.product_id, set())
        if raw_key in facts:
            return 'RESOLVE'
        return 'MATERIALIZE'
    # non-classified product (e.g. hypothetical raw-cursor product)
    if raw_key not in state.raw_present:
        return 'FETCH'
    return 'RESOLVE'


def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[r['action']] = out.get(r['action'], 0) + 1
    return out