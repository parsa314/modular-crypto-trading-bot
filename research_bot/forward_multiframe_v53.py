from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import pandas as pd

from .candle_time_v53 import CandleTimeContractV53, closed_bar_snapshot_v53
from .coinex_public import fetch_coinex_klines
from .confluence_v53 import ConfluenceConfigV53, decide_confluence_v53
from .multitimeframe_v53 import build_multitimeframe_feature_frame_v53


STRATEGY_VERSION_V53 = "MULTIFRAME_CONFLUENCE_V53_RESEARCH"


@dataclass(frozen=True)
class ForwardMultiframeConfigV53:
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT")
    decision_period: str = "1hour"
    higher_period: str = "4hour"
    decision_timeframe: str = "1h"
    higher_timeframe: str = "4h"
    decision_bars: int = 520
    higher_bars: int = 260
    publication_lag_seconds: int = 0
    minimum_family_quorum: int = 3
    minimum_score: float = 0.67


class MultiframeMarketClientV53(Protocol):
    def klines(self, symbol: str, period: str, bars: int) -> pd.DataFrame: ...


class CoinExMultiframeMarketClientV53:
    def klines(self, symbol: str, period: str, bars: int) -> pd.DataFrame:
        return fetch_coinex_klines(symbol, period=period, market_type="spot", bars=bars)


def _closed(raw: pd.DataFrame, timeframe: str, now: datetime, lag_seconds: int) -> pd.DataFrame:
    return closed_bar_snapshot_v53(
        raw,
        decision_time=pd.Timestamp(now),
        contract=CandleTimeContractV53(
            timeframe=timeframe,
            publication_lag=pd.Timedelta(seconds=lag_seconds),
        ),
    )


def observe_multiframe_symbol_v53(
    symbol: str,
    *,
    now: datetime | None = None,
    config: ForwardMultiframeConfigV53 | None = None,
    market_client: MultiframeMarketClientV53 | None = None,
    store=None,
) -> dict:
    """Observe real public market data and emit a research-only MTF candidate.

    This function never submits an order. It is deliberately separated from the
    execution engine so a feature/confluence experiment cannot bypass the frozen
    RiskEngine / execution gates.
    """

    cfg = config or ForwardMultiframeConfigV53()
    market = market_client or CoinExMultiframeMarketClientV53()
    now = now or datetime.now(timezone.utc)

    one_raw = market.klines(symbol, cfg.decision_period, cfg.decision_bars)
    four_raw = market.klines(symbol, cfg.higher_period, cfg.higher_bars)
    one = _closed(one_raw, cfg.decision_timeframe, now, cfg.publication_lag_seconds)
    four = _closed(four_raw, cfg.higher_timeframe, now, cfg.publication_lag_seconds)
    if len(one) < 160 or len(four) < 80:
        return {
            "symbol": symbol,
            "status": "INSUFFICIENT_CLOSED_BARS",
            "decision_rows": int(len(one)),
            "higher_rows": int(len(four)),
            "execution_authorized": False,
        }

    one = one[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    four = four[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    one["asset"] = symbol
    four["asset"] = symbol

    features = build_multitimeframe_feature_frame_v53(
        {cfg.decision_timeframe: one, cfg.higher_timeframe: four},
        decision_timeframe=cfg.decision_timeframe,
        publication_lags={
            cfg.decision_timeframe: pd.Timedelta(seconds=cfg.publication_lag_seconds),
            cfg.higher_timeframe: pd.Timedelta(seconds=cfg.publication_lag_seconds),
        },
    )
    latest = features.iloc[-1]
    decision = decide_confluence_v53(
        latest,
        ConfluenceConfigV53(
            minimum_family_quorum=cfg.minimum_family_quorum,
            minimum_score=cfg.minimum_score,
        ),
    )
    payload = {
        "observed_at": now.isoformat(),
        "symbol": symbol,
        "strategy_version": STRATEGY_VERSION_V53,
        "status": "RESEARCH_OBSERVED",
        "bar_timestamp": pd.Timestamp(latest["timestamp"]).isoformat(),
        "decision": decision.to_dict(),
        "execution_authorized": False,
        "paper_execution": False,
        "live_execution": False,
        "features": {
            "smc_structure_state": float(latest.get("smc_structure_state", 0.0)),
            "brooks_always_in": float(latest.get("brooks_always_in", 0.0)),
            "ichi_tk_bullish": float(latest.get("ichi_tk_bullish", 0.0)),
            "ichi_price_above_visible_cloud": latest.get("ichi_price_above_visible_cloud"),
            "ichi_projected_cloud_bullish": latest.get("ichi_projected_cloud_bullish"),
            "4h_smc_structure_state": latest.get("4h_smc_structure_state"),
            "4h_brooks_always_in": latest.get("4h_brooks_always_in"),
            "4h_ichi_projected_cloud_bullish": latest.get("4h_ichi_projected_cloud_bullish"),
            "4h_available_at": (
                pd.Timestamp(latest["4h_available_at"]).isoformat()
                if "4h_available_at" in latest and pd.notna(latest["4h_available_at"])
                else None
            ),
        },
    }
    if store is not None:
        store.record_observation(payload)
    return payload


def observe_multiframe_universe_v53(
    *,
    now: datetime | None = None,
    config: ForwardMultiframeConfigV53 | None = None,
    market_client: MultiframeMarketClientV53 | None = None,
    store=None,
) -> list[dict]:
    cfg = config or ForwardMultiframeConfigV53()
    return [
        observe_multiframe_symbol_v53(
            symbol,
            now=now,
            config=cfg,
            market_client=market_client,
            store=store,
        )
        for symbol in cfg.symbols
    ]
