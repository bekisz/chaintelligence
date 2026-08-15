"""Deduplicate liquidity_pool rows sharing pool_address or pool_id.

Runs in one session. Phases:
  1. Bulk-compute survivors (pre-aggregates scores for all pools)
  2. Reparent FK references (history, swaps, positions) in batches
  3. Delete doomed pools + add partial UNIQUE indexes

Usage:
    python chain-feeder/include/scripts/deduplicate_pools.py           # full run
    python chain-feeder/include/scripts/deduplicate_pools.py --dry-run # preview only
"""
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("dedup")

BATCH = 10000


def get_conn():
    import psycopg2
    dsn = os.environ.get(
        "DATA_WAREHOUSE_DB",
        "host=localhost port=5433 dbname=chaintelligence user=airflow password=airflow",
    )
    return psycopg2.connect(dsn)


def phase1_find_merge_map(cur):
    cur.execute("SELECT pool_id, COUNT(*) FROM liquidity_pool_daily_stats GROUP BY pool_id")
    hist_counts = dict(cur.fetchall())
    cur.execute("SELECT pool_id, COUNT(*) FROM swaps GROUP BY pool_id")
    swap_counts = dict(cur.fetchall())

    def score(pid):
        return (hist_counts.get(pid, 0), swap_counts.get(pid, 0), -pid)

    cur.execute("""
        SELECT pool_address, ARRAY_AGG(id ORDER BY id)
        FROM liquidity_pool WHERE pool_address IS NOT NULL
        GROUP BY pool_address HAVING COUNT(*) > 1
    """)
    addr_groups = {r[0]: list(r[1]) for r in cur.fetchall()}

    cur.execute("""
        SELECT pool_id, ARRAY_AGG(id ORDER BY id)
        FROM liquidity_pool WHERE pool_id IS NOT NULL
        GROUP BY pool_id HAVING COUNT(*) > 1
    """)
    id_groups = {r[0]: list(r[1]) for r in cur.fetchall()}

    merge_map = {}
    visited = set()

    for groups in [addr_groups, id_groups]:
        for key, ids in groups.items():
            active = [p for p in ids if p not in visited and p not in merge_map]
            if len(active) <= 1:
                continue
            survivor = max(active, key=score)
            visited.add(survivor)
            for p in active:
                if p != survivor:
                    merge_map[p] = survivor
                    visited.add(p)

    return merge_map


def reparent_pool(cur, conn, doomed, survivor):
    cur.execute("""
        UPDATE liquidity_pool_daily_stats SET pool_id = %s
        WHERE pool_id = %s AND day NOT IN (
            SELECT day FROM liquidity_pool_daily_stats WHERE pool_id = %s
        )
    """, (survivor, doomed, survivor))
    hist_moved = cur.rowcount
    cur.execute("DELETE FROM liquidity_pool_daily_stats WHERE pool_id = %s", (doomed,))
    hist_del = cur.rowcount
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM swaps WHERE pool_id = %s", (doomed,))
    total = cur.fetchone()[0]
    if total:
        done = 0
        while done < total:
            cur.execute("""
                UPDATE swaps SET pool_id = %s
                WHERE pool_id = %s
                  AND ctid IN (SELECT ctid FROM swaps WHERE pool_id = %s LIMIT %s)
            """, (survivor, doomed, doomed, BATCH))
            done += cur.rowcount
            conn.commit()

    cur.execute("UPDATE liquidity_pool_position SET pool_id = %s WHERE pool_id = %s",
                (survivor, doomed))
    pos_moved = cur.rowcount
    if pos_moved:
        conn.commit()

    if hist_moved or total or pos_moved:
        logger.info(f"  {doomed} -> {survivor}: hist_moved={hist_moved} hist_del={hist_del} swaps={total} pos={pos_moved}")


def phase2_reparent(cur, conn, rows):
    logger.info(f"Reparenting {len(rows)} pools...")
    for i, (doomed, survivor) in enumerate(rows):
        if (i + 1) % 50 == 0:
            logger.info(f"  progress: {i+1}/{len(rows)}")
        reparent_pool(cur, conn, doomed, survivor)
    logger.info("Reparenting complete.")


def phase3_cleanup(cur, conn, doomed_ids):
    logger.info(f"Deleting {len(doomed_ids)} doomed pools...")
    for i in range(0, len(doomed_ids), 100):
        batch = doomed_ids[i:i+100]
        ph = ",".join(["%s"] * len(batch))
        cur.execute(f"DELETE FROM liquidity_pool WHERE id IN ({ph})", batch)
        conn.commit()

    logger.info("Adding UNIQUE partial indexes...")
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lp_pool_address_unique
        ON liquidity_pool (pool_address) WHERE pool_address IS NOT NULL
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lp_pool_id_unique
        ON liquidity_pool (pool_id) WHERE pool_id IS NOT NULL
    """)
    conn.commit()

    cur.execute("DROP INDEX IF EXISTS idx_lp_pool_address")
    cur.execute("DROP INDEX IF EXISTS idx_lp_pool_id")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM liquidity_pool")
    logger.info(f"Remaining pools: {cur.fetchone()[0]}")


def main():
    dry_run = "--dry-run" in sys.argv
    conn = get_conn()
    cur = conn.cursor()

    merge_map = phase1_find_merge_map(cur)
    logger.info(f"Merge plan: {len(merge_map)} doomed pools")

    if dry_run:
        for doomed, survivor in list(merge_map.items())[:5]:
            cur.execute(
                "SELECT pool_name FROM liquidity_pool WHERE id = %s", (survivor,))
            name = cur.fetchone()
            logger.info(f"  {doomed} -> {survivor} ({name[0] if name else '?'})")
        conn.rollback()
        cur.close()
        conn.close()
        return

    rows = list(merge_map.items())
    phase2_reparent(cur, conn, rows)
    phase3_cleanup(cur, conn, [d for d, s in rows])

    cur.close()
    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()