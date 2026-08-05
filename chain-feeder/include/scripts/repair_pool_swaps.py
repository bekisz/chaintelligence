"""Repair swap->pool misattribution.

Background: pool 144044's irrational APR was traced to swap rows whose true
subgraph pool (per The Graph's `{tx}#{log_index}` swap id) differs from the
DB `swaps.pool_id` they were stored under. A full volume scan found 23 pools
whose DB swap volume exceeds their subgraph pool volume by >2x (up to 65x).
The misattribution is one-directional (the true pools contain zero of these
txs) and arose from the token+fee fallback in `save_swaps` and/or the legacy
`normalize_swaps_pool_id.sql` migration bulk-assigning swaps to the wrong pool
when multiple same-pair pools existed.

This script, for a given pool, resolves each swap's TRUE subgraph pool by
querying the subgraph swap entity by its exact id `{tx}#{log_index}`, maps
that address back to a DB `liquidity_pool` row (creating one if missing), and
reassigns `swaps.pool_id` accordingly.

Usage:
    python repair_pool_swaps.py --list                      # list suspect pools
    python repair_pool_swaps.py --pool 144044 --sample 30   # classify a sample
    python repair_pool_swaps.py --pool 144044 --dry-run     # full resolve, no writes
    python repair_pool_swaps.py --pool 144044 --apply       # resolve + reassign
    python repair_pool_swaps.py --all --dry-run             # all 23 pools
    python repair_pool_swaps.py --all --apply

Resolution is checkpointed to a temp dir so interrupted runs can resume.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

def _load_secret(name: str) -> str:
    env = ROOT / ".env.secrets"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return os.getenv(name, "")

def _dsn() -> str:
    dsn = _load_secret("DATA_WAREHOUSE_DB")
    if not dsn:
        dsn = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"
    return dsn.replace("host=postgres", "host=localhost").replace("port=5432", "port=5433")

GRAPH_API_KEY = _load_secret("GRAPH_API_KEY")

SUBGRAPH_IDS = {
    ("Ethereum", "Uniswap V3"): "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    ("Ethereum", "Uniswap V4"): "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G",
    ("Arbitrum", "Uniswap V3"): "FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM",
    ("Arbitrum", "Uniswap V4"): "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",
    ("Base", "Uniswap V3"): "43Hwfi3dJSoGpyas9VwNoDAv55yjgGrPpNSmbQZArzMG",
    ("Base", "Uniswap V4"): "Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",
}

# Suspect pools found by the volume scan (db_vol > 2x subgraph_vol).
SUSPECT_POOLS = [
    41412, 41273, 108787, 9979, 1097, 144044, 143003, 142775, 41291, 1343,
    144838, 143048, 1159, 1263, 1590, 1465, 1292, 1650, 41405, 1544, 41388,
    1618, 547,
]

CHECKPOINT_DIR = Path(os.getenv("TMPDIR", "/tmp")) / "repair_pool_swaps"


def graph_url(chain, protocol):
    sgid = SUBGRAPH_IDS.get((chain, protocol))
    if not sgid:
        raise ValueError(f"no subgraph for {chain}/{protocol}")
    return f"https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{sgid}"


def graph_query(url, query, retries=5):
    for i in range(retries):
        try:
            r = requests.post(url, json={"query": query}, timeout=120)
            j = r.json()
            if "errors" in j:
                time.sleep(2 * (i + 1))
                continue
            return j
        except Exception:
            time.sleep(2 * (i + 1))
    return {"errors": [{"message": "query failed after retries"}]}


def db_connect():
    return psycopg2.connect(_dsn())


def get_pool_info(conn, pool_id):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ch.name, pr.name, lp.pool_name, lp.fee_bps, lp.pool_address
               FROM liquidity_pool lp
               JOIN chain ch ON lp.chain_id = ch.id
               JOIN protocol pr ON lp.protocol_id = pr.id
               WHERE lp.id = %s""",
            (pool_id,),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"pool {pool_id} not found")
        return {"chain": row[0], "protocol": row[1], "pool_name": row[2],
                "fee_bps": row[3], "pool_address": row[4]}


def load_swaps(conn, pool_id):
    with conn.cursor() as cur:
        cur.execute("SELECT tx_hash, log_index FROM swaps WHERE pool_id = %s ORDER BY ts", (pool_id,))
        return cur.fetchall()


def resolve_chunk(url, ids):
    """Given [tx#log, ...], return {id: true_pool_address}."""
    if not ids:
        return {}
    q = ('query { swaps(where: { id_in: [%s] }, first: 1000) '
         '{ id pool { id } } }' % ",".join('"%s"' % i for i in ids))
    resp = graph_query(url, q)
    d = (resp.get("data") or {}).get("swaps") or []
    return {s["id"]: s["pool"]["id"] for s in d}


def resolve_pool(url, all_rows, checkpoint_file, workers=8, sample=None):
    """Resolve each (tx, log) to its true subgraph pool address."""
    if sample:
        import random
        random.seed(42)
        all_rows = random.sample(all_rows, min(sample, len(all_rows)))

    results = {}
    if checkpoint_file.exists():
        results = json.loads(checkpoint_file.read_text())
        remaining = [r for r in all_rows if "%s#%s" % r not in results]
    else:
        remaining = all_rows

    print(f"  {len(all_rows)} rows total, {len(results)} cached, {len(remaining)} to resolve", flush=True)
    if not remaining:
        return results

    ids = ["%s#%s" % (tx, log) for tx, log in remaining]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for i in range(0, len(ids), 60):
            chunk = ids[i:i + 60]
            futures[ex.submit(resolve_chunk, url, chunk)] = chunk
        for fut in as_completed(futures):
            chunk = futures[fut]
            try:
                found = fut.result()
                results.update(found)
            except Exception as e:
                print(f"  chunk failed: {e}", flush=True)
            done += len(chunk)
            if done % 2000 == 0:
                print(f"  resolved {done}/{len(ids)}", flush=True)
                checkpoint_file.write_text(json.dumps(results))

    # stash not-found separately so we don't lose them across runs
    notfound = [r for r in remaining if "%s#%s" % r not in results]
    checkpoint_file.write_text(json.dumps(results))
    return results, notfound


def resolve_by_transaction(url, tx_rows, workers=8):
    """Tier-2: given [(tx, log, amount_usd, amount0, amount1), ...] for a set of
    txs, query transaction_in and match rows to subgraph swaps by amount0/amount1
    (fallback: amountUSD). Returns {id_str: true_pool_address}."""
    out = {}
    txs = sorted({tx for tx, *_ in tx_rows})
    by_tx = {}
    for tx, log, *rest in tx_rows:
        by_tx.setdefault(tx, []).append((log, rest))
    chunks = [txs[i:i + 20] for i in range(0, len(txs), 20)]

    def one(chunk):
        res = {}
        q = ('query { swaps(where: { transaction_in: [%s] }, first: 500) '
             '{ id transaction { id } pool { id } amountUSD amount0 amount1 } }'
             % ",".join('"%s"' % t for t in chunk))
        resp = graph_query(url, q)
        d = (resp.get("data") or {}).get("swaps") or []
        # index by tx
        per_tx = defaultdict(list)
        for s in d:
            t = (s.get("transaction") or {}).get("id")
            per_tx[t].append(s)
        for tx in chunk:
            for log, rest in by_tx.get(tx, []):
                usd, a0, a1 = (rest + [None, None, None])[:3]
                cand = per_tx.get(tx, [])
                match = None
                # amount0+amount1 is the reliable discriminator (USD is shared
                # across multi-hop route legs, so never unique)
                for s in cand:
                    try:
                        if (a0 is not None and a1 is not None
                                and abs(float(s.get("amount0", 0)) - a0) < max(1e-9, abs(a0) * 1e-6)
                                and abs(float(s.get("amount1", 0)) - a1) < max(1e-9, abs(a1) * 1e-6)):
                            match = s
                            break
                    except (TypeError, ValueError):
                        pass
                if match is None and usd is not None and usd > 0:
                    for s in cand:
                        try:
                            if abs(float(s.get("amountUSD", 0)) - usd) < max(1e-6, usd * 1e-6):
                                match = s
                                break
                        except (TypeError, ValueError):
                            pass
                if match is not None:
                    res["%s#%s" % (tx, log)] = match["pool"]["id"]
        return res

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(one, c): c for c in chunks}
        for fut in as_completed(futures):
            try:
                out.update(fut.result())
            except Exception as e:
                print(f"  tier-2 chunk failed: {e}", flush=True)
            done += 1
            if done % 25 == 0:
                print(f"  tier-2 txs {done}/{len(chunks)}", flush=True)
    return out


def resolve_pool_simple(url, all_rows, checkpoint_file, pool_id=None, workers=8, sample=None):
    """Two-tier resolution. Tier-1: exact id `{tx}#{log}`. Tier-2 (for misses):
    match by transaction + amount_usd. Returns (results, notfound)."""
    if sample:
        import random
        random.seed(42)
        all_rows = random.sample(all_rows, min(sample, len(all_rows)))

    results = {}
    if checkpoint_file.exists():
        results = json.loads(checkpoint_file.read_text())
    rows_to_do = [r for r in all_rows if "%s#%s" % r not in results]

    print(f"  {len(all_rows)} rows, {len(results)} cached, {len(rows_to_do)} to resolve", flush=True)

    ids = ["%s#%s" % (tx, log) for tx, log in rows_to_do]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for i in range(0, len(ids), 60):
            chunk = ids[i:i + 60]
            futures[ex.submit(resolve_chunk, url, chunk)] = chunk
        for fut in as_completed(futures):
            chunk = futures[fut]
            try:
                found = fut.result()
                results.update(found)
            except Exception:
                pass
            done += len(chunk)
            if done % 4000 == 0:
                print(f"  resolved {done}/{len(ids)}", flush=True)
                checkpoint_file.write_text(json.dumps(results))
    checkpoint_file.write_text(json.dumps(results))

    # Tier-2: rows still unresolved -> transaction + amount match
    misses = [r for r in rows_to_do if "%s#%s" % r not in results]
    if misses and pool_id is not None:
        print(f"  tier-2: {len(misses)} rows unresolved by id, matching by transaction/amount", flush=True)
        conn = db_connect()
        cur = conn.cursor()
        tx_set = sorted({tx for tx, _ in misses})
        amt = {}
        for i in range(0, len(tx_set), 1000):
            cur.execute(
                "SELECT tx_hash, log_index, amount_usd, amount0, amount1 FROM swaps WHERE pool_id = %s AND tx_hash = ANY(%s)",
                (pool_id, tx_set[i:i + 1000]),
            )
            for t, l, u, a0, a1 in cur.fetchall():
                amt[(t, l)] = (u, a0, a1)
        cur.close()
        conn.close()
        tx_rows = [(tx, log) + amt.get((tx, log), (None, None, None)) for tx, log in misses]
        res2 = resolve_by_transaction(url, tx_rows, workers=workers)
        results.update(res2)
        print(f"  tier-2 resolved: {len(res2)}", flush=True)
        checkpoint_file.write_text(json.dumps(results))

    notfound = [r for r in all_rows if "%s#%s" % r not in results]
    return results, notfound


def map_true_pool_to_db(conn, address, pool_info, name_hint=None):
    """Return (db_pool_id, absorbed) for a true subgraph pool address.
    Creates the pool if missing. `absorbed` is True when the row was merged
    into an existing pool with a different address (same name+fee collision),
    in which case callers should treat the mapping as uncertain."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM liquidity_pool WHERE LOWER(pool_address) = %s OR LOWER(pool_id) = %s",
            (address.lower(), address.lower()),
        )
        row = cur.fetchone()
        if row:
            return row[0], False
    # fetch subgraph pool metadata (fee tier, tokens) to create it
    url = graph_url(pool_info["chain"], pool_info["protocol"])
    q = ('query { pools(where: { id: "%s" }) '
         '{ id feeTier totalValueLockedUSD token0 { id symbol } token1 { id symbol } } }' % address)
    resp = graph_query(url, q)
    d = (resp.get("data") or {}).get("pools") or []
    if not d:
        return None, False
    p = d[0]
    fee_tier = p.get("feeTier")
    try:
        fee_bps = float(fee_tier) / 100.0
    except (TypeError, ValueError):
        fee_bps = None
    c0, c1 = p.get("token0") or {}, p.get("token1") or {}
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM chain WHERE name = %s", (pool_info["chain"],))
        chain_id = cur.fetchone()
        cur.execute("SELECT id FROM protocol WHERE name = %s", (pool_info["protocol"],))
        protocol_id = cur.fetchone()
        cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c0.get("symbol", ""),))
        c0_id = cur.fetchone()
        cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c1.get("symbol", ""),))
        c1_id = cur.fetchone()
        if not chain_id or not protocol_id or not c0_id or not c1_id:
            return None, False
        name = name_hint or f"{c0.get('symbol')}-{c1.get('symbol')} {fee_bps/100 if fee_bps else 0}%"
        cur.execute(
            """INSERT INTO liquidity_pool (chain_id, protocol_id, pool_name, fee_bps,
                       coin0_id, coin1_id, pool_address, pool_id, reverted)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)
               ON CONFLICT (chain_id, protocol_id, pool_name, fee_bps, (COALESCE(pool_id, '')))
               DO UPDATE SET pool_address = COALESCE(liquidity_pool.pool_address, EXCLUDED.pool_address)
               RETURNING id, pool_address""",
            (chain_id[0], protocol_id[0], name, fee_bps, c0_id[0], c1_id[0],
             address.lower(), address.lower() if len(address) == 66 else None),
        )
        row = cur.fetchone()
        if row:
            conn.commit()
            absorbed = (row[1] or "").lower() != address.lower()
            return row[0], absorbed
    conn.rollback()
    return None, False


def build_reassignment(conn, pool_id, results):
    """Build {db_pool_id: [rows]} reassignment grouped by target."""
    # map true address -> db pool id
    addr_to_db = {}
    true_to_count = defaultdict(int)
    for row, true_addr in results.items():
        true_to_count[true_addr] += 1
    pool_info = get_pool_info(conn, pool_id)

    moves = defaultdict(list)
    keep = 0
    unmapped = []
    for key, true_addr in results.items():
        tx, log = key.rsplit("#", 1)
        if true_addr.lower() == (pool_info["pool_address"] or "").lower():
            keep += 1
            continue
        if true_addr not in addr_to_db:
            dbp, absorbed = map_true_pool_to_db(conn, true_addr, pool_info)
            if dbp is None:
                unmapped.append((tx, log, true_addr, "metadata_unresolvable"))
                continue
            if absorbed:
                unmapped.append((tx, log, true_addr, "absorbed_into_%s" % dbp))
                continue
            addr_to_db[true_addr] = dbp
        moves[addr_to_db[true_addr]].append((tx, log))
    return moves, keep, unmapped, dict(true_to_count)


def summarize(pool_id, all_rows, results, moves, keep, unmapped, true_to_count):
    print(f"\n=== pool {pool_id} ===")
    print(f"  rows={len(all_rows)} resolved={len(results)} not_resolved={len(all_rows)-len(results)}")
    print(f"  keep (already correct)={keep}")
    print(f"  unmapped (true pool not creatable)={len(unmapped)}")
    print("  true pool distribution:")
    for addr, n in sorted(true_to_count.items(), key=lambda x: -x[1]):
        print(f"    {addr}  x{n}")
    print("  reassignments:")
    for dbp, rows in sorted(moves.items(), key=lambda x: -len(x[1])):
        print(f"    -> pool {dbp}: {len(rows)} swaps")


def run_pool(pool_id, sample=None, dry_run=False, apply=False, workers=8):
    conn = db_connect()
    info = get_pool_info(conn, pool_id)
    print(f"pool {pool_id} ({info['chain']}/{info['protocol']}) \"{info['pool_name']}\"", flush=True)
    url = graph_url(info["chain"], info["protocol"])
    all_rows = load_swaps(conn, pool_id)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ck = CHECKPOINT_DIR / f"pool_{pool_id}.json"
    results, notfound = resolve_pool_simple(url, all_rows, ck, pool_id=pool_id, workers=workers, sample=sample)
    if sample:
        # sample run: just summarize attribution, no moves
        from collections import Counter
        c = Counter(results.values())
        print(f"  sample={len(results)} notfound={len(notfound)}")
        for addr, n in c.most_common():
            print(f"    {addr}  x{n}")
        return
    moves, keep, unmapped, true_to_count = build_reassignment(conn, pool_id, results)
    summarize(pool_id, all_rows, results, moves, keep, unmapped, true_to_count)

    if apply:
        total = sum(len(v) for v in moves.values())
        print(f"\n  APPLYING {total} reassignments...", flush=True)
        with conn.cursor() as cur:
            for dbp, rows in moves.items():
                for i in range(0, len(rows), 20000):
                    batch = rows[i:i + 20000]
                    cur.execute(
                        """
                        UPDATE swaps SET pool_id = %s
                        FROM (SELECT unnest(%s::varchar[]) AS tx_hash, unnest(%s::int[]) AS log_index) AS m
                        WHERE swaps.tx_hash = m.tx_hash AND swaps.log_index = m.log_index
                          AND swaps.pool_id = %s
                        """,
                        (dbp,
                         [t for t, _ in batch],
                         [l for _, l in batch],
                         pool_id),
                    )
                    conn.commit()
        print("  done.", flush=True)
    conn.close()


def list_pools():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """SELECT lp.id, ch.name, pr.name, lp.pool_name, lp.fee_bps, lp.pool_address,
                  (SELECT COUNT(*) FROM swaps WHERE pool_id = lp.id)
           FROM liquidity_pool lp
           JOIN chain ch ON lp.chain_id = ch.id
           JOIN protocol pr ON lp.protocol_id = pr.id
           WHERE lp.id = ANY(%s)
           ORDER BY (SELECT COUNT(*) FROM swaps WHERE pool_id = lp.id) DESC""",
        (SUSPECT_POOLS,),
    )
    for r in cur.fetchall():
        print(f"{r[0]:>7} {r[1]:<9} {r[2]:<12} {r[3]:<22} fee={r[4]:<6} addr={r[5]} swaps={r[6]}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pool", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if args.list:
        list_pools()
    elif args.pool:
        run_pool(args.pool, sample=args.sample, dry_run=args.dry_run, apply=args.apply, workers=args.workers)
    elif args.all:
        for pid in SUSPECT_POOLS:
            try:
                run_pool(pid, dry_run=args.dry_run, apply=args.apply, workers=args.workers)
            except SystemExit as e:
                print(f"skip {pid}: {e}")
    else:
        ap.print_help()
