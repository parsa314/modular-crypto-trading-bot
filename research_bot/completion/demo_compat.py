"""Verified boundary for the supplied complete_demo_bot contracts and SQLite schema.

Legacy REAL tables are a lossy compatibility projection. Exact monetary events
and restart state remain in the existing completion journal, never reconstructed
from REAL columns. No legacy neural checkpoint is admitted by this bridge.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import math
from typing import Any, Mapping, Protocol

from ..contracts import EvidenceStamp
from ..hypothesis import HypothesisCandidate, HypothesisFamily, HypothesisSide, canonical_json, utc_text
from ..jamalian_data import OrderBook, available, decimal_value
from .journal import TradeJournal
from .models import ZERO, ONE, ScenarioEconomics
from .execution.cost_preflight import ExecutionCostPreflight
from .execution.event_driven_simulator import EventDrivenExecutionSimulator, OrderRequest, TERMINAL
from .execution.impact_estimator import MarketSnapshot
from .portfolio.conflict_arbiter import PortfolioView


class LegacyHypothesis(Protocol):
    hypothesis_id: str
    strategy_id: str
    asset: str
    side: Any
    timestamp: datetime
    expected_return: float
    expected_cost: float
    risk_penalty: float
    uncertainty_penalty: float
    confidence: float
    reasons: tuple[str, ...]
    metadata: Mapping[str, Any]


def convert_demo_hypothesis(hypothesis: LegacyHypothesis, stamp: EvidenceStamp) -> HypothesisCandidate:
    """Preserve the original object; require explicit provenance, never add sizing.

    Legacy dimensionless floats are allowed only at this audited boundary. Their
    forecasts remain UNCALIBRATED. Monetary sizing/depth must come separately as
    Decimal. No legacy triangular BUY is interpreted as a one-pair trade.
    """
    available(stamp, hypothesis.timestamp)
    economics = {}
    for name in ("expected_return", "expected_cost", "risk_penalty", "uncertainty_penalty", "confidence"):
        value = getattr(hypothesis, name)
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) or not Decimal(str(value)).is_finite():
            raise ValueError("finite legacy dimensionless values required")
        economics[name] = str(value)
    ScenarioEconomics(**economics)
    if hypothesis.side.value not in {"buy", "sell"}:
        raise ValueError("unsupported legacy side")
    triangular = hypothesis.strategy_id == "tri_arb_v1"
    return HypothesisCandidate(hypothesis.strategy_id,
        HypothesisFamily.TRIANGULAR_ARBITRAGE if triangular else HypothesisFamily.GRID
        if hypothesis.strategy_id == "grid_v1" else HypothesisFamily.TECHNICAL_BASELINE,
        hypothesis.asset, HypothesisSide.MULTI_LEG if triangular else HypothesisSide(hypothesis.side.value.upper()),
        hypothesis.timestamp, hypothesis.timestamp + timedelta(milliseconds=20),
        tuple(hypothesis.reasons) or ("LEGACY_UNCALIBRATED_HYPOTHESIS",), (stamp,),
        {"adapter": "complete_demo_contract_v1"},
        {"legacy_hypothesis_id": hypothesis.hypothesis_id, "legacy_metadata": dict(hypothesis.metadata),
            "legacy_economics": economics, "calibration_status": "NOT_CALIBRATED", "execution_authorized": False})


@dataclass(frozen=True)
class DemoLimits:
    max_position_pct: Decimal = Decimal("0.10")
    max_open_positions: int = 3
    max_daily_loss_pct: Decimal = Decimal("0.03")
    cooldown: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        for name, ceiling in (("max_position_pct", Decimal("0.10")), ("max_daily_loss_pct", Decimal("0.03"))):
            value = decimal_value(getattr(self, name), positive=True)
            if value > ceiling:
                raise ValueError("cannot weaken supplied demo risk limits")
            object.__setattr__(self, name, value)
        if type(self.max_open_positions) is not int or not 1 <= self.max_open_positions <= 3 or self.cooldown < timedelta(seconds=30):
            raise ValueError("cannot weaken demo capacity or cooldown")


class DemoExecutionAdmission:
    """Extra preflight after the unchanged portfolio/DecisionEngine gates.

    Statistics are explicit synthetic scenarios here. This adapter is not a
    calibrated real-data research admission gate and refuses multi-leg fallback.
    """
    def __init__(self, preflight: ExecutionCostPreflight, volatility_24h: Decimal,
                 daily_volume_quote: Decimal, limits: DemoLimits = DemoLimits()) -> None:
        self.preflight, self.limits = preflight, limits
        self.volatility = decimal_value(volatility_24h, positive=True)
        self.volume = decimal_value(daily_volume_quote, positive=True)

    def configuration(self) -> dict:
        return {"kind": "SYNTHETIC_DEMO_ADMISSION", "volatility_24h": str(self.volatility),
            "daily_volume_quote": str(self.volume), "limits": {k: str(v) for k, v in self.limits.__dict__.items()},
            "estimator": self.preflight.estimator.to_dict(), "liquidity": self.preflight.gate.to_dict(),
            "resting_limit_depth": "DISPLAYED_BAND_PROXY_NOT_FILLABLE_DEPTH; simulator enforces limit on later books",
            "hpt": "DISABLED_UNVERIFIED_CHECKPOINT", "multileg": "REFUSE_SINGLE_PAIR_FALLBACK"}

    def reject_multileg(self, candidate: HypothesisCandidate, at: datetime) -> None:
        self._record(candidate, at, "MULTILEG_REQUIRES_PER_LEG_ADMISSION", False)

    def _record(self, candidate: HypothesisCandidate, at: datetime, reason: str, approved: bool) -> None:
        self.preflight.journal.append("demo_admission:" + candidate.hypothesis_id, "DEMO_ADMISSION", at,
            {"hypothesis_id": candidate.hypothesis_id, "strategy_id": candidate.strategy_id,
                "approved": approved, "reason": reason, "configuration": self.configuration(), "live_execution": False})

    def assess(self, request: OrderRequest, candidate: HypothesisCandidate, view: PortfolioView,
               book: OrderBook, simulator: EventDrivenExecutionSimulator, at: datetime) -> OrderRequest | None:
        reason = ""
        events = self.preflight.journal.events()
        fills = [e for e in events if e["kind"] == "ORDER_TRANSITION" and e["payload"].get("fill")]
        start = next((Decimal(e["payload"]["equity"]) for e in events if e["kind"] == "REPLAY_FRAME"
            and datetime.fromisoformat(e["timestamp"]).date() == at.date()), view.equity)
        notional = request.quantity * request.protection_price * view.marks[request.symbol.split("_")[1]]
        if simulator.policy.purpose != "SYNTHETIC_ENGINEERING":
            reason = "SYNTHETIC_STATISTICS_NOT_RESEARCH_EVIDENCE"
        elif candidate.strategy_id.lower().startswith("hpt"):
            reason = "UNVERIFIED_HPT_CHECKPOINT"
        elif notional > self.limits.max_position_pct * view.equity:
            reason = "DEMO_POSITION_TOO_LARGE"
        elif sum(o.state not in TERMINAL for o in simulator.orders()) >= self.limits.max_open_positions:
            reason = "DEMO_MAX_OPEN_POSITIONS"
        elif view.equity <= start * (ONE - self.limits.max_daily_loss_pct):
            reason = "DEMO_DAILY_LOSS_LIMIT"
        elif fills and at - datetime.fromisoformat(fills[-1]["timestamp"]) < self.limits.cooldown:
            reason = "DEMO_COOLDOWN"
        economics = ScenarioEconomics.from_metadata(candidate.metadata)
        if reason or economics is None:
            self._record(candidate, at, reason or "UNCALIBRATED_OR_UNSIZED_HYPOTHESIS", False)
            return None
        snapshot = MarketSnapshot(request.symbol, (book.bids[0].price + book.asks[0].price) / 2,
            self.volatility, self.volume, book.stamp)
        marketable = (request.protection_price >= book.asks[0].price if request.side == "BUY"
            else request.protection_price <= book.bids[0].price)
        # A resting grid limit has no currently executable depth. Use displayed
        # depth only as a liquidity proxy, never as evidence of a future fill.
        # The simulator retains the original protection and waits for a new book.
        result = self.preflight.assess(candidate.hypothesis_id, snapshot, book, request.side,
            request.quantity, economics.net_edge * Decimal(10000),
            simulator.balances().get(request.symbol.split("_")[0], ZERO), at, request.rules,
            request.protection_price if marketable else None)
        impact = result.impact.estimated_impact_bps
        # Preserve canonical 5bps minimum net alpha AFTER this additional scenario cost.
        if not result.passed or impact is None or economics.net_edge * Decimal(10000) - impact < Decimal(5):
            self._record(candidate, at, "COST_LIQUIDITY_OR_POST_COST_ALPHA_REJECTED", False)
            return None
        if simulator.policy.slippage_rate * Decimal(10000) + impact > Decimal(25):
            self._record(candidate, at, "TOTAL_SLIPPAGE_EXCEEDS_UNCHANGED_LIMIT", False)
            return None
        self._record(candidate, at, "APPROVED_WITH_EXPLICIT_RESIDUAL_IMPACT_SCENARIO", True)
        return replace(request, additional_impact_bps=impact)


LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
order_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, symbol TEXT NOT NULL,
side TEXT NOT NULL, order_type TEXT NOT NULL, qty REAL NOT NULL, limit_price REAL,
status TEXT NOT NULL, strategy_id TEXT, hypothesis_id TEXT);
CREATE TABLE IF NOT EXISTS paper_fills (
fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, ts TEXT NOT NULL, price REAL NOT NULL,
qty REAL NOT NULL, fee REAL NOT NULL, FOREIGN KEY(order_id) REFERENCES paper_orders(order_id));
CREATE TABLE IF NOT EXISTS equity_curve (ts TEXT PRIMARY KEY, equity REAL NOT NULL, cash REAL NOT NULL, pos_val REAL NOT NULL);
CREATE TABLE IF NOT EXISTS signals (ts TEXT NOT NULL, strategy_id TEXT, decision TEXT, net_alpha REAL, confidence REAL, reasons TEXT);
"""


def _real(value: str | Decimal) -> float:
    result = float(Decimal(value))
    if not math.isfinite(result):
        raise ValueError("exact amount cannot be projected to finite SQLite REAL")
    return result


class DemoJournalProjection:
    def __init__(self, journal: TradeJournal, quote_currency: str = "USDT") -> None:
        self.journal, self.quote = journal, quote_currency
        if not quote_currency.isalnum() or quote_currency.upper() != quote_currency:
            raise ValueError("uppercase quote currency required")
        expected = {"paper_orders": ("order_id", "created_at", "symbol", "side", "order_type", "qty", "limit_price", "status", "strategy_id", "hypothesis_id"),
            "paper_fills": ("fill_id", "order_id", "ts", "price", "qty", "fee"),
            "equity_curve": ("ts", "equity", "cash", "pos_val"),
            "signals": ("ts", "strategy_id", "decision", "net_alpha", "confidence", "reasons")}
        with journal.atomic():
            for statement in LEGACY_SCHEMA.split(";"):
                if statement.strip():
                    journal.connection.execute(statement)
            for table, columns in expected.items():
                if tuple(row[1] for row in journal.connection.execute("PRAGMA table_info(" + table + ")")) != columns:
                    raise ValueError("unknown legacy schema; automatic migration refused")
        saved = journal.restore("demo_projection")
        if saved and saved["quote"] != self.quote:
            raise ValueError("projection quote changed")

    def sync(self) -> None:
        events = self.journal.events()
        saved = self.journal.restore("demo_projection") or {"count": 0, "quote": self.quote}
        if saved["count"] > len(events):
            raise ValueError("projection source events disappeared")
        with self.journal.atomic():
            for event in events[saved["count"]:]:
                p = event["payload"]
                if event["kind"] == "ORDER_TRANSITION":
                    r = p.get("request")
                    if not r:
                        raise ValueError("old transition lacks request provenance; use a new namespace")
                    if r["symbol"].split("_")[1] != self.quote:
                        continue  # Exact multi-currency events remain in the authoritative ledger.
                    identity = "COMP:" + self.journal.experiment_id + ":" + r["client_order_id"]
                    row = (identity, r["created_at"], r["symbol"], r["side"].lower(), "limit", _real(r["quantity"]),
                        _real(r["protection_price"]), p["state"], r["strategy_id"], r["client_order_id"])
                    existing = self.journal.connection.execute("SELECT * FROM paper_orders WHERE order_id=?", (identity,)).fetchone()
                    if existing and tuple(existing[:7]) + tuple(existing[8:]) != row[:7] + row[8:]:
                        raise ValueError("legacy order identity collision; existing history preserved")
                    self.journal.connection.execute("INSERT INTO paper_orders VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET status=excluded.status", row)
                    if p.get("fill"):
                        f = p["fill"]
                        fee = Decimal(f["fee"])
                        if f["fee_currency"] != self.quote:
                            fee *= Decimal(f["price"])  # Quote equivalent, not an additional cash charge.
                        self.journal.connection.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?)",
                            (identity + ":" + event["event_id"], identity, event["timestamp"],
                                _real(f["price"]), _real(f["quantity"]), _real(fee)))
                elif event["kind"] == "ARBITRATION":
                    self.journal.connection.execute("INSERT INTO signals VALUES(?,?,?,?,?,?)",
                        (event["timestamp"], p.get("strategy_id"), p.get("decision"),
                            _real(p["net_edge"]) if p.get("net_edge") is not None else None, None, canonical_json(p)))
                elif event["kind"] == "REPLAY_FRAME":
                    if p.get("quote_currency") != self.quote:
                        raise ValueError("legacy equity projection requires the same quote unit")
                    cash = Decimal(p["balances"].get(self.quote, "0"))
                    # Existing history wins on timestamp collision; exact new curve is namespaced.
                    self.journal.connection.execute("INSERT OR IGNORE INTO equity_curve VALUES(?,?,?,?)",
                        (event["timestamp"], _real(p["equity"]), _real(cash), _real(Decimal(p["equity"]) - cash)))
            self.journal.checkpoint("demo_projection", {"count": len(events), "quote": self.quote,
                "interpretation": "LOSSY_REAL_PROJECTION_FEES_QUOTE_EQUIVALENT"})
