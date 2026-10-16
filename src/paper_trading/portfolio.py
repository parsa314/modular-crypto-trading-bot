from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math


@dataclass
class Position:
    quantity: float = 0.0
    cost_basis: float = 0.0


@dataclass
class Portfolio:
    cash: float
    initial_cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    execution_impact_paid: float = 0.0
    peak_equity: float = 0.0
    daily_peak_equity: float = 0.0
    utc_day: str = ""
    last_equity: float = 0.0
    halt_reason: str | None = None
    last_timestamp: str | None = None
    last_signal_slot: str | None = None
    last_rebalance_slot: str | None = None
    strategy_state: dict = field(default_factory=dict)
    sequence: int = 0

    @classmethod
    def create(cls, cash: float) -> "Portfolio":
        if not math.isfinite(cash) or cash <= 0:
            raise ValueError("initial_cash must be positive")
        return cls(cash=cash, initial_cash=cash, peak_equity=cash,
                   daily_peak_equity=cash, last_equity=cash)

    @classmethod
    def from_dict(cls, value: dict) -> "Portfolio":
        fields = dict(value)
        fields["positions"] = {s: Position(**p) for s, p in fields["positions"].items()}
        return cls(**fields)

    def to_dict(self) -> dict:
        return asdict(self)

    def equity(self) -> float:
        missing = set(self.positions) - set(self.marks)
        if missing:
            raise ValueError(f"cannot value positions without prices: {sorted(missing)}")
        return self.cash + sum(p.quantity * self.marks[s] for s, p in self.positions.items())

    def mark(self, prices: dict[str, float], now: datetime,
             daily_limit: float = .05, total_limit: float = .15) -> dict:
        if now.tzinfo is None:
            raise ValueError("timestamp requires UTC timezone")
        for s, p in prices.items():
            if not math.isfinite(p) or p <= 0:
                raise ValueError(f"invalid price: {s}")
        day = now.strftime("%Y-%m-%d")
        if day != self.utc_day:
            self.daily_peak_equity = self.last_equity or self.initial_cash
            self.utc_day = day
        self.marks.update(prices)
        nav = self.equity()
        self.peak_equity = max(self.peak_equity, nav)
        self.daily_peak_equity = max(self.daily_peak_equity, nav)
        daily_dd = 1 - nav / self.daily_peak_equity
        total_dd = 1 - nav / self.peak_equity
        if daily_dd > daily_limit + 1e-12:
            self.halt_reason = "DAILY_DRAWDOWN_LIMIT"
        if total_dd > total_limit + 1e-12:
            self.halt_reason = "TOTAL_DRAWDOWN_LIMIT"
        self.last_equity = nav
        return {"equity": nav, "daily_drawdown": daily_dd, "total_drawdown": total_dd}

    def fill(self, symbol: str, quantity_delta: float, price: float, fee: float,
             reference: float) -> None:
        if not all(math.isfinite(x) for x in (quantity_delta, price, fee, reference)) or min(price, reference) <= 0 or fee < 0:
            raise ValueError("invalid simulated fill")
        old = self.positions.get(symbol, Position())
        if quantity_delta > 0:
            debit = quantity_delta * price + fee
            if debit > self.cash + 1e-7:
                raise ValueError("paper cash cannot be negative")
            self.cash = max(0.0, self.cash - debit)
            self.positions[symbol] = Position(old.quantity + quantity_delta, old.cost_basis + debit)
            self.execution_impact_paid += quantity_delta * (price - reference)
        elif quantity_delta < 0:
            sold = -quantity_delta
            if sold > old.quantity + 1e-10:
                raise ValueError("paper book cannot short")
            basis = old.cost_basis * min(1.0, sold / old.quantity)
            proceeds = sold * price - fee
            self.cash += proceeds
            self.realized_pnl += proceeds - basis
            remaining = old.quantity - sold
            if remaining < 1e-10:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = Position(remaining, old.cost_basis - basis)
            self.execution_impact_paid += sold * (reference - price)
        self.fees_paid += fee

    def snapshot(self) -> dict:
        nav = self.equity()
        return {**self.to_dict(), "equity": nav,
                "unrealized_pnl": sum(p.quantity * self.marks[s] - p.cost_basis for s, p in self.positions.items()),
                "net_pnl": nav - self.initial_cash,
                "weights": {s: p.quantity * self.marks[s] / nav for s, p in self.positions.items()} if nav > 0 else {}}
