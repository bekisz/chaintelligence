"""
Stable-Pair Shortcut Finder

Scans multi-hop DEX routes between correlated token families to identify
opportunities for direct stable-pair pools that can undercut multi-hop fees
while remaining lucrative for LPs.

Concept: docs/concepts/stable-pair-shortcut.md
"""

import os
import sys
import logging
from itertools import combinations
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict

# Ensure routing modules are importable
ROUTING_DIR = os.path.dirname(os.path.abspath(__file__))
if ROUTING_DIR not in sys.path:
    sys.path.insert(0, ROUTING_DIR)

INCLUDE_DIR = os.path.join(os.path.dirname(ROUTING_DIR), 'include')
if INCLUDE_DIR not in sys.path:
    sys.path.insert(0, INCLUDE_DIR)

from postgres_fetcher import PostgresFetcher
from route_analyzer import RouteAnalyzer
from coin_family_resolver import CoinFamilyResolver
from config import DATA_WAREHOUSE_DB

logger = logging.getLogger(__name__)

# Standard Uniswap fee tiers (as percentages)
STANDARD_FEE_TIERS = [0.01, 0.05, 0.30, 1.0]

# Default TVL targets for APR projection (USD)
DEFAULT_TVL_TARGETS = [100_000, 500_000, 1_000_000]

# Minimum divertable volume (USD) to consider a shortcut worth listing
DEFAULT_MIN_VOLUME = 10_000


class ShortcutOpportunity:
    """Represents a single stable-pair shortcut opportunity."""

    def __init__(self, token_a: str, token_b: str, family_a: str, family_b: str):
        self.token_a = token_a
        self.token_b = token_b
        self.family_a = family_a
        self.family_b = family_b

        # Route data
        self.multihop_routes: List[Dict] = []  # Routes with 2+ hops via volatile tokens
        self.direct_routes: List[Dict] = []     # Existing direct routes (1 hop)
        self.total_divertable_volume: float = 0.0
        self.total_divertable_txns: int = 0
        self.dominant_route: Optional[Dict] = None

        # Existing pool info
        self.existing_pool_tvl: Optional[float] = None
        self.existing_pool_fee: Optional[str] = None

        # Economics
        self.current_cumulative_fee: float = 0.0  # Weighted average of multi-hop fees
        self.proposed_shortcut_fee: float = 0.0
        self.daily_revenue_by_tvl: Dict[float, float] = {}
        self.apr_by_tvl: Dict[float, float] = {}

    @property
    def pair_label(self) -> str:
        return f"{self.token_a}/{self.token_b}"

    @property
    def is_cross_family(self) -> bool:
        return self.family_a != self.family_b

    def calculate_economics(self, tvl_targets: List[float], period_days: float):
        """Calculate projected revenue and APR at various TVL levels."""
        if self.total_divertable_volume <= 0 or period_days <= 0:
            return

        daily_volume = self.total_divertable_volume / period_days

        for tvl in tvl_targets:
            daily_rev = daily_volume * (self.proposed_shortcut_fee / 100.0)
            self.daily_revenue_by_tvl[tvl] = daily_rev
            if tvl > 0:
                self.apr_by_tvl[tvl] = (daily_rev / tvl) * 365.0
            else:
                self.apr_by_tvl[tvl] = 0.0


class ShortcutFinder:
    """
    Main engine for finding stable-pair shortcut opportunities.

    Workflow:
    1. Load coin families and generate candidate pairs
    2. For each pair, run route analysis to find multi-hop routes
    3. Identify routes with volatile intermediaries
    4. Calculate shortcut economics
    5. Rank and return opportunities
    """

    def __init__(
        self,
        families: Optional[List[str]] = None,
        cross_family: bool = False,
        min_volume: float = DEFAULT_MIN_VOLUME,
        tvl_targets: Optional[List[float]] = None,
        verbose: bool = False,
    ):
        self.families = families  # None = all correlated families
        self.cross_family = cross_family
        self.min_volume = min_volume
        self.tvl_targets = tvl_targets or DEFAULT_TVL_TARGETS
        self.verbose = verbose

        config_path = os.path.join(INCLUDE_DIR, 'config', 'coin-families.yml')
        self.family_resolver = CoinFamilyResolver(config_path, DATA_WAREHOUSE_DB)
        self.fetcher = PostgresFetcher(verbose=verbose)
        self.prices = {}

    def _log(self, msg: str):
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [ShortcutFinder] {msg}")

    def _get_target_families(self) -> List[Dict]:
        """Get list of family configs to analyze."""
        all_families = self.family_resolver.families

        if self.families:
            # Filter to requested families
            return [f for f in all_families if f.get('name') in self.families]

        # Default: all families marked correlated, or all if none are marked
        correlated = [f for f in all_families if f.get('correlated', False)]
        if not correlated:
            # Fallback: use all families (assume all are relevant)
            return all_families

        return correlated

    def _get_family_tokens(self, family_name: str) -> Set[str]:
        """Resolve a family to its set of token symbols."""
        return self.family_resolver.resolve_family(family_name)

    def _generate_candidate_pairs(self) -> List[Tuple[str, str, str, str]]:
        """
        Generate all candidate (tokenA, tokenB, familyA, familyB) pairs.

        Intra-family: all pairs within each family (USDC-DAI, USDC-USDT, etc.)
        Cross-family: all pairs across families if enabled (USDC-EURC, etc.)
        """
        target_families = self._get_target_families()
        self._log(f"Target families: {[f.get('name') for f in target_families]}")

        family_tokens = {}
        for f in target_families:
            name = f.get('name')
            tokens = self._get_family_tokens(name)
            if tokens:
                family_tokens[name] = tokens
                self._log(f"  {name}: {sorted(tokens)}")

        candidates = []
        seen_pairs = set()

        # Intra-family pairs
        for fname, tokens in family_tokens.items():
            token_list = sorted(tokens)
            for a, b in combinations(token_list, 2):
                pair_key = tuple(sorted([a, b]))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    candidates.append((a, b, fname, fname))

        # Cross-family pairs
        if self.cross_family:
            family_names = sorted(family_tokens.keys())
            for fa, fb in combinations(family_names, 2):
                for ta in sorted(family_tokens[fa]):
                    for tb in sorted(family_tokens[fb]):
                        pair_key = tuple(sorted([ta, tb]))
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            candidates.append((ta, tb, fa, fb))

        self._log(f"Generated {len(candidates)} candidate pairs")
        return candidates

    def _is_volatile_intermediary(self, token: str, family_tokens_combined: Set[str]) -> bool:
        """
        Check if a token is a volatile intermediary (not in either family
        of the pair being analyzed).
        """
        return token.upper() not in family_tokens_combined

    def _parse_fee_pct(self, fee_str: str) -> float:
        """
        Parse a fee string to a float percentage.
        Handles formats: '0.05%|v3', '0.3%', '500', '3000', '0.05'
        """
        # Strip protocol suffix
        fee_part = str(fee_str).split('|')[0].strip()

        # Remove % sign
        fee_part = fee_part.replace('%', '').strip()

        try:
            val = float(fee_part)
            # If it's a large number, it's in basis points (500 = 0.05%)
            if val >= 10:
                return val / 10000.0
            # If small, it's already a percentage
            return val
        except (ValueError, TypeError):
            return 0.0

    def _pick_shortcut_fee(self, cumulative_fee_pct: float) -> float:
        """
        Pick the best Uniswap fee tier for the shortcut.
        Must be strictly below the cumulative multi-hop fee.
        Returns the highest standard fee tier that undercuts.
        """
        for fee in reversed(STANDARD_FEE_TIERS):
            if fee < cumulative_fee_pct:
                return fee
        # If cumulative fee is very low, use the minimum
        return STANDARD_FEE_TIERS[0]

    def _check_existing_pool(self, token_a: str, token_b: str) -> Tuple[Optional[float], Optional[str]]:
        """
        Check if a direct pool already exists in the DB for this pair.
        Returns (tvl, fee_tier) or (None, None).
        """
        try:
            import psycopg2
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
            cur = conn.cursor()

            cur.execute("""
                SELECT p.fee_tier, h.tvl_usd
                FROM liquidity_pool p
                LEFT JOIN LATERAL (
                    SELECT tvl_usd FROM liquidity_pool_history
                    WHERE pool_id = p.id AND tvl_usd > 0
                    ORDER BY date DESC LIMIT 1
                ) h ON true
                WHERE (
                    (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                    OR
                    (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                )
                AND p.reverted = false
                ORDER BY h.tvl_usd DESC NULLS LAST
                LIMIT 1
            """, (token_a.upper(), token_b.upper(), token_b.upper(), token_a.upper()))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                fee = row[0]
                tvl = float(row[1]) if row[1] else None
                return tvl, fee
            return None, None
        except Exception as e:
            self._log(f"Error checking existing pool for {token_a}/{token_b}: {e}")
            return None, None

    def _analyze_pair(
        self,
        token_a: str,
        token_b: str,
        family_a: str,
        family_b: str,
        swaps: List[Dict],
        period_days: float,
    ) -> Optional[ShortcutOpportunity]:
        """
        Analyze a single token pair for shortcut opportunities.
        """
        # Build set of all tokens in both families (these are "non-volatile" for this pair)
        family_a_tokens = self._get_family_tokens(family_a)
        family_b_tokens = self._get_family_tokens(family_b)
        family_tokens_combined = family_a_tokens | family_b_tokens

        # Run route analysis A -> B
        analyzer = RouteAnalyzer(verbose=False, prices=self.prices)
        analysis = analyzer.analyze_routes(swaps, [token_a], [token_b])

        routes = analysis.get('routes', [])
        if not routes:
            return None

        opp = ShortcutOpportunity(token_a, token_b, family_a, family_b)

        # Classify routes
        for route in routes:
            path_tokens = route.get('path_tokens', [])
            hops = route.get('hops', 0)
            volume = route.get('volume', 0.0)
            count = route.get('count', 0)

            if hops <= 1:
                # Direct route — already a shortcut
                opp.direct_routes.append(route)
                continue

            # Check if any intermediary is volatile
            # Path format: [Token, Fee, Token, Fee, Token, ...]
            intermediaries = [path_tokens[i] for i in range(2, len(path_tokens) - 1, 2)]
            has_volatile = any(
                self._is_volatile_intermediary(t, family_tokens_combined)
                for t in intermediaries
            )

            if has_volatile and volume > 0:
                # Calculate cumulative fee for this route
                fees = []
                for i in range(1, len(path_tokens), 2):
                    fee_entry = path_tokens[i]
                    if isinstance(fee_entry, dict):
                        fee_pct = self._parse_fee_pct(fee_entry.get('fee', '0'))
                    else:
                        fee_pct = self._parse_fee_pct(fee_entry)
                    fees.append(fee_pct)

                cum_fee = sum(fees)

                route_data = {
                    **route,
                    'intermediaries': intermediaries,
                    'cumulative_fee_pct': cum_fee,
                    'individual_fees': fees,
                }
                opp.multihop_routes.append(route_data)
                opp.total_divertable_volume += volume
                opp.total_divertable_txns += count

        if opp.total_divertable_volume < self.min_volume:
            return None

        # Sort multi-hop routes by volume
        opp.multihop_routes.sort(key=lambda r: r.get('volume', 0), reverse=True)
        opp.dominant_route = opp.multihop_routes[0] if opp.multihop_routes else None

        # Calculate weighted average cumulative fee
        total_vol = sum(r.get('volume', 0) for r in opp.multihop_routes)
        if total_vol > 0:
            opp.current_cumulative_fee = sum(
                r.get('cumulative_fee_pct', 0) * r.get('volume', 0)
                for r in opp.multihop_routes
            ) / total_vol
        else:
            opp.current_cumulative_fee = opp.multihop_routes[0].get('cumulative_fee_pct', 0) if opp.multihop_routes else 0

        # Pick shortcut fee
        opp.proposed_shortcut_fee = self._pick_shortcut_fee(opp.current_cumulative_fee)

        # Check existing pool
        existing_tvl, existing_fee = self._check_existing_pool(token_a, token_b)
        opp.existing_pool_tvl = existing_tvl
        opp.existing_pool_fee = existing_fee

        # If existing pool has TVL, add it to TVL targets for APR calc
        tvl_targets = list(self.tvl_targets)
        if existing_tvl and existing_tvl > 0 and existing_tvl not in tvl_targets:
            tvl_targets.append(existing_tvl)
            tvl_targets.sort()

        # Calculate economics
        opp.calculate_economics(tvl_targets, period_days)

        return opp

    def find(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[ShortcutOpportunity]:
        """
        Run the full shortcut finder analysis.

        Args:
            start_date: Start of analysis window
            end_date: End of analysis window

        Returns:
            List of ShortcutOpportunity sorted by divertable volume
        """
        period_days = (end_date - start_date).total_seconds() / 86400
        if period_days < 0.1:
            self._log("Period too short")
            return []

        self._log(f"Analysis period: {start_date.date()} to {end_date.date()} ({period_days:.1f} days)")

        # Fetch prices for volume fallback
        self.prices = self.fetcher.fetch_latest_prices()
        self._log(f"Loaded {len(self.prices)} token prices")

        # Generate candidate pairs
        candidates = self._generate_candidate_pairs()
        if not candidates:
            self._log("No candidate pairs found")
            return []

        # Build set of all target family tokens for pre-filtering
        target_tokens = set()
        for token_a, token_b, family_a, family_b in candidates:
            target_tokens.add(token_a.upper())
            target_tokens.add(token_b.upper())
            target_tokens |= self._get_family_tokens(family_a)
            target_tokens |= self._get_family_tokens(family_b)
        self._log(f"Pre-filter token set ({len(target_tokens)}): {sorted(target_tokens)[:20]}{'...' if len(target_tokens) > 20 else ''}")

        # Fetch swap data in batches, keeping only transactions that involve
        # at least one target token (this massively reduces memory for large ranges)
        self._log("Fetching swap data...")
        BATCH_DAYS = 5
        all_swaps = []
        total_fetched = 0
        current_start = start_date
        try:
            while current_start < end_date:
                chunk_end = min(current_start + timedelta(days=BATCH_DAYS), end_date)
                batch = self.fetcher.fetch_swaps(current_start, chunk_end)
                total_fetched += len(batch) if batch else 0

                if batch:
                    # First pass: find tx_hashes that involve a target token
                    relevant_tx_hashes = set()
                    for swap in batch:
                        t0 = (swap.get('token0_symbol') or '').upper()
                        t1 = (swap.get('token1_symbol') or '').upper()
                        if t0 in target_tokens or t1 in target_tokens:
                            relevant_tx_hashes.add(swap['tx_hash'])

                    # Second pass: keep ALL swaps in relevant transactions
                    # (we need the full tx to reconstruct multi-hop paths)
                    for swap in batch:
                        if swap['tx_hash'] in relevant_tx_hashes:
                            all_swaps.append(swap)

                current_start = chunk_end + timedelta(microseconds=1)
        except Exception as e:
            self._log(f"ERROR: Could not fetch swap data from database: {e}")
            self._log("Make sure the database is running (docker-compose up -d)")
            return []

        self._log(f"Loaded {len(all_swaps)} relevant swap events (from {total_fetched:,} total)")

        if not all_swaps:
            self._log("No swap data found")
            return []

        # Analyze each candidate pair
        opportunities = []
        total = len(candidates)

        for idx, (token_a, token_b, family_a, family_b) in enumerate(candidates):
            self._log(f"  [{idx+1}/{total}] Analyzing {token_a}/{token_b} ({family_a}{'×'+family_b if family_a != family_b else ''})...")

            opp = self._analyze_pair(
                token_a, token_b, family_a, family_b,
                all_swaps, period_days
            )

            if opp:
                self._log(f"    → FOUND: ${opp.total_divertable_volume:,.0f} divertable via {len(opp.multihop_routes)} multi-hop routes")
                opportunities.append(opp)

        # Also check reverse direction for each pair
        # (A→B might have different routes than B→A)
        reverse_candidates = [(b, a, fb, fa) for a, b, fa, fb in candidates]
        for idx, (token_a, token_b, family_a, family_b) in enumerate(reverse_candidates):
            # Check if we already have this pair in forward direction
            existing = next(
                (o for o in opportunities if
                 {o.token_a, o.token_b} == {token_a, token_b}),
                None
            )

            self._log(f"  [Rev {idx+1}/{total}] Analyzing {token_a}/{token_b}...")

            opp = self._analyze_pair(
                token_a, token_b, family_a, family_b,
                all_swaps, period_days
            )

            if opp:
                if existing:
                    # Merge reverse volume into existing opportunity
                    existing.multihop_routes.extend(opp.multihop_routes)
                    existing.direct_routes.extend(opp.direct_routes)
                    existing.total_divertable_volume += opp.total_divertable_volume
                    existing.total_divertable_txns += opp.total_divertable_txns

                    # Recalculate economics
                    total_vol = sum(r.get('volume', 0) for r in existing.multihop_routes)
                    if total_vol > 0:
                        existing.current_cumulative_fee = sum(
                            r.get('cumulative_fee_pct', 0) * r.get('volume', 0)
                            for r in existing.multihop_routes
                        ) / total_vol
                    existing.proposed_shortcut_fee = self._pick_shortcut_fee(existing.current_cumulative_fee)

                    tvl_targets = list(self.tvl_targets)
                    if existing.existing_pool_tvl and existing.existing_pool_tvl > 0:
                        tvl_targets.append(existing.existing_pool_tvl)
                        tvl_targets.sort()
                    existing.calculate_economics(tvl_targets, period_days)

                    # Update dominant route
                    existing.multihop_routes.sort(key=lambda r: r.get('volume', 0), reverse=True)
                    existing.dominant_route = existing.multihop_routes[0]

                    self._log(f"    → MERGED: ${existing.total_divertable_volume:,.0f} total")
                else:
                    self._log(f"    → FOUND: ${opp.total_divertable_volume:,.0f}")
                    opportunities.append(opp)

        # Sort by divertable volume
        opportunities.sort(key=lambda o: o.total_divertable_volume, reverse=True)

        self._log(f"\nFound {len(opportunities)} shortcut opportunities")
        return opportunities

    def format_results(self, opportunities: List[ShortcutOpportunity], period_days: float) -> str:
        """Format opportunities as a human-readable report."""
        if not opportunities:
            return "No stable-pair shortcut opportunities found."

        lines = []
        lines.append("=" * 90)
        lines.append(f"Stable-Pair Shortcut Opportunities (Last {period_days:.0f} days)")
        lines.append(f"Found {len(opportunities)} opportunities")
        lines.append("=" * 90)
        lines.append("")

        for rank, opp in enumerate(opportunities, 1):
            fam_label = f"{opp.family_a}" if not opp.is_cross_family else f"{opp.family_a}×{opp.family_b}"

            lines.append(f"#{rank}  {opp.pair_label}  [{fam_label}]")

            # Existing pool status
            if opp.existing_pool_tvl is not None:
                lines.append(f"    ⚡ Existing pool: fee={opp.existing_pool_fee}, TVL=${opp.existing_pool_tvl:,.0f}")
            elif opp.existing_pool_fee is not None:
                lines.append(f"    ⚡ Existing pool found (fee={opp.existing_pool_fee}), but no TVL data")
            else:
                lines.append(f"    🆕 No direct pool exists on-chain")

            # Dominant route
            if opp.dominant_route:
                dom = opp.dominant_route
                lines.append(f"    Dominant Route: {dom.get('path', 'N/A')}")
                lines.append(f"      Intermediaries: {', '.join(dom.get('intermediaries', []))}")
                lines.append(f"      Fee breakdown: {' + '.join(f'{f:.2f}%' for f in dom.get('individual_fees', []))} = {dom.get('cumulative_fee_pct', 0):.2f}%")

            # Volume summary
            daily_vol = opp.total_divertable_volume / period_days if period_days > 0 else 0
            avg_tx = opp.total_divertable_volume / opp.total_divertable_txns if opp.total_divertable_txns > 0 else 0
            lines.append(f"    Divertable Volume: ${opp.total_divertable_volume:,.0f} ({opp.total_divertable_txns} txns, avg ${avg_tx:,.0f})")
            lines.append(f"    Daily Volume: ${daily_vol:,.0f}/day")

            # Fee comparison
            lines.append(f"    Current Avg Fee: {opp.current_cumulative_fee:.3f}%")
            lines.append(f"    Proposed Shortcut Fee: {opp.proposed_shortcut_fee:.2f}%")
            fee_saving = opp.current_cumulative_fee - opp.proposed_shortcut_fee
            lines.append(f"    Fee Saving for Traders: {fee_saving:.3f}%")

            # Direct route volume (if exists)
            if opp.direct_routes:
                direct_vol = sum(r.get('volume', 0) for r in opp.direct_routes)
                direct_txns = sum(r.get('count', 0) for r in opp.direct_routes)
                lines.append(f"    Existing Direct Route Volume: ${direct_vol:,.0f} ({direct_txns} txns)")

            # Revenue projections
            lines.append(f"    Revenue Projections:")
            for tvl, rev in opp.daily_revenue_by_tvl.items():
                apr = opp.apr_by_tvl.get(tvl, 0)
                tvl_label = f"${tvl:,.0f}"
                if tvl == opp.existing_pool_tvl:
                    tvl_label += " (actual)"
                lines.append(f"      @ {tvl_label} TVL: ${rev:,.2f}/day → APR: {apr:.2%}")

            # Other multi-hop routes
            if len(opp.multihop_routes) > 1:
                lines.append(f"    Other multi-hop routes:")
                for r in opp.multihop_routes[1:5]:  # Show up to 4 more
                    lines.append(f"      {r.get('path', 'N/A')}  (${r.get('volume', 0):,.0f}, {r.get('count', 0)} txns)")

            lines.append("")
            lines.append("-" * 90)
            lines.append("")

        return "\n".join(lines)

    def to_json(self, opportunities: List[ShortcutOpportunity], period_days: float) -> List[Dict]:
        """Convert opportunities to JSON-serializable format."""
        results = []
        for rank, opp in enumerate(opportunities, 1):
            daily_vol = opp.total_divertable_volume / period_days if period_days > 0 else 0

            results.append({
                'rank': rank,
                'pair': opp.pair_label,
                'token_a': opp.token_a,
                'token_b': opp.token_b,
                'family_a': opp.family_a,
                'family_b': opp.family_b,
                'is_cross_family': opp.is_cross_family,
                'divertable_volume': opp.total_divertable_volume,
                'divertable_txns': opp.total_divertable_txns,
                'daily_volume': daily_vol,
                'current_cumulative_fee_pct': opp.current_cumulative_fee,
                'proposed_shortcut_fee_pct': opp.proposed_shortcut_fee,
                'fee_saving_pct': opp.current_cumulative_fee - opp.proposed_shortcut_fee,
                'existing_pool': {
                    'exists': opp.existing_pool_fee is not None,
                    'fee_tier': opp.existing_pool_fee,
                    'tvl': opp.existing_pool_tvl,
                },
                'dominant_route': {
                    'path': opp.dominant_route.get('path', '') if opp.dominant_route else '',
                    'volume': opp.dominant_route.get('volume', 0) if opp.dominant_route else 0,
                    'intermediaries': opp.dominant_route.get('intermediaries', []) if opp.dominant_route else [],
                    'cumulative_fee_pct': opp.dominant_route.get('cumulative_fee_pct', 0) if opp.dominant_route else 0,
                } if opp.dominant_route else None,
                'projections': [
                    {
                        'tvl': tvl,
                        'daily_revenue': rev,
                        'apr': opp.apr_by_tvl.get(tvl, 0),
                        'is_actual_tvl': tvl == opp.existing_pool_tvl,
                    }
                    for tvl, rev in opp.daily_revenue_by_tvl.items()
                ],
                'multihop_routes': [
                    {
                        'path': r.get('path', ''),
                        'volume': r.get('volume', 0),
                        'txns': r.get('count', 0),
                        'cumulative_fee_pct': r.get('cumulative_fee_pct', 0),
                        'intermediaries': r.get('intermediaries', []),
                    }
                    for r in opp.multihop_routes
                ],
                'direct_routes': [
                    {
                        'path': r.get('path', ''),
                        'volume': r.get('volume', 0),
                        'txns': r.get('count', 0),
                    }
                    for r in opp.direct_routes
                ],
            })

        return results
