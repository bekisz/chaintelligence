import urllib.request
import re

url = 'https://revert.finance/js/app.C4601414F411E7E6B2B28017135F16A1.js'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    js = response.read().decode('utf-8')

# Search for occurrences of uniswapv4, or the subgraph IDs we found, to see how they are referenced
# Let's print sections of code containing 'uniswapv4' or 'GZWD' or 'DiYP'
for word in ['uniswapv4', 'GZWDNw5b7XH2iqnmG91FLDDkfEVEDQotfPv4GMdraEKY', 'DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G']:
    print(f"--- MATCHES FOR: {word} ---")
    for match in re.finditer(re.escape(word), js):
        start = max(0, match.start() - 300)
        end = min(len(js), match.end() + 300)
        print(js[start:end])
        print("="*60)
