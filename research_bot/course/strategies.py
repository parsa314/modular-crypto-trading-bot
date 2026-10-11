"""Causal reconstructions of course rules; immutable hypotheses, never orders."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contracts import BookSnapshot, HypothesisCandidate, aware, digest

CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "course_strategies_v1.json"
STRATEGY_IDS = (
    "sma_crossover", "bollinger_reversion", "rsi_reversal", "donchian_breakout",
    "macd_crossover", "technical_confluence", "simple_scalper",
    "order_book_imbalance", "rsi_moving_average",
)


def strategy_ids() -> tuple[str, ...]:
    return STRATEGY_IDS


def load_strategy_config(path: str | Path | None = None) -> dict[str, Any]:
    """Validate versioned parameters before any strategy can consume them."""
    config = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
    if config.get("schema") != "COURSE_STRATEGIES_V1":
        raise ValueError("UNSUPPORTED_STRATEGY_CONFIG")
    if set(config.get("strategies", {})) != set(STRATEGY_IDS):
        raise ValueError("MISSING_OR_UNKNOWN_STRATEGY")
    for strategy_id, strategy in config["strategies"].items():
        if type(strategy.get("enabled")) is not bool or strategy.get("status") != "RESEARCH_CANDIDATE":
            raise ValueError("INVALID_STRATEGY_GOVERNANCE")
        for param in strategy["parameters"].values():
            required = {"value", "type", "unit", "range", "reference", "source", "version",
                        "method", "scientific_status"}
            if not required <= param.keys() or param["scientific_status"] != "UNVERIFIED":
                raise ValueError("INCOMPLETE_PARAMETER_GOVERNANCE")
            value = param["value"]
            if param["type"] == "integer":
                valid = type(value) is int
            elif param["type"] == "number":
                valid = type(value) in (int, float) and np.isfinite(value)
            else:
                valid = False
            bounds = param["range"]
            if not valid or len(bounds) != 2 or not bounds[0] <= value <= bounds[1]:
                raise ValueError("INVALID_PARAMETER_VALUE")
        values = {k: v["value"] for k, v in strategy["parameters"].items()}
        integer_names = {"fast", "slow", "window", "signal", "rsi_window", "ema_window",
                         "ma_window", "warmup", "max_age_seconds", "depth", "timeout_bars"}
        for name in integer_names.intersection(values):
            if type(values[name]) is not int or values[name] < 1:
                raise ValueError("INVALID_INTEGER_ALGORITHM_PARAMETER")
        minimum = {
            "sma_crossover": values.get("slow", 0) + 1,
            "bollinger_reversion": max(2, values.get("window", 0)),
            "rsi_reversal": values.get("window", 0) + 1,
            "donchian_breakout": values.get("window", 0) + 1,
            "macd_crossover": max(2, values.get("slow", 0) + values.get("signal", 0)),
            "technical_confluence": max(values.get("ema_window", 0) + 1,
                                         values.get("rsi_window", 0) + 1),
            "simple_scalper": 1,
            "order_book_imbalance": 1,
            "rsi_moving_average": max(values.get("ma_window", 0) + 1,
                                      values.get("rsi_window", 0) + 1),
        }[strategy_id]
        if values.get("warmup", 0) < minimum:
            raise ValueError("INSUFFICIENT_CONFIGURED_WARMUP")
        if "fast" in values and values["fast"] >= values["slow"]:
            raise ValueError("INVALID_FAST_SLOW_ORDER")
        if "oversold" in values and "overbought" in values and values["oversold"] >= values["overbought"]:
            raise ValueError("INVALID_RSI_THRESHOLD_ORDER")
    return config


def rsi_adjusted(prices: pd.Series, window: int = 14) -> pd.Series:
    """Notebook 5 adjusted EWM(alpha=1/window), not Wilder's seeded RSI."""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / window, adjust=True).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / window, adjust=True).mean()
    return 100 - 100 / (1 + gain / loss)


def rsi_simple(prices: pd.Series, window: int = 14) -> pd.Series:
    """Notebook 6 trailing simple-average RSI; flat windows undefined, not 50."""
    delta = prices.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    return 100 - 100 / (1 + gain / loss)


def macd(prices: pd.Series, fast: int, slow: int, signal: int,
         *, price_adjust: bool) -> tuple[pd.Series, pd.Series]:
    line = prices.ewm(span=fast, adjust=price_adjust).mean() - prices.ewm(span=slow, adjust=price_adjust).mean()
    return line, line.ewm(span=signal, adjust=True).mean()


def validated_candles(frame: pd.DataFrame, decision_time: datetime,
                      *, max_age_seconds: int) -> pd.DataFrame:
    """Reject invalid inputs wholesale. Caller must supply the observed prefix."""
    aware(decision_time)
    names = ["close_time", "available_at", "open", "high", "low", "close", "volume"]
    if not isinstance(frame, pd.DataFrame) or frame.empty or not set(names) <= set(frame):
        raise ValueError("INVALID_CANDLE_SCHEMA")
    data = frame[names].copy(deep=True)
    for name in ("close_time", "available_at"):
        converted = []
        for value in data[name]:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise ValueError("NAIVE_OR_INVALID_CANDLE_TIMESTAMP")
            converted.append(stamp.tz_convert("UTC"))
        data[name] = converted
    if data.close_time.duplicated().any() or not data.close_time.is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_UNSORTED_CANDLES")
    if (data.close_time > data.available_at).any() or (data.available_at > decision_time).any():
        raise ValueError("POINT_IN_TIME_CANDLE_UNAVAILABLE")
    if "quality_status" in frame and not frame.quality_status.eq("VALID").all():
        raise ValueError("INVALID_CANDLE_QUALITY")
    if "revision" in frame and not frame.revision.eq(0).all():
        raise ValueError("REVISED_CANDLES_REQUIRE_NEW_PROTOCOL")
    if "is_closed" in frame and not frame.is_closed.eq(True).all():
        raise ValueError("OPEN_CANDLE_REJECTED")
    if "open_time" in frame:
        for start, end in zip(frame.open_time, data.close_time):
            stamp = pd.Timestamp(start)
            if pd.isna(stamp) or stamp.tzinfo is None or stamp >= end:
                raise ValueError("INVALID_CANDLE_OPEN_TIME")
    numeric = data[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or (numeric[:, :4] <= 0).any() or (numeric[:, 4] < 0).any():
        raise ValueError("INVALID_CANDLE_NUMERIC_VALUE")
    if ((numeric[:, 1] < np.maximum(numeric[:, 0], numeric[:, 3])) |
        (numeric[:, 2] > np.minimum(numeric[:, 0], numeric[:, 3])) |
        (numeric[:, 1] < numeric[:, 2])).any():
        raise ValueError("INVALID_OHLC_GEOMETRY")
    if decision_time - data.close_time.iloc[-1].to_pydatetime() > timedelta(seconds=max_age_seconds):
        raise ValueError("STALE_CANDLES")
    return data.reset_index(drop=True)


class CourseStrategy:
    """Independent disabled-by-default rule adapter satisfying StrategyProtocol.

    Direction zero records an evaluated standaside; warm-up returns no candidate.
    Unknown edge/confidence remain unknown and cannot become admitted signals.
    """

    def __init__(self, strategy_id: str, config_path: str | Path | None = None,
                 *, enabled: bool | None = None) -> None:
        if strategy_id not in STRATEGY_IDS:
            raise ValueError("UNKNOWN_COURSE_STRATEGY")
        document = load_strategy_config(config_path)
        self.strategy_id = strategy_id
        self._config = deepcopy(document["strategies"][strategy_id])
        self._version = document["version"]
        if enabled is not None:
            if type(enabled) is not bool:
                raise ValueError("INVALID_ENABLE_OVERRIDE")
            self._config["enabled"] = enabled
        self.config_hash = digest({"schema": document["schema"], "version": self._version,
                                   "strategy_id": strategy_id, "strategy": self._config,
                                   "implementation_hash": digest(Path(__file__).read_text(encoding="utf-8"))})

    @property
    def enabled(self) -> bool:
        return bool(self._config["enabled"])

    @property
    def parameters(self) -> dict[str, int | float]:
        return {name: spec["value"] for name, spec in self._config["parameters"].items()}

    def generate(self, frame: Any, *, symbol: str, venue: str,
                 decision_time: datetime) -> list[HypothesisCandidate]:
        aware(decision_time)
        if not self.enabled:
            return []
        p = self.parameters
        if self.strategy_id == "order_book_imbalance":
            if not isinstance(frame, BookSnapshot) or (symbol, venue) != (frame.market.symbol, frame.market.venue):
                raise ValueError("INVALID_BOOK_MARKET_IDENTITY")
            frame.validate_at(decision_time, max_age=timedelta(seconds=p["max_age_seconds"]))
            depth = int(p["depth"])
            if len(frame.bids) < depth or len(frame.asks) < depth:
                raise ValueError("INSUFFICIENT_ORDER_BOOK_DEPTH")
            bid = sum((size for _, size in frame.bids[:depth]), Decimal(0))
            ask = sum((size for _, size in frame.asks[:depth]), Decimal(0))
            imbalance = (bid - ask) / (bid + ask)
            side = int(imbalance > Decimal(str(p["threshold"])))
            snapshot = frame.snapshot_hash
            available_at, observed_at = frame.available_at, frame.observed_at
            refs = ("Python_6_Build_Bot.ipynb:cell8", f"available_at={frame.available_at.isoformat()}")
        else:
            data = validated_candles(frame, decision_time, max_age_seconds=int(p["max_age_seconds"]))
            if len(data) < int(p["warmup"]):
                return []
            side = self._direction(data, p)
            # Undefined flat RSI/missing features produce a traced standaside.
            snapshot = digest(data.to_dict(orient="records"))
            available_at = data.available_at.max().to_pydatetime()
            observed_at = data.close_time.iloc[-1].to_pydatetime()
            refs = (self._config["source_cell"], f"available_at={data.available_at.max().isoformat()}")
        identity = {"strategy": self.strategy_id, "version": self._version, "symbol": symbol,
                    "venue": venue, "decision_time": decision_time.isoformat(), "direction": side,
                    "snapshot": snapshot, "config": self.config_hash}
        candidate = HypothesisCandidate(
            event_id=digest(identity), strategy_id=self.strategy_id, strategy_version=self._version,
            symbol=symbol, venue=venue, direction=side, decision_time=decision_time,
            earliest_execution_time=decision_time + timedelta(microseconds=1),
            feature_snapshot_id=snapshot, config_hash=self.config_hash,
            expected_edge=None, expected_cost=Decimal(str(p["cost_bps"])) / Decimal(10000),
            uncertainty=Decimal(1), confidence=None, regime="UNCLASSIFIED",
            invalidation_conditions=("RESEARCH_ONLY", "UNESTIMATED_EDGE_OR_CONFIDENCE",
                                     "ENTRY_ONLY_RULE_NOT_POSITION_LIFECYCLE",
                                     "EXECUTION_REQUIRES_FUTURE_OBSERVED_PRICE",
                                     self._config["invalidation"],
                                     f"stop_loss_fraction={p['stop_loss']}",
                                     f"take_profit_fraction={p['take_profit']}",
                                     f"timeout_bars={p['timeout_bars']}"),
            data_quality="VALID", evidence_refs=refs, provenance=self._config["provenance"],
            evidence_available_at=available_at, source_observed_at=observed_at)
        return [candidate]

    def _direction(self, data: pd.DataFrame, p: dict[str, int | float]) -> int:
        close = data.close.astype(float)
        sid = self.strategy_id
        if sid == "simple_scalper":
            return int(close.iloc[-1] <= p["buy_threshold"])
        if sid == "sma_crossover":
            fast = close.rolling(int(p["fast"])).mean()
            slow = close.rolling(int(p["slow"])).mean()
            return int(fast.iloc[-2] < slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1])
        if sid == "bollinger_reversion":
            mean = close.rolling(int(p["window"])).mean()
            lower = mean - p["std_multiplier"] * close.rolling(int(p["window"])).std(ddof=1)
            return int(close.iloc[-1] < lower.iloc[-1])
        if sid == "rsi_reversal":
            rsi = rsi_adjusted(close, int(p["window"]))
            if rsi.iloc[-2] <= p["oversold"] < rsi.iloc[-1]:
                return 1
            if rsi.iloc[-2] >= p["overbought"] > rsi.iloc[-1]:
                return -1
            return 0
        if sid == "donchian_breakout":
            upper = data.high.astype(float).rolling(int(p["window"])).max().iloc[-2]
            lower = data.low.astype(float).rolling(int(p["window"])).min().iloc[-2]
            return 1 if close.iloc[-1] > upper else (-1 if close.iloc[-1] < lower else 0)
        if sid in {"macd_crossover", "technical_confluence"}:
            line, signal = macd(close, int(p["fast"]), int(p["slow"]), int(p["signal"]),
                                price_adjust=sid == "macd_crossover")
            up = line.iloc[-2] <= signal.iloc[-2] and line.iloc[-1] > signal.iloc[-1]
            down = line.iloc[-2] >= signal.iloc[-2] and line.iloc[-1] < signal.iloc[-1]
            if sid == "technical_confluence":
                rsi = rsi_adjusted(close, int(p["rsi_window"])).iloc[-1]
                ema = close.ewm(span=int(p["ema_window"]), adjust=False).mean().iloc[-1]
                up = up and rsi > p["rsi_mid"] and close.iloc[-1] > ema
                down = down and rsi < p["rsi_mid"] and close.iloc[-1] < ema
            return 1 if up else (-1 if down else 0)
        if sid == "rsi_moving_average":
            rsi = rsi_simple(close, int(p["rsi_window"])).iloc[-1]
            mean = close.rolling(int(p["ma_window"])).mean().iloc[-1]
            return int(close.iloc[-1] > mean and rsi < p["oversold"])
        raise ValueError("UNIMPLEMENTED_STRATEGY")
