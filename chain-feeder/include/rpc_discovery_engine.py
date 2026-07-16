import logging
import time
import os
import requests
from datetime import datetime, timezone
from airflow.models import Variable
from airflow.providers.postgres.hooks.postgres import PostgresHook

logger = logging.getLogger(__name__)

# Constants
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

CONTRACTS = {
    "Ethereum": {
        "V3_POS_MGR": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "V4_POS_MGR": "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e", 
        "RPC": "https://eth.llamarpc.com"
    },
    "Arbitrum": {
        "V3_POS_MGR": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "V4_POS_MGR": "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        "RPC": "https://arb1.arbitrum.io/rpc"
    },
    "Base": {
        "V3_POS_MGR": "0x03a520b32C04BF3bEEf7BEb72E919cf822EdC299",
        "V4_POS_MGR": "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        "RPC": "https://mainnet.base.org"
    }
}

# Signatures
SIG_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SIG_V3_POSITIONS = "0x99fbab88" # positions(uint256)
SIG_V4_POS_INFO = "0x7ba03aad" # getPoolAndPositionInfo(uint256)

class RpcClient:
    """
    Handles RPC communications, rotation, retries, and logging
    """
    def __init__(self, network, config):
        self.network = network
        self.config = config
        self.rpc_urls = self._load_rpc_urls(network, config)
        
    def _load_rpc_urls(self, network, config):
        """Resolves RPC URLs from Envrionment with fallback"""
        env_key = f"RPC_URL_{network.upper()}"
        env_urls = os.getenv(env_key)
        
        urls = []
        if env_urls:
            urls = [u.strip() for u in env_urls.split(',') if u.strip()]
            
        default_rpc = config.get("RPC")
        if default_rpc and default_rpc not in urls:
            urls.append(default_rpc)
            
        return urls

    def _get_rpc_for_attempt(self, attempt):
        if not self.rpc_urls: return None
        return self.rpc_urls[attempt % len(self.rpc_urls)]
    
    def _mask_url(self, url):
        if not url: return "None"
        if "@" in url: return url.split("@")[-1]
        if "api" in url or "key" in url or len(url) > 40:
             parts = url.split('/')
             return parts[0] + "//.../" + parts[-1][:4] + "***"
        return url

    def call_rpc(self, method, params, retries=5, backoff=2):
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        
        if retries < len(self.rpc_urls):
            retries = len(self.rpc_urls) * 2 
            
        for attempt in range(retries):
            rpc_endpoint = self._get_rpc_for_attempt(attempt)
            masked_t = self._mask_url(rpc_endpoint)
            
            try:
                resp = requests.post(rpc_endpoint, json=payload, timeout=30)
                if resp.status_code == 429:
                    logger.warning(f"Rate limited (429) on {masked_t} for {method}. Retrying in {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    err_code = data['error'].get('code')
                    if err_code == -32005: 
                         logger.warning(f"RPC Limit Exceeded (-32005) on {masked_t} for {method}. Retrying...")
                         time.sleep(backoff)
                         backoff *= 2
                         continue
                    logger.error(f"RPC Error ({method}) via {masked_t}: {data['error']}")
                    return None
                
                # Log Success (Truncated)
                p_str = str(params)
                if len(p_str) > 100: p_str = p_str[:100] + "..."
                logger.info(f"RPC OK: {method} ({p_str}) via {masked_t}")
                return data.get("result")
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in [429, 500, 502, 503, 504]:
                    logger.warning(f"RPC HTTP {e.response.status_code} on {method} via {masked_t}. Retrying...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    logger.error(f"RPC HTTP Error ({method}) via {masked_t}: {e}")
                    return None
            except Exception as e:
                logger.error(f"RPC Fail ({method}) via {masked_t}: {e}. Retrying...")
                time.sleep(backoff)
                backoff *= 2
        return None

    def get_log_safe_providers(self):
        return [self._mask_url(u) for u in self.rpc_urls]


class DbManager:
    """
    Handles all Database operations
    """
    def __init__(self, network):
        self.network = network
        self.pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')

    def register_positions(self, found_set):
        if not found_set: return
        sql = """
        INSERT INTO liquidity_pool_position (position_key, wallet_address, token_id, pool_id, created_at)
        VALUES (%s, %s, %s, NULL, NOW())
        ON CONFLICT (position_key) DO NOTHING;
        """
        for (tid, proto, mgr, owner) in found_set:
            key = f"{proto.replace(' ', '').lower()}-{self.network}-{tid}"
            try:
                self.pg_hook.run(sql, parameters=(key, owner, str(tid)))
                logger.info(f"Registered {key} for {owner}")
            except Exception as e:
                logger.error(f"DB Error {key}: {e}")

    def fetch_enrichment_candidates(self, wallets):
        if not wallets: return None
        placeholders = ','.join(['%s'] * len(wallets))
        query = f"""
            SELECT id, token_id, position_key FROM liquidity_pool_position 
            WHERE wallet_address IN ({placeholders}) AND position_key LIKE %s
        """
        params = list(wallets) + [f"%{self.network}%"]
        return self.pg_hook.get_records(query, tuple(params))

    def update_enriched_position(self, pos_id, proto, c0_addr, c1_addr, fee, tl, tu, liq):
        conn = self.pg_hook.get_conn()
        cursor = conn.cursor()
        try:
            c0_sym = self._resolve_symbol(c0_addr, cursor)
            c1_sym = self._resolve_symbol(c1_addr, cursor)
            
            cursor.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c0_sym,))
            c0_id = cursor.fetchone()[0]
            cursor.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c1_sym,))
            c1_id = cursor.fetchone()[0]
            
            cursor.execute("SELECT id FROM chain WHERE LOWER(name) = LOWER(%s)", (self.network,))
            chain_id = cursor.fetchone()[0]
            cursor.execute("SELECT id FROM protocol WHERE LOWER(name) = LOWER(%s)", (proto,))
            proto_id = cursor.fetchone()[0]

            # Calculate fee_bps
            fee_bps = None
            if fee and str(fee).lower() != 'dynamic':
                fee_str = str(fee).strip()
                if '%' in fee_str:
                    try:
                        fee_bps = float(fee_str.replace('%', '')) * 100.0
                    except: pass
                elif fee_str.isdigit():
                    try:
                        fee_bps = float(fee_str) / 100.0
                    except: pass

            pool_name = f"{c0_sym} - {c1_sym}"
            pool_addr = f"rpc-derived-{c0_sym}-{c1_sym}"
            
            insert_pool = """
            INSERT INTO liquidity_pool (chain_id, protocol_id, pool_name, fee_bps, coin0_id, coin1_id, pool_address, reverted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chain_id, protocol_id, pool_name, fee_bps, (COALESCE(pool_id, ''))) DO UPDATE SET pool_address = EXCLUDED.pool_address
            RETURNING id;
            """
            cursor.execute(insert_pool, (chain_id, proto_id, pool_name, fee_bps, c0_id, c1_id, pool_addr, False))
            pid = cursor.fetchone()[0]
            
            update_pos = """
            UPDATE liquidity_pool_position 
            SET pool_id = %s, tick_lower = %s, tick_upper = %s
            WHERE id = %s
            """
            cursor.execute(update_pos, (pid, tl, tu, pos_id))
            conn.commit()
        except Exception as e:
            logger.error(f"DB Update failed: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    def _resolve_symbol(self, addr, cursor):
        cursor.execute("SELECT symbol FROM coin WHERE ethereum_address = %s", (addr.lower(),))
        row = cursor.fetchone()
        if row: return row[0]
        
        raw_sym = f"UNK-{addr.lower()[:6]}"
        cursor.execute("SELECT symbol FROM coin WHERE symbol = %s", (raw_sym,))
        if cursor.fetchone(): return raw_sym
        
        try:
            cursor.execute("SAVEPOINT insert_coin")
            cursor.execute("""
                INSERT INTO coin (symbol, name, ethereum_address) 
                VALUES (%s, %s, %s)
            """, (raw_sym, f"Unknown Token {addr}", addr.lower()))
            cursor.execute("RELEASE SAVEPOINT insert_coin")
            return raw_sym
        except Exception:
             cursor.execute("ROLLBACK TO SAVEPOINT insert_coin")
             return raw_sym

    def ensure_coins(self, details_map):
        conn = self.pg_hook.get_conn()
        cursor = conn.cursor()
        try:
            seen = set()
            for d in details_map.values():
                if d['token0'] not in seen:
                    self._resolve_symbol(d['token0'], cursor)
                    seen.add(d['token0'])
                if d['token1'] not in seen:
                    self._resolve_symbol(d['token1'], cursor)
                    seen.add(d['token1'])
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def ensure_pools(self, details_map):
        conn = self.pg_hook.get_conn()
        cursor = conn.cursor()
        try:
            for tid, d in details_map.items():
                 c0_sym = self._resolve_symbol(d['token0'], cursor)
                 c1_sym = self._resolve_symbol(d['token1'], cursor)
                 
                 cursor.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c0_sym,))
                 c0_id = cursor.fetchone()[0]
                 cursor.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c1_sym,))
                 c1_id = cursor.fetchone()[0]
                 
                 cursor.execute("SELECT id FROM chain WHERE LOWER(name) = LOWER(%s)", (self.network,))
                 chain_id = cursor.fetchone()[0]
                 
                 proto = d.get('protocol', 'Uniswap V3')
                 cursor.execute("SELECT id FROM protocol WHERE LOWER(name) = LOWER(%s)", (proto,))
                 proto_id = cursor.fetchone()[0]

                 # Calculate fee_bps
                 fee = d['fee']
                 fee_bps = None
                 if fee and str(fee).lower() != 'dynamic':
                     fee_str = str(fee).strip()
                     if '%' in fee_str:
                         try:
                             fee_bps = float(fee_str.replace('%', '')) * 100.0
                         except: pass
                     elif fee_str.isdigit():
                         try:
                             fee_bps = float(fee_str) / 100.0
                         except: pass

                 pool_name = f"{c0_sym} - {c1_sym}"
                 pool_addr = f"rpc-derived-{c0_sym}-{c1_sym}"
                 
                 sql = """
                 INSERT INTO liquidity_pool (chain_id, protocol_id, pool_name, fee_bps, coin0_id, coin1_id, pool_address, reverted)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (chain_id, protocol_id, pool_name, fee_bps, (COALESCE(pool_id, ''))) DO UPDATE SET pool_address = EXCLUDED.pool_address
                 RETURNING id;
                 """
                 cursor.execute(sql, (chain_id, proto_id, pool_name, fee_bps, c0_id, c1_id, pool_addr, False))
                 pid = cursor.fetchone()[0]
                 d['pool_id'] = pid 
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def upsert_positions(self, details_map, wallet_map):
        conn = self.pg_hook.get_conn()
        cursor = conn.cursor()
        try:
            sql = """
            INSERT INTO liquidity_pool_position (position_key, wallet_address, token_id, pool_id, tick_lower, tick_upper, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (position_key) DO UPDATE SET 
                pool_id = EXCLUDED.pool_id, 
                tick_lower = EXCLUDED.tick_lower,
                tick_upper = EXCLUDED.tick_upper;
            """
            for tid, d in details_map.items():
                if 'pool_id' not in d: continue
                owner = wallet_map.get(tid, '0x0000000000000000000000000000000000000000')
                proto = d.get('protocol', 'Uniswap V3')
                key = f"{proto.replace(' ', '').lower()}-{self.network}-{tid}"
                
                cursor.execute(sql, (key, owner, str(tid), d['pool_id'], d['tick_lower'], d['tick_upper']))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def insert_events(self, events):
        conn = self.pg_hook.get_conn()
        cursor = conn.cursor()
        try:
            sql = """
            INSERT INTO liquidity_pool_position_event 
            (position_id, tx_hash, block_number, timestamp, event_type, created_at)
            VALUES ((SELECT id FROM liquidity_pool_position WHERE position_key = %s), %s, %s, NOW(), %s, NOW())
            ON CONFLICT (position_id, tx_hash, event_type) DO NOTHING
            """
            for e in events:
                proto = e['protocol']
                tid = e['token_id']
                key = f"{proto.replace(' ', '').lower()}-{self.network}-{tid}"
                evt_type = "TRANSFER_IN" if e['direction'] == 'IN' else "TRANSFER_OUT"
                
                cursor.execute(sql, (key, e['tx_hash'], e['block_number'], evt_type))
            conn.commit()
        finally:
            cursor.close()
            conn.close()


class RpcDiscoveryEngine:
    def __init__(self, network, wallet_addresses_str, batch_size=None):
        self.network = network
        self.wallets = [w.strip().lower() for w in wallet_addresses_str.split(',') if w.strip()]
        self.config = CONTRACTS.get(network)
        
        if not self.config:
            raise ValueError(f"Network {network} not supported")
        
        env_batch = os.getenv("RPC_LOG_BATCH_SIZE")
        default_batch = int(env_batch) if env_batch else 2000
        self.log_batch_size = int(batch_size) if batch_size else default_batch
        
        self.rpc = RpcClient(network, self.config)
        self.db = DbManager(network)

    def get_current_block(self):
        res = self.rpc.call_rpc("eth_blockNumber", [])
        return int(res, 16) if res else 0

    def discover_positions(self, force_start_date=None):
        current_block = self.get_current_block()
        if current_block == 0: return

        var_key = f"rpc_discovery_last_block_{self.network}"
        last_block = self._resolve_start_block(var_key, current_block, force_start_date)
        
        if last_block >= current_block:
            logger.info(f"Using cached block {last_block}. No new blocks to scan.")
            return

        self._log_scan_scope(last_block, current_block)
        
        contracts = [self.config["V3_POS_MGR"]]
        if "V4_POS_MGR" in self.config:
             contracts.append(self.config["V4_POS_MGR"])
             
        wallets_padded = ["0x" + w[2:].zfill(64) for w in self.wallets]
        found_tokens = set()
        
        batch = self.log_batch_size
        total_blocks = current_block - last_block
        processed_blocks = 0
        
        logger.info(f"Using Scan Batch Size: {batch}")
        
        for start in range(last_block + 1, current_block + 1, batch):
            end = min(start + batch - 1, current_block)
            
            processed_blocks += (end - start + 1)
            pct = round((processed_blocks / total_blocks) * 100, 1)
            prog_str = f"Progress: {processed_blocks}/{total_blocks} ({pct}%)"
            
            self._scan_range(start, end, contracts, [SIG_TRANSFER, None, wallets_padded], found_tokens, prog_str)
            self._scan_range(start, end, contracts, [SIG_TRANSFER, wallets_padded, None], found_tokens, prog_str)
            
        self.db.register_positions(found_tokens)
        Variable.set(var_key, str(current_block))
        logger.info(f"Discovery complete. New head: {current_block}")
        
    def _resolve_start_block(self, var_key, current_block, force_date):
        default_start = current_block - 100000
        start_date_str = force_date or os.getenv("RPC_DISCOVERY_START_DATE")
        last_block = default_start

        try:
            existing_val = Variable.get(var_key, default_var=None)
            if not force_date and existing_val:
                return int(existing_val)
                
            if start_date_str:
                dt = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())
                slug = self.network.lower()
                res = requests.get(f"https://coins.llama.fi/block/{slug}/{ts}", timeout=10)
                if res.status_code == 200:
                    last_block = res.json().get("height", last_block)
            
            logger.info(f"Start Block Resolved: {last_block} (Date: {start_date_str})")
            return last_block
        except Exception as e:
            logger.error(f"Error resolving start block: {e}")
            return default_start

    def _log_scan_scope(self, last_block, current_block):
        try:
             b_start = self.rpc.call_rpc("eth_getBlockByNumber", [hex(last_block), False])
             b_end = self.rpc.call_rpc("eth_getBlockByNumber", [hex(current_block), False])
             
             d_start, d_end = "Unknown", "Unknown"
             if b_start: 
                 ts = int(b_start.get("timestamp", "0"), 16)
                 d_start = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
             if b_end:
                 ts = int(b_end.get("timestamp", "0"), 16)
                 d_end = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
             
             logger.info(f"Using Providers: {self.rpc.get_log_safe_providers()}")
             logger.info(f"Scanning {self.network} | From: {last_block} ({d_start}) -> To: {current_block} ({d_end}) | Wallets: {len(self.wallets)}")
        except Exception:
             pass

    def _scan_range(self, start, end, addresses, topics, found_set, progress_info=""):
        addr_param = addresses if len(addresses) > 1 else addresses[0]
        params = [{
            "fromBlock": hex(start), "toBlock": hex(end),
            "address": addr_param, "topics": topics
        }]
        
        logger.info(f"{progress_info} Scanning Block Range {start}-{end}")
        logs = self.rpc.call_rpc("eth_getLogs", params)
        if not logs: return
        
        for log in logs:
            if len(log["topics"]) > 3:
                token_id = int(log["topics"][3], 16)
                mgr = log["address"].lower()
                proto = "Uniswap V3" if mgr == self.config["V3_POS_MGR"].lower() else "Uniswap V4"
                
                to_addr = "0x" + log["topics"][2][26:]
                if to_addr.lower() in self.wallets:
                    found_set.add((token_id, proto, mgr, to_addr.lower()))

    def enrich_positions(self):
        if not self.wallets: return
        rows = self.db.fetch_enrichment_candidates(self.wallets)
        if not rows: return
        
        v3_ids = [r for r in rows if "uniswapv3" in r[2]]
        v4_ids = [r for r in rows if "uniswapv4" in r[2]]
        
        if v3_ids: self._batch_enrich_v3(v3_ids)
        if v4_ids: self._batch_enrich_v4(v4_ids)

    def _batch_enrich_v3(self, rows):
        calls = []
        for r in rows:
            tid = int(r[1])
            calldata = SIG_V3_POSITIONS + format(tid, '064x')
            calls.append({"target": self.config["V3_POS_MGR"], "callData": calldata})
            
        results = self._multicall(calls)
        for idx, res in enumerate(results):
            if not res['success'] or res['returnData'] == '0x': continue
            
            b = bytes.fromhex(res['returnData'][2:])
            if len(b) < 32 * 8: continue
            
            def w(i): return int.from_bytes(b[i*32:(i+1)*32], byteorder='big')
            token0 = "0x" + b[2*32+12:3*32].hex()
            token1 = "0x" + b[3*32+12:4*32].hex()
            fee = w(4)
            tl = self._to_signed(w(5))
            tu = self._to_signed(w(6))
            liq = w(7)
            
            self.db.update_enriched_position(rows[idx][0], "Uniswap V3", token0, token1, fee, tl, tu, liq)

    def _batch_enrich_v4(self, rows):
        calls = []
        for r in rows:
            tid = int(r[1])
            calldata = SIG_V4_POS_INFO + format(tid, '064x')
            calls.append({"target": self.config["V4_POS_MGR"], "callData": calldata})
            
        results = self._multicall(calls)
        for idx, res in enumerate(results):
             if not res['success'] or res['returnData'] == '0x': continue
             
             b = bytes.fromhex(res['returnData'][2:])
             def w(i): return int.from_bytes(b[i*32:(i+1)*32], byteorder='big')
             c0 = "0x" + b[0*32+12:1*32].hex()
             c1 = "0x" + b[1*32+12:2*32].hex()
             fee = w(2)
             packed = w(5)
             
             def parse_int24(val): return val - 0x1000000 if val & 0x800000 else val
             tl = parse_int24((packed >> 8) & 0xFFFFFF)
             tu = parse_int24((packed >> 32) & 0xFFFFFF)
             
             self.db.update_enriched_position(rows[idx][0], "Uniswap V4", c0, c1, fee, tl, tu, 0)

    def _multicall(self, calls):
        batch_payload = []
        for i, call in enumerate(calls):
            batch_payload.append({
                "jsonrpc": "2.0", "method": "eth_call",
                "params": [{"to": call["target"], "data": call["callData"]}, "latest"],
                "id": i
            })
        try:
            resp = requests.post(self.config["RPC"], json=batch_payload, timeout=30)
            return [{"success": True, "returnData": r.get("result", "0x")} for r in resp.json()]
        except:
             return [{"success": False} for _ in calls]

    def _to_signed(self, val):
        return val - 2**256 if val > 2**255 else val

    # --- V2 Methods ---

    def scan_transfer_events(self, start_block, end_block):
        # Convert wallets to padded 32-byte topics (lowercase to ensure matching)
        wallets_padded = ["0x" + w[2:].lower().zfill(64) for w in self.wallets]
        
        contracts = [self.config["V3_POS_MGR"]]
        if "V4_POS_MGR" in self.config:
             contracts.append(self.config["V4_POS_MGR"])
             
        found_events = []
        
        def _fetch(topics, direction):
            params = [{
                "fromBlock": hex(start_block), "toBlock": hex(end_block),
                "address": contracts, "topics": topics
            }]
            logs = self.rpc.call_rpc("eth_getLogs", params) or []
            for log in logs:
                if len(log["topics"]) > 3:
                     tid = int(log["topics"][3], 16)
                     mgr = log["address"].lower()
                     proto = "Uniswap V3" if mgr == self.config["V3_POS_MGR"].lower() else "Uniswap V4"
                     
                     found_events.append({
                         "tx_hash": log["transactionHash"],
                         "log_index": int(log["logIndex"], 16),
                         "block_number": int(log["blockNumber"], 16),
                         "direction": direction,
                         "token_id": tid,
                         "protocol": proto,
                         "manager": mgr,
                         "from": "0x" + log["topics"][1][26:],
                         "to": "0x" + log["topics"][2][26:]
                     })

        _fetch([SIG_TRANSFER, None, wallets_padded], "IN")
        _fetch([SIG_TRANSFER, wallets_padded, None], "OUT")
        return found_events

    def fetch_onchain_details(self, token_ids):
        if not token_ids: return {}
        tids = list(set(token_ids))
        results = {}
        
        calls_v3 = []
        for tid in tids:
             calldata = SIG_V3_POSITIONS + format(tid, '064x')
             calls_v3.append({"target": self.config["V3_POS_MGR"], "callData": calldata})
        
        # Process in chunks of 50 to avoid RPC limitations
        chunk_size = 50
        for i in range(0, len(calls_v3), chunk_size):
            batch = calls_v3[i:i+chunk_size]
            res_v3 = self._multicall(batch)
            
            for j, r in enumerate(res_v3):
                if r['success'] and r['returnData'] != '0x':
                    # Calculate true index in tids
                    tid = tids[i + j]
                    b = bytes.fromhex(r['returnData'][2:])
                    if len(b) >= 32*8:
                        def w(k): return int.from_bytes(b[k*32:(k+1)*32], byteorder='big')
                        token0 = "0x" + b[2*32+12:3*32].hex()
                        token1 = "0x" + b[3*32+12:4*32].hex()
                        fee = w(4)
                        tl = self._to_signed(w(5))
                        tu = self._to_signed(w(6))
                        liq = w(7)
                        
                        results[tid] = {
                            "token0": token0, "token1": token1, "fee": fee,
                            "tick_lower": tl, "tick_upper": tu, 
                            "liquidity": str(liq), # Convert to string to avoid XCom overflow
                            "protocol": "Uniswap V3" 
                        }
        return results
