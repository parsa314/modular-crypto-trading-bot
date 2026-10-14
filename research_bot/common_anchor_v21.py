from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from statistics import mean
from typing import Iterable

import numpy as np
import pandas as pd

from research_bot.forward_microstructure_v19 import TRADE_SIDE_SEMANTICS, summarize_trade_window


V21_PROTOCOL_VERSION = "v0.21-phase-q-common-anchor-1"


@dataclass(frozen=True)
class V21CommonAnchorConfig:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
    venues: tuple[str, ...] = ("coinex", "okx", "kucoin")
    depth_levels: int = 10
    trades_limit: int = 500
    max_trade_pages: int = 12
    trade_window_seconds: int = 60
    min_recent_trade_count: int = 5
    max_trade_staleness_seconds: float = 30.0
    max_book_age_seconds: float = 20.0
    min_signed_trade_coverage: float = 0.80
    max_spread_bps: float = 50.0
    max_mid_dispersion_bps: float = 100.0
    min_venues_per_symbol: int = 2
    allow_local_book_timestamp_fallback: bool = False


@dataclass(frozen=True)
class CommonAnchorVenueObservation:
    venue: str
    symbol: str
    anchor_at: str
    book_observed_at: str
    book_response_at: str
    book_timestamp_source: str
    best_bid: float
    best_ask: float
    bid_depth_notional: float
    ask_depth_notional: float
    trade_buy_notional: float
    trade_sell_notional: float
    trade_unknown_notional: float
    trade_count: int
    raw_trade_count: int
    future_trade_count_excluded: int
    trade_window_start: str
    trade_window_end: str
    trade_first_at: str | None
    trade_last_at: str | None
    trade_staleness_seconds: float | None
    trade_left_boundary_established: bool
    trade_fetch_pages: int
    trade_side_semantics: str
    source: str

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return (self.best_ask - self.best_bid) / m * 10_000.0 if m > 0 else float("nan")

    @property
    def depth_imbalance(self) -> float:
        d = self.bid_depth_notional + self.ask_depth_notional
        return (self.bid_depth_notional - self.ask_depth_notional) / d if d > 0 else 0.0

    @property
    def trade_imbalance(self) -> float:
        d = self.trade_buy_notional + self.trade_sell_notional
        return (self.trade_buy_notional - self.trade_sell_notional) / d if d > 0 else 0.0

    @property
    def signed_trade_coverage(self) -> float:
        d = self.trade_buy_notional + self.trade_sell_notional + self.trade_unknown_notional
        return (self.trade_buy_notional + self.trade_sell_notional) / d if d > 0 else 0.0

    @property
    def book_age_seconds(self) -> float:
        return float((_utc(self.anchor_at) - _utc(self.book_observed_at)).total_seconds())


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _finite(value) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def choose_common_anchor(book_timestamps: Iterable[str]) -> str:
    values = [_utc(x) for x in book_timestamps]
    if not values:
        raise ValueError("no book timestamps")
    return max(values).isoformat()


def build_common_anchor_observation(
    *,
    venue: str,
    symbol: str,
    anchor_at: str,
    book_observed_at: str,
    book_response_at: str,
    book_timestamp_source: str,
    bids: Iterable[Iterable[float]],
    asks: Iterable[Iterable[float]],
    trades: pd.DataFrame,
    trade_left_boundary_established: bool,
    trade_fetch_pages: int,
    source: str,
    config: V21CommonAnchorConfig | None = None,
) -> CommonAnchorVenueObservation:
    cfg = config or V21CommonAnchorConfig()
    bid_rows = list(bids)[: int(cfg.depth_levels)]
    ask_rows = list(asks)[: int(cfg.depth_levels)]
    if not bid_rows or not ask_rows:
        raise ValueError("empty order book")
    best_bid = float(bid_rows[0][0])
    best_ask = float(ask_rows[0][0])
    bid_depth = float(sum(float(p) * float(q) for p, q, *_ in bid_rows))
    ask_depth = float(sum(float(p) * float(q) for p, q, *_ in ask_rows))
    tw = summarize_trade_window(
        trades,
        observed_at=_utc(anchor_at).isoformat(),
        window_seconds=int(cfg.trade_window_seconds),
    )
    return CommonAnchorVenueObservation(
        venue=str(venue).lower(),
        symbol=str(symbol).upper(),
        anchor_at=_utc(anchor_at).isoformat(),
        book_observed_at=_utc(book_observed_at).isoformat(),
        book_response_at=_utc(book_response_at).isoformat(),
        book_timestamp_source=str(book_timestamp_source),
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth_notional=bid_depth,
        ask_depth_notional=ask_depth,
        trade_buy_notional=float(tw["trade_buy_notional"]),
        trade_sell_notional=float(tw["trade_sell_notional"]),
        trade_unknown_notional=float(tw["trade_unknown_notional"]),
        trade_count=int(tw["trade_count"]),
        raw_trade_count=int(tw["raw_trade_count"]),
        future_trade_count_excluded=int(tw["future_trade_count_excluded"]),
        trade_window_start=str(tw["trade_window_start"]),
        trade_window_end=str(tw["trade_window_end"]),
        trade_first_at=tw["trade_first_at"],
        trade_last_at=tw["trade_last_at"],
        trade_staleness_seconds=tw["trade_staleness_seconds"],
        trade_left_boundary_established=bool(trade_left_boundary_established),
        trade_fetch_pages=max(0, int(trade_fetch_pages)),
        trade_side_semantics=str(tw["trade_side_semantics"]),
        source=source,
    )


def validate_common_anchor_observation(
    obs: CommonAnchorVenueObservation,
    config: V21CommonAnchorConfig | None = None,
) -> list[str]:
    cfg = config or V21CommonAnchorConfig()
    issues: list[str] = []
    if obs.symbol not in set(cfg.symbols):
        issues.append("UNCONFIGURED_SYMBOL")
    if obs.venue not in set(cfg.venues):
        issues.append("UNCONFIGURED_VENUE")
    if not obs.source:
        issues.append("MISSING_SOURCE")
    if not _finite(obs.best_bid) or not _finite(obs.best_ask) or obs.best_bid <= 0 or obs.best_ask <= 0:
        issues.append("INVALID_TOP_OF_BOOK")
    elif obs.best_ask < obs.best_bid:
        issues.append("CROSSED_BOOK")
    if not _finite(obs.spread_bps) or obs.spread_bps < 0:
        issues.append("INVALID_SPREAD")
    elif obs.spread_bps > cfg.max_spread_bps:
        issues.append("SPREAD_TOO_WIDE")
    if min(obs.bid_depth_notional, obs.ask_depth_notional, obs.trade_buy_notional, obs.trade_sell_notional, obs.trade_unknown_notional) < 0:
        issues.append("NEGATIVE_NOTIONAL")
    if obs.trade_side_semantics != TRADE_SIDE_SEMANTICS:
        issues.append("UNDECLARED_TRADE_SIDE_SEMANTICS")
    if not cfg.allow_local_book_timestamp_fallback and obs.book_timestamp_source != "provider":
        issues.append("LOCAL_BOOK_TIMESTAMP_FALLBACK_NOT_ALLOWED")

    try:
        anchor = _utc(obs.anchor_at)
        book = _utc(obs.book_observed_at)
        response = _utc(obs.book_response_at)
        age = float((anchor - book).total_seconds())
        if age < -1e-6:
            issues.append("BOOK_AFTER_COMMON_ANCHOR")
        elif age > cfg.max_book_age_seconds:
            issues.append("BOOK_TOO_OLD_AT_COMMON_ANCHOR")
        if response < book - pd.Timedelta(seconds=5):
            issues.append("BOOK_RESPONSE_PRECEDES_BOOK_TIMESTAMP")
    except Exception:
        issues.append("INVALID_BOOK_OR_ANCHOR_TIMESTAMP")
        anchor = None

    if anchor is not None:
        expected_start = anchor - pd.Timedelta(seconds=int(cfg.trade_window_seconds))
        try:
            if abs((_utc(obs.trade_window_end) - anchor).total_seconds()) > 1e-6:
                issues.append("TRADE_WINDOW_END_NOT_COMMON_ANCHOR")
            if abs((_utc(obs.trade_window_start) - expected_start).total_seconds()) > 1e-6:
                issues.append("TRADE_WINDOW_START_MISMATCH")
            if obs.trade_first_at is not None and _utc(obs.trade_first_at) < expected_start:
                issues.append("TRADE_BEFORE_WINDOW")
            if obs.trade_last_at is not None and _utc(obs.trade_last_at) > anchor:
                issues.append("FUTURE_TRADE_LEAKAGE")
        except Exception:
            issues.append("INVALID_TRADE_WINDOW_TIMESTAMP")

    if not obs.trade_left_boundary_established:
        issues.append("TRADE_WINDOW_BOUNDARY_NOT_ESTABLISHED")
    if obs.trade_count < int(cfg.min_recent_trade_count):
        issues.append("INSUFFICIENT_RECENT_TRADES")
    if obs.trade_staleness_seconds is None or not _finite(obs.trade_staleness_seconds):
        issues.append("TRADE_STALENESS_UNKNOWN")
    elif float(obs.trade_staleness_seconds) > cfg.max_trade_staleness_seconds:
        issues.append("STALE_RECENT_TRADES")
    if obs.signed_trade_coverage < cfg.min_signed_trade_coverage:
        issues.append("SIGNED_TRADE_COVERAGE_INSUFFICIENT")
    if not -1.0000001 <= obs.depth_imbalance <= 1.0000001:
        issues.append("INVALID_DEPTH_IMBALANCE")
    if not -1.0000001 <= obs.trade_imbalance <= 1.0000001:
        issues.append("INVALID_TRADE_IMBALANCE")
    return sorted(set(issues))


def _missing_symbol_row(symbol: str, cfg: V21CommonAnchorConfig) -> dict:
    return {
        "symbol": symbol,
        "status": "NO_VALID_COMMON_ANCHOR_OBSERVATIONS",
        "accepted_venues": 0,
        "accepted_venue_names": [],
        "required_venues": int(cfg.min_venues_per_symbol),
        "feature_authorized": False,
        "quality_flags": ["NO_VALID_COMMON_ANCHOR_OBSERVATIONS"],
        "venues": [],
        "rejected": [],
    }


def aggregate_common_anchor_symbol(
    observations: Iterable[CommonAnchorVenueObservation],
    config: V21CommonAnchorConfig | None = None,
) -> dict:
    cfg = config or V21CommonAnchorConfig()
    rows = list(observations)
    if not rows:
        raise ValueError("no observations")
    symbols = {x.symbol for x in rows}
    if len(symbols) != 1:
        raise ValueError("one symbol required")
    symbol = next(iter(symbols))

    accepted: list[tuple[CommonAnchorVenueObservation, dict]] = []
    rejected: list[dict] = []
    for obs in rows:
        record = {
            **asdict(obs),
            "book_age_seconds": obs.book_age_seconds,
            "mid": obs.mid,
            "spread_bps": obs.spread_bps,
            "depth_imbalance": obs.depth_imbalance,
            "reported_trade_imbalance": obs.trade_imbalance,
            "signed_trade_coverage": obs.signed_trade_coverage,
        }
        issues = validate_common_anchor_observation(obs, cfg)
        if issues:
            record["quality_issues"] = issues
            rejected.append(record)
        else:
            accepted.append((obs, record))

    venue_names = [x.venue for x, _ in accepted]
    duplicate_venues = sorted({v for v in venue_names if venue_names.count(v) > 1})
    unique_names = sorted(set(venue_names))
    quality_flags: list[str] = []
    if duplicate_venues:
        quality_flags.append("DUPLICATE_VENUE_OBSERVATION")
    if len(unique_names) < int(cfg.min_venues_per_symbol):
        quality_flags.append("INSUFFICIENT_UNIQUE_VENUE_COVERAGE")

    if not accepted:
        out = _missing_symbol_row(symbol, cfg)
        out["status"] = "NO_ACCEPTED_VENUES"
        out["quality_flags"] = sorted(set(out["quality_flags"] + quality_flags))
        out["rejected"] = rejected
        return out

    anchors = {_utc(x.anchor_at).isoformat() for x, _ in accepted}
    starts = {_utc(x.trade_window_start).isoformat() for x, _ in accepted}
    ends = {_utc(x.trade_window_end).isoformat() for x, _ in accepted}
    if len(anchors) != 1 or len(starts) != 1 or len(ends) != 1:
        quality_flags.append("CROSS_VENUE_COMMON_WINDOW_MISMATCH")

    mids = np.asarray([x.mid for x, _ in accepted], dtype=float)
    median_mid = float(np.median(mids))
    mid_dispersion_bps = float((mids.max() - mids.min()) / median_mid * 10_000.0) if median_mid > 0 else float("inf")
    if mid_dispersion_bps > cfg.max_mid_dispersion_bps:
        quality_flags.append("CROSS_VENUE_MID_DISPERSION_TOO_LARGE")

    eligible_flow = [x.trade_imbalance for x, _ in accepted if x.signed_trade_coverage >= cfg.min_signed_trade_coverage]
    if len({x.venue for x, _ in accepted if x.signed_trade_coverage >= cfg.min_signed_trade_coverage}) < cfg.min_venues_per_symbol:
        quality_flags.append("SIGNED_TRADE_COVERAGE_INSUFFICIENT")

    signs = [int(np.sign(x)) for x in eligible_flow if x != 0]
    sign_agreement = float(abs(sum(signs)) / len(signs)) if signs else 0.0
    feature_authorized = not quality_flags
    return {
        "symbol": symbol,
        "status": "OK" if feature_authorized else "QUALITY_GATED",
        "common_anchor_at": next(iter(anchors)) if len(anchors) == 1 else None,
        "trade_window_start": next(iter(starts)) if len(starts) == 1 else None,
        "trade_window_end": next(iter(ends)) if len(ends) == 1 else None,
        "accepted_venues": len(unique_names),
        "accepted_venue_names": unique_names,
        "required_venues": int(cfg.min_venues_per_symbol),
        "median_mid": median_mid,
        "mid_dispersion_bps": mid_dispersion_bps,
        "mean_spread_bps": float(mean([x.spread_bps for x, _ in accepted])),
        "mean_depth_imbalance": float(mean([x.depth_imbalance for x, _ in accepted])),
        "mean_reported_trade_imbalance": float(mean(eligible_flow)) if eligible_flow else None,
        "trade_sign_agreement": sign_agreement,
        "max_book_age_seconds": float(max(x.book_age_seconds for x, _ in accepted)),
        "max_trade_staleness_seconds": float(max(float(x.trade_staleness_seconds or 0.0) for x, _ in accepted)),
        "trade_side_semantics": TRADE_SIDE_SEMANTICS,
        "feature_authorized": feature_authorized,
        "quality_flags": sorted(set(quality_flags)),
        "duplicate_venue_names": duplicate_venues,
        "venues": [record for _, record in accepted],
        "rejected": rejected,
    }


def build_common_anchor_snapshot(
    observations: Iterable[CommonAnchorVenueObservation],
    config: V21CommonAnchorConfig | None = None,
) -> dict:
    cfg = config or V21CommonAnchorConfig()
    obs = list(observations)
    by_symbol: dict[str, list[CommonAnchorVenueObservation]] = {}
    for row in obs:
        by_symbol.setdefault(row.symbol, []).append(row)
    symbol_rows: list[dict] = []
    for symbol in cfg.symbols:
        rows = by_symbol.get(symbol, [])
        symbol_rows.append(aggregate_common_anchor_symbol(rows, cfg) if rows else _missing_symbol_row(symbol, cfg))
    complete = [x for x in symbol_rows if x.get("feature_authorized")]
    payload = {
        "version": "v0.21",
        "research_status": "PROSPECTIVE_COMMON_ANCHOR_MICROSTRUCTURE_COLLECTION_ONLY",
        "measurement_contract": {
            "primary_trading_horizon": "4h",
            "snapshot_measurement_frequency_target": "30min",
            "reported_trade_window_seconds": int(cfg.trade_window_seconds),
            "trade_window_anchor_mode": "single_common_anchor_per_symbol_cycle",
            "book_age_measured_separately": True,
            "trade_side_semantics": TRADE_SIDE_SEMANTICS,
            "rest_snapshot_not_event_stream": True,
        },
        "config": asdict(cfg),
        "symbols": symbol_rows,
        "authorized_symbol_count": len(complete),
        "signal_authorized": False,
        "paper_strategy_replacement_authorized": False,
        "testnet_promotion_authorized": False,
        "live_execution_authorized": False,
        "claim_scope": "prospective common-anchor public REST microstructure measurement only; no alpha claim before a separately frozen predictive protocol",
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    payload["pre_metadata_sha256"] = sha256(raw).hexdigest()
    return payload
