# UI Tests

This directory contains Playwright-based UI tests for the Chain Intelligence routing interface.

## Test File

- **`test_routing_ui_simple.py`** — Main test script that:
  1. Calls the `/api/routes/analyze` endpoint directly via HTTP streaming to capture detailed NDJSON progress timing
  2. Runs a Playwright browser test to verify the UI renders correctly and captures a video recording
  3. Compares API results with UI results

## Prerequisites

```bash
# Install Python dependencies
pip install playwright httpx python-dotenv

# Install Chromium for Playwright
playwright install chromium
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_URL` | `http://localhost:8000` | Base URL of the FastAPI server |
| `PORTAL_USERNAME` | `admin` | HTTP Basic Auth username |
| `PORTAL_PASSWORD` | `chaintelligence77` | HTTP Basic Auth password |
| `HEADLESS` | `true` | Run browser in headless mode (`false` for visible browser) |
| `RECORD_VIDEO` | `true` | Record video of test run |
| `VIDEO_DIR` | `./recordings` | Directory for video recordings |

## Running the Tests

### Basic Run (Headless, with Video Recording)

```bash
cd web/test
PORTAL_USERNAME=admin PORTAL_PASSWORD=chaintelligence77 python test_routing_ui_simple.py
```

### Headful Mode (Visible Browser)

```bash
HEADLESS=false PORTAL_USERNAME=admin PORTAL_PASSWORD=chaintelligence77 python test_routing_ui_simple.py
```

### Disable Video Recording

```bash
RECORD_VIDEO=false PORTAL_USERNAME=admin PORTAL_PASSWORD=chaintelligence77 python test_routing_ui_simple.py
```

### Custom API URL

```bash
API_URL=http://my-server:8000 PORTAL_USERNAME=admin PORTAL_PASSWORD=chaintelligence77 python test_routing_ui_simple.py
```

### Using `.env` File

Create a `.env` file in the `ui` directory:

```env
API_URL=http://localhost:8000
PORTAL_USERNAME=admin
PORTAL_PASSWORD=chaintelligence77
HEADLESS=true
RECORD_VIDEO=true
VIDEO_DIR=./recordings
```

Then simply run:
```bash
python test_routing_ui_simple.py
```

## Test Output

The test produces:
1. **API Timing Report** — Detailed breakdown of each streaming phase (fetch chunks, building routes, pool stats, pool addresses, formatting)
2. **UI Verification** — Confirms the frontend renders results correctly
3. **Video Recording** — WebM file in `./recordings/` (if enabled)

Example output:
```
========================================================================
  🎯  Chain Intelligence - Route Analysis UI Test
========================================================================
  API URL:        http://localhost:8000
  Token pair:     USDC → USDT
  Date range:     2026-07-02 to 2026-07-04
  Networks:       ethereum, arbitrum, optimism, base, polygon, bsc, avalanche, gnosis, fantom, linea
  Headless:       true
  Record video:   true

========================================================================
  🚀  Calling API endpoint directly (streaming NDJSON)
========================================================================

  ⏮️  Progress Event Timeline:
     🔄  +0.1s  Fetching 2026-07-02...
     🔄  +1.2s  Fetching 2026-07-03...
     🔄  +2.3s  Fetching 2026-07-04...
     🔨  +2.5s  Building route graph...
     📊  +5.1s  Loading pool stats & APRs...
     🏗️  +8.2s  Generating pool addresses...
     🎨  +8.5s  Formatting results...
     ✅  +8.7s  Complete

  📅 Fetch Chunk Detail (3 chunks):
     Chunk                    Duration     Cumulative    % of API
     -------------------------------------------------------------
     2026-07-03              1.10s        1.10s         12.6%
     2026-07-04              1.10s        2.20s         25.3%

  📊 Phase Timing Breakdown:
     Phase                                          Duration     % of Total
     --------------------------------------------------------
     Fetching 2026-07-02...                         0.10s        1.1%
     Fetching 2026-07-03...                         1.10s        12.6%
     Fetching 2026-07-04...                         1.10s        12.6%
     Building route graph...                        0.20s        2.3%
     Loading pool stats & APRs...                   3.10s        35.6%
     Generating pool addresses...                   0.30s        3.4%
     Formatting results...                          0.20s        2.3%
     Inter-phase overhead                           2.60s        29.9%
     --------------------------------------------------------
     Total API time                                 8.70s       100.0%

  📦 Result Summary:
     Routes:          42
     Total TXs:       15,234
     Total Volume:    $2,341,567.89

  🌐 Per-Network Route Count:
     ethereum        12 routes
     arbitrum        10 routes
     optimism         8 routes
     base             7 routes
     polygon          5 routes

========================================================================
  🎭  Running Playwright UI Test (Chromium)
========================================================================

  🌐  Navigating to http://localhost:8000/routing.html...
  📝  Filling form: USDC → USDT, 2026-07-02 to 2026-07-04
  ▶️  Clicking 'Analyze Routes'...
  ✅  UI completed in 12.45s

  📦 UI Result Summary:
     Routes rendered: 42
     API routes:      42

  🎥  Video recorded to: /path/to/recordings/video-123.webm

========================================================================
  ✅  All tests passed!
========================================================================
```

## Video Recordings

Videos are saved as WebM files in the `recordings/` directory with timestamps in the filename. Open them in any browser or media player.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: playwright` | Run `pip install playwright && playwright install chromium` |
| `TimeoutError` on page load | Increase timeout or check if API server is running |
| `401 Unauthorized` | Verify `PORTAL_USERNAME` and `PORTAL_PASSWORD` match your `.env.secrets` |
| No routes found | Check if data exists for the date range in the database |
| Video not saving | Ensure `RECORD_VIDEO=true` and `VIDEO_DIR` is writable |

## CI/CD Integration

For CI pipelines, run headless without video:

```yaml
- name: Run UI Tests
  run: |
    cd web/test
    RECORD_VIDEO=false python test_routing_ui_simple.py
  env:
    PORTAL_USERNAME: ${{ secrets.PORTAL_USERNAME }}
    PORTAL_PASSWORD: ${{ secrets.PORTAL_PASSWORD }}
    API_URL: http://localhost:8000
```

## Related

- Parent test docs: [../../chain-feeder/tests/test.md](../../chain-feeder/tests/test.md)
- API documentation: [/docs](/docs) (when server is running)
- Architecture docs: [../../docs/architecture.md](../../docs/architecture.md)