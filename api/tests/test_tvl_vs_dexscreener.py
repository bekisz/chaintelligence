"""Compare TVL and volume against DexScreener and DeFiLlama APIs for top 50 USD-USD pools."""
import os
import re
import time
import unittest
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.secrets"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env.config"))

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
USERNAME = os.getenv("PORTAL_USERNAME")
PASSWORD = os.getenv("PORTAL_PASSWORD")
DS_API_BASE = "https://api.dexscreener.com/latest/dex/pairs"
DL_POOLS_URL = "https://yields.llama.fi/pools"


def _fetch_pools():
    global _POOL_DAYS
    end = datetime.now()
    start = end - timedelta(days=8)
    auth = (USERNAME, PASSWORD) if USERNAME and PASSWORD else None
    url = (
        f"{BASE_URL}/api/pools/search"
        f"?start_token=USD&end_token=USD"
        f"&start_date={start.strftime('%Y-%m-%d')}&end_date={end.strftime('%Y-%m-%d')}"
        f"&sort_by=volume&limit=50&stream=false"
    )
    resp = requests.get(url, auth=auth)
    resp.raise_for_status()
    _POOL_DAYS = (end - start).days or 1
    return resp.json().get("pools", [])


_POOL_DAYS = 8


def _ds_api_url(web_url: str) -> str | None:
    parts = web_url.rstrip("/").rsplit("/", 2)
    if len(parts) < 3:
        return None
    chain = parts[1]
    address = parts[2].split("?")[0]
    if not re.match(r'^0x[a-fA-F0-9]{40,64}$', address):
        return None
    return f"{DS_API_BASE}/{chain}/{address.lower()}"


def _fetch_ds_data(ds_url: str) -> dict | None:
    api_url = _ds_api_url(ds_url)
    if not api_url:
        return None
    try:
        r = requests.get(api_url, timeout=10)
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        return {
            "tvl": float(pairs[0].get("liquidity", {}).get("usd", 0) or 0),
            "volume": float(pairs[0].get("volume", {}).get("h24", 0) or 0),
        }
    except Exception:
        return None


def _fetch_dl_index() -> dict:
    try:
        r = requests.get(DL_POOLS_URL, timeout=60)
        r.raise_for_status()
        data = r.json().get("data") or []
        idx = {}
        for p in data:
            uid = p["pool"]
            idx[uid] = {
                "tvl": float(p.get("tvlUsd", 0) or 0),
                "volume": float(p.get("volumeUsd1d", 0) or 0),
            }
        return idx
    except Exception:
        return {}


def _ratio(a: float | None, b: float | None) -> float | None:
    if a and b and a > 0 and b > 0:
        return abs(a - b) / max(a, b)
    return None


class TestDataVsExternal(unittest.TestCase):

    def setUp(self):
        self.auth = (USERNAME, PASSWORD) if USERNAME and PASSWORD else None
        self.dl_index = _fetch_dl_index()

    def test_data_comparison(self):
        pools = _fetch_pools()
        self.assertGreater(len(pools), 0, "No USD-USD pools found")

        ds_skip, ds_tvl_bad, ds_vol_bad = [], [], []
        dl_skip, dl_tvl_bad, dl_vol_bad = [], [], []

        for pool in pools:
            pid = pool["id"]
            pair = f"{pool['token0']['symbol']}/{pool['token1']['symbol']}"
            our_tvl = pool.get("tvl_usd") or 0
            our_vol_avg = (pool.get("volume_usd") or 0) / _POOL_DAYS
            links = pool.get("links") or {}
            ds_url = links.get("dexscreener", "")
            dl_uuid = pool.get("defillama_uuid")

            if ds_url:
                time.sleep(0.06)
                ds = _fetch_ds_data(ds_url)
                if ds is None:
                    ds_skip.append((pid, pair, "DS lookup failed"))
                else:
                    tvl_r = _ratio(our_tvl, ds["tvl"])
                    if tvl_r is not None and tvl_r > 0.10:
                        ds_tvl_bad.append((pid, pair, our_tvl, ds["tvl"], f"TVL diff {tvl_r:.1%}"))
                    vol_r = _ratio(our_vol_avg, ds["volume"])
                    if vol_r is not None and vol_r > 0.30:
                        ds_vol_bad.append((pid, pair, our_vol_avg, ds["volume"], f"Vol diff {vol_r:.1%}"))
            else:
                ds_skip.append((pid, pair, "no DexScreener link"))

            if dl_uuid:
                dl = self.dl_index.get(dl_uuid)
                if dl is None:
                    dl_skip.append((pid, pair, "UUID not in DL index"))
                else:
                    tvl_r = _ratio(our_tvl, dl["tvl"])
                    if tvl_r is not None and tvl_r > 0.10:
                        dl_tvl_bad.append((pid, pair, our_tvl, dl["tvl"], f"TVL diff {tvl_r:.1%}"))
                    vol_r = _ratio(our_vol_avg, dl["volume"])
                    if vol_r is not None and vol_r > 0.30:
                        dl_vol_bad.append((pid, pair, our_vol_avg, dl["volume"], f"Vol diff {vol_r:.1%}"))
            else:
                dl_skip.append((pid, pair, "no defillama_uuid"))

        msg = []
        if ds_tvl_bad:
            msg.append(f"{len(ds_tvl_bad)} DexScreener TVL mismatch(es) > 10%:")
            msg.extend(f"  Pool {pid} ({pair}): our={our:.0f} ds={ds:.0f} {r}" for pid, pair, our, ds, r in ds_tvl_bad)
        if ds_vol_bad:
            msg.append(f"{len(ds_vol_bad)} DexScreener Volume mismatch(es) > 30%:")
            msg.extend(f"  Pool {pid} ({pair}): our_daily_avg={our:.0f} ds_h24={ds:.0f} {r}" for pid, pair, our, ds, r in ds_vol_bad)
        if dl_tvl_bad:
            msg.append(f"{len(dl_tvl_bad)} DeFiLlama TVL mismatch(es) > 10%:")
            msg.extend(f"  Pool {pid} ({pair}): our={our:.0f} dl={dl:.0f} {r}" for pid, pair, our, dl, r in dl_tvl_bad)
        if dl_vol_bad:
            msg.append(f"{len(dl_vol_bad)} DeFiLlama Volume mismatch(es) > 30%:")
            msg.extend(f"  Pool {pid} ({pair}): our_daily_avg={our:.0f} dl_daily={dl:.0f} {r}" for pid, pair, our, dl, r in dl_vol_bad)
        if ds_skip:
            msg.append(f"{len(ds_skip)} DexScreener skip(s):")
            msg.extend(f"  Pool {pid} ({pair}): {r}" for pid, pair, r in ds_skip)
        if dl_skip:
            msg.append(f"{len(dl_skip)} DeFiLlama skip(s):")
            msg.extend(f"  Pool {pid} ({pair}): {r}" for pid, pair, r in dl_skip)
        if not any([ds_tvl_bad, ds_vol_bad, dl_tvl_bad, dl_vol_bad]):
            print("All matched pools within thresholds across all sources")
        if msg:
            self.fail("\n".join(msg))

    def test_report(self):
        """Report TVL + Volume comparison (daily avg vs 24h external)."""
        pools = _fetch_pools()
        print(f"\nData vs DexScreener + DeFiLlama for top {len(pools)} USD-USD pools\n")
        print(f"Our Volume = {_POOL_DAYS}-day sum; External = 24h snapshot; ratio on daily avg\n")
        hdr = (
            f"  {'Pool':>6} {'Pair':>22}"
            f" {'Our TVL':>12} {'DS TVL':>12} {'DL TVL':>12}"
            f" {'Our/D':>14} {'DS h24':>14} {'DL 1d':>14}"
            f" {'TVL-DS':>6} {'TVL-DL':>6} {'Vol-DS':>6} {'Vol-DL':>6}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 3))
        for pool in pools:
            pid = pool["id"]
            pair = f"{pool['token0']['symbol']}/{pool['token1']['symbol']}"
            our_tvl = pool.get("tvl_usd") or 0
            our_vol_day = (pool.get("volume_usd") or 0) / _POOL_DAYS
            links = pool.get("links") or {}
            dl_uuid = pool.get("defillama_uuid")

            ds_tvl = ds_vol = None
            ds_url = links.get("dexscreener", "")
            if ds_url:
                time.sleep(0.06)
                ds = _fetch_ds_data(ds_url)
                if ds:
                    ds_tvl, ds_vol = ds["tvl"], ds["volume"]

            dl_tvl = dl_vol = None
            if dl_uuid:
                dl = self.dl_index.get(dl_uuid)
                if dl:
                    dl_tvl, dl_vol = dl["tvl"], dl["volume"]

            def fmt(v, w=12):
                return f"{v:>{w},.0f}" if v is not None else f"{'N/A':>{w}}"

            def pct(a, b):
                r = _ratio(a, b)
                return f"{r*100:5.1f}%" if r is not None else f"{'N/A':>6}"

            print(
                f"  {pid:>6} ({pair:>20}):"
                f" {fmt(our_tvl,12)} {fmt(ds_tvl,12)} {fmt(dl_tvl,12)}"
                f" {fmt(our_vol_day,14)} {fmt(ds_vol,14)} {fmt(dl_vol,14)}"
                f" {pct(our_tvl, ds_tvl)} {pct(our_tvl, dl_tvl)}"
                f" {pct(our_vol_day, ds_vol)} {pct(our_vol_day, dl_vol)}"
            )


if __name__ == "__main__":
    unittest.main()
