"""O&D goal-state self-healing backfill DAG.

Trigger manually (``airflow dags trigger ods_goal_state_backfill``) or let the
schedule run it to converge the warehouse toward the goal state declared in
`config/ods-goal-state.yaml`. It keeps working until every requirement is met:

Each run:
  1. Evaluates coverage and lists what is still missing (``gaps``).
  2. Recomputes ``route_daily_stats`` / ``route_daily_stats_bucket`` from the
     swaps already present (cheap, no network call).
  3. If gaps remain, triggers the per-chain swap ETL DAGs (``graph_*_swaps``)
     with a ``backfill_days`` conf for the networks that have raw-``swaps``
     gaps, plus the rollup DAGs (``route_daily_stats_rollup``,
     ``global_liquidity_pool_daily_stats_rollup``).

The swap ETL DAGs ingest asynchronously, so the DAG is scheduled (default every
30 min) to re-check and keep converging. Once every requirement is met,
``compute_backfill_plan`` returns no triggers and the run becomes a cheap
recompute+report no-op — it never re-queries The Graph for data already present.

Only networks with an actual raw-``swaps`` gap are asked to re-fetch. Param
``backfill_days_cap`` caps how far back each network backfills per trigger.
"""
import logging
from datetime import timedelta

from airflow import DAG
from airflow.sdk import task, Param
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
import pendulum
import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
from include.od_retention import (
    load_goal_state,
    run_checks,
    export_gaps,
    backfill_missing_daily_stats,
)

DEFAULT_CAP_DAYS = 90
DEFAULT_SCHEDULE = '*/30 * * * *'  # keep converging until requirements are met

# (network display name, conf key used by the target DAG, dag_id)
SWAP_DAGS = [
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v2_swaps'),
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v3_swaps'),
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v4_swaps'),
    ('Arbitrum', 'Arbitrum', 'graph_arbitrum_uniswap_v3_swaps'),
    ('Arbitrum', 'Arbitrum', 'graph_arbitrum_uniswap_v4_swaps'),
    ('Base', 'Base', 'graph_base_uniswap_v3_swaps'),
    ('Base', 'Base', 'graph_base_uniswap_v4_swaps'),
    ('Base', 'Base_Aerodrome', 'graph_base_aerodrome_v3_swaps'),
    ('BNB', 'BNB', 'graph_bnb_uniswap_v3_swaps'),
    ('BNB', 'BNB', 'graph_bnb_uniswap_v4_swaps'),
    ('BNB', 'BNB_PancakeSwap_V3', 'graph_bnb_pancakeswap_v3_swaps'),
    ('BNB', 'BNB_PancakeSwap_V4', 'graph_bnb_pancakeswap_v4_swaps'),
]

ROLLUP_DAGS = ['route_daily_stats_rollup', 'global_liquidity_pool_daily_stats_rollup']

CHAIN_TO_NETWORK = {'ethereum': 'Ethereum', 'arbitrum': 'Arbitrum',
                    'base': 'Base', 'bnb': 'BNB'}


def connect():
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    conn.autocommit = False
    return conn


def _networks_for_chain(chain) -> list:
    """Resolve a gap's ``chain`` (``*`` or a list) to network display names."""
    if chain == '*':
        return list(CHAIN_TO_NETWORK.values())
    out = []
    for c in chain:
        net = CHAIN_TO_NETWORK.get(str(c).lower())
        if net:
            out.append(net)
    return out


def _network_has_raw_swaps(conn, network: str, since_date, today) -> bool:
    """True if any raw swap (classified or not) exists for a network from a date.

    Used to distinguish a genuine data-missing gap from classification lag: if
    raw swaps are already present, re-fetching from The Graph would be wasted
    work — the gap just needs classification to catch up.
    """
    from datetime import datetime, timedelta
    end = today + timedelta(days=1)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS(
                SELECT 1 FROM swaps s
                JOIN liquidity_pool lp ON s.pool_id = lp.id
                JOIN chain ch ON lp.chain_id = ch.id
                WHERE LOWER(ch.name) = LOWER(%s)
                  AND s.ts >= %s AND s.ts < %s
                LIMIT 1
            )
        """, (network, since_date.isoformat(), end.isoformat()))
        return bool(cur.fetchone()[0])


@task
def compute_backfill_plan(**context):
    params = context.get('params', {})
    cap = int(params.get('backfill_days_cap', DEFAULT_CAP_DAYS))

    goal = load_goal_state()
    conn = connect()
    try:
        report = run_checks(conn, goal)
    finally:
        conn.close()
    not_ok = [r for r in report if r['status'] != 'ok']
    gaps = export_gaps(report)

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()

    swaps_gaps = [g for g in gaps if g['layer'] == 'swaps']
    other_gaps = [g for g in gaps if g['layer'] != 'swaps']

    oldest_per_net = {}
    for g in swaps_gaps:
        for net in _networks_for_chain(g['chain']):
            f = datetime.strptime(g['from'], '%Y-%m-%d').date()
            if net not in oldest_per_net or f < oldest_per_net[net]:
                oldest_per_net[net] = f

    # A swaps coverage gap can be classification lag rather than missing raw
    # data. Only re-ingest a network when raw swaps (classified OR unclassified)
    # are actually absent from its gap window, otherwise re-fetching would never
    # converge (it would just keep re-adding already-present swaps while the
    # classifier catches up). 
    missing_nets = {}
    if oldest_per_net:
        conn2 = connect()
        try:
            for net, min_from in oldest_per_net.items():
                if not _network_has_raw_swaps(conn2, net, min_from, today):
                    missing_nets[net] = min_from
                    logging.info("  %s: raw swaps MISSING since %s -> will backfill",
                                 net, min_from)
                else:
                    logging.info("  %s: raw swaps PRESENT (classification lag) -> skip re-fetch", net)
        finally:
            conn2.close()

    specs = []
    if missing_nets:
        for net, conf_key, dag_id in SWAP_DAGS:
            if net in missing_nets:
                days = min((today - missing_nets[net]).days + 1, cap)
                specs.append({'trigger_dag_id': dag_id,
                              'conf': {'backfill_days': {conf_key: days}}})
    if swaps_gaps or other_gaps:
        for dag_id in ROLLUP_DAGS:
            specs.append({'trigger_dag_id': dag_id, 'conf': {}})

    logging.info("coverage: %d checks, %d not ok", len(report), len(not_ok))
    logging.info("backfill plan: %d ETL triggers", len(specs))
    for s in specs:
        logging.info("  -> %s conf=%s", s['trigger_dag_id'], s['conf'])

    context['task_instance'].xcom_push(key='report', value=report)
    return specs


@task
def recompute_and_report(**context):
    """Recompute route daily stats/buckets from present swaps and report coverage."""
    goal = load_goal_state()
    conn = connect()
    try:
        report = run_checks(conn, goal)
        n = backfill_missing_daily_stats(conn, report)
    finally:
        conn.close()
    not_ok = [r for r in report if r['status'] != 'ok']
    logging.info("recomputed %d missing route daily-stats days", n)
    logging.info("FINAL coverage: %d/%d checks not ok", len(not_ok), len(report))
    return {'not_ok': len(not_ok), 'checks': len(report)}


with DAG(
    'ods_goal_state_backfill',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': True,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Converge the warehouse toward the O&D goal state (self-healing ETL backfill)',
    schedule=DEFAULT_SCHEDULE,
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['ods', 'goal-state', 'backfill'],
    params={
        'backfill_days_cap': Param(
            default=DEFAULT_CAP_DAYS, type='integer',
            description='Max number of days each network is asked to backfill per trigger.'),
    },
) as dag:

    plan = compute_backfill_plan()

    trigger_etl = TriggerDagRunOperator.partial(
        task_id='trigger_etl',
        wait_for_completion=False,
        reset_dag_run=True,
        trigger_rule='all_done',
    ).expand_kwargs(plan)

    recompute = recompute_and_report()

    plan >> trigger_etl
    plan >> recompute
