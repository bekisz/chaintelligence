import logging
import requests
import psycopg2

SOURCE_CONFIDENCE = {
    'manual': 100,
    'cmc': 90,
    'swap_logs': 85,
    'subgraph': 85,
    'coingecko': 80,
    'dexscreener': 70
}

# Chain name normalizers to match database `chain.name`
CHAIN_NAME_MAP = {
    'ethereum': 'Ethereum',
    'eth': 'Ethereum',
    'bsc': 'BNB',
    'bnb': 'BNB',
    'binance-smart-chain': 'BNB',
    'arbitrum': 'Arbitrum',
    'arbitrum-one': 'Arbitrum',
    'base': 'Base',
    'solana': 'Solana',
    'celo': 'Celo',
    'polygon': 'Polygon',
    'polygon-pos': 'Polygon'
}


class MultiSourceContractEngine:
    """
    Engine to ingest and resolve token contract addresses across multiple data sources
    (CoinMarketCap, DexScreener, CoinGecko, DEX Subgraphs) with priority-based conflict resolution.
    """

    def __init__(self, conn=None):
        self.conn = conn

    def upsert_contract(self, cur, coin_id: int, chain_id: int, contract_address: str,
                        decimals: int = 18, is_native: bool = False, source: str = 'cmc') -> bool:
        """
        Upsert a contract address into `coin_contract`.
        Conflict Resolution Rule: Overwrites existing contract address ONLY if new source
        has equal or higher confidence_score than the existing row.
        Handles both (coin_id, chain_id) primary key and (chain_id, contract_address) unique index.
        """
        if not contract_address:
            return False

        clean_addr = contract_address.strip().lower()
        confidence = SOURCE_CONFIDENCE.get(source, 50)

        # Check if this contract address is already mapped to another coin_id on this chain
        cur.execute("""
            SELECT coin_id, confidence_score 
            FROM coin_contract 
            WHERE chain_id = %s AND LOWER(contract_address) = %s
        """, (chain_id, clean_addr))
        existing_match = cur.fetchone()

        if existing_match:
            ex_coin_id, ex_conf = existing_match
            if ex_coin_id != coin_id:
                if confidence > ex_conf:
                    # Update mapping to the higher confidence coin_id
                    cur.execute("""
                        UPDATE coin_contract
                        SET coin_id = %s, decimals = COALESCE(%s, decimals), is_native = %s,
                            source = %s, confidence_score = %s, verified_at = NOW()
                        WHERE chain_id = %s AND LOWER(contract_address) = %s
                    """, (coin_id, decimals or 18, is_native, source, confidence, chain_id, clean_addr))
                    return True
                else:
                    # Existing coin mapping has higher confidence, skip
                    return False

        cur.execute("SAVEPOINT upsert_contract_sp")
        try:
            cur.execute("""
                INSERT INTO coin_contract (
                    coin_id, chain_id, contract_address, decimals,
                    is_native, source, confidence_score, verified_at, tracked
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), true)
                ON CONFLICT (coin_id, chain_id) DO UPDATE SET
                    contract_address = EXCLUDED.contract_address,
                    decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
                    is_native = EXCLUDED.is_native,
                    source = EXCLUDED.source,
                    confidence_score = EXCLUDED.confidence_score,
                    verified_at = EXCLUDED.verified_at
                WHERE EXCLUDED.confidence_score >= coin_contract.confidence_score;
            """, (coin_id, chain_id, clean_addr, decimals or 18, is_native, source, confidence))
            cur.execute("RELEASE SAVEPOINT upsert_contract_sp")
            return True
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT upsert_contract_sp")
            logging.debug(f"Could not upsert contract {clean_addr} for coin {coin_id}: {e}")
            return False

    def resolve_missing_contracts(self, conn, min_liquidity_usd: float = 1000.0) -> int:
        """
        Find coins in `coin` table missing contracts and attempt multi-source fallback resolution.
        """
        with conn.cursor() as cur:
            # Load DB chain name -> chain_id mapping
            cur.execute("SELECT LOWER(name), id, name FROM chain")
            chain_db_map = {row[0]: row[1] for row in cur.fetchall()}

            # Query coins missing contract addresses in coin_contract
            cur.execute("""
                SELECT c.coin_id, c.symbol, c.name, c.decimals
                FROM coin c
                LEFT JOIN coin_contract cc ON c.coin_id = cc.coin_id
                WHERE cc.coin_id IS NULL
                ORDER BY c.market_cap DESC NULLS LAST, c.cmc_rank ASC NULLS LAST
            """)
            missing_coins = cur.fetchall()

            logging.info(f"Found {len(missing_coins)} coins with missing contracts requiring fallback resolution.")
            resolved_count = 0

            for coin_id, symbol, name, coin_decimals in missing_coins:
                if not symbol:
                    continue

                resolved = False

                # 1. DexScreener API Search
                ds_url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
                try:
                    r = requests.get(ds_url, timeout=10)
                    if r.status_code == 200:
                        pairs = r.json().get("pairs", []) or []
                        logged_resolutions = set()
                        for p in pairs:
                            liq_usd = float((p.get("liquidity") or {}).get("usd") or 0)
                            if liq_usd < min_liquidity_usd:
                                continue

                            chain_raw = p.get("chainId", "").lower()
                            db_chain_name = CHAIN_NAME_MAP.get(chain_raw) or chain_raw.title()
                            chain_id = chain_db_map.get(db_chain_name.lower())

                            if not chain_id:
                                continue

                            base = p.get("baseToken", {})
                            quote = p.get("quoteToken", {})

                            addr = None
                            if base.get("symbol", "").upper() == symbol.upper():
                                addr = base.get("address")
                            elif quote.get("symbol", "").upper() == symbol.upper():
                                addr = quote.get("address")

                            if addr and addr.lower() not in logged_resolutions:
                                is_inserted = self.upsert_contract(
                                    cur, coin_id, chain_id, addr,
                                    decimals=coin_decimals or 18,
                                    is_native=False,
                                    source='dexscreener'
                                )
                                logged_resolutions.add(addr.lower())
                                if is_inserted:
                                    logging.info(f"Resolved contract for {symbol} on {db_chain_name} via DexScreener: {addr}")
                                    resolved = True
                except Exception as e:
                    logging.warning(f"DexScreener resolution error for {symbol}: {e}")

                # 2. CoinGecko Search API Fallback (if DexScreener did not resolve)
                if not resolved:
                    cg_url = f"https://api.coingecko.com/api/v3/search?query={symbol}"
                    try:
                        r = requests.get(cg_url, timeout=10)
                        if r.status_code == 200:
                            coins = r.json().get("coins", []) or []
                            logged_resolutions = set()
                            for c in coins:
                                if c.get("symbol", "").upper() == symbol.upper():
                                    cg_id = c.get("id")
                                    detail_url = f"https://api.coingecko.com/api/v3/coins/{cg_id}"
                                    r_det = requests.get(detail_url, timeout=10)
                                    if r_det.status_code == 200:
                                        platforms = r_det.json().get("platforms", {}) or {}
                                        for plat, addr in platforms.items():
                                            if not addr:
                                                continue
                                            db_chain_name = CHAIN_NAME_MAP.get(plat.lower()) or plat.title()
                                            chain_id = chain_db_map.get(db_chain_name.lower())
                                            if chain_id and addr.lower() not in logged_resolutions:
                                                is_inserted = self.upsert_contract(
                                                    cur, coin_id, chain_id, addr,
                                                    decimals=coin_decimals or 18,
                                                    is_native=False,
                                                    source='coingecko'
                                                )
                                                logged_resolutions.add(addr.lower())
                                                if is_inserted:
                                                    logging.info(f"Resolved contract for {symbol} on {db_chain_name} via CoinGecko: {addr}")
                                                    resolved = True
                    except Exception as e:
                        logging.warning(f"CoinGecko resolution error for {symbol}: {e}")

                if resolved:
                    resolved_count += 1

            conn.commit()
            logging.info(f"Successfully resolved contract addresses for {resolved_count} missing coins.")
            return resolved_count
