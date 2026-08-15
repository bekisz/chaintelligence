"""JSON:API compound-document serializer.

Pure helpers (no DB access) for building responses that mirror the
Chaintelligence object graph defined in ``model.yaml``. Every resource has the
same grammar:

    {"type": ..., "id": ..., "attributes": {...}, "relationships": {...}}

Endpoints call ``make_resource`` / ``build_document`` and let this module
validate `include` / `fields` query params against the schema.
"""

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'model.yaml')
with open(_SCHEMA_PATH, 'r') as _f:
    _MODEL = yaml.safe_load(_f)

RESOURCES: Dict[str, Dict[str, Any]] = _MODEL['resources']


def resource_types() -> List[str]:
    return list(RESOURCES.keys())


def attributes_for(res_type: str) -> List[str]:
    info = RESOURCES.get(res_type, {})
    return list(info.get('attributes', []))


def relationships_for(res_type: str) -> Dict[str, Dict[str, Any]]:
    info = RESOURCES.get(res_type, {})
    return info.get('relationships', {}) or {}


def relationship_target(res_type: str, rel: str) -> Optional[Dict[str, Any]]:
    """Return the relationship descriptor (type, many) or None if invalid."""
    rels = relationships_for(res_type)
    desc = rels.get(rel)
    if desc is None:
        return None
    return {'type': desc.get('type'), 'many': bool(desc.get('many', False))}


# --- include parsing ------------------------------------------------------

def parse_include(spec: Optional[str], root_type: str) -> List[List[str]]:
    """Parse a comma-separated `?include=` value into validated relationship paths.

    Each path is a list of relationship names starting from ``root_type``,
    e.g. "routes.hops.pool,pool.coin0" -> [["routes","hops","pool"], ["pool","coin0"]].

    Raises ValueError on an unknown relationship so the endpoint can 400.
    """
    if not spec:
        return []
    paths: List[List[str]] = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        segs = [s.strip() for s in part.split('.') if s.strip()]
        if not segs:
            continue
        cur = root_type
        for seg in segs:
            target = relationship_target(cur, seg)
            if target is None:
                raise ValueError(
                    f"Unknown include path segment '{seg}' on resource '{cur}' "
                    f"(valid: {', '.join(relationships_for(cur).keys()) or 'none'})"
                )
            cur = target['type']
        paths.append(segs)
    return paths


def include_matches_any(path: List[str], paths: List[List[str]]) -> bool:
    """True if ``path`` (e.g. ['routes','hops','pool']) is a prefix of any included path."""
    for p in paths:
        if path == p[:len(path)]:
            return True
    return False


# --- sparse fieldsets -----------------------------------------------------

_FIELDS_RE = re.compile(r'([a-z_0-9]+)\[([a-zA-Z0-9_,\s]+)\]')


def parse_fields(spec: Optional[str]) -> Dict[str, Set[str]]:
    """Parse `?fields[type]=a,b,c&fields[other]=d` into {type: set(attrs)}."""
    if not spec:
        return {}
    # Support both the JSON:API query-string form (`fields[od]=...`) and the
    # shorthand where the query value is itself `type[attrs]` (FastAPI turns
    # `fields[od]` into a distinct param; use the former).
    fields: Dict[str, Set[str]] = {}
    for match in _FIELDS_RE.finditer(spec):
        res_type, attrs = match.group(1), match.group(2)
        fields.setdefault(res_type, set()).update(
            a.strip() for a in attrs.split(',') if a.strip()
        )
    return fields


# --- resource builders ----------------------------------------------------

def make_resource(res_type: str, res_id: Any,
                  attributes: Optional[Dict[str, Any]] = None,
                  relationships: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build one JSON:API resource node."""
    node: Dict[str, Any] = {'type': res_type, 'id': res_id}
    if attributes:
        node['attributes'] = attributes
    if relationships:
        node['relationships'] = relationships
    return node


def rel_data(res_type: str, res_id: Any) -> Dict[str, Any]:
    """Relationship `data` for a single (possibly null) related resource."""
    if res_id is None:
        return {'data': None}
    return {'data': {'type': res_type, 'id': res_id}}


def rel_data_many(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Relationship `data` for a list of (type, id) pairs."""
    return {'data': [{'type': t, 'id': i} for t, i in items if i is not None]}


def _dedupe(resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for r in resources:
        key = (r.get('type'), r.get('id'))
        if key in seen:
            seen[key] = r
        else:
            seen[key] = r
    return list(seen.values())


def build_document(data: Any, included: Optional[List[Dict[str, Any]]] = None,
                   links: Optional[Dict[str, Any]] = None,
                   meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble a JSON:API compound document: {data, included, links, meta}."""
    doc: Dict[str, Any] = {'data': data}
    if included:
        doc['included'] = _dedupe(included)
    if links:
        doc['links'] = links
    if meta:
        doc['meta'] = meta
    return doc


def apply_fields(document: Dict[str, Any], fields: Dict[str, Set[str]]) -> None:
    """Apply sparse fieldsets in-place: drop attributes not requested per type.

    ``fields`` maps resource type -> set of attribute names (see parse_fields).
    Touches both ``data`` and ``included`` nodes. Relationship id references
    are preserved so the graph stays navigable.
    """
    if not fields:
        return
    targets = [document.get('data')] if isinstance(document.get('data'), dict) else list(document.get('data') or [])
    targets.extend(list(document.get('included') or []))

    def _trim(node):
        if not isinstance(node, dict):
            return
        allowed = fields.get(node.get('type'))
        if allowed is None:
            return
        attrs = node.get('attributes')
        if isinstance(attrs, dict):
            node['attributes'] = {k: v for k, v in attrs.items() if k in allowed}

    for node in targets:
        _trim(node)

