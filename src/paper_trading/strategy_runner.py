from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .data_feed import Snapshot
from .portfolio import Portfolio


def rsi14(close: pd.Series) -> float:
    """Wilder RSI with the initial 14 differences averaged explicitly."""
    delta = close.astype(float).diff().dropna()
    if len(delta) < 14:
        raise ValueError("RSI requires 15 closed daily prices")
    gains, losses = delta.clip(lower=0), -delta.clip(upper=0)
    up, down = float(gains.iloc[:14].mean()), float(losses.iloc[:14].mean())
    for gain, loss in zip(gains.iloc[14:], losses.iloc[14:]):
        up = (13 * up + gain) / 14
        down = (13 * down + loss) / 14
    if down == 0:
        return 100.0 if up > 0 else 50.0
    return 100 - 100 / (1 + up / down)


class StrategyRunner:
    def __init__(self, config: dict, root: Path):
        self.config = config
        self.name = config["strategy"]["name"]
        self.root = root
        if self.name == "auto":
            self.name = self._auto_strategy()
        if self.name not in {"observe", "momentum", "hybrid", "v3"}:
            raise ValueError("strategy must be observe, momentum, hybrid, v3, or auto")
        self.catalog = None
        if self.name == "v3":
            if self._auto_strategy() != "v3":
                raise ValueError("v3 paper adapter requires an explicit recorded PASS")
            from research_bot.cross_sectional_v3 import prepare_catalog
            path = root / config["strategy"]["catalog_file"]
            record = json.loads(path.read_text())
            self.catalog, self.catalog_sha = prepare_catalog(pd.DataFrame(record["rows"]), complete=record.get("complete") is True)

    def _auto_strategy(self) -> str:
        path = self.root / self.config["strategy"].get("result_file", "results/v3_final.json")
        if not path.exists():
            return "observe"
        result = json.loads(path.read_text())
        verdict = result.get("verdict", result.get("validation", {}).get("verdict"))
        # A failed data prerequisite authorizes the paper fallback, without
        # inventing economic performance or a measured v3 rejection.
        if result.get("outcome") == "FAIL_DATA_AND_FROZEN_WINDOW_GATE":
            return "hybrid"
        if verdict in {"PASS", "LIVE_READY", "V3_TEST_PASS"}:
            return "v3"
        if verdict in {"FAIL", "REJECTED_FINAL", "V3_REJECTED_DEVELOPMENT"}:
            return "hybrid"
        return "observe"

    def rebalance_slot(self, snapshot: Snapshot) -> str | None:
        t = snapshot.timestamp
        if t.hour != 0 or self.name == "observe":
            return None
        if self.name == "hybrid" and t.weekday() != 0:
            return None
        if self.name == "v3" and t.weekday() != 1:
            return None
        return f"{self.name}:{t:%Y-%m-%d}"

    def targets(self, snapshot: Snapshot, portfolio: Portfolio) -> dict[str, float]:
        if self.name == "observe":
            return {}
        if self.name == "v3":
            return self._v3(snapshot)
        history = snapshot.history
        returns, rsis = {}, {}
        for symbol in self.config["universe"]:
            if snapshot.volume24.get(symbol, 0) <= self.config["risk"]["min_volume24_usdt"]:
                continue
            rows = history[history.symbol == symbol].sort_values("date")
            if len(rows) < 30:
                continue
            tail = rows.tail(29)
            if (tail.date.diff().dropna() != pd.Timedelta(days=1)).any():
                raise ValueError(f"gap in momentum history: {symbol}")
            if tail.date.iloc[-1] != pd.Timestamp(snapshot.timestamp).normalize() - pd.Timedelta(days=1):
                raise ValueError(f"daily strategy history is stale: {symbol}")
            returns[symbol] = float(tail.close.iloc[-1] / tail.close.iloc[0] - 1)
            rsis[symbol] = rsi14(rows.close)
        if not returns:
            return {}
        top = sorted(returns, key=lambda s: (-returns[s], s))[:math.ceil(len(returns) / 5)]
        if self.name == "momentum":
            return {s: min(self.config["risk"]["max_position"], 1 / len(top)) for s in top}
        held = set(portfolio.strategy_state.get("mr_hold", [])) & set(returns)
        held.update(s for s, rsi in rsis.items() if rsi < 30)
        held.difference_update(s for s, rsi in rsis.items() if rsi > 70)
        portfolio.strategy_state["mr_hold"] = sorted(held)
        targets: dict[str, float] = {s: .5 / len(top) for s in top}
        for s in held:
            targets[s] = targets.get(s, 0.0) + .5 / len(held)
        return {s: min(self.config["risk"]["max_position"], w) for s, w in targets.items()}

    def _v3(self, snapshot: Snapshot) -> dict[str, float]:
        # Shared data and universe functions; the research holdout API stays sealed.
        from research_bot.cross_sectional_v3 import _eligible_snapshot, prepare_panel
        day = pd.Timestamp(snapshot.timestamp).normalize() - pd.Timedelta(days=1)
        sunday = day - pd.Timedelta(days=(day.weekday() - 6) % 7)
        active = self.catalog[(self.catalog.listed_at <= sunday) & (self.catalog.delisted_at.isna() | (self.catalog.delisted_at > sunday))]
        if set(active.symbol) - set(self.config["universe"]):
            raise ValueError("v3 requires coverage of every active catalog symbol before ranking")
        eligible = _eligible_snapshot(prepare_panel(snapshot.history), self.catalog, sunday)
        selected = eligible.sort_index().sort_values("median30", ascending=False, kind="stable").head(50)
        if len(selected) < 25:
            return {}
        score = selected[["R7", "R14", "R28"]].rank(method="average", pct=True).mean(axis=1)
        winners = score.sort_index().sort_values(ascending=False, kind="stable").head(math.ceil(len(selected) / 5)).index
        absolute = selected[["R7", "R14", "R28"]].mean(axis=1)
        positive = [s for s in winners if absolute[s] > 0]
        n = len(positive)
        exposure = 1.0 if n >= 10 else .1 * n if n >= 5 else min(.1 * n, .3)
        return {s: exposure / n for s in positive} if n else {}
