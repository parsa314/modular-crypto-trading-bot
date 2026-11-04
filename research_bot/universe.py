from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class MarketListing:
    exchange: str
    symbol: str
    base: str
    quote: str
    market_type: str
    active: bool
    volume_24h_quote: float | None = None
    spread_bps: float | None = None
    history_bars: int | None = None
    missing_fraction: float | None = None
    abnormal_fraction: float | None = None
    market_cap_usd: float | None = None
    provenance: str = ""

    @property
    def asset_id(self) -> str:
        return self.base.upper()


@dataclass(frozen=True)
class EligibilityPolicy:
    allowed_quotes: tuple[str, ...] = ("USDT", "USDC", "USD")
    allowed_market_types: tuple[str, ...] = ("spot", "perpetual")
    min_volume_24h_quote: float = 1_000_000.0
    max_spread_bps: float = 40.0
    min_history_bars: int = 500
    max_missing_fraction: float = 0.01
    max_abnormal_fraction: float = 0.01
    # Explicit fiat-pegged bases are excluded from alpha ranking.  This list is
    # intentionally conservative and reviewable; newly discovered stable bases
    # must be added with evidence rather than inferred from ticker shape alone.
    exclude_stable_bases: tuple[str, ...] = (
        "USDT",
        "USDC",
        "DAI",
        "FDUSD",
        "TUSD",
        "USDE",
        "PYUSD",
        "USD1",
        "USDG",
        "USDS",
        "USDP",
        "BUSD",
        "GUSD",
        "FRAX",
        "LUSD",
        "USDD",
        "EURC",
    )
    leveraged_suffixes: tuple[str, ...] = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


@dataclass(frozen=True)
class EligibilityResult:
    listing: MarketListing
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CoverageReport:
    total_discovered: int
    active_tradable: int
    eligible_listings: int
    eligible_assets: int
    rejected_listings: int
    eligible_market_cap_coverage: float | None
    market_cap_status: str


def _looks_leveraged(base: str, suffixes: tuple[str, ...]) -> bool:
    b = base.upper()
    return any(b.endswith(s) and len(b) > len(s) + 1 for s in suffixes)


def _finite_nonnegative(value: float | None) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number >= 0


def evaluate_listing(listing: MarketListing, policy: EligibilityPolicy | None = None) -> EligibilityResult:
    p = policy or EligibilityPolicy()
    reasons: list[str] = []
    base = listing.base.upper()
    quote = listing.quote.upper()
    mtype = listing.market_type.lower()

    if not listing.active:
        reasons.append("INACTIVE")
    if quote not in p.allowed_quotes:
        reasons.append("QUOTE_NOT_ALLOWED")
    if mtype not in p.allowed_market_types:
        reasons.append("MARKET_TYPE_NOT_ALLOWED")
    if base in p.exclude_stable_bases:
        reasons.append("STABLECOIN_BASE")
    if _looks_leveraged(base, p.leveraged_suffixes):
        reasons.append("LEVERAGED_TOKEN")

    if listing.volume_24h_quote is None:
        reasons.append("MISSING_VOLUME")
    elif not _finite_nonnegative(listing.volume_24h_quote):
        reasons.append("INVALID_VOLUME")
    elif listing.volume_24h_quote < p.min_volume_24h_quote:
        reasons.append("LOW_LIQUIDITY")

    if listing.spread_bps is None:
        reasons.append("MISSING_SPREAD")
    elif not _finite_nonnegative(listing.spread_bps):
        reasons.append("INVALID_SPREAD")
    elif listing.spread_bps > p.max_spread_bps:
        reasons.append("SPREAD_TOO_WIDE")

    if listing.history_bars is None:
        reasons.append("MISSING_HISTORY")
    elif listing.history_bars < p.min_history_bars:
        reasons.append("INSUFFICIENT_HISTORY")

    if listing.missing_fraction is None:
        reasons.append("MISSING_DATA_QUALITY_AUDIT")
    elif not _finite_nonnegative(listing.missing_fraction) or listing.missing_fraction > 1:
        reasons.append("INVALID_MISSING_FRACTION")
    elif listing.missing_fraction > p.max_missing_fraction:
        reasons.append("TOO_MANY_MISSING_BARS")

    if listing.abnormal_fraction is None:
        reasons.append("MISSING_ABNORMAL_CANDLE_AUDIT")
    elif not _finite_nonnegative(listing.abnormal_fraction) or listing.abnormal_fraction > 1:
        reasons.append("INVALID_ABNORMAL_FRACTION")
    elif listing.abnormal_fraction > p.max_abnormal_fraction:
        reasons.append("TOO_MANY_ABNORMAL_BARS")

    return EligibilityResult(listing=listing, eligible=not reasons, reasons=tuple(reasons))


def choose_canonical_listing(results: Iterable[EligibilityResult]) -> dict[str, EligibilityResult]:
    """Deduplicate an asset across venues while preserving provenance.

    Preference order: eligible > ineligible, then greater 24h quote volume,
    then tighter spread.  This is intentionally transparent and deterministic;
    ranking models may later replace it only after out-of-sample validation.
    """

    chosen: dict[str, EligibilityResult] = {}
    for result in results:
        key = result.listing.asset_id
        current = chosen.get(key)
        if current is None:
            chosen[key] = result
            continue
        a = result
        b = current
        a_key = (
            int(a.eligible),
            float(a.listing.volume_24h_quote or -1.0),
            -float(a.listing.spread_bps if a.listing.spread_bps is not None else 1e12),
        )
        b_key = (
            int(b.eligible),
            float(b.listing.volume_24h_quote or -1.0),
            -float(b.listing.spread_bps if b.listing.spread_bps is not None else 1e12),
        )
        if a_key > b_key:
            chosen[key] = a
    return chosen


def coverage_report(
    listings: Iterable[MarketListing],
    *,
    policy: EligibilityPolicy | None = None,
    global_market_cap_usd: float | None = None,
) -> tuple[list[EligibilityResult], CoverageReport]:
    rows = list(listings)
    results = [evaluate_listing(x, policy) for x in rows]
    canonical = choose_canonical_listing(results)
    eligible_assets = [x for x in canonical.values() if x.eligible]
    eligible_mcap_values = [x.listing.market_cap_usd for x in eligible_assets]

    if global_market_cap_usd is None or global_market_cap_usd <= 0:
        mcap_coverage = None
        mcap_status = "DATA_UNAVAILABLE"
    elif any(v is None for v in eligible_mcap_values):
        mcap_coverage = None
        mcap_status = "INCOMPLETE_DENOMINATOR_OR_NUMERATOR"
    else:
        mcap_coverage = float(sum(float(v) for v in eligible_mcap_values) / global_market_cap_usd)
        mcap_status = "AVAILABLE"

    report = CoverageReport(
        total_discovered=len(rows),
        active_tradable=sum(1 for x in rows if x.active),
        eligible_listings=sum(1 for x in results if x.eligible),
        eligible_assets=len(eligible_assets),
        rejected_listings=sum(1 for x in results if not x.eligible),
        eligible_market_cap_coverage=mcap_coverage,
        market_cap_status=mcap_status,
    )
    return results, report
