#!/usr/bin/env python3
"""One-shot migration: re-key the route taxonomy to deterministic 64-bit hash ids.

Live `origin_destination_pair.id` / `route.route_id` are integer sequence ids,
but the classifier computes signed 64-bit md5 hashes (compute_pair_id /
compute_route_id) and repo DDL declares BIGINT. This drift causes the
`integer out of range` storm in route_classification_queue.

This script:
  1. Reads the current route taxonomy and remaps every pair/route/hop/stats/
     bucket/config id to its 64-bit hash equivalent (identical to the
     classifier's own compute_pair_id/compute_route_id/canonical_key).
  2. Drops the reference FKs, widens id columns to BIGINT, drops the serial
     defaults, applies the remapped ids, and recreates the FKs.
  3. Nulls + widens the vestigial legacy `swaps.route_id` column (it is no
     longer written; the classifier now writes swaps_staging.route_id).

Modes:
    --check        read-only: recompute expected ids and validate no collisions.
    --rollback-test apply everything inside a transaction, then ROLLBACK.
    (default)      apply and COMMIT.

Warnings:
  - Do not run while the classifier DAGs are actively re-keying the same rows;
    prefer pausing route_classification_queue for the duration.
  - Legacy `swaps` is large; the route_id column is dropped/re-added (metadata
    only) rather than ALTER TYPE, which would rewrite every partition.
"""

import argparse
import logging
import os
import sys

import psycopg2
import psycopg2.extras

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))

from common.utils.config import DATA_WAREHOUSE_DB  # noqa: E402
from include.route_classifier import (  # noqa: E402
    compute_pair_id,
    compute_route_id,
    canonical_key,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('migrate_route_hash_ids')


def get_db_dsn() -> str:
    dsn = os.getenv('DATA_WAREHOUSE_DB', DATA_WAREHOUSE_DB)
    if 'host=postgres' in dsn or '@postgres' in dsn:
        import socket
        try:
            socket.gethostbyname('postgres')
        except socket.gaierror:
            dsn = dsn.replace('host=postgres', 'host=localhost')
    return dsn


FK_CONSTRAINTS = [
    ('route', 'route_pair_id_fkey'),
    ('route_hop', 'route_hop_route_id_fkey'),
    ('route_daily_stats', 'route_daily_stats_route_id_fkey'),
    ('route_distribution_bucket', 'route_distribution_bucket_route_id_fkey'),
    ('route_distribution_config', 'route_distribution_config_route_id_fkey'),
    ('swaps', 'swaps_route_id_fkey'),
    ('swaps_staging', 'swaps_staging_route_id_fkey'),
]


def load_pairs(cur) -> dict:
    """Return {old_id: (chain_id, origin_lower, dest_lower)}."""
    cur.execute(
        "SELECT id, chain_id, origin_contract, dest_contract FROM origin_destination_pair"
    )
    pairs = {}
    for old_id, chain_id, origin, dest in cur.fetchall():
        pairs[old_id] = (chain_id, (origin or '').lower(), (dest or '').lower())
    return pairs


def load_routes(cur) -> list:
    """Return [(old_route_id, old_pair_id, [pool ids ordered by seq])]."""
    cur.execute("""
        SELECT r.route_id, r.pair_id,
               array_agg(h.pool_id ORDER BY h.seq) AS pools
        FROM route r
        JOIN route_hop h ON h.route_id = r.route_id
        GROUP BY r.route_id, r.pair_id
    """)
    return [(rid, pid, list(pool_ids)) for rid, pid, pool_ids in cur.fetchall()]


def build_maps(pairs, routes):
    """Compute deterministic remaps, raising on collisions.

    Returns:
        pair_map: {old_pair_id: new_pair_id}
        route_map: {old_route_id: (new_route_id, new_pair_id, new_canonical_key)}
    """
    pair_map = {}
    for old_id, (chain_id, origin, dest) in pairs.items():
        pair_map[old_id] = compute_pair_id(chain_id, origin, dest)

    route_map = {}
    for old_rid, old_pid, pools in routes:
        new_pair_id = pair_map[old_pid]
        new_rid = compute_route_id(new_pair_id, pools)
        new_ckey = canonical_key(new_pair_id, pools)
        route_map[old_rid] = (new_rid, new_pair_id, new_ckey)

    if len(set(pair_map.values())) != len(pair_map):
        raise SystemExit('FATAL: new pair id collision detected; aborting.')
    if len(set(r[0] for r in route_map.values())) != len(route_map):
        raise SystemExit('FATAL: new route id collision detected; aborting.')
    return pair_map, route_map


def check_invariants(cur, pair_map, route_map):
    """Compare stored ids against recomputed hashes for a row sample."""
    cur.execute("SELECT id FROM origin_destination_pair LIMIT 5000")
    mism = 0
    for (old_id,) in cur.fetchall():
        if pair_map[old_id] == old_id:
            mism += 1
    log.info('check: %d sampled pairs already match their hash id', mism)
    cur.execute("SELECT route_id FROM route LIMIT 5000")
    mism = 0
    for (old_rid,) in cur.fetchall():
        rec = route_map.get(old_rid)
        if rec is not None and rec[0] == old_rid:
            mism += 1
    log.info('check: %d sampled routes already match their hash id', mism)


def apply(cur, pair_map, route_map):
    """Perform the DDL/id rewrites in the caller's transaction."""
    # 1. Drop reference FKs (swaps_staging parent drop cascades to partitions).
    for table, cname in FK_CONSTRAINTS:
        cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {cname}")

    # 2. Drop serial sequence defaults; ids are now deterministic hashes.
    cur.execute("ALTER TABLE origin_destination_pair ALTER COLUMN id DROP DEFAULT")
    cur.execute("ALTER TABLE route ALTER COLUMN route_id DROP DEFAULT")
    cur.execute("DROP SEQUENCE IF EXISTS origin_destination_pair_id_seq")
    cur.execute("DROP SEQUENCE IF EXISTS route_route_id_seq")

    # 3. Widen the taxonomy id columns to BIGINT.
    cur.execute("ALTER TABLE origin_destination_pair ALTER COLUMN id TYPE bigint")
    cur.execute("ALTER TABLE route ALTER COLUMN route_id TYPE bigint, ALTER COLUMN pair_id TYPE bigint")
    cur.execute("ALTER TABLE route_hop ALTER COLUMN route_id TYPE bigint")
    cur.execute("ALTER TABLE route_daily_stats ALTER COLUMN route_id TYPE bigint")

    # 4. Remap ids via temp mapping tables (COPY for speed).
    pair_rows = [(old, new) for old, new in pair_map.items()]
    route_rows = [(old, new_rid, new_pid, new_ckey)
                  for old, (new_rid, new_pid, new_ckey) in route_map.items()]

    cur.execute("CREATE TEMP TABLE _pair_map (old_id bigint PRIMARY KEY, new_id bigint NOT NULL) ON COMMIT DROP")
    cur.execute("CREATE TEMP TABLE _route_map (old_id bigint PRIMARY KEY, new_rid bigint NOT NULL, new_pid bigint NOT NULL, new_ckey text NOT NULL) ON COMMIT DROP")
    psycopg2.extras.execute_values(
        cur, "INSERT INTO _pair_map (old_id, new_id) VALUES %s", pair_rows, page_size=1000)
    psycopg2.extras.execute_values(
        cur, "INSERT INTO _route_map (old_id, new_rid, new_pid, new_ckey) VALUES %s",
        route_rows, page_size=1000)

    cur.execute("""
        UPDATE origin_destination_pair p
        SET id = m.new_id
        FROM _pair_map m
        WHERE p.id = m.old_id
    """)
    cur.execute("""
        UPDATE route r
        SET route_id = m.new_rid, pair_id = m.new_pid, canonical_key = m.new_ckey
        FROM _route_map m
        WHERE r.route_id = m.old_id
    """)
    for table in ('route_hop', 'route_daily_stats', 'route_distribution_bucket',
                  'route_distribution_config'):
        cur.execute(f"""
            UPDATE {table} t
            SET route_id = m.new_rid
            FROM _route_map m
            WHERE t.route_id = m.old_id
        """)

    # 5. Legacy swaps: null + widen the vestigial route_id.
    #    DROP COLUMN is metadata-only (avoids rewriting 60 GB of partitions);
    #    re-add as BIGINT to match repo DDL.
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname='swaps_route_id_fkey'")
    if cur.fetchone():
        cur.execute("ALTER TABLE swaps DROP CONSTRAINT swaps_route_id_fkey")
    cur.execute("ALTER TABLE swaps DROP COLUMN IF EXISTS route_id")
    cur.execute("ALTER TABLE swaps ADD COLUMN route_id BIGINT REFERENCES route(route_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_swaps_route ON swaps (route_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_swaps_unclassified ON swaps (tx_hash) WHERE route_id IS NULL")

    # 6. Recreate the taxonomy FKs.
    cur.execute(
        "ALTER TABLE route ADD CONSTRAINT route_pair_id_fkey "
        "FOREIGN KEY (pair_id) REFERENCES origin_destination_pair(id)")
    cur.execute(
        "ALTER TABLE route_hop ADD CONSTRAINT route_hop_route_id_fkey "
        "FOREIGN KEY (route_id) REFERENCES route(route_id) ON DELETE CASCADE")
    cur.execute(
        "ALTER TABLE route_daily_stats ADD CONSTRAINT route_daily_stats_route_id_fkey "
        "FOREIGN KEY (route_id) REFERENCES route(route_id) ON DELETE CASCADE")
    cur.execute(
        "ALTER TABLE route_distribution_bucket ADD CONSTRAINT route_distribution_bucket_route_id_fkey "
        "FOREIGN KEY (route_id) REFERENCES route(route_id) ON DELETE CASCADE")
    cur.execute(
        "ALTER TABLE route_distribution_config ADD CONSTRAINT route_distribution_config_route_id_fkey "
        "FOREIGN KEY (route_id) REFERENCES route(route_id) ON DELETE CASCADE")
    cur.execute(
        "ALTER TABLE swaps_staging ADD CONSTRAINT swaps_staging_route_id_fkey "
        "FOREIGN KEY (route_id) REFERENCES route(route_id)")


def main() -> int:
    ap = argparse.ArgumentParser(description='Re-key route taxonomy to 64-bit hash ids.')
    ap.add_argument('--dsn', default=None, help='Override DATA_WAREHOUSE_DB.')
    ap.add_argument('--check', action='store_true', help='Read-only invariant check.')
    ap.add_argument('--rollback-test', action='store_true',
                    help='Apply inside a transaction, then roll back.')
    args = ap.parse_args()

    dsn = args.dsn or get_db_dsn()
    log.info('Connecting to %s', dsn.split('password=')[0])
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            pairs = load_pairs(cur)
            routes = load_routes(cur)
        pair_map, route_map = build_maps(pairs, routes)
        log.info('Mapped %d pairs and %d routes', len(pair_map), len(route_map))

        with conn.cursor() as cur:
            if args.check:
                check_invariants(cur, pair_map, route_map)
                log.info('Check complete (read-only). No changes applied.')
                return 0

            log.info('Starting migration transaction...')
            apply(cur, pair_map, route_map)

            if args.rollback_test:
                conn.rollback()
                log.info('ROLLBACK: applied then rolled back. No persistent changes.')
                return 0

            conn.commit()
            log.info('COMMIT: migration applied.')
            return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())