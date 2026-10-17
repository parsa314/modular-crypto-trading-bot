"""Position sizing on nonoverlapping, real ML-approved trade events only.

Each transition spans entry to exit, so the account is flat at every decision.
Only entry features enter observations. Daily NAV uses supplied intratrade
closed-bar marks and actual exits; absent paths use an explicitly labeled
exit-only fallback. No path is exposed to the policy before its trade resolves.
"""
from __future__ import annotations

from typing import Callable

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd


def _utc(value):
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("Calendar bounds must be finite UTC timestamps")
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def _calendar(events, start=None, end=None):
    start = start if start is not None else events.attrs.get("period_start")
    end = end if end is not None else events.attrs.get("period_end")
    if start is None:
        if events.empty:
            raise ValueError("Empty evaluation requires an explicit calendar start and end")
        start = pd.to_datetime(events.available_at, utc=True).min().floor("D")
    if end is None:
        if events.empty:
            raise ValueError("Empty evaluation requires an explicit calendar start and end")
        end = (pd.to_datetime(events.exit_time, utc=True).max() - pd.Timedelta(nanoseconds=1)).floor("D") + pd.Timedelta(days=1)
    start, end = _utc(start), _utc(end)
    if start != start.floor("D") or end != end.floor("D") or end <= start:
        raise ValueError("Calendar bounds must be ordered UTC midnights, with end exclusive")
    return pd.date_range(start, end, freq="D", inclusive="left"), start, end


def _validate(events):
    data = events.copy().reset_index(drop=True)
    required = {
        "event_id", "available_at", "entry_time", "exit_time", "entry_price", "exit_price",
        "gross_return", "net_return", "ml_probability", "atr_ratio", "atr_pct", "regime",
        "signal_confidence", "split", "probability_source",
    }
    missing = required.difference(data.columns)
    if missing:
        raise ValueError("Missing approved-signal fields: " + ", ".join(sorted(missing)))
    if data.empty:
        raise ValueError("TradingEnv requires at least one actual approved trade")
    if data.event_id.isna().any() or data.event_id.duplicated().any():
        raise ValueError("Trade event IDs must be present and unique")
    for name in ("available_at", "entry_time", "exit_time"):
        data[name] = pd.to_datetime(data[name], utc=True)
        if data[name].isna().any() or not data[name].is_monotonic_increasing:
            raise ValueError("Trade times must be finite and chronologically ordered")
    if (data.available_at > data.entry_time).any() or (data.exit_time <= data.entry_time).any():
        raise ValueError("Trades must enter after feature availability and exit after entry")
    if len(data) > 1 and (data.available_at.array[1:] < data.exit_time.array[:-1]).any():
        raise ValueError("Every decision must follow the preceding trade exit; overlapping outcomes are forbidden")
    numerical = ["entry_price", "exit_price", "gross_return", "net_return", "ml_probability",
                 "atr_ratio", "atr_pct", "regime", "signal_confidence"]
    numbers = data[numerical].to_numpy(dtype=np.float64)
    if not np.isfinite(numbers).all():
        raise ValueError("Approved-trade values must be finite")
    if (data[["entry_price", "exit_price", "atr_ratio", "atr_pct"]] <= 0).any().any():
        raise ValueError("Prices and ATR features must be positive")
    if not np.isin(data.regime, [0, 1, 2]).all():
        raise ValueError("regime must be 0, 1 or 2")
    for name in ("ml_probability", "signal_confidence"):
        if ((data[name] < 0) | (data[name] > 1)).any():
            raise ValueError(f"{name} must be in [0, 1]")
    if (data.net_return <= -1).any():
        raise ValueError("A collateralized event must retain positive capital after costs")
    if not np.allclose(data.gross_return, data.exit_price / data.entry_price - 1., atol=1e-10, rtol=1e-8):
        raise ValueError("gross_return must match actual long-only entry and exit prices")
    if (data.net_return > data.gross_return + 1e-12).any():
        raise ValueError("Net returns cannot exceed gross returns under positive trading costs")
    expected = data["split"].map({"train": "purged_oof", "val": "frozen_train_model"})
    if expected.isna().any() or not expected.equals(data.probability_source):
        raise ValueError("Train requires purged_oof probabilities; validation requires frozen_train_model")
    return data


class TradingEnv(gym.Env):
    """Eight entry-time features, one cash-collateralized allocation action.

    Reward = daily-NAV Sharpe over the trailing 30 calendar days available at
    this exit, minus 2*max(0, current drawdown - .1). A zero-variance reward
    window contributes zero Sharpe. Reported zero-variance Sharpe is undefined.
    """

    metadata = {"render_modes": []}
    observation_names = (
        "position", "cash_initial_capital_units", "unrealized_pnl",
        "ml_probability", "atr_pct", "regime_range", "regime_trend", "regime_volatile",
    )

    def __init__(self, events: pd.DataFrame, *, start=None, end=None):
        super().__init__()
        calendar, start, end = _calendar(events, start, end)
        data = _validate(events)
        self.full_period_start, self.full_period_end = start, end
        self._episode_configs = []
        if "episode_id" in data:
            runs = data.episode_id.loc[data.episode_id.ne(data.episode_id.shift())]
            if data.episode_id.isna().any() or runs.duplicated().any():
                raise ValueError("Each episode ID must identify one contiguous chronological segment")
            keys = data.episode_id.drop_duplicates().tolist()
            for key in keys:
                group = data.loc[data.episode_id.eq(key)].copy()
                bounds = events.attrs.get("episode_periods", {}).get(key, {})
                group.attrs = {}
                if len(keys) == 1 and not bounds:
                    segment_calendar, segment_start, segment_end = calendar, start, end
                else:
                    segment_calendar, segment_start, segment_end = _calendar(group, bounds.get("start"), bounds.get("end"))
                self._episode_configs.append((group, segment_calendar, segment_start, segment_end))
        else:
            self._episode_configs.append((data, calendar, start, end))
        self.episode_count = len(self._episode_configs)
        self._episode_cursor = -1
        state_names = (
            "calendar", "period_start", "period_end", "events", "net_returns", "probabilities",
            "atr_pct", "regimes", "exit_times", "event_ids", "exit_days", "mark_returns",
            "mark_days", "mark_day_ends", "has_mark_paths", "valuation",
        )
        self._episode_states = []
        for config in self._episode_configs:
            self._configure(*config)
            self._episode_states.append({name: getattr(self, name) for name in state_names})
        self.__dict__.update(self._episode_states[0])
        self.action_space = spaces.Box(0., 1., shape=(1,), dtype=np.float32)
        self.observation_space = spaces.Box(-np.inf, np.inf, shape=(8,), dtype=np.float32)
        self._terminated = True

    def _configure(self, data, calendar, start, end):
        if (data.available_at < start).any() or (data.entry_time >= end).any() or (data.exit_time > end).any():
            raise ValueError("Trade availability, entry and exit must belong to the evaluation calendar")
        self.calendar, self.period_start, self.period_end = calendar, start, end
        self.events = data
        self.net_returns = data.net_return.to_numpy(dtype=np.float64)
        self.probabilities = data.ml_probability.to_numpy(dtype=np.float64)
        self.atr_pct = data.atr_pct.to_numpy(dtype=np.float64)
        self.regimes = data.regime.to_numpy(dtype=np.int8)
        self.exit_times = data.exit_time.to_numpy()
        self.event_ids = data.event_id.to_numpy()
        # 00:00 exits close the preceding UTC day, not the day starting then.
        exit_days = (data.exit_time - pd.Timedelta(nanoseconds=1)).dt.floor("D")
        self.exit_days = calendar.get_indexer(exit_days)
        if (self.exit_days < 0).any():
            raise ValueError("Trade exits fall outside the selected daily calendar")
        self.mark_returns, self.mark_days, self.mark_day_ends = [], [], []
        self.has_mark_paths = "mark_times" in data and "mark_returns" in data
        if ("mark_times" in data) != ("mark_returns" in data):
            raise ValueError("mark_times and mark_returns must be supplied together")
        for i in range(len(data)):
            if self.has_mark_paths:
                times = pd.DatetimeIndex(pd.to_datetime(data.mark_times.iloc[i], utc=True))
                returns = np.asarray(data.mark_returns.iloc[i], dtype=np.float64)
                if not len(times) or len(times) != len(returns) or returns.ndim != 1:
                    raise ValueError("Intratrade mark paths must have equal nonempty lengths")
                if times.hasnans or times.has_duplicates or not times.is_monotonic_increasing:
                    raise ValueError("Intratrade mark times must be finite and strictly ordered")
                if times[0] < data.entry_time.iloc[i] or times[-1] != data.exit_time.iloc[i]:
                    raise ValueError("Intratrade marks must start at/after entry and end exactly at exit")
                if not np.isfinite(returns).all() or (returns <= -1).any() or not np.isclose(returns[-1], self.net_returns[i]):
                    raise ValueError("Intratrade returns must be collateralized and finish at event net_return")
                days = calendar.get_indexer((times - pd.Timedelta(nanoseconds=1)).floor("D"))
                if (days < 0).any():
                    raise ValueError("Intratrade marks fall outside the episode calendar")
            else:
                returns = self.net_returns[i:i + 1]
                days = self.exit_days[i:i + 1]
            self.mark_returns.append(returns)
            self.mark_days.append(days)
            self.mark_day_ends.append(np.r_[np.flatnonzero(np.diff(days)), len(days) - 1])
        self.valuation = ("actual intratrade closed-bar NAV and realized exit" if self.has_mark_paths
                          else "realized exits only; intratrade mark-to-market unavailable")

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._episode_cursor = (self._episode_cursor + 1) % self.episode_count
        if self.episode_count > 1:
            self.__dict__.update(self._episode_states[self._episode_cursor])
        self.index, self.equity, self.peak_equity = 0, 1., 1.
        self.drawdown, self.max_drawdown = 0., 0.
        self.allocated_trades, self.total_fraction = 0, 0.
        self.daily_nav = np.full(len(self.calendar), np.nan, dtype=np.float64)
        self.last_mark_day = -1
        self._terminated = False
        return self._observation(), {"equity": 1., "position": 0., "cash": 1., "unrealized_pnl": 0.}

    def _observation(self):
        state = np.zeros(8, dtype=np.float32)
        state[1] = self.equity
        if self.index < len(self.net_returns):
            state[3] = self.probabilities[self.index]
            state[4] = self.atr_pct[self.index]
            state[5 + self.regimes[self.index]] = 1.
        return state

    def _rolling_sharpe(self, day):
        low = max(0, day - 29)
        nav = self.daily_nav[low:day + 1]
        previous = self.daily_nav[low - 1] if low else 1.
        returns = nav / np.r_[previous, nav[:-1]] - 1.
        std = float(np.std(returns, ddof=1)) if returns.size > 1 else 0.
        return float(np.mean(returns) / std * np.sqrt(365.)) if std > 0 else 0.

    def step(self, action):
        if self._terminated:
            raise RuntimeError("Episode ended; reset before requesting another trade")
        action = np.asarray(action, dtype=np.float64).reshape(-1)
        if action.size != 1 or not np.isfinite(action[0]) or not 0 <= action[0] <= 1:
            raise ValueError("Action must contain one finite capital fraction in [0, 1]")
        fraction = float(action[0])
        previous_equity = self.equity
        event_return = self.net_returns[self.index]
        pnl = previous_equity * fraction * event_return
        marks = previous_equity * (1. + fraction * self.mark_returns[self.index])
        self.equity = float(marks[-1])
        if not np.isfinite(self.equity) or self.equity <= 0:
            raise RuntimeError("Portfolio equity became nonpositive or nonfinite")
        peaks = np.maximum.accumulate(np.r_[self.peak_equity, marks])[1:]
        self.max_drawdown = max(self.max_drawdown, float(np.max(1. - marks / peaks)))
        self.peak_equity = float(peaks[-1])
        self.drawdown = 1. - self.equity / self.peak_equity
        last_equity = previous_equity
        for position in self.mark_day_ends[self.index]:
            day = self.mark_days[self.index][position]
            self.daily_nav[self.last_mark_day + 1:day + 1] = last_equity
            self.daily_nav[day] = marks[position]
            self.last_mark_day = int(day)
            last_equity = marks[position]
        day = self.last_mark_day
        rolling_sharpe = self._rolling_sharpe(day)
        reward = rolling_sharpe - 2. * max(0., self.drawdown - .1)
        self.total_fraction += fraction
        self.allocated_trades += int(fraction > 0)
        info = {
            "event_id": self.event_ids[self.index],
            "exit_time": self.exit_times[self.index], "equity": self.equity,
            "cash": self.equity, "position": 0., "unrealized_pnl": 0.,
            "allocated_fraction": fraction, "realized_pnl": pnl,
            "net_return_invested_capital": float(event_return),
            "portfolio_return": float(fraction * event_return),
            "drawdown": self.drawdown, "rolling_30_calendar_day_sharpe": rolling_sharpe,
        }
        self.index += 1
        self._terminated = self.index == len(self.net_returns)
        return self._observation(), float(reward), self._terminated, False, info

    def daily_returns(self):
        """Realized exit NAV, including zero-return quiet days in the calendar."""
        nav = self.daily_nav.copy()
        if self.last_mark_day < 0:
            nav[:] = 1.
        else:
            nav[self.last_mark_day + 1:] = self.equity
        returns = nav / np.r_[1., nav[:-1]] - 1.
        return pd.Series(returns, index=self.calendar, name="return")


def _metrics(daily_returns, *, equity, max_drawdown, events, trades, fraction,
             valuation="actual intratrade closed-bar NAV and realized exit"):
    std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.
    return {
        "sharpe": float(daily_returns.mean() / std * np.sqrt(365.)) if std > 0 else None,
        "sharpe_defined": bool(std > 0), "return": float(equity - 1.),
        "max_drawdown": float(max_drawdown), "approved_events": int(events),
        "num_trades": int(trades), "daily_observations": len(daily_returns),
        "mean_allocation": float(fraction / events) if events else 0.,
        "valuation": valuation,
    }


def evaluate_signal_policy(events: pd.DataFrame, policy: Callable, *, start=None, end=None):
    """Evaluate allocations on the same actual approved events and net costs.

    Calendar bounds may be supplied explicitly or through period_start/end
    DataFrame attrs. Empty out-of-sample groups return cash over that calendar.
    """
    if events.empty:
        calendar, _, _ = _calendar(events, start, end)
        daily = pd.Series(0., index=calendar, name="return")
        return _metrics(daily, equity=1., max_drawdown=0., events=0, trades=0, fraction=0.,
                        valuation="cash; no approved trade events"), daily
    env = TradingEnv(events, start=start, end=end)
    if env.episode_count > 1:
        raise ValueError("Evaluate each contiguous calendar episode separately before reconstructing paths")
    observation, _ = env.reset(seed=42)
    while True:
        result = policy(observation)
        action = result[0] if isinstance(result, tuple) else result
        observation, _, terminated, truncated, _ = env.step(action)
        if terminated or truncated:
            break
    daily = env.daily_returns()
    return _metrics(daily, equity=env.equity, max_drawdown=env.max_drawdown,
                    events=len(events), trades=env.allocated_trades, fraction=env.total_fraction,
                    valuation=env.valuation), daily
