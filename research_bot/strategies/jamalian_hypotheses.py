"""Course-derived hypotheses with explicit availability and no invented alpha.

LONG/SHORT are candidate metadata. Every output is NO_TRADE with confidence=0
until a separate frozen experiment supplies an admissible calibrated forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Sequence

from ..contracts import Decision, EvidenceStamp, SignalEvidence
from ..jamalian_data import Candle, OrderBook, available, aware, currencies, decimal_value


ZERO, ONE, HUNDRED = Decimal("0"), Decimal("1"), Decimal("100")


def _period(value: int) -> int:
    if type(value) is not int or value < 2:
        raise ValueError("indicator periods must be integers >= 2")
    return value


def _closed(candles: Sequence[Candle], at: datetime, interval: timedelta) -> tuple[Candle, ...]:
    aware(at)
    if interval <= timedelta(0):
        raise ValueError("bar interval must be positive")
    if not candles:
        return ()
    if len({c.symbol for c in candles}) != 1:
        raise ValueError("mixed symbol history")
    selected = tuple(c for c in candles if c.stamp.available_at <= at)
    for candle in selected:
        available(candle.stamp, at)
    for previous, current in zip(selected, selected[1:]):
        if current.stamp.observed_at - previous.stamp.observed_at != interval:
            raise ValueError("bars must be chronological, unique and contiguous")
    if selected:
        available(selected[-1].stamp, at, interval * 2)
    return selected


def _rsi(gain: Decimal, loss: Decimal) -> Decimal:
    if gain == 0 and loss == 0:
        return Decimal("50")
    if loss == 0:
        return HUNDRED
    return HUNDRED - HUNDRED / (ONE + gain / loss)


def rolling_rsi(prices: Sequence[Decimal], period: int) -> Decimal:
    _period(period)
    if len(prices) < period + 1:
        raise ValueError("RSI warmup not complete")
    recent = prices[-(period + 1):]
    delta = [b - a for a, b in zip(recent, recent[1:])]
    return _rsi(sum((max(ZERO, x) for x in delta), ZERO),
                sum((max(ZERO, -x) for x in delta), ZERO))


def wilder_rsi(prices: Sequence[Decimal], period: int) -> Decimal:
    _period(period)
    if len(prices) < period + 1:
        raise ValueError("RSI warmup not complete")
    delta = [b - a for a, b in zip(prices, prices[1:])]
    gain = sum((max(ZERO, x) for x in delta[:period]), ZERO) / period
    loss = sum((max(ZERO, -x) for x in delta[:period]), ZERO) / period
    for x in delta[period:]:
        gain = (gain * (period - 1) + max(ZERO, x)) / period
        loss = (loss * (period - 1) + max(ZERO, -x)) / period
    return _rsi(gain, loss)


def _ema(prices: Sequence[Decimal], period: int) -> list[Decimal]:
    _period(period)
    alpha = Decimal("2") / (period + 1)
    output = [prices[0]]
    for price in prices[1:]:
        output.append(alpha * price + (ONE - alpha) * output[-1])
    return output


def _signal(hypothesis: str, symbol: str, at: datetime, stamps: tuple[EvidenceStamp, ...],
            *, candidate: bool, direction: str | None, details: dict, reason="UNVALIDATED_HYPOTHESIS") -> SignalEvidence:
    metadata = {"hypothesis_id": hypothesis, "symbol": symbol, "research_status": "HYPOTHESIS",
        "candidate": candidate, "candidate_direction": direction,
        "execution_authorized": False, "eligible_for_v51": False,
        "forecast_available": False, "cost_estimate_available": False, **details}
    provenance = [(s.source, s.observed_at.isoformat(), s.available_at.isoformat(), s.lineage) for s in stamps]
    identity = json.dumps([hypothesis, symbol, at.isoformat(), metadata, provenance], sort_keys=True, default=str)
    metadata["signal_id"] = sha256(identity.encode()).hexdigest()
    return SignalEvidence(asset=symbol.split("_")[0], timestamp=at,
        expected_return=0.0, expected_cost=0.0, risk_penalty=0.0,
        uncertainty_penalty=0.0, confidence=0.0, decision=Decision.NO_TRADE,
        reasons=(reason, "RESEARCH_ONLY_PAPER_OFF_LIVE_OFF"), evidence=stamps, metadata=metadata)


class RsiMaHypothesis:
    def __init__(self, *, rsi_period=14, ma_period=20, oversold=Decimal("30"),
                 bar_interval=timedelta(hours=1)):
        self.rsi_period, self.ma_period = _period(rsi_period), _period(ma_period)
        self.oversold = decimal_value(oversold, positive=True)
        if self.oversold >= HUNDRED:
            raise ValueError("oversold must be below 100")
        self.bar_interval = bar_interval

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[SignalEvidence]:
        history = _closed(candles, at, self.bar_interval)
        if len(history) < max(self.rsi_period + 1, self.ma_period):
            return []
        prices = [x.close for x in history]
        rsi = rolling_rsi(prices, self.rsi_period)
        ma = sum(prices[-self.ma_period:], ZERO) / self.ma_period
        candidate = prices[-1] > ma and rsi < self.oversold
        return [_signal("JAMALIAN_RSI_MA_V1", history[-1].symbol, at,
            tuple(c.stamp for c in history), candidate=candidate,
            direction="LONG" if candidate else None,
            details={"rsi": str(rsi), "ma": str(ma), "rsi_method": "ROLLING_SIMPLE",
                "rsi_period": self.rsi_period, "ma_period": self.ma_period,
                "oversold": str(self.oversold), "bar_interval_seconds": self.bar_interval.total_seconds()})]


class MacdRsiEmaHypothesis:
    def __init__(self, *, fast=12, slow=26, signal=9, ema_period=50, rsi_period=14,
                 bar_interval=timedelta(hours=4)):
        self.fast, self.slow, self.signal = _period(fast), _period(slow), _period(signal)
        self.ema_period, self.rsi_period = _period(ema_period), _period(rsi_period)
        if self.fast >= self.slow:
            raise ValueError("fast period must be below slow period")
        self.bar_interval = bar_interval

    def generate(self, candles: Sequence[Candle], at: datetime) -> list[SignalEvidence]:
        history = _closed(candles, at, self.bar_interval)
        if len(history) < max(self.slow + self.signal, self.ema_period, self.rsi_period + 1) + 1:
            return []
        prices = [x.close for x in history]
        macd = [a - b for a, b in zip(_ema(prices, self.fast), _ema(prices, self.slow))]
        signal = _ema(macd, self.signal)
        rsi = wilder_rsi(prices, self.rsi_period)
        ema = _ema(prices, self.ema_period)[-1]
        direction = None
        if macd[-2] <= signal[-2] and macd[-1] > signal[-1] and rsi > 50 and prices[-1] > ema:
            direction = "LONG"
        elif macd[-2] >= signal[-2] and macd[-1] < signal[-1] and rsi < 50 and prices[-1] < ema:
            direction = "SHORT"
        return [_signal("JAMALIAN_MACD_RSI_EMA_V1", history[-1].symbol, at,
            tuple(c.stamp for c in history), candidate=direction is not None,
            direction=direction, details={"macd": str(macd[-1]), "macd_signal": str(signal[-1]),
                "ema": str(ema), "rsi": str(rsi), "rsi_method": "WILDER_SMA_SEEDED",
                "fast": self.fast, "slow": self.slow, "signal_period": self.signal,
                "ema_period": self.ema_period, "rsi_period": self.rsi_period,
                "bar_interval_seconds": self.bar_interval.total_seconds(),
                "short_is_spot_authorization": False})]


@dataclass(frozen=True)
class InventoryState:
    free_base: Decimal
    free_quote: Decimal
    stamp: EvidenceStamp
    symbol: str

    def __post_init__(self):
        currencies(self.symbol)
        object.__setattr__(self, "free_base", decimal_value(self.free_base))
        object.__setattr__(self, "free_quote", decimal_value(self.free_quote))


class GridHypothesis:
    def __init__(self, *, lower: Decimal, upper: Decimal, intervals: int,
                 quantity: Decimal, price_tick=Decimal("0.01"), fee_rate=Decimal("0.001"),
                 max_age=timedelta(seconds=30)):
        self.lower, self.upper = decimal_value(lower, positive=True), decimal_value(upper, positive=True)
        self.quantity, self.price_tick = decimal_value(quantity, positive=True), decimal_value(price_tick, positive=True)
        self.fee_rate = decimal_value(fee_rate)
        self.intervals, self.max_age = _period(intervals), max_age
        if self.upper <= self.lower or self.fee_rate >= 1:
            raise ValueError("invalid grid bounds or fee")
        self.step = (self.upper - self.lower) / self.intervals
        if self.lower % self.price_tick or self.upper % self.price_tick or self.step % self.price_tick:
            raise ValueError("grid must exactly respect supplied price tick")

    def generate(self, book: OrderBook, inventory: InventoryState, at: datetime) -> list[SignalEvidence]:
        if inventory.symbol != book.symbol:
            raise ValueError("inventory currency pair must match the book")
        available(book.stamp, at, self.max_age)
        available(inventory.stamp, at, self.max_age)
        stamps = (book.stamp, inventory.stamp)
        mid = (book.bids[0].price + book.asks[0].price) / 2
        details = {"lower": str(self.lower), "upper": str(self.upper), "step": str(self.step),
            "quantity": str(self.quantity), "fee_rate": str(self.fee_rate),
            "inventory_state": "OBSERVED_FREE_BALANCES", "free_base": str(inventory.free_base),
            "free_quote": str(inventory.free_quote)}
        if book.bids[0].price < self.lower or book.asks[0].price > self.upper:
            return [_signal("JAMALIAN_GRID_V1", book.symbol, at, stamps,
                candidate=False, direction=None, details={**details, "grid_state": "SUSPENDED_BREAKOUT",
                    "outstanding_simulated_orders_require_cancel_review": True},
                reason="GRID_BREAKOUT_NO_NEW_CANDIDATES")]
        quote, base = inventory.free_quote, inventory.free_base
        output = []
        for i in range(self.intervals + 1):
            price = self.lower + self.step * i
            if price == mid:
                continue
            buy = price < mid
            required = self.quantity * price * (1 + self.fee_rate) if buy else self.quantity
            affordable = required <= (quote if buy else base)
            if affordable:
                if buy:
                    quote -= required
                else:
                    base -= required
            output.append(_signal("JAMALIAN_GRID_V1", book.symbol, at, stamps,
                candidate=affordable, direction=("LONG" if buy else "SELL_INVENTORY") if affordable else None,
                details={**details, "grid_state": "IN_RANGE", "level": i, "limit_price": str(price),
                    "reserved_asset_amount_scenario": str(required)},
                reason="UNVALIDATED_HYPOTHESIS" if affordable else "INSUFFICIENT_FREE_INVENTORY"))
        return output


class TriangularArbitrageHypothesis:
    """IRT->USDT->BTC->IRT scenario. Depth/fees do not make legs atomic."""
    SYMBOLS = ("USDT_IRT", "BTC_USDT", "BTC_IRT")

    def __init__(self, *, fee_rate=Decimal("0.004"), slippage_rate=Decimal("0"),
                 min_profit=Decimal("0"), max_age=timedelta(seconds=5), max_skew=timedelta(seconds=1)):
        self.fee_rate, self.slippage_rate = decimal_value(fee_rate), decimal_value(slippage_rate)
        self.min_profit = decimal_value(min_profit)
        if self.fee_rate >= 1 or self.slippage_rate >= 1 or max_skew < timedelta(0):
            raise ValueError("invalid fees, slippage or skew")
        self.max_age, self.max_skew = max_age, max_skew

    def generate(self, books: Sequence[OrderBook], investment_irt: Decimal, at: datetime) -> list[SignalEvidence]:
        investment = decimal_value(investment_irt, positive=True)
        if len(books) != 3 or {b.symbol for b in books} != set(self.SYMBOLS):
            raise ValueError("exact IRT triangle with three unique books required")
        mapping = {b.symbol: b for b in books}
        stamps = tuple(mapping[s].stamp for s in self.SYMBOLS)
        for stamp in stamps:
            available(stamp, at, self.max_age)
        times = [s.observed_at for s in stamps]
        if max(times) - min(times) > self.max_skew:
            raise ValueError("triangle snapshots are not synchronized")
        def convert(amount, levels, buy):
            output, remaining = ZERO, amount
            for level in levels:
                price = level.price * (1 + self.slippage_rate if buy else 1 - self.slippage_rate)
                if buy:
                    consumed = min(remaining, level.quantity * price)
                    output += consumed / price
                else:
                    consumed = min(remaining, level.quantity)
                    output += consumed * price
                remaining -= consumed
                if remaining == 0:
                    break
            return output * (1 - self.fee_rate), remaining == 0
        usdt, ok1 = convert(investment, mapping["USDT_IRT"].asks, True)
        btc, ok2 = convert(usdt, mapping["BTC_USDT"].asks, True)
        final_irt, ok3 = convert(btc, mapping["BTC_IRT"].bids, False)
        depth_ok = ok1 and ok2 and ok3
        profit = final_irt - investment
        return [_signal("JAMALIAN_TRIANGLE_V1", "BTC_IRT", at, stamps,
            candidate=depth_ok and profit > self.min_profit, direction="TRIANGLE" if depth_ok and profit > self.min_profit else None,
            details={"investment_irt": str(investment), "final_irt_scenario": str(final_irt),
                "profit_irt_scenario": str(profit), "fee_rate": str(self.fee_rate),
                "slippage_rate": str(self.slippage_rate), "depth_sufficient": depth_ok,
                "non_atomic": True, "residual_inventory_risk": True,
                "lot_rounding_modeled": False, "fee_convention": "DEDUCT_FROM_RECEIVED_ASSET",
                "scenario_is_executable_profit": False, "quote_unit": "IRT",
                "min_profit_irt": str(self.min_profit)},
            reason="NON_ATOMIC_UNVALIDATED_HYPOTHESIS" if depth_ok else "INSUFFICIENT_DEPTH")]
