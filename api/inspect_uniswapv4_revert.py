import urllib.request
import re

url = 'https://revert.finance/js/app.C4601414F411E7E6B2B28017135F16A1.js'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    js = response.read().decode('utf-8')

# Search for matches of "uniswapv4" case-insensitively and print 1000 characters around each
matches = list(re.finditer(r'uniswapv4', js, re.IGNORECASE))
print(f"Found {len(matches)} matches")
for i, match in enumerate(matches):
    print(f"\n--- MATCH {i+1} ---")
    start = max(0, match.start() - 500)
    end = min(len(js), match.end() + 500)
    print(js[start:end])
    print("="*60)
