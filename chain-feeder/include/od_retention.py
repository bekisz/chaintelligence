"""O&D Set data-governance engine.

Defines and enforces a *goal state* for ingested data keyed to O&D sets.

The goal state is declared in ``config/ods-goal-state.yaml`` (see that file for
the full schema). Conceptually:

* **floor requirements** — ordinary requirements with both sides wild
  (``origin: "*"`` / ``dest: "*"``) that define the retention *floor* for any
  (pair, layer) not covered by a more-specific requirement. They fall out of
  the override cascade automatically: a chain-specific floor overrides the
  global floor, and floor requirements also provide the per-chain floors for
  unclassified raw swaps (``route_id IS NULL``) that cannot be attributed to
  any O&D set.
* **requirements** — per-O&D-set goals. Each names an origin/destination
  selector (symbol / coin family / ``*`` / contract address), a
  ``bidirectional`` flag (default true), an
  optional chain list, and per-layer windows. For every (pair, layer) the
  MOST-SPECIFIC matching requirement wins (override cascade): contract >
  symbol > family > ``*``, specific chain > ``*``, explicit window > rolling.

There is no separate ``defaults`` category any more — a layer that no
requirement (base or otherwise) covers is "unclaimed", meaning every row of it
is a deletion candidate.

Layers: ``swaps`` (raw rows, route-attributable), ``route_daily_stats``,
``route_daily_stats_bucket`` (swap-size distribution), and three LP layers for
pools used as route hops: ``liquidity_pool`` (position snapshots),
``liquidity_pool_daily_stats`` and ``liquidity_pool_daily_stats_bucket``. Each
LP layer has its own independent window.

The engine is intentionally DB-light for resolution: pair rows are fetched
once and rules are matched in pure Python, so the matching/specificity logic
is unit-testable without a database (see ``test_od_rennetion.py``).

This module is used by the ``ods_goal_state_retention`` Airflow DAG, the CLI
in ``scripts/ods_goal_state.py`` and the ``GET /api/ods/goal-state`` endpoint.
"""
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

LAYERS = ('swaps', 'route_daily_stats', 'route_daily_stats_bucket',
          'liquidity_pool', 'liquidity_pool_daily_stats', 'liquidity_pool_daily_stats_bucket')
LP_LAYERS = ('liquidity_pool', 'liquidity_pool_daily_stats', 'liquidity_pool_daily_stats_bucket')
CONFIG_BASENAME = 'ods-goal-state.yaml'


def _lp_table(layer: str) -> str:
    """Physical table for an LP layer."""
    return {
        'liquidity_pool': 'liquidity_pool_position_snapshot',
        'liquidity_pool_daily_stats': 'liquidity_pool_daily_stats',
        'liquidity_pool_daily_stats_bucket': 'liquidity_pool_daily_stats_bucket',
    }[layer]


# --------------------------------------------------------------------------
# Config loading (mirrors chain-feeder/include/settings.load_distribution_config)
# --------------------------------------------------------------------------

def _config_candidates() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(here, '..', '..', 'config', CONFIG_BASENAME),
        os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'config', CONFIG_BASENAME),
        os.path.join('/app', 'config', CONFIG_BASENAME),
    ]


# Fallback used only when the config file cannot be found: a single global
# base requirement so a missing file never turns into "delete everything".
FALLBACK_BASE_REQUIREMENT: Dict[str, Any] = {
    'name': 'Base: built-in floor',
    'origin': '*',
    'dest': '*',
    'bidirectional': True,
    'chains_all': True,
    'chains': set(),
    'layers': {
        'swaps': {'kind': 'rolling', 'days': 3},
        'liquidity_pool': {'kind': 'rolling', 'days': 32},
    },
    'idx': 0,
}


def _parse_date(value: Any, path: str) -> date:
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise ValueError(f"{path}: expected 'YYYY-MM-DD', got {value!r}")


def parse_window(spec: Any, path: str) -> Optional[Dict[str, Any]]:
    """Normalize one layer's window spec.

    Accepted shapes: ``{last_days: N}`` (rolling), ``{since: 'YYYY-MM-DD'}``
    (fixed until today), ``{from: ..., to: ...}`` (exact bucket) — or absent/None.
    """
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: window must be a map with last_days/since/from/to, got {spec!r}")
    keys = set(spec)
    if 'last_days' in keys:
        days = spec['last_days']
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise ValueError(f"{path}.last_days: expected a positive integer, got {days!r}")
        return {'kind': 'rolling', 'days': days}
    if 'since' in keys:
        return {'kind': 'fixed', 'start': _parse_date(spec['since'], f"{path}.since"), 'end': None}
    if 'from' in keys or 'to' in keys:
        if 'from' not in keys or 'to' not in keys:
            raise ValueError(f"{path}: explicit windows need both 'from' and 'to'")
        start = _parse_date(spec['from'], f"{path}.from")
        end = _parse_date(spec['to'], f"{path}.to")
        if end < start:
            raise ValueError(f"{path}: 'to' ({end}) is before 'from' ({start})")
        return {'kind': 'fixed', 'start': start, 'end': end}
    raise ValueError(f"{path}: window must use last_days, since, or from/to — got keys {sorted(keys)}")


def window_resolve(win: Optional[Dict[str, Any]], today: date) -> Optional[Tuple[Optional[date], Optional[date]]]:
    """Resolve a window to an inclusive (start, end) pair.

    ``None`` start/end means 'no bound' (used when a layer has no floor, i.e.
    everything is a deletion candidate). Rolling windows are inclusive of
    ``today - N .. today``.
    """
    if win is None:
        return None
    if win['kind'] == 'rolling':
        return today - timedelta(days=win['days']), today
    return win['start'], win['end'] or today


def _parse_chains(value: Any) -> Tuple[bool, set]:
    if value is None:
        return True, set()
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in ('all', '*'):
            return True, set()
        return False, {c.strip().lower() for c in text.split(',') if c.strip()}
    if isinstance(value, list):
        names = [str(v).strip().lower() for v in value if str(v).strip()]
        return (not names), set(names)
    raise ValueError(f"chains: expected '*', a comma-separated string or a list, got {value!r}")


def load_goal_state(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and normalize ``config/ods-goal-state.yaml``.

    Returns ``{'requirements': [...], 'config_path': path}`` with every window
    parsed. Raises ValueError on malformed content; falls back to a single
    built-in base requirement (swaps 3d, liquidity_pool 32d) when the file
    cannot be found.
    """
    import yaml

    path = config_path
    if path is None:
        for candidate in _config_candidates():
            if os.path.exists(candidate):
                path = candidate
                break
    if path is None or not os.path.exists(path):
        log.warning("Goal-state config not found; using built-in base floor (no O&D requirements)")
        return {'requirements': [dict(FALLBACK_BASE_REQUIREMENT)], 'config_path': path}

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    log.debug(f"Loaded goal-state config from {path}")

    requirements: List[Dict[str, Any]] = []
    for idx, row in enumerate(raw.get('requirements', []) or []):
        requirements.append(_normalize_requirement(row, idx))

    return {'requirements': requirements, 'config_path': path}


def _normalize_requirement(row: Dict[str, Any], idx: int) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"requirements[{idx}]: expected a map")
    name = row.get('name') or f"requirement-{idx + 1}"
    for key in ('origin', 'dest'):
        if not row.get(key):
            raise ValueError(f"requirements[{idx}]: missing '{key}'")
    wild_all, chains = _parse_chains(row.get('chains'))
    bidirectional = bool(row.get('bidirectional', True))

    layers: Dict[str, Dict[str, Any]] = {}
    for layer in LAYERS:
        if layer in row:
            win = parse_window(row.get(layer), f"requirements[{idx}].{layer}")
            if win is not None:
                layers[layer] = win

    return {
        'name': str(name),
        'origin': str(row['origin']).strip(),
        'dest': str(row['dest']).strip(),
        'bidirectional': bidirectional,
        'chains_all': wild_all,
        'chains': chains,
        'layers': layers,
        'idx': idx,
    }


def is_floor_requirement(req: Dict[str, Any]) -> bool:
    """True when a requirement is a *floor* (both sides wild).

    Floors fall out of the cascade naturally: ``origin:"*"`` / ``dest:"*"``
    match every pair, so any more-specific requirement overrides them. They
    additionally define the per-chain floors for unclassified raw swaps.
    """
    origin = str(req.get('origin', '')).strip()
    dest = str(req.get('dest', '')).strip()
    return origin in ('*', '') and dest in ('*', '')


# --------------------------------------------------------------------------
# Side resolution (mirrors api/main.resolve_od_set_side)
# --------------------------------------------------------------------------

KIND_SPEC = {'wild': 0, 'literal': 1, 'family': 2, 'symbol': 2, 'address': 3}


def resolve_side(cur, term: str) -> Dict[str, Any]:
    """Resolve one O&D-goal side selector against the coin tables.

    Returns ``{'wild', 'coin_ids', 'symbols', 'addresses', 'kind', 'spec'}``.
    ``kind`` is used for specificity ranking; the other four keys are the
    additive match constraints (same semantics as ``_od_set_side_sql``).
    """
    term = (term or '').strip()
    if not term or term == '*':
        return {'wild': True, 'coin_ids': [], 'symbols': [], 'addresses': [], 'kind': 'wild', 'spec': 0}

    looks_like_addr = term.lower().startswith('0x') or (
        len(term) == 40 and all(c in '0123456789abcdefABCDEF' for c in term)
    )
    if looks_like_addr:
        addr = term.lower()
        cur.execute("SELECT DISTINCT coin_id FROM coin_contract WHERE LOWER(contract_address) = %s", (addr,))
        coin_ids = [r[0] for r in cur.fetchall()]
        return {'wild': False, 'coin_ids': coin_ids, 'symbols': [],
                'addresses': [addr], 'kind': 'address', 'spec': KIND_SPEC['address']}

    cur.execute("""
        SELECT c.coin_id, c.symbol
        FROM coin_family f
        JOIN coin c ON f.coin_id = c.coin_id
        WHERE UPPER(f.name) = %s
    """, (term.upper(),))
    rows = cur.fetchall()
    if rows:
        return {'wild': False, 'coin_ids': [r[0] for r in rows], 'symbols': [r[1] for r in rows],
                'addresses': [], 'kind': 'family', 'spec': KIND_SPEC['family']}

    cur.execute("SELECT DISTINCT coin_id, symbol FROM coin WHERE UPPER(symbol) = UPPER(%s)", (term,))
    rows = cur.fetchall()
    if rows:
        return {'wild': False, 'coin_ids': [r[0] for r in rows], 'symbols': [r[1] for r in rows],
                'addresses': [], 'kind': 'symbol', 'spec': KIND_SPEC['symbol']}
    return {'wild': False, 'coin_ids': [], 'symbols': [term], 'addresses': [],
            'kind': 'literal', 'spec': KIND_SPEC['literal']}


def resolve_requirement_sides(cur, requirements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach resolved sides (``_origin``/``_dest``) to each requirement."""
    from copy import deepcopy
    copies = []
    for req in requirements:
        c = deepcopy(req)
        c['_origin'] = resolve_side(cur, req['origin'])
        c['_dest'] = resolve_side(cur, req['dest'])
        copies.append(c)
    return copies


def _resolve_sides(conn, requirements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DB-backed version of :func:`resolve_requirement_sides`."""
    with conn.cursor() as cur:
        return resolve_requirement_sides(cur, requirements)


# --------------------------------------------------------------------------
# Pure-Python matching / specificity / effective windows
# --------------------------------------------------------------------------

def side_matches(res: Dict[str, Any], contract: Optional[str], symbol: Optional[str],
                 coin_id: Optional[int]) -> bool:
    """True when a pair side satisfies the resolved selector (additive)."""
    if res.get('wild'):
        return True
    if res.get('addresses') and contract:
        if contract.lower() in {a.lower() for a in res['addresses']}:
            return True
    if res.get('coin_ids') and coin_id and coin_id in res['coin_ids']:
        return True
    if res.get('symbols') and symbol:
        if symbol.upper() in {s.upper() for s in res['symbols']}:
            return True
    return False


def rule_matches_pair(req: Dict[str, Any], pair: Dict[str, Any]) -> bool:
    """True when a rule governs the pair (chain + origin/dest + direction)."""
    if not req['chains_all'] and pair.get('chain') and (pair['chain'].lower() not in req['chains']):
        return False
    forward = (side_matches(req['_origin'], pair.get('origin_contract'), pair.get('origin_symbol'),
                            pair.get('origin_coin_id'))
               and side_matches(req['_dest'], pair.get('dest_contract'), pair.get('dest_symbol'),
                                pair.get('dest_coin_id')))
    if not req.get('bidirectional', True):
        return forward
    reversed_ = (side_matches(req['_origin'], pair.get('dest_contract'), pair.get('dest_symbol'),
                              pair.get('dest_coin_id'))
                 and side_matches(req['_dest'], pair.get('origin_contract'), pair.get('origin_symbol'),
                                  pair.get('origin_coin_id')))
    return forward or reversed_


def rule_specificity(req: Dict[str, Any], layer: str) -> Tuple:
    """Orderable specificity key for (pair, layer). Higher wins; tie -> later index."""
    token = req['_origin']['spec'] + req['_dest']['spec']
    chain = 0 if req['chains_all'] else 1
    win = req['layers'].get(layer)
    window = 0 if win is None or win['kind'] == 'rolling' else 1
    return (token, chain, window, req['idx'])


def effective_window(pair: Dict[str, Any], layer: str, goal: Dict[str, Any],
                     today: date) -> Tuple[Optional[date], Optional[date]]:
    """Return the inclusive (start, end) keep-window for the pair + layer.

    The most-specific requirement wins; a layer no requirement (base or
    otherwise) covers is "unclaimed" and returns ``(None, None)`` — every row
    is a delete candidate.
    """
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple] = None
    for req in goal['requirements']:
        if layer not in req['layers']:
            continue
        if not rule_matches_pair(req, pair):
            continue
        key = rule_specificity(req, layer)
        if best_key is None or key > best_key:
            best_key = key
            best = req
    if best is not None:
        return window_resolve(best['layers'][layer], today)
    return (None, None)


def base_requirements(goal: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The goal's floor rules: requirements whose sides are both wild."""
    return [r for r in goal.get('requirements', []) if is_floor_requirement(r)]


# --------------------------------------------------------------------------
# Pair / pool row fetching
# --------------------------------------------------------------------------

def fetch_pairs(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pair.id, ch.name AS chain,
                   pair.origin_contract, pair.dest_contract,
                   pair.origin_coin_id, pair.dest_coin_id,
                   pair.origin_symbol, pair.dest_symbol
            FROM origin_destination_pair pair
            JOIN chain ch ON pair.chain_id = ch.id
        """)
        return [
            {
                'pair_id': r[0],
                'chain': r[1],
                'origin_contract': r[2],
                'dest_contract': r[3],
                'origin_coin_id': r[4],
                'dest_coin_id': r[5],
                'origin_symbol': r[6],
                'dest_symbol': r[7],
            }
            for r in cur.fetchall()
        ]


def _pool_ids_for_pairs(conn, pair_ids: List[int]) -> List[int]:
    if not pair_ids:
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT h.pool_id
            FROM route_hop h
            JOIN route r ON h.route_id = r.route_id
            WHERE r.pair_id = ANY(%s)
        """, (pair_ids,))
        return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------
# Coverage checks
# --------------------------------------------------------------------------

def _coverage_days(conn, layer: str, pair_ids, pool_ids,
                   start: date, end: date) -> set:
    """Distinct days with data for the given pairs/pools.

    ``pair_ids``/``pool_ids`` of ``None`` means "any pair/pool" (used for base
    requirements, which cover every pair — avoids a giant ``ANY(...)`` list).
    Empty list means no matches -> empty coverage.
    """
    pair_pred = ''
    pair_args: tuple = ()
    if pair_ids is None:
        pass
    elif pair_ids:
        pair_pred = ' AND r.pair_id = ANY(%s)'
        pair_args = (pair_ids,)
    else:
        return set()
    with conn.cursor() as cur:
        if layer == 'route_daily_stats':
            cur.execute(f"""
                SELECT DISTINCT rs.day
                FROM route_daily_stats rs
                JOIN route r ON rs.route_id = r.route_id
                WHERE rs.day BETWEEN %s AND %s{pair_pred}
            """, (start, end) + pair_args)
        elif layer == 'route_daily_stats_bucket':
            cur.execute(f"""
                SELECT DISTINCT rb.day
                FROM route_daily_stats_bucket rb
                JOIN route r ON rb.route_id = r.route_id
                WHERE rb.day BETWEEN %s AND %s{pair_pred}
            """, (start, end) + pair_args)
        elif layer == 'swaps':
            end_ex = end + timedelta(days=1)
            if pair_ids is None:
                # Base coverage: all classified swaps, no route/pair join needed.
                # Raw ts range lets the ts/partial index prune instead of
                # casting ts::date (which defeats partition pruning).
                cur.execute("""
                    SELECT DISTINCT s.ts::date AS day
                    FROM swaps s
                    WHERE s.route_id IS NOT NULL AND s.ts >= %s AND s.ts < %s
                """, (start, end_ex))
            else:
                cur.execute(f"""
                    SELECT DISTINCT s.ts::date AS day
                    FROM swaps s
                    JOIN route r ON s.route_id = r.route_id
                    WHERE s.ts >= %s AND s.ts < %s{pair_pred}
                """, (start, end_ex) + pair_args)
        elif layer in LP_LAYERS:
            if pool_ids is not None and not pool_ids:
                return set()
            pool_pred = ''
            pool_args: tuple = ()
            if pool_ids is not None:
                pool_pred = ' AND p.pool_id = ANY(%s)' if layer == 'liquidity_pool' else ' AND t.pool_id = ANY(%s)'
                pool_args = (pool_ids,)
            if layer == 'liquidity_pool':
                # Position snapshots for the pools.
                cur.execute(f"""
                    SELECT DISTINCT ps.timestamp::date AS day
                    FROM liquidity_pool_position_snapshot ps
                    JOIN liquidity_pool_position p ON ps.position_id = p.id
                    WHERE ps.timestamp::date BETWEEN %s AND %s{pool_pred}
                """, (start, end) + pool_args)
            else:
                cur.execute(f"""
                    SELECT DISTINCT t.day
                    FROM {_lp_table(layer)} t
                    WHERE t.day BETWEEN %s AND %s{pool_pred}
                """, (start, end) + pool_args)
            return {r[0] for r in cur.fetchall()}
        else:
            return set()
        return {r[0] for r in cur.fetchall()}


def _expected_days(start: date, end: date) -> set:
    if start is None or end is None:
        return set()
    out = set()
    d = start
    while d <= end:
        out.add(d)
        d += timedelta(days=1)
    return out


def _window_label(win: Optional[Dict[str, Any]]) -> str:
    """Human-readable requirement label for a layer's parsed window spec."""
    if not win:
        return '—'
    if win['kind'] == 'rolling':
        return f"last {win['days']}d"
    start = win['start']
    end = win.get('end')
    if end is None:
        return f"since {start.isoformat()}"
    return f"{start.isoformat()}..{end.isoformat()}"


def _check_status(present: set, expected: set, start: date, end: date, today: date) -> str:
    if not present:
        return 'missing'
    missing = expected - present if expected else set()
    if not missing:
        return 'ok'
    # "stale": data exists but the most recent day is old (rolling/since
    # windows are supposed to reach today).
    if end is not None and end == today and (today - max(present)).days >= 2:
        return 'stale'
    return 'partial'


def run_checks(conn, goal: Dict[str, Any], today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Evaluate every requirement/layer against the warehouse; returns a report."""
    today = today or date.today()
    reqs = _resolve_sides(conn, goal['requirements'])
    pairs = fetch_pairs(conn)
    report: List[Dict[str, Any]] = []

    for req in reqs:
        is_base = is_floor_requirement(req)
        if is_base:
            # Base requirements cover every pair; skip the giant ANY(...) list.
            matched = pairs
            pair_ids = None
            pool_ids = None
        else:
            matched = [p for p in pairs if rule_matches_pair(req, p)]
            pair_ids = [p['pair_id'] for p in matched]
            pool_ids = _pool_ids_for_pairs(conn, pair_ids)
        for layer in LAYERS:
            win = req['layers'].get(layer)
            if win is None:
                continue
            start, end = window_resolve(win, today)
            if start is None or end is None:
                continue
            present = _coverage_days(conn, layer, pair_ids, pool_ids, start, end)
            expected = _expected_days(start, end)
            missing = sorted(expected - present) if expected else []
            status = _check_status(present, expected, start, end, today)
            report.append({
                'name': req['name'],
                'origin': req['origin'],
                'dest': req['dest'],
                'bidirectional': bool(req.get('bidirectional', True)),
                'chains': '*' if req['chains_all'] else sorted(req['chains']),
                'base': is_floor_requirement(req),
                'layer': layer,
                'window': {'start': start.isoformat(), 'end': end.isoformat()},
                'window_label': _window_label(win),
                'pairs': len(matched),
                'expected_days': len(expected),
                'present_days': len(present),
                'missing_days': [d.isoformat() for d in missing],
                'min_date': min(present).isoformat() if present else None,
                'max_date': max(present).isoformat() if present else None,
                'status': status,
            })
    return report


def export_gaps(report: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse each report row's missing days into contiguous ranges."""
    out: List[Dict[str, Any]] = []
    for row in report:
        if not row['missing_days']:
            continue
        dates = sorted(datetime.strptime(d, '%Y-%m-%d').date() for d in row['missing_days'])
        ranges: List[Tuple[date, date]] = []
        start = prev = dates[0]
        for d in dates[1:]:
            if (d - prev).days == 1:
                prev = d
            else:
                ranges.append((start, prev))
                start = prev = d
        ranges.append((start, prev))
        for a, b in ranges:
            out.append({
                'name': row['name'], 'layer': row['layer'],
                'chain': row['chains'], 'from': a.isoformat(), 'to': b.isoformat(),
                'days': (b - a).days + 1,
            })
    return out


# --------------------------------------------------------------------------
# Pruning
# --------------------------------------------------------------------------

def _group_pair_windows(pairs: List[Dict[str, Any]], layer: str,
                        goal: Dict[str, Any], today: date) -> List[Tuple[List[int], Optional[date], Optional[date]]]:
    groups: Dict[Tuple, List[int]] = {}
    key_to_tuple: Dict[Tuple, Tuple[Optional[date], Optional[date]]] = {}
    for p in pairs:
        start, end = effective_window(p, layer, goal, today)
        key = (start.isoformat() if start else None, end.isoformat() if end else None)
        groups.setdefault(key, []).append(p['pair_id'])
        key_to_tuple[key] = (start, end)
    return [(ids, key_to_tuple[k][0], key_to_tuple[k][1]) for k, ids in groups.items()]


def _delete_batched(conn, delete_sql: str, args: tuple, batch: int = 10000) -> int:
    deleted = 0
    with conn.cursor() as cur:
        while True:
            cur.execute(delete_sql, args + (batch,))
            batch_deleted = cur.rowcount
            conn.commit()
            deleted += batch_deleted
            if batch_deleted < batch:
                break
    return deleted


def _prune_route_layer(conn, layer: str, groups, batch: int) -> int:
    table = 'route_daily_stats' if layer == 'route_daily_stats' else 'route_daily_stats_bucket'
    tmp = 'rs' if layer == 'route_daily_stats' else 'rb'
    col = 't.day'
    total = 0
    for pair_ids, start, end in groups:
        if not pair_ids:
            continue
        args: Tuple = (pair_ids,)
        pred: List[str] = []
        if start is not None:
            pred.append(f"{col} < %s")
            args += (start,)
        if end is not None:
            pred.append(f"{col} > %s")
            args += (end,)
        pred_sql = " OR ".join(pred) if pred else "1=1"
        sql = f"""
            DELETE FROM {table} {tmp}
            WHERE {tmp}.ctid IN (
                SELECT t.ctid FROM {table} t
                JOIN route r ON t.route_id = r.route_id
                WHERE r.pair_id = ANY(%s) AND ({pred_sql})
                LIMIT %s
            )
        """
        total += _delete_batched(conn, sql, args, batch)
    return total


def _prune_swaps(conn, pairs, goal, today, batch) -> Dict[str, int]:
    total = 0
    # Classified rows: per-pair effective windows (requirements + chain floors).
    groups = _group_pair_windows(pairs, 'swaps', goal, today)
    for pair_ids, start, end in groups:
        if not pair_ids:
            continue
        args: Tuple = (pair_ids,)
        pred: List[str] = []
        if start is not None:
            pred.append("s2.ts::date < %s")
            args += (start,)
        if end is not None:
            pred.append("s2.ts::date > %s")
            args += (end,)
        pred_sql = " OR ".join(pred) if pred else "1=1"
        sql = f"""
            DELETE FROM swaps s
            WHERE s.ctid IN (
                SELECT s2.ctid FROM swaps s2
                JOIN route r ON s2.route_id = r.route_id
                WHERE s2.route_id IS NOT NULL AND r.pair_id = ANY(%s) AND ({pred_sql})
                LIMIT %s
            )
        """
        total += _delete_batched(conn, sql, args, batch)

    # Unclassified rows (route_id IS NULL): apply the per-chain base floor.
    # They cannot be attributed to an O&D set, so only the base requirement's
    # swaps window governs them (this replaces the old network/protocol
    # retention mechanism).
    floors = _window_row_for_unclassified(conn, goal, today)
    for chain, (start, end) in floors.items():
        args = (chain,)
        pred = []
        if start is not None:
            pred.append("s2.ts::date < %s")
            args += (start,)
        if end is not None:
            pred.append("s2.ts::date > %s")
            args += (end,)
        pred_sql = " OR ".join(pred) if pred else "1=1"
        sql = f"""
            DELETE FROM swaps s
            WHERE s.ctid IN (
                SELECT s2.ctid FROM swaps s2
                JOIN liquidity_pool lp ON s2.pool_id = lp.id
                JOIN chain ch ON lp.chain_id = ch.id
                WHERE s2.route_id IS NULL
                  AND LOWER(ch.name) = LOWER(%s) AND ({pred_sql})
                LIMIT %s
            )
        """
        total += _delete_batched(conn, sql, args, batch)
    return {'swaps': total}


def _window_row_for_unclassified(conn, goal, today) -> Dict[str, Tuple[Optional[date], Optional[date]]]:
    """Per-chain floor windows for unclassified raw-swap rows (all chains).

    Unclassified swaps (``route_id IS NULL``) cannot be attributed to any O&D
    set, so they are governed only by the ``swaps`` window of the *base*
    requirements: a chain-specific base overrides the global base (same
    override cascade; tie -> later rule wins).
    """
    bases = base_requirements(goal)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT LOWER(ch.name) FROM chain ch")
        chains = [r[0] for r in cur.fetchall()]
    out: Dict[str, Tuple[Optional[date], Optional[date]]] = {}
    for chain in chains:
        floor: Optional[Dict[str, Any]] = None
        floor_key: Optional[Tuple] = None
        for req in bases:
            win = req['layers'].get('swaps')
            if win is None:
                continue
            if not req['chains_all'] and chain not in req['chains']:
                continue
            key = (0 if req['chains_all'] else 1, req['idx'])
            if floor is None or key > floor_key:
                floor = win
                floor_key = key
        resolved = window_resolve(floor, today) if floor else None
        out[chain] = resolved if resolved else (None, None)
    return out


def _layer_pool_windows(conn, pairs, goal, today, layer: str) -> Dict[int, Tuple[Optional[date], Optional[date]]]:
    """Most-specific requirement's window per used pool for an LP layer.

    Base requirements (wild on both sides) act as the floor for pools no
    specific requirement covers; a pool with no governing requirement at all
    maps to ``(None, None)`` (everything deletable).
    """
    pool_win: Dict[int, Tuple[Optional[date], Optional[date]]] = {}
    pool_spec: Dict[int, Tuple] = {}

    for req in goal['requirements']:
        win = req['layers'].get(layer)
        if win is None:
            continue
        matched = [p for p in pairs if rule_matches_pair(req, p)]
        pair_ids = [p['pair_id'] for p in matched]
        pool_ids = _pool_ids_for_pairs(conn, pair_ids)
        resolved = window_resolve(win, today)
        key = rule_specificity(req, layer)
        for pid in pool_ids:
            if pool_spec.get(pid, None) is None or key > pool_spec[pid]:
                pool_spec[pid] = key
                pool_win[pid] = resolved
    return pool_win


def _prune_lp_layer(conn, pairs, goal, today, batch, layer: str) -> int:
    """Prune one LP layer's table outside each used pool's keep-window."""
    from collections import defaultdict as dd
    pool_win = _layer_pool_windows(conn, pairs, goal, today, layer)

    groups: Dict[Tuple, List[int]] = dd(list)
    key_to_win: Dict[Tuple, Tuple[Optional[date], Optional[date]]] = {}
    # Leftover pools (used by some route but covered by no requirement) are
    # unclaimed -> everything is a deletion candidate.
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT h.pool_id FROM route_hop h")
        used = [r[0] for r in cur.fetchall()]
    for pid in used:
        win = pool_win.get(pid, (None, None))
        key = (win[0].isoformat() if win[0] else None, win[1].isoformat() if win[1] else None)
        groups[key].append(pid)
        key_to_win[key] = (win[0], win[1])

    total = 0
    if layer == 'liquidity_pool':
        for key, pool_ids in groups.items():
            if not pool_ids:
                continue
            start, end = key_to_win[key]
            pred, args = _window_pred('ps2.timestamp::date', 'ps2', start, end)
            pred_sql = " OR ".join(pred)
            sql = f"""
                DELETE FROM liquidity_pool_position_snapshot ps
                WHERE ps.ctid IN (
                    SELECT ps2.ctid FROM liquidity_pool_position_snapshot ps2
                    JOIN liquidity_pool_position p ON ps2.position_id = p.id
                    WHERE p.pool_id = ANY(%s) AND ({pred_sql})
                    LIMIT %s
                )
            """
            total += _delete_batched(conn, sql, (pool_ids,) + args, batch)
    else:
        table = _lp_table(layer)
        for key, pool_ids in groups.items():
            if not pool_ids:
                continue
            start, end = key_to_win[key]
            pred, args = _window_pred('t2.day', 't2', start, end)
            pred_sql = " OR ".join(pred)
            sql = f"""
                DELETE FROM {table} t
                WHERE t.ctid IN (
                    SELECT t2.ctid FROM {table} t2
                    WHERE t2.pool_id = ANY(%s) AND ({pred_sql})
                    LIMIT %s
                )
            """
            total += _delete_batched(conn, sql, (pool_ids,) + args, batch)
    return total


def _verify_no_inwindow_casualties(pairs, goal, today) -> None:
    """Guard: no pair governed by a requirement may ever be pruned inside that
    requirement's keep-window.

    Every requirement's matched pair must be assigned by ``effective_window``
    to a group that is at least as protective as the requirement's own window for
    that layer. If an in-window row would ever be represented as a deletion
    candidate this raises before any data is touched.
    """
    from copy import deepcopy as _dc
    for req in goal.get('requirements', []):
        # Floor requirements (wild on both sides) are *meant* to be overridden
        # by more specific requirements (which may intentionally narrow the
        # window, e.g. an exact bounded window inside a 180-day floor), so skip
        # them here.
        if is_floor_requirement(req):
            continue
        if not req.get('layers'):
            continue
        for layer, win in req['layers'].items():
            req_window = window_resolve(win, today)
            if req_window is None:
                continue
            rstart, rend = req_window
            for p in pairs:
                if not rule_matches_pair(req, p):
                    continue
                start, end = effective_window(p, layer, goal, today)
                if (start is None) or (rstart is not None and start > rstart):
                    raise RuntimeError(
                        f"prune-safety: requirement '{req['name']}' layer '{layer}' pair "
                        f"{p['pair_id']} would lose in-window data "
                        f"(effective window start {start} < requirement start {rstart})")
                if rend is not None and (end is not None and end < rend):
                    raise RuntimeError(
                        f"prune-safety: requirement '{req['name']}' layer '{layer}' pair "
                        f"{p['pair_id']} would lose in-window data "
                        f"(effective window end {end} > requirement end {rend})")


def prune(conn, goal: Dict[str, Any], today: Optional[date] = None,
          dry_run: bool = True, batch: int = 10000,
          progress=None) -> Dict[str, Any]:
    """Delete data outside the effective keep-windows of the goal state.

    Returns per-layer delete counts (all zero when ``dry_run``). Never touches
    unclassified rows that cannot be attributed to a chain.
    """
    today = today or date.today()
    log_fn = progress or (lambda msg: log.info(msg))
    pairs = fetch_pairs(conn)
    goal = dict(goal)
    goal['requirements'] = _resolve_sides(conn, goal.get('requirements') or [])
    layer_counts: Dict[str, int] = {layer: 0 for layer in LAYERS}

    if not dry_run:
        _verify_no_inwindow_casualties(pairs, goal, today)
        for layer in ('route_daily_stats', 'route_daily_stats_bucket'):
            groups = _group_pair_windows(pairs, layer, goal, today)
            layer_counts[layer] = _prune_route_layer(conn, layer, groups, batch)
            log_fn(f"pruned {layer}: {layer_counts[layer]} rows")

        layer_counts.update(_prune_swaps(conn, pairs, goal, today, batch))
        log_fn(f"pruned swaps: {layer_counts['swaps']} rows")

        for layer in LP_LAYERS:
            layer_counts[layer] = _prune_lp_layer(conn, pairs, goal, today, batch, layer)
            log_fn(f"pruned {layer}: {layer_counts[layer]} rows")

    # Estimate (dry-run) counts too, cheaply.
    if dry_run:
        counts = {'swaps': _count_deletable(conn, pairs, goal, today, 'swaps'),
                  'route_daily_stats': _count_deletable(conn, pairs, goal, today, 'route_daily_stats'),
                  'route_daily_stats_bucket': _count_deletable(conn, pairs, goal, today, 'route_daily_stats_bucket')}
        for layer in LP_LAYERS:
            counts[layer] = _count_lp_deletable(conn, pairs, goal, today, layer)
        layer_counts.update(counts)

    return {'dry_run': dry_run, 'rows': layer_counts, 'vacuum': not dry_run}


def _count_deletable(conn, pairs, goal, today, layer) -> int:
    if layer == 'swaps':
        total = 0
        for pair_ids, start, end in _group_pair_windows(pairs, 'swaps', goal, today):
            pred, args = _window_pred('s2.ts::date', 's2', start, end)
            if not pred:
                continue
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT COUNT(*) FROM swaps s2
                    JOIN route r ON s2.route_id = r.route_id
                    WHERE s2.route_id IS NOT NULL AND r.pair_id = ANY(%s) AND ({" OR ".join(pred)})
                """, (pair_ids,) + args)
                total += cur.fetchone()[0]
        # unclassified floor
        for chain, (start, end) in _window_row_for_unclassified(conn, goal, today).items():
            pred, args = _window_pred('s2.ts::date', 's2', start, end)
            if not pred:
                continue
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT COUNT(*) FROM swaps s2
                    JOIN liquidity_pool lp ON s2.pool_id = lp.id
                    JOIN chain ch ON lp.chain_id = ch.id
                    WHERE s2.route_id IS NULL AND LOWER(ch.name) = LOWER(%s) AND ({" OR ".join(pred)})
                """, (chain,) + args)
                total += cur.fetchone()[0]
        return total
    if layer in ('route_daily_stats', 'route_daily_stats_bucket'):
        table = 'route_daily_stats' if layer == 'route_daily_stats' else 'route_daily_stats_bucket'
        total = 0
        for pair_ids, start, end in _group_pair_windows(pairs, layer, goal, today):
            pred, args = _window_pred('t.day', 't', start, end)
            if not pred:
                continue
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT COUNT(*) FROM {table} t
                    JOIN route r ON t.route_id = r.route_id
                    WHERE r.pair_id = ANY(%s) AND ({" OR ".join(pred)})
                """, (pair_ids,) + args)
                total += cur.fetchone()[0]
        return total
    return 0


def _count_lp_deletable(conn, pairs, goal, today, layer: str) -> int:
    pool_win = _layer_pool_windows(conn, pairs, goal, today, layer)
    total = 0
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT h.pool_id FROM route_hop h")
        pool_ids = [r[0] for r in cur.fetchall()]
        if layer == 'liquidity_pool':
            col = 'ps.timestamp::date'
            for pid in pool_ids:
                start, end = pool_win.get(pid, (None, None))
                pred, args = _window_pred(col, 'ps', start, end)
                if not pred:
                    continue
                cur.execute(
                    f"SELECT COUNT(*) FROM liquidity_pool_position_snapshot ps "
                    f"JOIN liquidity_pool_position p ON ps.position_id = p.id "
                    f"WHERE p.pool_id = %s AND ({' OR '.join(pred)})", (pid,) + args)
                total += cur.fetchone()[0]
        else:
            col = 't.day'
            table = _lp_table(layer)
            for pid in pool_ids:
                start, end = pool_win.get(pid, (None, None))
                pred, args = _window_pred(col, 't', start, end)
                if not pred:
                    continue
                cur.execute(f"SELECT COUNT(*) FROM {table} t WHERE t.pool_id = %s AND ({' OR '.join(pred)})", (pid,) + args)
                total += cur.fetchone()[0]
    return total


def _window_pred(col: str, alias: str, start: Optional[date], end: Optional[date]) -> Tuple[List[str], tuple]:
    """Build (predicate, args) deleting rows outside [start, end].

    Both-None means "no floor for this layer" -> everything is a delete
    candidate, so the predicate collapses to `1=1`.
    """
    if start is None and end is None:
        return ['1=1'], ()
    pred = []
    args: tuple = ()
    if start is not None:
        pred.append(f"{col} < %s")
        args += (start,)
    if end is not None:
        pred.append(f"{col} > %s")
        args += (end,)
    return pred, args


def backfill_missing_daily_stats(conn, report: List[Dict[str, Any]], chunk_days: int = 7) -> int:
    """Recompute route daily_stats/buckets for every day reported as missing.

    Idempotent (delete+insert per day). Requires the corresponding raw swaps.
    """
    from include.route_classifier import recompute_daily_stats, recompute_distribution_buckets
    days = sorted({
        d
        for row in report
        if row['layer'] in ('route_daily_stats', 'route_daily_stats_bucket')
        for d in row['missing_days']
    })
    if not days:
        return 0
    with conn.cursor() as cur:
        recompute_daily_stats(cur, days, chunk_days=chunk_days)
        recompute_distribution_buckets(cur, days, chunk_days=chunk_days)
    conn.commit()
    return len(days)