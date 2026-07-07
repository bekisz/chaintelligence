#!/usr/bin/env python3
"""Regression tests for routing page date input behavior."""

import asyncio
import base64
from pathlib import Path

import pytest
import yaml
from playwright.async_api import async_playwright


CONFIG_PATH = Path(__file__).with_name("config.yaml")


def load_config():
    raw = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    return {
        "api_url": raw.get("api_url", "http://localhost:8000"),
        "portal_username": raw.get("portal_username", "admin"),
        "portal_password": raw.get("portal_password", "chaintelligence77"),
        "headless": bool(raw.get("headless", True)),
    }


@pytest.mark.asyncio
async def test_pending_date_range_fetch_does_not_overwrite_user_start_date_during_analysis():
    """A slow initial date-range response must not mutate a user-entered query date."""
    cfg = load_config()
    auth_bytes = f"{cfg['portal_username']}:{cfg['portal_password']}".encode()
    auth_header_val = "Basic " + base64.b64encode(auth_bytes).decode()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg["headless"], args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context(
            http_credentials={"username": cfg["portal_username"], "password": cfg["portal_password"]},
            extra_http_headers={"Authorization": auth_header_val},
        )
        page = await context.new_page()

        async def delayed_date_range(route):
            await asyncio.sleep(0.5)
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"min_date":"2026-07-02","max_date":"2026-07-05"}',
            )

        async def fake_analyze(route):
            await route.fulfill(
                status=200,
                content_type="application/x-ndjson",
                body=(
                    '{"type":"progress","pct":100,"message":"Analysis complete!"}\n'
                    '{"type":"result","data":{"routes":[],"total_tx":0,"total_volume":0}}\n'
                ),
            )

        await page.route("**/api/routes/date-range**", delayed_date_range)
        await page.route("**/api/routes/analyze**", fake_analyze)

        await page.goto(f"{cfg['api_url']}/routing", wait_until="domcontentloaded", timeout=30000)
        await page.fill("#start-token", "USD")
        await page.fill("#end-token", "USD")
        await page.fill("#start-date", "2026-07-01")
        await page.fill("#end-date", "2026-07-05")
        await page.click("#analyze-btn")

        await page.wait_for_timeout(800)
        start_date = await page.input_value("#start-date")

        await context.close()
        await browser.close()

    assert start_date == "2026-07-01"
