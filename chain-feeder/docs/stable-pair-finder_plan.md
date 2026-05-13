# Stable-Pair Shortcut Finder

## Problem

On Uniswap, swaps between correlated assets (e.g. USDC→DAI, EURC→EURI) often route through volatile intermediaries like WETH because no direct pool exists. These multi-hop routes:
- Expose LPs to unnecessary impermanent loss via volatile tokens
- Cost traders more in cumulative fees (e.g. 0.05% + 0.3% = 0.35% for USDC→WETH→DAI)
- Create an opportunity: a direct stable pair pool (USDC/DAI at 0.01%) can **undercut** the multi-hop fee while still being **lucrative** for the LP

## Goal

Build a CLI tool that automatically:
1. Scans all multi-hop routes between correlated token families
2. Identifies routes where volume flows through volatile intermediaries
3. Calculates the "shortcut opportunity" — how much fee revenue could be captured by providing a direct pool
4. Ranks opportunities by attractiveness (volume × fee margin)

## Proposed Changes

### Core Module

#### [NEW] [shortcut_finder.py](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/routing/shortcut_finder.py)

The main analysis engine. A `ShortcutFinder` class that:

1. **Identifies candidate pairs**: Uses coin families (from `coin-families.yml`) to generate all cross-family pairs where a stable-pair shortcut could exist. For example:
   - `USD × USD` → USDC-DAI, USDC-USDT, USDS-DAI, etc.
   - `USD × EUR` → USDC-EURC, USDT-EURI, etc.
   - `ETH × ETH` → WETH-STETH, WETH-RETH, etc.
   - `GOLD × GOLD` → PAXG-XAUT

2. **Scans multi-hop routes**: For each candidate pair (A, B), runs route analysis (using existing `RouteAnalyzer`) on A→B to find all routes. Filters to routes with **2+ hops** that pass through a **volatile intermediary** (a token NOT in either family).

3. **Calculates shortcut economics**:
   - **Divertable volume**: Total volume on multi-hop routes through volatile tokens
   - **Current cumulative fee**: Sum of fees along the multi-hop route (e.g. USDC→WETH 0.05% + WETH→DAI 0.3% = 0.35%)
   - **Proposed shortcut fee**: A direct pool fee that undercuts the multi-hop fee but is still above break-even. Uses the lowest standard Uniswap fee tier that is strictly below the cumulative fee: `{0.01%, 0.05%, 0.3%, 1.0%}`
   - **Fee margin**: shortcut_fee − 0 (LP's cost is just impermanent loss, which is minimal for correlated pairs)
   - **Projected daily revenue**: divertable_volume × shortcut_fee
   - **Projected APR**: (daily_revenue × 365 / estimated_tvl) — where estimated TVL is a configurable parameter

4. **Output**: Ranked list of shortcut opportunities sorted by projected daily revenue, showing:
   - Pair (e.g. USDC/DAI)
   - Current dominant route (e.g. USDC→WETH→DAI)
   - Divertable volume (USD)
   - Current fee vs proposed shortcut fee
   - Projected daily revenue & APR at different TVL targets

Key design decisions:
- Leverages existing `PostgresFetcher` for swap data and `RouteAnalyzer` for route reconstruction
- Leverages existing `CoinFamilyResolver` for token family definitions
- Configurable fee tiers, lookback period, and TVL targets
- Pure analysis — no external API calls needed beyond DB access

---

### CLI Entry Point

#### [NEW] [find_shortcuts.py](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/routing/find_shortcuts.py)

Command-line tool wrapping `ShortcutFinder`:

```bash
# Find all stable-pair shortcut opportunities in the last 30 days
python find_shortcuts.py --days 30

# Focus on specific families
python find_shortcuts.py --days 30 --families USD EUR

# Include cross-family analysis (USD×EUR, not just USD×USD)
python find_shortcuts.py --days 30 --cross-family

# JSON output
python find_shortcuts.py --days 30 --format json

# Custom TVL targets for APR estimation
python find_shortcuts.py --days 30 --tvl-targets 100000 500000 1000000
```

Output example:
```
================================================================================
Stable-Pair Shortcut Opportunities (Last 30 days)
================================================================================

#1  USDC / DAI
    Dominant Route: USDC -- 0.05%|v3 --> WETH -- 0.3%|v3 --> DAI
    Divertable Volume: $12,450,000 (342 txns, avg $36,404)
    Current Cumulative Fee: 0.35%
    Proposed Shortcut Fee: 0.01%
    Daily Revenue @ $100K TVL: $41.50/day → APR: 15.15%
    Daily Revenue @ $500K TVL: $41.50/day → APR: 3.03%
    ---
    Other routes diverted:
      USDC -- 0.3%|v4 --> WETH -- 0.3%|v3 --> DAI  ($2.1M, 89 txns)

#2  EURC / EURI  
    Dominant Route: EURC -- 0.3%|v3 --> WETH -- 0.3%|v3 --> EURI
    ...
```

---

### Coin Family Config Update

#### [MODIFY] [coin-families.yml](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/include/config/coin-families.yml)

Add a `correlated: true` flag to families where intra-family tokens are expected to trade near parity. This allows the finder to know which families are suitable for stable-pair shortcuts:

```yaml
coin-families:
  - name: ETH
    correlated: true  # <-- NEW
    coin-list:
      - ETH
      - STETH
      - rETH
      - WETH
    regexp-rule: ".*ETH$"
  # ...
  - name: Stablecoins
    correlated: true  # <-- NEW
    sql-rule: "symbol IN ('GHO', 'DAI', 'MIM')"
    regexp-rule: ".*USD.*"
```

## User Review Required

> [!IMPORTANT]
> **Family scope**: Currently you have `USD`, `EUR`, `GOLD`, `BTC`, `ETH`, `AAVE` families. Should the shortcut finder scan all intra-family permutations by default, or only specific families you care about? Cross-family analysis (e.g. USD×EUR) could find USDC/EURC shortcuts — should that be enabled by default?

> [!IMPORTANT]
> **TVL assumptions**: For APR projections, we need an assumed pool TVL. Should I use:
> - Fixed targets (e.g. $100K, $500K, $1M) for comparison?
> - The actual TVL of any existing pool if one already exists in the DB?
> - Both, with existing pool TVL as primary and fixed targets as fallback?

> [!IMPORTANT]
> **Minimum thresholds**: What minimum divertable volume should filter out noise? Suggestion: $10,000/period minimum to consider a shortcut opportunity worth listing.

## Open Questions

1. Should the tool also check if a direct pool **already exists** on-chain (via `liquidity_pool` table) and flag it as "pool exists but underutilized" vs "pool doesn't exist yet"?
2. Should we consider V4 dynamic fee pools as potential shortcuts, or only standard V3/V4 fee tiers?

## Verification Plan

### Automated Tests
- Unit tests with mock swap data testing the shortcut identification logic
- Run against live DB: `python find_shortcuts.py --days 30` and verify output is sensible

### Manual Verification
- Cross-reference top opportunities with actual Uniswap UI to verify the routes exist
- Check that fee calculations are correct (cumulative fee > shortcut fee)
