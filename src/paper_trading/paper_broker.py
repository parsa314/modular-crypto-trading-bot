from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import math

import pandas as pd

from .portfolio import Portfolio, Position


@dataclass(frozen=True)
class OrderBook:
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    observed_at: datetime
    source: str = "binance_public_depth"

    def __post_init__(self):
        for levels, descending in ((self.bids, True), (self.asks, False)):
            if not levels or any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0 for p, q in levels):
                raise ValueError("invalid order book levels")
            prices = [p for p, _ in levels]
            if prices != sorted(prices, reverse=descending):
                raise ValueError("unordered depth levels")

    @property
    def mid(self) -> float:
        if not self.bids or not self.asks:
            raise ValueError("empty order book")
        if not all(math.isfinite(p) and p > 0 for p in (self.bids[0][0], self.asks[0][0])):
            raise ValueError("invalid top-of-book price")
        if self.bids[0][0] > self.asks[0][0]:
            raise ValueError("crossed order book")
        return (self.bids[0][0] + self.asks[0][0]) / 2

    def vwap(self, quantity: float, buy: bool) -> float:
        remaining, quote = quantity, 0.0
        for price, available in self.asks if buy else self.bids:
            if not math.isfinite(price + available) or price <= 0 or available <= 0:
                raise ValueError("invalid depth level")
            used = min(remaining, available)
            quote += used * price
            remaining -= used
            if remaining <= max(1e-12, quantity * 1e-12):
                return quote / quantity
        raise ValueError("INSUFFICIENT_DISPLAYED_DEPTH")


def liquidity_tiers(history: pd.DataFrame, symbols: list[str]) -> dict[str, int]:
    means = {}
    for symbol in symbols:
        rows = history[history.symbol == symbol].sort_values("date").tail(30)
        if len(rows) != 30 or rows.date.nunique() != 30 or rows.quote_volume.isna().any():
            raise ValueError(f"30 closed daily volumes required for cost tier: {symbol}")
        if (rows.date.diff().dropna() != pd.Timedelta(days=1)).any():
            raise ValueError(f"gap in cost-tier history: {symbol}")
        means[symbol] = float(rows.quote_volume.mean())
    ranked = sorted(means, key=lambda s: (-means[s], s))
    ranks = {s: i + 1 for i, s in enumerate(ranked)}
    return {s: 1 if s in {"BTCUSDT", "ETHUSDT"} else 2 if ranks[s] <= 20 else 3 for s in ranked}


class PaperBroker:
    def __init__(self, fee_bps: float = 10.0, penalty_bps: dict[int, float] | None = None,
                 max_weight: float = .10, min_volume: float = 5_000_000.0):
        self.fee = fee_bps / 10_000
        self.penalties = penalty_bps or {1: 5.0, 2: 15.0, 3: 40.0}
        self.max_weight = max_weight
        self.min_volume = min_volume

    def _quote(self, delta: float, book: OrderBook, tier: int) -> tuple[float, float, float]:
        quantity = abs(delta)
        if quantity < 1e-12:
            return book.mid, 0.0, 0.0
        mid = book.mid
        observed = book.vwap(quantity, delta > 0)
        floor = self.penalties[tier] / 10_000
        fill = max(observed, mid * (1 + floor)) if delta > 0 else min(observed, mid * (1 - floor))
        fee = quantity * fill * self.fee
        cost = quantity * abs(fill - mid) + fee
        return fill, fee, cost

    def rebalance(self, portfolio: Portfolio, targets: dict[str, float], books: dict[str, OrderBook],
                  tiers: dict[str, int], volume24: dict[str, float], now: datetime) -> tuple[Portfolio, list[dict]]:
        if portfolio.halt_reason:
            raise ValueError("paper book is halted")
        if any(not math.isfinite(w) or w < 0 or w > self.max_weight + 1e-12 for w in targets.values()) or sum(targets.values()) > 1 + 1e-12:
            raise ValueError("target violates long-only / max-position constraints")
        symbols = set(targets) | set(portfolio.positions)
        if symbols - (set(books) & set(tiers)):
            raise ValueError("missing current book or historical tier")
        for s in symbols:
            age = (now - books[s].observed_at).total_seconds()
            if age < -5 or age > 10:
                raise ValueError(f"stale order book: {s}")
        mids = {s: books[s].mid for s in symbols}
        pre = portfolio.cash + sum(p.quantity * mids[s] for s, p in portfolio.positions.items())

        def estimate(nav):
            return sum(self._quote(targets.get(s, 0) * nav / mids[s] - portfolio.positions.get(s, _ZERO).quantity, books[s], tiers[s])[2] for s in symbols)

        low, high = 0.0, pre
        for _ in range(60):
            mid = (low + high) / 2
            if mid + estimate(mid) > pre:
                high = mid
            else:
                low = mid
        post = (low + high) / 2
        deltas = {s: targets.get(s, 0.0) * post / mids[s] - portfolio.positions.get(s, _ZERO).quantity for s in symbols}
        result = deepcopy(portfolio)
        result.marks.update(mids)
        trades = []
        # Sell before buying. The whole batch commits atomically in the caller.
        for s in sorted(symbols, key=lambda s: (deltas[s] > 0, s)):
            delta = deltas[s]
            if abs(delta) * mids[s] < 1e-6:
                continue
            if delta > 0 and volume24.get(s, 0.0) <= self.min_volume:
                raise ValueError(f"MIN_LIQUIDITY: cannot buy {s}")
            fill, fee, cost = self._quote(delta, books[s], tiers[s])
            observed = books[s].vwap(abs(delta), delta > 0)
            result.fill(s, delta, fill, fee, mids[s])
            trades.append({"symbol": s, "side": "buy" if delta > 0 else "sell", "quantity": abs(delta),
                           "fill_price": fill, "reference_mid": mids[s], "observed_vwap": observed,
                           "fee": fee, "execution_impact": abs(delta) * abs(fill - mids[s]),
                           "total_cost": cost, "tier": tiers[s], "source": books[s].source})
        if not math.isclose(result.equity(), post, rel_tol=1e-9, abs_tol=1e-6):
            raise AssertionError("paper cash ledger does not balance")
        if any(p.quantity * result.marks[s] > post * self.max_weight + 1e-6 for s, p in result.positions.items()):
            raise AssertionError("post-cost position exceeds limit")
        return result, trades


_ZERO = Position()
