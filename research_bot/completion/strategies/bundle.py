from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from ...hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, utc_text
from ...jamalian_data import Candle, OrderBook
from ...strategies.baselines import RsiMaConfluenceHypothesis, RsiMaParams
from ...strategies.jamalian_hypotheses import _closed, _ema, _period
from ..models import Inventory, ZERO
from .grid_hypothesis import GridHypothesisGenerator
from .triangular_arb_hypothesis import TriangularArbitrageHypothesis


class EmaCrossHypothesis:
    def __init__(self, fast: int = 5, slow: int = 12, interval: timedelta = timedelta(seconds=1)) -> None:
        self.fast, self.slow, self.interval = _period(fast), _period(slow), interval
        if fast >= slow:
            raise ValueError("fast EMA must precede slow EMA")

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[HypothesisCandidate]:
        history = _closed(candles, at, self.interval)
        if len(history) < self.slow + 1:
            return []
        prices = [c.close for c in history]
        fast, slow = _ema(prices, self.fast), _ema(prices, self.slow)
        fire = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        return [HypothesisCandidate("EMA_CROSS_COMPLETION_V1", HypothesisFamily.TECHNICAL_BASELINE,
            history[-1].symbol, HypothesisSide.LONG if fire else HypothesisSide.NEUTRAL,
            at, at + timedelta(milliseconds=20), ("EMA_CROSS_UP" if fire else "NO_EMA_CROSS",),
            tuple(c.stamp for c in history), {"fast": self.fast, "slow": self.slow},
            {"fast_ema": str(fast[-1]), "slow_ema": str(slow[-1]), "calibration_status": "NOT_CALIBRATED"})]


class StrategyBundle:
    def __init__(self, grid: GridHypothesisGenerator | None = None,
                 triangular: TriangularArbitrageHypothesis | None = None,
                 rsi_ma: RsiMaConfluenceHypothesis | None = None,
                 ema: EmaCrossHypothesis | None = None, arbitrage_budget: Decimal = Decimal("100")) -> None:
        from ...jamalian_data import decimal_value
        self.grid, self.triangular, self.rsi_ma, self.ema = grid, triangular, rsi_ma, ema
        self.arbitrage_budget = decimal_value(arbitrage_budget, positive=True)
        self.history: list[Candle] = []

    def generate(self, candle: Candle, books: Sequence[OrderBook], inventory: Inventory, at: datetime,
                 registrations: Mapping[str, Mapping]) -> list[HypothesisCandidate]:
        self.history.append(candle)
        result = []
        if self.grid:
            book = next(b for b in books if b.symbol == self.grid.parameters.symbol)
            _, quote = book.symbol.split("_")
            result += self.grid.generate((book.bids[0].price + book.asks[0].price) / 2, inventory,
                inventory.reserved.get(quote, ZERO), book.stamp, at, registrations.get(book.symbol))
        if self.triangular:
            start = self.triangular.parameters.cycle[0]
            budget = min(self.arbitrage_budget, inventory.free(start))
            if budget > ZERO:
                graph_books = [b for b in books if set(b.symbol.split("_")).issubset(self.triangular.parameters.cycle)]
                result += self.triangular.generate(graph_books, budget, at,
                    tuple(registrations[b.symbol] for b in graph_books))
        if self.rsi_ma:
            result += self.rsi_ma.generate(self.history, at)
        if self.ema:
            result += self.ema.generate(self.history, at)
        return result

    def configuration(self) -> dict:
        from ..models import seconds
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(v) for v in value]
            return str(value) if isinstance(value, Decimal) else str(seconds(value)) if isinstance(value, timedelta) else value
        return {"grid": clean(asdict(self.grid.parameters)) if self.grid else None,
            "triangular": clean(asdict(self.triangular.parameters)) if self.triangular else None,
            "rsi_ma": clean(asdict(self.rsi_ma.params)) if self.rsi_ma else None,
            "ema": {"fast": self.ema.fast, "slow": self.ema.slow, "interval": str(seconds(self.ema.interval))} if self.ema else None,
            "arbitrage_budget": str(self.arbitrage_budget)}

    def checkpoint(self) -> dict:
        return {"configuration": self.configuration(), "grid": self.grid.checkpoint() if self.grid else None,
            "triangle": {"open_since": utc_text(self.triangular.open_since) if self.triangular.open_since else None,
                "last_at": utc_text(self.triangular.last_at) if self.triangular.last_at else None,
                "diagnostics": self.triangular.diagnostics} if self.triangular else None,
            "history": [{"symbol": c.symbol, "close": str(c.close), "stamp": {
                "source": c.stamp.source, "observed_at": utc_text(c.stamp.observed_at),
                "available_at": utc_text(c.stamp.available_at), "lineage": c.stamp.lineage,
                "reliability_tier": c.stamp.reliability_tier, "status": c.stamp.status.value}} for c in self.history]}

    def restore(self, state: dict) -> None:
        from ...contracts import EvidenceStamp, ResearchStatus
        if state["configuration"] != self.configuration():
            raise ValueError("strategy configuration changed across resume")
        if self.grid and state["grid"]:
            self.grid.restore(state["grid"])
        if self.triangular and state["triangle"]:
            row = state["triangle"]
            self.triangular.open_since = datetime.fromisoformat(row["open_since"]) if row["open_since"] else None
            self.triangular.last_at = datetime.fromisoformat(row["last_at"]) if row["last_at"] else None
            self.triangular.diagnostics = row["diagnostics"]
        self.history = []
        for row in state["history"]:
            s = row["stamp"]
            self.history.append(Candle(row["symbol"], Decimal(row["close"]), EvidenceStamp(s["source"],
                datetime.fromisoformat(s["observed_at"]), datetime.fromisoformat(s["available_at"]),
                s["lineage"], s["reliability_tier"], ResearchStatus(s["status"]))))
