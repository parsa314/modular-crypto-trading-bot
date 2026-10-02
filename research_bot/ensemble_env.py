"""Offline, long-only portfolio simulator with causal next-open execution.

The market features supplied by the caller must already be causal and normalized.
At decision index ``t`` the observation contains closed bars through ``t``;
orders execute at ``t + 1`` open and equity is marked at that bar's close.
Fees are charged on executed notional, and adverse slippage is charged through
the fill price. Neither cost is subtracted a second time from the reward.

CVaR is an empirical historical one-bar asset-loss estimate, not a forecast or
a constrained PPO algorithm. Its sizing rule is a research heuristic and does
not guarantee a future loss bound. Drawdown liquidation uses the observed
close with costs as an explicit simulator convention; gaps and costs may exceed
the configured drawdown threshold. All residual holdings are also liquidated
at the final data close, making terminal equity comparable with a realized
buy-and-hold baseline. Both risk stops and dataset ends terminate this finite
episode; they are not time-limit truncations that PPO should bootstrap beyond.
This module has no exchange or execution-service hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral, Real
from typing import Iterable, Sequence

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite real number")
    return float(value)


@dataclass(frozen=True)
class TradingEnvConfig:
    lookback: int = 30
    initial_balance: float = 10_000.0
    transaction_cost: float = 0.001
    slippage_bps: float = 5.0
    max_asset_weight: float = 0.35
    max_drawdown: float = 0.12
    cvar_alpha: float = 0.95
    cvar_window: int = 100
    cvar_min_samples: int = 20
    max_cvar: float = 0.035

    def __post_init__(self) -> None:
        for name in ("lookback", "cvar_window", "cvar_min_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.cvar_min_samples > self.cvar_window:
            raise ValueError("cvar_min_samples cannot exceed cvar_window")
        for name in (
            "initial_balance", "transaction_cost", "slippage_bps",
            "max_asset_weight", "max_drawdown", "cvar_alpha", "max_cvar",
        ):
            _finite_number(name, getattr(self, name))
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 <= self.transaction_cost < 1:
            raise ValueError("transaction_cost must be in [0, 1)")
        if not 0 <= self.slippage_bps < 10_000:
            raise ValueError("slippage_bps must be in [0, 10000)")
        if not 0 <= self.max_asset_weight <= 1:
            raise ValueError("max_asset_weight must be in [0, 1]")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be in (0, 1)")
        if not 0 < self.cvar_alpha < 1:
            raise ValueError("cvar_alpha must be in (0, 1)")
        if not 0 < self.max_cvar <= 1:
            raise ValueError("max_cvar must be in (0, 1]")


def empirical_cvar(losses: Iterable[float], alpha: float = 0.95) -> float:
    """Mean of the worst ``1 - alpha`` probability mass of empirical losses.

Each observation has equal probability. When the tail contains a fractional
observation, include exactly that fraction of the next-largest loss. Positive
numbers denote losses, negative numbers gains; an all-gain sample can therefore
have negative CVaR. Asset sizing separately clips this estimate at zero.
"""
    confidence = _finite_number("alpha", alpha)
    if not 0 < confidence < 1:
        raise ValueError("alpha must be in (0, 1)")
    try:
        values = np.asarray(list(losses), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("losses must be a finite nonempty one-dimensional sample") from exc
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("losses must be a finite nonempty one-dimensional sample")
    descending = np.sort(values)[::-1]
    tail_mass = len(descending) * (1.0 - confidence)
    whole = int(math.floor(tail_mass))
    fraction = tail_mass - whole
    # Normalize weights before adding to avoid overflowing a finite tail mean.
    if whole == 0:
        return float(descending[0])
    terms = [float(value) / tail_mass for value in descending[:whole]]
    if fraction:
        terms.append(float(descending[whole]) * (fraction / tail_mass))
    result = math.fsum(terms)
    if not math.isfinite(result):
        raise ValueError("CVaR is not finite for this sample")
    return result


class EnsembleTradingEnv(gym.Env):
    """Offline Gymnasium environment; actions select target long exposure.

    ``0`` retains the previous target weight, ``1`` selects the current risk
    cap, and ``2`` selects zero. Every target is clipped to the causal risk cap,
    and holdings rebalance at the next open to that weight after costs. Exposure
    may drift above the cap within the ensuing bar. An insufficient historical
    sample sets the cap to zero and prevents buying.

    Observations have shape ``(lookback, len(feature_cols) + 4)``. The final four
    columns repeat the current exposure, cash / initial capital, equity / initial
    capital and drawdown; these are current account state, not historical states.
    ``info['cvar']`` is the nonnegative asset CVaR used for that decision, or
    ``None`` while history is insufficient. ``info['decision_timestamp']`` gives
    the cutoff for that estimate, while ``timestamp`` gives the equity mark.

    ``start_index`` is the final context bar before the first simulated trade.
    Preceding rows supply observations and risk history without trading them.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        df: pd.DataFrame,
        feature_cols: Sequence[str],
        config: TradingEnvConfig | None = None,
        start_index: int | None = None,
    ) -> None:
        super().__init__()
        self.config = TradingEnvConfig() if config is None else config
        if not isinstance(self.config, TradingEnvConfig):
            raise ValueError("config must be a TradingEnvConfig")
        self.feature_cols = list(feature_cols)
        if not self.feature_cols or len(set(self.feature_cols)) != len(self.feature_cols):
            raise ValueError("feature_cols must be nonempty and unique")
        if not isinstance(df, pd.DataFrame):
            raise ValueError("df must be a pandas DataFrame")
        required = {"timestamp", "open", "high", "low", "close", "volume", *self.feature_cols}
        if not df.columns.is_unique or not required.issubset(df.columns):
            raise ValueError("df must contain unique OHLCV, timestamp and feature columns")
        if len(df) < self.config.lookback + 1:
            raise ValueError("df needs lookback context and at least one executable bar")
        self.df = df.copy().reset_index(drop=True)
        try:
            self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], utc=True, errors="raise")
            ohlcv = self.df[["open", "high", "low", "close", "volume"]].to_numpy(dtype=np.float64)
            features = self.df[self.feature_cols].to_numpy(dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("timestamps, OHLCV and features must be valid numeric data") from exc
        timestamps = self.df["timestamp"]
        if timestamps.isna().any() or timestamps.duplicated().any() or not timestamps.is_monotonic_increasing:
            raise ValueError("timestamps must be finite, unique and strictly increasing")
        if not np.isfinite(ohlcv).all() or not np.isfinite(features).all():
            raise ValueError("OHLCV and features must be finite")
        open_, high, low, close, volume = ohlcv.T
        if (
            (ohlcv[:, :4] <= 0).any() or (volume < 0).any()
            or (high < np.maximum(open_, close)).any()
            or (low > np.minimum(open_, close)).any() or (high < low).any()
        ):
            raise ValueError("OHLCV must have positive prices, consistent bounds and nonnegative volume")
        if (np.abs(features) > np.finfo(np.float32).max).any():
            raise ValueError("features must be representable as finite float32 observations")
        self._features = features.astype(np.float32)
        self._open = open_
        self._close = close
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            self._asset_returns = close[1:] / close[:-1] - 1.0
        if not np.isfinite(self._asset_returns).all():
            raise ValueError("raw close returns must be finite")
        self.start_index = self.config.lookback - 1 if start_index is None else start_index
        if (
            isinstance(self.start_index, bool) or not isinstance(self.start_index, Integral)
            or not self.config.lookback - 1 <= self.start_index < len(self.df) - 1
        ):
            raise ValueError("start_index must retain lookback context and a following execution bar")
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(self.config.lookback, len(self.feature_cols) + 4), dtype=np.float32,
        )
        self.action_space = spaces.Discrete(3)
        self.initial_balance = self.config.initial_balance
        self.lookback = self.config.lookback
        self._done = True

    def _historical_risk(self) -> tuple[float | None, float, int]:
        # Return i describes close[i] -> close[i + 1]; stopping at t excludes t + 1.
        sample = self._asset_returns[max(0, self.step_idx - self.config.cvar_window):self.step_idx]
        if len(sample) < self.config.cvar_min_samples:
            return None, 0.0, len(sample)
        cvar = max(0.0, empirical_cvar(-sample, self.config.cvar_alpha))
        cap = self.config.max_asset_weight
        if cvar > 0:
            cap = min(cap, self.config.max_cvar / cvar)
        return cvar, cap, len(sample)

    def _get_obs(self) -> np.ndarray:
        exposure = self.quantity * self._close[self.step_idx] / self.equity
        state = np.array([
            exposure, self.cash / self.initial_balance,
            self.equity / self.initial_balance, self.drawdown,
        ], dtype=np.float64)
        if not np.isfinite(state).all() or (np.abs(state) > np.finfo(np.float32).max).any():
            raise ValueError("account state cannot be represented as a finite observation")
        window = self._features[self.step_idx - self.lookback + 1:self.step_idx + 1]
        account = np.broadcast_to(state.astype(np.float32), (self.lookback, 4))
        return np.concatenate((window, account), axis=1).astype(np.float32, copy=False)

    def _info(
        self,
        fees: float,
        cvar: float | None,
        cap: float,
        sample_count: int,
        decision_index: int,
        risk_stop: bool = False,
        terminal_liquidation: bool = False,
    ) -> dict:
        return {
            "equity": float(self.equity), "cash": float(self.cash),
            "quantity": float(self.quantity),
            "exposure": float(self.quantity * self._close[self.step_idx] / self.equity),
            "fees": float(fees), "drawdown": float(self.drawdown), "cvar": cvar,
            "risk_cap": float(cap), "cvar_sample_count": sample_count,
            "target_weight": float(self.target_weight),
            "timestamp": self.df["timestamp"].iloc[self.step_idx].isoformat(),
            "decision_timestamp": self.df["timestamp"].iloc[decision_index].isoformat(),
            "risk_stop": risk_stop, "terminal_liquidation": terminal_liquidation,
            "data_exhausted": self.step_idx == len(self.df) - 1,
            "episode_end_reason": "MAX_DRAWDOWN" if risk_stop else "DATA_END" if terminal_liquidation else None,
        }

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.cash = float(self.initial_balance)
        self.quantity = 0.0
        self.equity = float(self.initial_balance)
        self.peak_value = float(self.initial_balance)
        self.drawdown = 0.0
        self.target_weight = 0.0
        self.step_idx = int(self.start_index)
        self._done = False
        cvar, cap, samples = self._historical_risk()
        return self._get_obs(), self._info(0.0, cvar, cap, samples, self.step_idx)

    def _rebalance(self, reference_price: float, target: float) -> float:
        """Solve target exposure against post-cost equity at this reference price."""
        equity = self.cash + self.quantity * reference_price
        difference = target * equity - self.quantity * reference_price
        slippage = self.config.slippage_bps / 10_000.0
        fee_rate = self.config.transaction_cost
        tolerance = 1e-12 * max(1.0, equity)
        if difference > tolerance:
            fill_price = reference_price * (1.0 + slippage)
            cash_per_unit = fill_price * (1.0 + fee_rate)
            quantity = difference / (reference_price + target * (cash_per_unit - reference_price))
            quantity = min(quantity, self.cash / cash_per_unit)
            fees = quantity * fill_price * fee_rate
            self.cash = max(0.0, self.cash - quantity * cash_per_unit)
            self.quantity += quantity
            return fees
        if difference < -tolerance:
            fill_price = reference_price * (1.0 - slippage)
            net_per_unit = fill_price * (1.0 - fee_rate)
            quantity = -difference / (reference_price - target * (reference_price - net_per_unit))
            quantity = min(quantity, self.quantity)
            fees = quantity * fill_price * fee_rate
            self.cash += quantity * net_per_unit
            self.quantity = max(0.0, self.quantity - quantity)
            return fees
        return 0.0

    def _liquidate(self, reference_price: float) -> float:
        fill_price = reference_price * (1.0 - self.config.slippage_bps / 10_000.0)
        notional = self.quantity * fill_price
        fees = notional * self.config.transaction_cost
        self.cash += notional - fees
        self.quantity = 0.0
        self.target_weight = 0.0
        return fees

    def step(self, action):
        if self._done:
            raise RuntimeError("reset is required before stepping a new or finished episode")
        if isinstance(action, np.ndarray) and action.shape == ():
            action = action.item()
        if isinstance(action, bool) or not isinstance(action, Integral) or not self.action_space.contains(action):
            raise ValueError("action must be 0 (hold target), 1 (buy), or 2 (sell)")
        decision_index = self.step_idx
        previous_equity = self.equity
        cvar, cap, samples = self._historical_risk()
        if action == 1:
            self.target_weight = cap
        elif action == 2:
            self.target_weight = 0.0
        else:
            self.target_weight = min(self.target_weight, cap)
        self.step_idx += 1
        fees = self._rebalance(float(self._open[self.step_idx]), self.target_weight)
        close = float(self._close[self.step_idx])
        self.equity = self.cash + self.quantity * close
        if not math.isfinite(self.equity) or self.equity <= 0:
            raise ValueError("portfolio equity must remain finite and positive")
        self.peak_value = max(self.peak_value, self.equity)
        self.drawdown = 1.0 - self.equity / self.peak_value
        risk_stop = self.drawdown >= self.config.max_drawdown
        exhausted = self.step_idx == len(self.df) - 1
        if risk_stop or exhausted:
            fees += self._liquidate(close)
            self.equity = self.cash
            self.drawdown = 1.0 - self.equity / self.peak_value
        self._done = risk_stop or exhausted
        reward = math.log(self.equity) - math.log(previous_equity)
        return (
            self._get_obs(), float(reward), bool(risk_stop or exhausted), False,
            self._info(fees, cvar, cap, samples, decision_index, risk_stop, exhausted and not risk_stop),
        )
