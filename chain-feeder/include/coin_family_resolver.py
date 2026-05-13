import yaml
import re
import psycopg2
from typing import List, Dict, Set
import os

class CoinFamilyResolver:
    def __init__(self, config_path: str, db_config: str):
        self.config_path = config_path
        self.db_config = db_config
        self.families = self._load_config()

    def _load_config(self) -> List[Dict]:
        if not os.path.exists(self.config_path):
            return []
        with open(self.config_path, 'r') as f:
            data = yaml.safe_load(f)
            return data.get('coin-families', [])

    def get_family_by_name(self, name: str) -> Dict:
        for f in self.families:
            if f.get('name') == name:
                return f
        return None

    def resolve_family(self, family_name: str) -> Set[str]:
        family = self.get_family_by_name(family_name)
        if not family:
            return set()

        symbols = set()
        
        # 1. Hardcoded list
        if 'coin-list' in family:
            symbols.update(family['coin-list'])
        
        # 2. Include coins
        if 'include-coin' in family:
            symbols.update(family['include-coin'])

        # 3. SQL Rule & Regexp Rule require DB/List access
        has_db_rules = 'sql-rule' in family or 'regexp-rule' in family
        if has_db_rules:
            try:
                conn = psycopg2.connect(self.db_config)
                cur = conn.cursor()
                
                try:
                    # SQL Rule
                    if 'sql-rule' in family:
                        sql = f"SELECT symbol FROM coin WHERE {family['sql-rule']}"
                        cur.execute(sql)
                        rows = cur.fetchall()
                        symbols.update([r[0] for r in rows])

                    # Regexp Rule (we need all coins to match against)
                    if 'regexp-rule' in family:
                        cur.execute("SELECT symbol FROM coin")
                        all_symbols = [r[0] for r in cur.fetchall()]
                        pattern = re.compile(family['regexp-rule'], re.IGNORECASE)
                        matched = [s for s in all_symbols if s and pattern.match(s)]
                        symbols.update(matched)
                        
                finally:
                    cur.close()
                    conn.close()
            except Exception as e:
                # DB unavailable — fall back to coin-list/include-coin only
                import logging
                logging.getLogger(__name__).warning(
                    f"DB unavailable for family '{family_name}' SQL/regexp rules: {e}. "
                    f"Using coin-list only ({len(symbols)} tokens)."
                )

        return {s.upper() for s in symbols if s}

    def resolve_target_symbols(self, targets: List[str]) -> List[str]:
        """
        Resolves a list of targets which can be:
        - "FamilyName.*" -> All coins in family
        - "COIN" -> Specific coin
        """
        resolved_symbols = set()
        
        for target in targets:
            target = target.strip()
            if target.endswith('.*'):
                family_name = target[:-2]
                resolved_symbols.update(self.resolve_family(family_name))
            else:
                resolved_symbols.add(target.upper())
        
        return sorted(list(resolved_symbols))

    def sync_to_db(self):
        """
        Syncs the dynamic families to the coin_family table.
        Only inserts symbols that exist in the coin table (FK constraint).
        """
        import logging
        logger = logging.getLogger(__name__)

        conn = psycopg2.connect(self.db_config)
        cur = conn.cursor()
        try:
            # Fetch all known symbols from the coin table
            cur.execute("SELECT UPPER(symbol) FROM coin")
            known_symbols = {r[0] for r in cur.fetchall()}
            logger.info(f"Known coins in DB: {len(known_symbols)}")

            # Clear existing mappings
            cur.execute("DELETE FROM coin_family")
            
            total_inserted = 0
            total_skipped = 0
            for family in self.families:
                name = family.get('name')
                symbols = self.resolve_family(name)
                for symbol in symbols:
                    if symbol.upper() in known_symbols:
                        cur.execute(
                            "INSERT INTO coin_family (name, symbol) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (name, symbol)
                        )
                        total_inserted += 1
                    else:
                        total_skipped += 1
                        logger.debug(f"Skipping {symbol} (not in coin table)")

            conn.commit()
            logger.info(f"Synced {total_inserted} family mappings ({total_skipped} skipped — not in coin table)")
        finally:
            cur.close()
            conn.close()

# For Airflow Task convenience
def resolve_coins(targets: List[str], config_path: str, db_config: str) -> List[str]:
    resolver = CoinFamilyResolver(config_path, db_config)
    return resolver.resolve_target_symbols(targets)
