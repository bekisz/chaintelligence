import re

with open('api/main.py', 'r') as f:
    content = f.read()

# Fix 1: token_addresses scoping and fetching
old_block1 = """            token_addresses = {}
            for target_network in ["Ethereum", "Arbitrum", "BNB", "Base"]:
                net_map = NETWORK_TOKEN_MAPS.get(target_network, {})
                for sym in token_symbols:
                    if sym in net_map:
                        token_addresses[sym] = net_map[sym]
                        
                # Database lookup only for any remaining missing symbols (e.g. custom test tokens)
                missing_symbols = [sym for sym in token_symbols if sym not in token_addresses]
                if missing_symbols:
                    try:
                        import psycopg2
                        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
                        cur = conn.cursor()
                        
                        # 1. Fetch addresses from V3 swaps table for this specific network
                        cur.execute(\"\"\"
                            SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                            UNION
                            SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                        \"\"\", (target_network, missing_symbols, target_network, missing_symbols))
                        for row in cur.fetchall():
                            if row[1]:
                                token_addresses[row[0]] = row[1]
                                
                        # 2. Fetch from V4 swaps table for any missing symbols
                        missing_tokens_v4 = [sym for sym in missing_symbols if sym not in token_addresses]
                        if missing_tokens_v4:
                            cur.execute(\"\"\"
                                SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                                UNION
                                SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                            \"\"\", (target_network, missing_tokens_v4, target_network, missing_tokens_v4))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[row[0]] = row[1]
                                    
                        # 3. Fallback to general coin table for any remaining ones
                        still_missing = [sym for sym in missing_symbols if sym not in token_addresses]
                        if still_missing:
                            cur.execute("SELECT UPPER(symbol), ethereum_address FROM coin WHERE UPPER(symbol) = ANY(%s)", (still_missing,))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[row[0]] = row[1]
                                    
                        cur.close()
                        conn.close()
                    except Exception as e:
                        print(f"Error fetching token addresses for network {network}: {e}")"""

new_block1 = """            token_addresses = {}
            for target_network in ["Ethereum", "Arbitrum", "BNB", "Base"]:
                token_addresses[target_network] = {}
                net_map = NETWORK_TOKEN_MAPS.get(target_network, {})
                for sym in token_symbols:
                    if sym in net_map:
                        token_addresses[target_network][sym] = net_map[sym]
                        
                # Database lookup only for any remaining missing symbols (e.g. custom test tokens)
                missing_symbols = [sym for sym in token_symbols if sym not in token_addresses[target_network]]
                if missing_symbols:
                    try:
                        import psycopg2
                        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
                        cur = conn.cursor()
                        
                        # 1. Fetch addresses from V3 swaps table for this specific network
                        cur.execute(\"\"\"
                            SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                            UNION
                            SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v3_swaps
                            WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                        \"\"\", (target_network, missing_symbols, target_network, missing_symbols))
                        for row in cur.fetchall():
                            if row[1]:
                                token_addresses[target_network][row[0]] = row[1]
                                
                        # 2. Fetch from V4 swaps table for any missing symbols
                        missing_tokens_v4 = [sym for sym in missing_symbols if sym not in token_addresses[target_network]]
                        if missing_tokens_v4:
                            cur.execute(\"\"\"
                                SELECT DISTINCT UPPER(token0_symbol), token0_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token0_symbol) = ANY(%s)
                                UNION
                                SELECT DISTINCT UPPER(token1_symbol), token1_address FROM uniswap_v4_swaps
                                WHERE network = %s AND UPPER(token1_symbol) = ANY(%s)
                            \"\"\", (target_network, missing_tokens_v4, target_network, missing_tokens_v4))
                            for row in cur.fetchall():
                                if row[1]:
                                    token_addresses[target_network][row[0]] = row[1]
                                    
                        # 3. Fallback to general coin table for any remaining ones (assumes Ethereum)
                        if target_network == "Ethereum":
                            still_missing = [sym for sym in missing_tokens_v4 if sym not in token_addresses[target_network]]
                            if still_missing:
                                cur.execute("SELECT UPPER(symbol), ethereum_address FROM coin WHERE UPPER(symbol) = ANY(%s)", (still_missing,))
                                for row in cur.fetchall():
                                    if row[1]:
                                        token_addresses[target_network][row[0]] = row[1]
                                        
                        cur.close()
                        conn.close()
                    except Exception as e:
                        print(f"Error fetching token addresses from DB: {e}")"""

content = content.replace(old_block1, new_block1)

# Fix 2: using parsed network to get token addresses
old_block2 = """            try:
                from Crypto.Hash import keccak
                for (t0, t1, fee) in pools_to_fetch:
                    t0_sym, t1_sym = t0.upper(), t1.upper()
                    addr0 = token_addresses.get(t0_sym)
                    addr1 = token_addresses.get(t1_sym)
                    if not addr0 or not addr1:
                        continue
                        
                    fee_raw = str(fee).split('|')[0].strip()
                    f_clean = fee_raw.replace('%', '').strip()
                    
                    network = "Ethereum"
                    parts = str(fee).split('|')
                    if len(parts) >= 3:
                        network = parts[2].strip()
                        
                    protocol = "Uniswap V3"
                    if len(parts) >= 2:
                        protocol = parts[1].strip()"""

new_block2 = """            try:
                from Crypto.Hash import keccak
                for (t0, t1, fee) in pools_to_fetch:
                    t0_sym, t1_sym = t0.upper(), t1.upper()
                    
                    network = "Ethereum"
                    protocol = "Uniswap V3"
                    parts = str(fee).split('|')
                    if len(parts) >= 3:
                        network = parts[2].strip()
                        protocol = parts[1].strip()
                    elif len(parts) == 2:
                        protocol = parts[1].strip()

                    addr0 = token_addresses.get(network, {}).get(t0_sym)
                    addr1 = token_addresses.get(network, {}).get(t1_sym)
                    if not addr0 or not addr1:
                        continue
                        
                    fee_raw = str(fee).split('|')[0].strip()
                    f_clean = fee_raw.replace('%', '').strip()"""

content = content.replace(old_block2, new_block2)

with open('api/main.py', 'w') as f:
    f.write(content)
