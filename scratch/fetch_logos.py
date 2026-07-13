import requests
import re

urls = {
    "Revert": "https://revert.finance/",
    "DexScreener": "https://dexscreener.com/",
    "DeFiLlama": "https://defillama.com/"
}

for name, url in urls.items():
    print(f"\n--- {name} ---")
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = r.text
        
        icons = re.findall(r'<link[^>]*rel="[^"]*icon[^"]*"[^>]*href="([^"]+)"', html, re.IGNORECASE)
        print("Favicons:", icons)
        
        svgs = re.findall(r'(<svg[^>]*>.*?</svg>)', html, re.IGNORECASE | re.DOTALL)
        for i, svg in enumerate(svgs[:5]):
            # print first 150 chars of SVG
            print(f"SVG {i}: {svg[:200]}...")
            if 'viewBox' in svg:
                # Let's save the first 3 SVGs
                with open(f"scratch/{name.lower()}_svg_{i}.svg", "w") as f:
                    f.write(svg)
            
    except Exception as e:
        print(f"Error: {e}")
