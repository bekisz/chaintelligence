from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import timedelta
import logging

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
}

@task
def migrate_keys():
    """Migrates snapshots from legacy Zapper position keys to native Graph keys and deletes Zapper positions."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # Find token_ids with multiple positions
    cur.execute("""
        SELECT token_id, array_agg(id), array_agg(position_key)
        FROM liquidity_pool_position
        WHERE token_id IS NOT NULL AND position_key NOT LIKE 'uniswap%'
        GROUP BY token_id
    """)
    rows = cur.fetchall()
    
    if not rows:
        logging.info("No Zapper-keyed positions found. Migration complete.")
        return
        
    updated_snapshots = 0
    deleted_positions = 0
    
    for row in rows:
        token_id, pos_ids, pos_keys = row
        # Find if there is a Graph-keyed position for this token_id
        cur.execute("""
            SELECT id FROM liquidity_pool_position
            WHERE token_id = %s AND position_key LIKE 'uniswap%'
            LIMIT 1
        """, (token_id,))
        graph_res = cur.fetchone()
        
        if graph_res:
            graph_pos_id = graph_res[0]
            # We have a Graph position. Reassign snapshots from the Zapper positions.
            for pid, pkey in zip(pos_ids, pos_keys):
                if pkey.startswith('uniswap'): continue
                
                # Reassign snapshots
                cur.execute("""
                    UPDATE liquidity_pool_position_snapshot
                    SET position_id = %s
                    WHERE position_id = %s
                """, (graph_pos_id, pid))
                updated_snapshots += cur.rowcount
                
                # Delete Zapper position
                cur.execute("DELETE FROM liquidity_pool_position WHERE id = %s", (pid,))
                deleted_positions += cur.rowcount
                
    conn.commit()
    cur.close()
    conn.close()
    
    logging.info(f"Reassigned {updated_snapshots} snapshots and deleted {deleted_positions} Zapper positions.")

with DAG(
    'migrate_zapper_position_keys',
    default_args=default_args,
    description='One-time migration to consolidate Zapper-keyed and Graph-keyed positions',
    schedule=None,
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['migration', 'zapper'],
) as dag:
    
    migrate_keys()
