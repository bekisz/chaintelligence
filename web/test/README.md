# Routing UI Test

End-to-end Playwright test for the Route Analysis page. Calls the backend API directly for timing data, then drives the browser to verify the UI renders correctly.

## Files

| File | Purpose |
|---|---|
| `test_routing_ui_simple.py` | The test — pytest with asyncio + Playwright |
| `config.yaml` | Configuration (API URL, credentials, tokens, date range, networks) |
| `recordings/` | Video recordings (created when `record_video: true`) |

## Setup

```bash
# Install Python dependencies
pip3 install --break-system-packages pytest pytest-asyncio httpx playwright pyyaml

# Install Playwright Chromium browser
python3 -m playwright install chromium
```

## Configuration

Edit `config.yaml` in this directory to set test parameters:

```yaml
api_url: "http://localhost:8000"      # Chaintelligence server
portal_username: "admin"              # HTTP Basic Auth username
portal_password: "chaintelligence77"  # HTTP Basic Auth password
headless: true                        # true = no visible browser; false = watch it run
record_video: true                    # save .webm recording
token_in: "USDC"                      # Source token
token_out: "USDT"                     # Destination token
start_date: ""                        # empty → computed as 2 days ago
end_date: ""                          # empty → computed as today
networks:
  - ethereum
  - arbitrum
  - base
  - bsc
```

## Running the test

```bash
cd web/test
pytest -s test_routing_ui_simple.py
```

The `-s` flag shows live output (phase timings, route counts, pool-link verifications).

### Run API-only (no browser)

```bash
cd web/test
python3 -c "
import asyncio, httpx, json, yaml
from pathlib import Path

cfg = yaml.safe_load(Path('config.yaml').read_text())
cfg['end_date'] = '\$(date +%Y-%m-%d)'
cfg['start_date'] = '\$(date -d '2 days ago' +%Y-%m-%d)'

async def main():
    params = {'start_token': cfg['token_in'], 'end_token': cfg['token_out'],
              'start_date': cfg['start_date'], 'end_date': cfg['end_date'],
              'networks': ','.join(cfg['networks'])}
    auth = httpx.BasicAuth(cfg['portal_username'], cfg['portal_password'])
    async with httpx.AsyncClient(auth=auth, timeout=300.0) as client:
        async with client.stream('GET', f'{cfg[\"api_url\"]}/api/routes/analyze', params=params) as resp:
            print(f'Status: {resp.status_code}')
            async for line in resp.aiter_lines():
                if line.strip():
                    print(line[:200])
                    print()
asyncio.run(main())
"
```

## What the test does

1. **Calls the API** (`/api/routes/analyze`) via NDJSON streaming — captures progress events and the final result payload.
2. **Validates the result** — asserts at least 10 routes, checks Ethereum/Arbitrum/Base/BNB networks, and verifies Uniswap V2/V3/V4 + PancakeSwap V3 protocols appear.
3. **Drives the browser** — fills the form, clicks "Analyze", waits for the progress bar.
4. **Verifies pool external links** — for the highest-volume route per chain, clicks the pool link and checks the URL resolves.

## Test output example

```
API routes: 29 | networks: Arbitrum, Base, BNB, Ethereum | protocols: PancakeSwap V3, Uniswap V3, Uniswap V4
UI completed in 12.34s
  ✅ Ethereum: Uniswap V3 external link → https://app.uniswap.org/explore/pools/ethereum/0x3416cf6c...
  ✅ Arbitrum: Uniswap V3 external link → https://app.uniswap.org/explore/pools/arbitrum/0xbe3ad6...
  ✅ Base: Uniswap V3 external link → https://app.uniswap.org/explore/pools/base/0xd56da2b...
  ✅ BNB: Uniswap V3 external link → https://app.uniswap.org/explore/pools/bnb/0x2c3c320...
  ✅ PancakeSwap V3 (BNB): external link → https://pancakeswap.finance/info/v3/pairs/0x92b78...
  UI rendered 29 routes, API reported 29
  Video saved to ./recordings/2026-07-05_1430/test_route_analysis_ui.webm
```

## Known issues

- **"No Uniswap V2 pools found" assertion** — the `uniswap_v2_swaps` table is empty when no V2 DAG has populated it yet. If V2 data hasn't been loaded, comment out or remove the V2 assertion (line 380) in the test.
- **External site blocks** — Uniswap/PancakeSwap frontends may block headless browsers. The test navigates to the pool page and checks the URL; if blocked, the assertion may time out.

## Video Recordings

Videos are saved as WebM files in the `recordings/` directory, timestamped per run.

## Troubleshooting

| Issue | Solution |
|---|---|
| `ModuleNotFoundError: playwright` | `pip3 install --break-system-packages playwright && python3 -m playwright install chromium` |
| `TimeoutError` on page load | Check that the API server is running at `api_url` |
| `401 Unauthorized` | Verify credentials in `config.yaml` match your `.env.secrets` |
| No routes returned | Pick a date range that has swap data (use `curl` to check) |

## CI/CD Integration

```yaml
- name: Run UI Tests
  run: |
    cd web/test
    sed -i '' 's/headless: false/headless: true/' config.yaml
    pytest -s test_routing_ui_simple.py