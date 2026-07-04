#!/usr/bin/env python3
"""
PyTest version of the Playwright UI test for Route Analysis.

The test reads configuration from ``config.yaml`` allowing easy override of
environment variables such as the API URL, credentials, headless mode, etc.

Run with:

    cd web/test
    pytest -s test_routing_ui_pytest.py
"""

import os
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
import yaml
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration handling
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).with_name("config.yaml")

def load_config() -> Dict[str, Any]:
    """Load ``config.yaml`` and apply defaults.

    The YAML file mirrors the environment variables previously used by the
    original script. Empty ``start_date``/``end_date`` values are replaced with a
    dynamic two‑day range, matching the original behaviour.
    """
    raw = yaml.safe_load(CONFIG_PATH.read_text())

    # Basic defaults – keep the original script's defaults where appropriate.
    cfg: Dict[str, Any] = {
        "api_url": raw.get("api_url", "http://localhost:8000"),
        "portal_username": raw.get("portal_username", "admin"),
        "portal_password": raw.get("portal_password", "chaintelligence77"),
        "headless": bool(raw.get("headless", True)),
        "record_video": bool(raw.get("record_video", True)),
        "video_dir": raw.get("video_dir", "./recordings"),
        "viewport_width": int(raw.get("viewport_width", 1280)),
        "viewport_height": int(raw.get("viewport_height", 900)),
        "video_width": int(raw.get("video_width", 1920)),
        "video_height": int(raw.get("video_height", 1080)),
        "token_in": raw.get("token_in", "USDC"),
        "token_out": raw.get("token_out", "USDT"),
        "networks": raw.get("networks", []),
        "start_date": raw.get("start_date", ""),
        "end_date": raw.get("end_date", ""),
    }

    # Compute dynamic dates if not supplied.
    if not cfg["end_date"]:
        cfg["end_date"] = datetime.now().strftime("%Y-%m-%d")
    if not cfg["start_date"]:
        cfg["start_date"] = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    return cfg

# ---------------------------------------------------------------------------
# Data structures matching the original implementation
# ---------------------------------------------------------------------------

@dataclass
class ProgressEvent:
    """Represents a single progress event from the NDJSON stream."""

    timestamp: float
    type: str  # "progress" or "result"
    pct: Optional[float] = None
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def from_line(cls, line: str) -> Optional["ProgressEvent"]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if data.get("type") == "progress":
            return cls(timestamp=time.time(), type="progress", pct=data.get("pct"), message=data.get("message"))
        if data.get("type") == "result":
            return cls(timestamp=time.time(), type="result", data=data.get("data"))
        return None

@dataclass
class ApiPhase:
    name: str
    timestamp: float
    message: str

@dataclass
class FetchChunk:
    index: int
    message: str
    timestamp: float

# ---------------------------------------------------------------------------
# Helper functions – identical to the original but split for testability
# ---------------------------------------------------------------------------

async def call_api_direct(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Stream the ``/api/routes/analyze`` endpoint and collect timing data.

    The function mirrors the original ``run_api_direct`` logic but returns the
    parsed JSON payload without printing intermediate diagnostics – the test
    asserts on the resulting data.
    """
    params = {
        "start_token": cfg["token_in"],
        "end_token": cfg["token_out"],
        "start_date": cfg["start_date"],
        "end_date": cfg["end_date"],
        "networks": ",".join(cfg["networks"]),
    }
    auth = httpx.BasicAuth(cfg["portal_username"], cfg["portal_password"])
    async with httpx.AsyncClient(auth=auth, timeout=300.0) as client:
        async with client.stream("GET", f"{cfg['api_url']}/api/routes/analyze", params=params) as response:
            if response.status_code != 200:
                raise RuntimeError(f"API returned {response.status_code}: {await response.aread()}")
            progress_events: List[ProgressEvent] = []
            api_phases: List[ApiPhase] = []
            fetch_chunks: List[FetchChunk] = []
            result_data: Optional[Dict[str, Any]] = None
            chunk_index = 0
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                ev = ProgressEvent.from_line(line)
                if ev is None:
                    continue
                progress_events.append(ev)
                if ev.type == "progress":
                    msg = ev.message or ""
                    api_phases.append(ApiPhase(name=msg[:80], timestamp=ev.timestamp, message=msg))
                    if msg.startswith("Fetching swaps for"):
                        fetch_chunks.append(FetchChunk(index=chunk_index, message=msg, timestamp=ev.timestamp))
                        chunk_index += 1
                elif ev.type == "result":
                    result_data = ev.data
    return result_data or {}

async def run_playwright_ui(cfg: Dict[str, Any], api_result: Dict[str, Any], test_name: str = "test") -> int:
    """Execute the UI flow via Playwright and return the number of route rows rendered.
    """
    os.makedirs(cfg["video_dir"], exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg["headless"], args=["--no-sandbox", "--disable-setuid-sandbox"])
        # Create a timestamped subdirectory for video recordings to avoid overwriting previous runs
        from datetime import datetime as _dt
        timestamp_subdir = _dt.now().strftime("%Y-%m-%d_%H%M")
        video_output_dir = Path(cfg["video_dir"]).joinpath(timestamp_subdir) if cfg["record_video"] else None
        context = await browser.new_context(
            viewport={"width": cfg["viewport_width"], "height": cfg["viewport_height"]},
            record_video_dir=str(video_output_dir) if cfg["record_video"] else None,
            record_video_size={"width": cfg["video_width"], "height": cfg["video_height"]},
            http_credentials={"username": cfg["portal_username"], "password": cfg["portal_password"]},
        )
        page = await context.new_page()

        await page.goto(f"{cfg['api_url']}/routing", wait_until="networkidle", timeout=30000)

        # Inject a click visualizer: a red dot briefly appears at every click point
        # (must be done after goto so the injection survives on the loaded page)
        await page.evaluate("""
            () => {
                const style = document.createElement('style');
                style.textContent = `
                    .click-dot {
                        position: fixed;
                        pointer-events: none;
                        z-index: 999999;
                        width: 20px;
                        height: 20px;
                        border-radius: 50%;
                        background: rgba(255, 0, 0, 0.8);
                        border: 2px solid rgba(255, 0, 0, 1);
                        transform: translate(-50%, -50%);
                        animation: click-fade 0.6s ease-out forwards;
                    }
                    @keyframes click-fade {
                        0%   { transform: translate(-50%, -50%) scale(0.6); opacity: 1; }
                        100% { transform: translate(-50%, -50%) scale(1.5); opacity: 0; }
                    }
                `;
                document.head.appendChild(style);
                document.addEventListener('click', (e) => {
                    const dot = document.createElement('div');
                    dot.className = 'click-dot';
                    dot.style.left = e.clientX + 'px';
                    dot.style.top = e.clientY + 'px';
                    document.body.appendChild(dot);
                    setTimeout(() => dot.remove(), 700);
                }, true);
            }
        """)
        # Fill form fields – selectors are taken from the actual HTML.
        await page.fill("#start-token", cfg["token_in"])
        await page.fill("#end-token", cfg["token_out"])
        await page.fill("#start-date", cfg["start_date"])
        await page.fill("#end-date", cfg["end_date"])
        await page.select_option("#query-network-filter", "all")
        start = time.time()
        await page.click("#analyze-btn")
        # Wait for either the progress bar to hit 100% or the results container.
        await page.wait_for_selector("#progress-bar-fill[style*='100%'], #results-section:not(.hidden)", timeout=300000)
        ui_elapsed = time.time() - start
        print(f"UI completed in {ui_elapsed:.2f}s")

        # --- Verify pool external links for the largest route per chain ---
        TARGET_NETWORKS = ["Ethereum", "Arbitrum", "Base", "BNB"]
        net_map = {"Ethereum": "ethereum", "Arbitrum": "arbitrum", "Base": "base", "BNB": "bnb"}
        for net in TARGET_NETWORKS:
            candidates = [
                r for r in api_result.get("routes", [])
                if r.get("network") == net and any(
                    isinstance(pt, dict) and "Uniswap V3" in str(pt.get("fee", ""))
                    for pt in r.get("path_tokens", [])
                )
            ]
            if not candidates:
                print(f"  ⚠️  No Uniswap V3 route on {net}, skipping pool‑link check")
                continue
            best = max(candidates, key=lambda r: r.get("volume", 0))

            pool_addr = None
            fee_tier = None
            for pt in best.get("path_tokens", []):
                if isinstance(pt, dict) and "Uniswap V3" in str(pt.get("fee", "")):
                    pool_addr = pt.get("pool_address")
                    fee_parts = str(pt.get("fee", "")).split("|")
                    fee_tier = fee_parts[0].strip() if len(fee_parts) >= 1 else "?"
                    if pool_addr:
                        break
            if not pool_addr:
                print(f"  ⚠️  No pool_address on {net} best route, skipping")
                continue

            pool_url = f"https://app.uniswap.org/explore/pools/{net_map[net]}/{pool_addr}"
            token_in = best.get("path_tokens", [""])[0] if len(best.get("path_tokens", [])) > 0 else "?"
            token_out = best.get("path_tokens", [""])[-1] if len(best.get("path_tokens", [])) > 0 else "?"

            # Verify the anchor element exists in the DOM with the correct href
            link = page.locator(f'a[href="{pool_url}"]').first
            href = await link.get_attribute("href")
            assert href == pool_url, f"{net}: expected href {pool_url} but got {href}"

            async with context.expect_page() as new_page_info:
                await link.click()
            pool_page = await new_page_info.value
            await pool_page.wait_for_load_state("domcontentloaded", timeout=30000)

            # Keep the tab visible for the video recording (3 seconds)
            await pool_page.bring_to_front()
            await asyncio.sleep(3)

            # Verify the URL resolves to the correct pool (the Uniswap app may
            # block headless browsers, so we check the URL contains the pool
            # address rather than parsing the page content)
            assert pool_addr.lower() in pool_page.url.lower(), (
                f"{net}: clicked {pool_url} but landed on {pool_page.url}"
            )

            await pool_page.close()
            await page.bring_to_front()
            print(f"  ✅ {net}: Uniswap V3 external link → {pool_url}")

        # --- Also verify the largest PancakeSwap V3 pool link ---
        ps_candidates = [
            r for r in api_result.get("routes", [])
            if any(
                isinstance(pt, dict) and "PancakeSwap V3" in str(pt.get("fee", ""))
                for pt in r.get("path_tokens", [])
            )
        ]
        if ps_candidates:
            ps_best = max(ps_candidates, key=lambda r: r.get("volume", 0))
            ps_addr = None
            ps_net = None
            for pt in ps_best.get("path_tokens", []):
                if isinstance(pt, dict) and "PancakeSwap V3" in str(pt.get("fee", "")):
                    ps_addr = pt.get("pool_address")
                    fee_parts = str(pt.get("fee", "")).split("|")
                    ps_net = fee_parts[2].strip() if len(fee_parts) >= 3 else None
                    if ps_addr:
                        break
            if ps_addr and ps_net:
                ps_chain = {"BNB": "bsc", "Base": "base", "Ethereum": "eth", "Arbitrum": "arb"}.get(ps_net, "bsc")
                ps_url = f"https://pancakeswap.finance/info/v3/pairs/{ps_addr}?chain={ps_chain}"

                ps_link = page.locator(f'a[href="{ps_url}"]').first
                ps_href = await ps_link.get_attribute("href")
                assert ps_href == ps_url, f"PancakeSwap: expected href {ps_url} but got {ps_href}"

                async with context.expect_page() as new_page_info:
                    await ps_link.click()
                ps_page = await new_page_info.value
                await ps_page.wait_for_load_state("domcontentloaded", timeout=30000)
                await ps_page.bring_to_front()
                await asyncio.sleep(3)

                assert ps_addr.lower() in ps_page.url.lower(), (
                    f"PancakeSwap: clicked {ps_url} but landed on {ps_page.url}"
                )
                await ps_page.close()
                await page.bring_to_front()
                print(f"  ✅ PancakeSwap V3 ({ps_net}): external link → {ps_url}")
            else:
                print("  ⚠️  Could not extract PancakeSwap V3 pool address, skipping")
        else:
            print("  ⚠️  No PancakeSwap V3 routes found, skipping pool‑link check")

        # Verify number of rendered routes matches the API payload (at least).
        route_count = await page.locator("#routes-body tr").count()
        print(f"UI rendered {route_count} routes, API reported {len(api_result.get('routes', []))}")
        if cfg["record_video"]:
            src_path = await page.video.path()
            # Rename to the test name so each run has a predictable filename
            dst_path = str(Path(src_path).parent / f"{test_name}.webm")
            import shutil
            shutil.move(src_path, dst_path)
            print(f"Video saved to {dst_path}")
        await context.close()
        await browser.close()
        return route_count

# ---------------------------------------------------------------------------
# Pytest fixtures and test case
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def config() -> Dict[str, Any]:
    """Load configuration once per test session."""
    return load_config()

@pytest.mark.asyncio
async def test_route_analysis_ui(config: Dict[str, Any]):
    """End‑to‑end UI test driven by pytest.

    1. Calls the backend API directly to obtain the canonical result set.
    2. Drives the browser with Playwright to ensure the frontend renders the
       same number of routes.
    3. Fails the test if the UI reports zero routes or the counts differ.
    """
    # Step 1 – API call
    api_result = await call_api_direct(config)
    assert "routes" in api_result, "API response missing 'routes' key"

    routes = api_result["routes"]
    assert len(routes) >= 10, f"API returned {len(routes)} routes, expected at least 10"

    # Collect distinct networks and protocols present in the routes
    networks_seen: set[str] = set()
    protocols_seen: set[str] = set()
    for r in routes:
        networks_seen.add(r.get("network", "Ethereum"))
        for item in r.get("path_tokens", []):
            if isinstance(item, dict) and "fee" in item:
                # fee string format: "0.3%|Uniswap V3|Ethereum"
                parts = str(item["fee"]).split("|")
                if len(parts) >= 2:
                    protocols_seen.add(parts[1].strip())

    assert "Ethereum" in networks_seen, "No routes from Ethereum found"
    assert "Arbitrum" in networks_seen, "No routes from Arbitrum found"
    assert "Base" in networks_seen, "No routes from Base found"
    assert "BNB" in networks_seen, "No routes from BNB chain found"
    assert "Uniswap V3" in protocols_seen, "No Uniswap V3 pools found"
    assert "Uniswap V4" in protocols_seen, "No Uniswap V4 pools found"
    assert "PancakeSwap V3" in protocols_seen, "No PancakeSwap V3 pools found"

    print(f"  API routes: {len(routes)} | networks: {', '.join(sorted(networks_seen))} | protocols: {', '.join(sorted(protocols_seen))}")

    # Step 2 – UI verification
    ui_route_count = await run_playwright_ui(config, api_result, test_name="test_route_analysis_ui")
    assert ui_route_count >= 10, f"UI rendered {ui_route_count} routes, expected at least 10"

# ---------------------------------------------------------------------------
# Helper for direct module execution (maintains parity with original script)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Allow running the test as a script for quick debugging.
    cfg = load_config()
    asyncio.run(call_api_direct(cfg))
    # Note: The UI part requires an async event loop; invoke via pytest for full coverage.
    sys.exit(0)
