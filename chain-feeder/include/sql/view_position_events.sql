
SELECT 
    p.network,
    p.protocol,
    p.pool_name,
    pos.id AS position_id,
    pos.token_id,
    e.event_type,
    e.amount0,
    e.amount1,
    e.amount_usd,
    e.liquidity_change,
    e.timestamp,
    e.tx_hash,
    e.block_number
FROM liquidity_pool_position pos
JOIN liquidity_pool p ON pos.pool_id = p.id
JOIN liquidity_pool_position_event e ON pos.id = e.position_id
ORDER BY e.timestamp DESC;
