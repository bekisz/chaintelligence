import requests
import re

url = "https://revert.finance/js/app.C4601414F411E7E6B2B28017135F16A1.js"
print("Downloading JS...")
resp = requests.get(url, timeout=30)
js_content = resp.text
print(f"Downloaded {len(js_content)} bytes")

# Find any URLs matching subgraphs or API endpoints
urls = re.findall(r'https?://[^\s"\']+', js_content)
print(f"Found {len(urls)} URLs")

print("\n--- Subgraph / Graph / API URLs ---")
seen = set()
for u in urls:
    # filter for interesting domains
    if any(domain in u for domain in ['thegraph', 'revert', 'envio', 'api']):
        clean_url = u.split('?')[0].split('#')[0].rstrip('),;.:')
        if clean_url not in seen:
            seen.add(clean_url)
            print(clean_url)
            
# Search for API endpoints like "/uniswapv3" or "/pool/" or graphql queries
print("\n--- Searching for query strings ---")
queries = re.findall(r'"/api/[^"]+"|\'/api/[^\']+\'', js_content)
for q in queries[:20]:
    print(q)
