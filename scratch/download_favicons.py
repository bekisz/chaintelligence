import urllib.request
import os

assets_dir = "/Users/szabi/git/chaintelligence/web/static/assets"

favicons = {
    "explorer.ico": [
        "https://etherscan.io/images/favicon.ico",
        "https://etherscan.io/favicon.ico",
        "https://www.google.com/s2/favicons?domain=etherscan.io&sz=64"
    ],
    "geckoterminal.ico": [
        "https://www.geckoterminal.com/favicon.ico",
        "https://www.google.com/s2/favicons?domain=geckoterminal.com&sz=64"
    ],
    "dextools.ico": [
        "https://www.dextools.io/app/en/assets/icons/favicon.ico",
        "https://www.dextools.io/favicon.ico",
        "https://www.google.com/s2/favicons?domain=dextools.io&sz=64"
    ],
    "defined.ico": [
        "https://www.defined.fi/favicon.ico",
        "https://www.google.com/s2/favicons?domain=defined.fi&sz=64"
    ]
}

headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

for fname, urls in favicons.items():
    dest_path = os.path.join(assets_dir, fname)
    success = False
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                if len(data) > 100:
                    with open(dest_path, "wb") as f:
                        f.write(data)
                    print(f"Downloaded {fname} ({len(data)} bytes) from {url}")
                    success = True
                    break
        except Exception as e:
            print(f"Failed {url}: {e}")
    if not success:
        print(f"ERROR: Could not download {fname}")

print("\nDone checking/downloading favicons.")
