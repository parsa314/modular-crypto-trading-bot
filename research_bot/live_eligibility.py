from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from datetime import datetime, timezone
from typing import Iterable

import ccxt
import numpy as np
import pandas as pd

from .fast_scanner import FastScanConfig, scan_universe
from .universe import EligibilityPolicy, MarketListing, evaluate_listing


_STABLE_BASES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USD1"}
_LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


@dataclass(frozen=True)
class LiveEligibilityConfig:
    timeframe: str = "4h"
    history_bars: int = 650
    max_assets: int = 40
    allowed_market_types: tuple[str, ...] = ("spot",)
    allowed_quotes: tuple[str, ...] = ("USDT", "USDC", "USD")
    min_volume_24h_quote: float = 2_000_000.0
    max_spread_bps: float = 40.0
    min_history_bars: int = 500
    max_missing_fraction: float = 0.01
    max_abnormal_fraction: float = 0.01
    candidate_quantile: float = 0.80
    timeout_ms: int = 15_000


@dataclass(frozen=True)
class HistoryAudit:
    rows: int
    coverage_start: str | None
    coverage_end: str | None
    missing_fraction: float
    abnormal_fraction: float
    gap_count: int
    expected_rows: int


@dataclass(frozen=True)
class ProviderFailure:
    exchange: str
    symbol: str
    stage: str
    error_type: str
    message: str


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }
    if timeframe not in mapping:
        raise ValueError(f"Unsupported audit timeframe: {timeframe}")
    return pd.Timedelta(mapping[timeframe])


def _looks_leveraged(base: str) -> bool:
    b = str(base).upper()
    return any(b.endswith(s) and len(b) > len(s) + 1 for s in _LEVERAGED_SUFFIXES)


def _abnormal_fraction(df: pd.DataFrame) -> float:
    required = ["open", "high", "low", "close", "volume"]
    if df.empty or any(c not in df.columns for c in required):
        return 1.0
    x = df[required].apply(pd.to_numeric, errors="coerce")
    bad = (
        (~np.isfinite(x).all(axis=1))
        | (x["high"] < x[["open", "close", "low"]].max(axis=1))
        | (x["low"] > x[["open", "close", "high"]].min(axis=1))
        | (x[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (x["volume"] < 0)
    )
    return float(bad.mean())


def audit_ohlcv(df: pd.DataFrame, timeframe: str = "4h") -> HistoryAudit:
    if df.empty or "timestamp" not in df.columns:
        return HistoryAudit(0, None, None, 1.0, 1.0, 0, 0)
    x = df.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    x = x.dropna(subset=["timestamp"]).drop_duplicates("timestamp").sort_values("timestamp")
    if x.empty:
        return HistoryAudit(0, None, None, 1.0, 1.0, 0, 0)

    step = _timeframe_delta(timeframe)
    expected_rows = int(((x["timestamp"].iloc[-1] - x["timestamp"].iloc[0]) / step)) + 1
    expected_rows = max(expected_rows, len(x))
    missing_rows = max(0, expected_rows - len(x))
    missing_fraction = float(missing_rows / expected_rows) if expected_rows else 1.0
    diffs = x["timestamp"].diff().dropna()
    gap_count = int((diffs > step * 1.5).sum())
    row_missing = float(x[[c for c in ("open", "high", "low", "close", "volume") if c in x]].isna().any(axis=1).mean())
    missing_fraction = max(missing_fraction, row_missing)
    return HistoryAudit(
        rows=int(len(x)),
        coverage_start=x["timestamp"].iloc[0].isoformat(),
        coverage_end=x["timestamp"].iloc[-1].isoformat(),
        missing_fraction=missing_fraction,
        abnormal_fraction=_abnormal_fraction(x),
        gap_count=gap_count,
        expected_rows=expected_rows,
    )


def prefilter_discovery(
    listings: Iterable[MarketListing], config: LiveEligibilityConfig | None = None
) -> list[MarketListing]:
    """Select one liquid venue per asset before expensive history auditing.

    This is a discovery prefilter, not final eligibility. Missing spread is
    tolerated here because some venues require an order-book request to obtain
    it; final eligibility is evaluated only after the audit stage.
    """
    cfg = config or LiveEligibilityConfig()
    viable: list[MarketListing] = []
    for x in listings:
        if not x.active:
            continue
        if x.market_type.lower() not in cfg.allowed_market_types:
            continue
        if x.quote.upper() not in cfg.allowed_quotes:
            continue
        if x.base.upper() in _STABLE_BASES or _looks_leveraged(x.base):
            continue
        if (
            x.volume_24h_quote is None
            or not math.isfinite(x.volume_24h_quote)
            or x.volume_24h_quote < cfg.min_volume_24h_quote
        ):
            continue
        if x.spread_bps is not None and (
            not math.isfinite(x.spread_bps)
            or x.spread_bps < 0
            or x.spread_bps > cfg.max_spread_bps
        ):
            continue
        viable.append(x)

    viable.sort(
        key=lambda x: (
            float(x.volume_24h_quote or -1.0),
            -float(x.spread_bps if x.spread_bps is not None else 1e9),
        ),
        reverse=True,
    )
    chosen: dict[str, MarketListing] = {}
    for listing in viable:
        if listing.asset_id not in chosen:
            chosen[listing.asset_id] = listing
        if len(chosen) >= cfg.max_assets:
            break
    return list(chosen.values())


class CCXTLiveAuditor:
    def __init__(self, timeout_ms: int = 15_000):
        self.timeout_ms = int(timeout_ms)
        self._exchanges: dict[str, ccxt.Exchange] = {}

    def _exchange(self, exchange_id: str):
        if exchange_id not in self._exchanges:
            cls = getattr(ccxt, exchange_id, None)
            if cls is None:
                raise ValueError(f"CCXT exchange not available: {exchange_id}")
            ex = cls({"enableRateLimit": True, "timeout": self.timeout_ms})
            ex.load_markets()
            self._exchanges[exchange_id] = ex
        return self._exchanges[exchange_id]

    def fetch_bars(self, listing: MarketListing, timeframe: str, limit: int) -> pd.DataFrame:
        ex = self._exchange(listing.exchange)
        if listing.symbol not in ex.markets:
            raise ValueError(f"{listing.symbol} not found on {listing.exchange}")
        if not ex.has.get("fetchOHLCV"):
            raise RuntimeError(f"{listing.exchange} does not expose fetchOHLCV")

        target = int(limit)
        step_ms = int(ex.parse_timeframe(timeframe) * 1000)
        cursor = ex.milliseconds() - int(target * step_ms * 1.08)
        rows: list[list] = []
        last_seen = None
        request_limit = min(500, target)

        # Exchanges impose different hard page caps (for example ~300 or ~499)
        # even when a larger limit is requested. A short page is therefore not
        # evidence that history ended; continue while the timestamp advances.
        for _ in range(12):
            batch = ex.fetch_ohlcv(
                listing.symbol,
                timeframe=timeframe,
                since=cursor,
                limit=request_limit,
            )
            if not batch:
                break
            rows.extend(batch)
            new_last = int(batch[-1][0])
            if last_seen is not None and new_last <= last_seen:
                break
            last_seen = new_last
            cursor = new_last + 1
            if len({int(r[0]) for r in rows}) >= target:
                break

        if not rows:
            raise RuntimeError("No OHLCV returned")
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        return frame.drop_duplicates("timestamp").sort_values("timestamp").tail(target).reset_index(drop=True)

    def fetch_spread_bps(self, listing: MarketListing) -> float | None:
        if listing.spread_bps is not None and math.isfinite(listing.spread_bps) and listing.spread_bps >= 0:
            return float(listing.spread_bps)
        ex = self._exchange(listing.exchange)
        book = ex.fetch_order_book(listing.symbol, limit=5)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        if not (math.isfinite(bid) and math.isfinite(ask)):
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 10_000.0 if mid > 0 else float("nan")
        return float(spread) if math.isfinite(spread) else None


def run_live_eligibility_pipeline(
    listings: Iterable[MarketListing],
    config: LiveEligibilityConfig | None = None,
    auditor: CCXTLiveAuditor | None = None,
) -> dict:
    cfg = config or LiveEligibilityConfig()
    audit_client = auditor or CCXTLiveAuditor(cfg.timeout_ms)
    selected = prefilter_discovery(listings, cfg)
    failures: list[ProviderFailure] = []
    audited: list[dict] = []
    eligible_bars: dict[str, pd.DataFrame] = {}

    policy = EligibilityPolicy(
        allowed_quotes=cfg.allowed_quotes,
        allowed_market_types=cfg.allowed_market_types,
        min_volume_24h_quote=cfg.min_volume_24h_quote,
        max_spread_bps=cfg.max_spread_bps,
        min_history_bars=cfg.min_history_bars,
        max_missing_fraction=cfg.max_missing_fraction,
        max_abnormal_fraction=cfg.max_abnormal_fraction,
    )

    for listing in selected:
        try:
            bars = audit_client.fetch_bars(listing, cfg.timeframe, cfg.history_bars)
            hist = audit_ohlcv(bars, cfg.timeframe)
        except Exception as exc:
            failures.append(ProviderFailure(listing.exchange, listing.symbol, "history", type(exc).__name__, str(exc)[:500]))
            audited.append({
                "asset_id": listing.asset_id,
                "exchange": listing.exchange,
                "symbol": listing.symbol,
                "eligible": False,
                "reasons": ["HISTORY_PROVIDER_FAILURE"],
            })
            continue

        try:
            spread = audit_client.fetch_spread_bps(listing)
        except Exception as exc:
            failures.append(ProviderFailure(listing.exchange, listing.symbol, "spread", type(exc).__name__, str(exc)[:500]))
            spread = listing.spread_bps

        enriched = replace(
            listing,
            spread_bps=spread,
            history_bars=hist.rows,
            missing_fraction=hist.missing_fraction,
            abnormal_fraction=hist.abnormal_fraction,
            provenance=(listing.provenance + f"|ohlcv_audit:{datetime.now(timezone.utc).isoformat()}"),
        )
        result = evaluate_listing(enriched, policy)
        row = {
            "asset_id": enriched.asset_id,
            "exchange": enriched.exchange,
            "symbol": enriched.symbol,
            "market_type": enriched.market_type,
            "volume_24h_quote": enriched.volume_24h_quote,
            "spread_bps": enriched.spread_bps,
            "eligible": result.eligible,
            "reasons": list(result.reasons),
            "history": asdict(hist),
            "provenance": enriched.provenance,
        }
        audited.append(row)
        if result.eligible:
            eligible_bars[enriched.symbol] = bars

    scanner = scan_universe(
        eligible_bars,
        FastScanConfig(
            min_bars=cfg.min_history_bars,
            max_missing_fraction=cfg.max_missing_fraction,
            max_abnormal_fraction=cfg.max_abnormal_fraction,
            candidate_quantile=cfg.candidate_quantile,
        ),
    )
    scan_rows = scanner.to_dict(orient="records") if not scanner.empty else []
    for row in scan_rows:
        for k, v in list(row.items()):
            if isinstance(v, (np.floating, np.integer)):
                row[k] = v.item()
            elif pd.isna(v) if not isinstance(v, (dict, list, tuple, str, bool)) else False:
                row[k] = None

    rejection_counts: dict[str, int] = {}
    for row in audited:
        for reason in row.get("reasons", []):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    candidates = [r for r in scan_rows if r.get("status") == "DEEP_ANALYSIS_CANDIDATE"]
    return {
        "research_status": "LIVE_ELIGIBILITY_AND_FAST_SCAN_NOT_ENTRY_SIGNAL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(cfg),
        "selected_for_audit": len(selected),
        "audited": len(audited),
        "eligible_assets": sum(1 for x in audited if x.get("eligible")),
        "rejected_assets": sum(1 for x in audited if not x.get("eligible")),
        "provider_failures": [asdict(x) for x in failures],
        "rejection_counts": rejection_counts,
        "deep_analysis_candidates": candidates,
        "audit_rows": audited,
        "scan_rows": scan_rows,
        "scientific_warning": (
            "DEEP_ANALYSIS_CANDIDATE is a triage label only. It is not a BUY signal. "
            "No candidate may reach paper execution before OOS model, cost, uncertainty and independent risk gates."
        ),
    }
