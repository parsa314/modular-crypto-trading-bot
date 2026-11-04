from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research_bot.fast_scanner import FastScanConfig, scan_symbol
from research_bot.live_eligibility import audit_ohlcv, prefilter_discovery
from research_bot.market_discovery import normalize_market_listing
from research_bot.universe import EligibilityPolicy, MarketListing, evaluate_listing


def _valid_listing() -> MarketListing:
    return MarketListing(
        exchange="fixture",
        symbol="BTC/USDT",
        base="BTC",
        quote="USDT",
        market_type="spot",
        active=True,
        volume_24h_quote=10_000_000.0,
        spread_bps=5.0,
        history_bars=600,
        missing_fraction=0.0,
        abnormal_fraction=0.0,
    )


@pytest.mark.parametrize(
    ("field", "invalid_value", "reason"),
    [
        ("volume_24h_quote", float("nan"), "INVALID_VOLUME"),
        ("volume_24h_quote", float("inf"), "INVALID_VOLUME"),
        ("volume_24h_quote", -1.0, "INVALID_VOLUME"),
        ("spread_bps", float("nan"), "INVALID_SPREAD"),
        ("spread_bps", float("inf"), "INVALID_SPREAD"),
        ("spread_bps", -2.0, "INVALID_SPREAD"),
        ("missing_fraction", float("nan"), "INVALID_MISSING_FRACTION"),
        ("abnormal_fraction", float("-inf"), "INVALID_ABNORMAL_FRACTION"),
        ("missing_fraction", 1.2, "INVALID_MISSING_FRACTION"),
    ],
)
def test_universe_rejects_invalid_risk_and_liquidity_inputs(field, invalid_value, reason):
    result = evaluate_listing(replace(_valid_listing(), **{field: invalid_value}))
    assert not result.eligible
    assert reason in result.reasons


def test_valid_listing_remains_eligible():
    assert evaluate_listing(_valid_listing()).eligible


def test_discovery_converts_nonfinite_quote_and_spread_to_missing():
    market = {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "spot": True, "active": True}
    listing = normalize_market_listing(
        exchange_id="fixture",
        market=market,
        ticker={"quoteVolume": float("inf"), "bid": float("nan"), "ask": 100.0},
    )
    assert listing.volume_24h_quote is None
    assert listing.spread_bps is None
    assert prefilter_discovery([listing]) == []


def test_quote_volume_fallback_rejects_nonfinite_product_and_accepts_finite():
    market = {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "spot": True}
    listing = normalize_market_listing(
        exchange_id="fixture", market=market,
        ticker={"quoteVolume": float("nan"), "baseVolume": 100.0, "last": 100_000.0},
    )
    assert listing.volume_24h_quote == 10_000_000.0


def _valid_bars(n: int = 320) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
        "open": np.full(n, 100.0),
        "high": np.full(n, 102.0),
        "low": np.full(n, 98.0),
        "close": np.full(n, 101.0),
        "volume": np.full(n, 1_000.0),
    })


def test_audit_counts_infinite_candles_as_abnormal():
    bars = _valid_bars()
    bars.loc[100, "close"] = float("inf")
    bars.loc[101, "volume"] = float("-inf")
    audit = audit_ohlcv(bars, "4h")
    assert audit.abnormal_fraction == pytest.approx(2 / len(bars))


def test_fast_scanner_rejects_invalid_latest_candle_even_below_fraction_threshold():
    bars = _valid_bars()
    bars.loc[len(bars) - 1, "close"] = float("inf")
    result = scan_symbol("BTC/USDT", bars, FastScanConfig(min_bars=240, max_abnormal_fraction=0.01))
    assert result.status == "REJECT"
    assert "LATEST_CANDLE_INVALID" in result.rejection_reasons
    assert result.raw_score is None


def test_prefilter_rejects_direct_nonfinite_listings():
    assert prefilter_discovery([replace(_valid_listing(), volume_24h_quote=float("inf"))]) == []
    assert prefilter_discovery([replace(_valid_listing(), spread_bps=float("nan"))]) == []
