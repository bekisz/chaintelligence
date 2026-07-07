import re

with open('api/main.py', 'r') as f:
    content = f.read()

target = """            try:
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

replacement = """            try:
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

content = content.replace(target, replacement)

with open('api/main.py', 'w') as f:
    f.write(content)
