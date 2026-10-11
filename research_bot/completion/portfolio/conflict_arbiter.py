from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Mapping, Sequence

from ...contracts import Decision, EvidenceStamp, SignalEvidence
from ...decision import DecisionEngine
from ...hypothesis import HypothesisCandidate, HypothesisSide, canonical_json
from ...jamalian_data import currencies, decimal_value, available
from ...risk import RiskLimits
from ..models import Inventory, ScenarioEconomics, ZERO, ONE, signed_decimal
from ..journal import TradeJournal


@dataclass(frozen=True)
class PortfolioLimits:
    max_drawdown: Decimal = Decimal("0.12")
    max_gross_exposure: Decimal = Decimal("1")
    max_asset_weight: Decimal = Decimal("0.35")
    max_turnover: Decimal = Decimal("0.50")
    max_spread: Decimal = Decimal("0.0035")
    max_slippage: Decimal = Decimal("0.0025")
    max_cvar: Decimal = Decimal("0.035")
    min_cash_buffer: Decimal = Decimal("0.05")
    max_positions: int = 3

    def __post_init__(self) -> None:
        core = RiskLimits()
        ceilings = {"max_drawdown": core.max_drawdown, "max_gross_exposure": core.max_gross_exposure,
            "max_asset_weight": core.max_asset_weight, "max_turnover": core.max_turnover_per_step,
            "max_spread": core.max_spread_bps / 10000, "max_slippage": core.max_slippage_bps / 10000,
            "max_cvar": core.max_cvar_95}
        for name in self.__dataclass_fields__:
            if name == "max_positions":
                continue
            value = decimal_value(getattr(self, name), positive=True)
            object.__setattr__(self, name, value)
            if name in ceilings and value > Decimal(str(ceilings[name])):
                raise ValueError("completion layer cannot increase existing risk limits")
        if self.min_cash_buffer < Decimal(str(core.min_cash_buffer)) or self.min_cash_buffer >= ONE:
            raise ValueError("cash buffer cannot be weakened")
        if type(self.max_positions) is not int or not 1 <= self.max_positions <= 3:
            raise ValueError("position capacity cannot exceed the existing independent arbiter")


@dataclass(frozen=True)
class PortfolioView:
    inventory: Inventory
    marks: Mapping[str, Decimal]  # Prices in quote_currency, including all cash currencies.
    mark_stamp: EvidenceStamp
    quote_currency: str
    peak_equity: Decimal
    cash_currencies: tuple[str, ...] = ("USDT", "IRT")
    active_assets: tuple[str, ...] = ()
    active_ids: tuple[str, ...] = ()
    recent_returns: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        from types import MappingProxyType
        marks = {k: decimal_value(v, positive=True) for k, v in self.marks.items()}
        if marks.get(self.quote_currency) != ONE or not set(self.inventory.balances).issubset(marks):
            raise ValueError("complete point-in-time quote valuations required")
        object.__setattr__(self, "marks", MappingProxyType(marks))
        object.__setattr__(self, "peak_equity", decimal_value(self.peak_equity, positive=True))
        object.__setattr__(self, "recent_returns", tuple(signed_decimal(x) for x in self.recent_returns))

    @property
    def equity(self) -> Decimal:
        return sum((v * self.marks[k] for k, v in self.inventory.balances.items()), ZERO)


@dataclass(frozen=True)
class RejectedHypothesis:
    candidate: HypothesisCandidate
    reason: str


@dataclass(frozen=True)
class ArbitrationResult:
    approved: tuple[HypothesisCandidate, ...]
    rejected: tuple[RejectedHypothesis, ...]
    scores: Mapping[str, Decimal]
    signals: Mapping[str, SignalEvidence]


class RiskGate:
    """Decimal pre-trade constraints, capped by the canonical RiskLimits defaults."""
    def __init__(self, limits: PortfolioLimits | None = None) -> None:
        self.limits = limits or PortfolioLimits()

    def check(self, view: PortfolioView, at: datetime) -> str | None:
        available(view.inventory.stamp, at, timedelta(seconds=30))
        available(view.mark_stamp, at, timedelta(seconds=30))
        if view.equity <= ZERO or ONE - view.equity / view.peak_equity >= self.limits.max_drawdown:
            return "DRAWDOWN_OR_EQUITY_LIMIT"
        if len(view.recent_returns) >= 20:
            losses = sorted((-r for r in view.recent_returns), reverse=True)
            n_tail = max(1, (len(losses) + 19) // 20)
            if sum(losses[:n_tail], ZERO) / n_tail > self.limits.max_cvar:
                return "CVAR_LIMIT"
        return None


class PortfolioConflictArbiter:
    def __init__(self, journal: TradeJournal, decision_engine: DecisionEngine | None = None,
                 risk_gate: RiskGate | None = None) -> None:
        self.journal, self.engine, self.risk_gate = journal, decision_engine or DecisionEngine(), risk_gate or RiskGate()
        state = journal.restore("arbiter") or {"recent": [], "decided": []}
        self.recent, self.decided = list(state["recent"]), set(state["decided"])

    def resolve(self, candidates: Sequence[HypothesisCandidate], view: PortfolioView, at: datetime,
                correlations: Mapping[tuple[str, str], Decimal] | None = None,
                regime_fit: Mapping[str, Decimal] | None = None,
                context_stamp: EvidenceStamp | None = None) -> ArbitrationResult:
        before = list(self.recent), set(self.decided)
        try:
            return self._resolve(candidates, view, at, correlations, regime_fit, context_stamp)
        except BaseException:
            self.recent, self.decided = before
            raise

    def _resolve(self, candidates: Sequence[HypothesisCandidate], view: PortfolioView, at: datetime,
                 correlations: Mapping[tuple[str, str], Decimal] | None,
                 regime_fit: Mapping[str, Decimal] | None, context_stamp: EvidenceStamp | None) -> ArbitrationResult:
        if len({h.hypothesis_id for h in candidates}) != len(candidates):
            raise ValueError("duplicate candidates in arbitration batch")
        if correlations or regime_fit:
            if context_stamp is None:
                raise ValueError("PIT context stamp required for regime/correlation scores")
            available(context_stamp, at, timedelta(seconds=30))
        correlations, regime_fit = correlations or {}, regime_fit or {}
        if any(abs(signed_decimal(v)) > ONE for v in correlations.values()) or any(decimal_value(v) > ONE for v in regime_fit.values()):
            raise ValueError("bounded correlation/regime estimates required")
        hard_reason = self.risk_gate.check(view, at)
        limits, equity = self.risk_gate.limits, view.equity
        records, signals, scores = [], {}, {}
        for h in candidates:
            if h.timestamp != at:
                raise ValueError("arbiter only ranks the current simultaneous decision cohort")
            economics = ScenarioEconomics.from_metadata(h.metadata)
            fingerprint = f"{h.strategy_id}:{h.symbol}:{h.side.value}"
            uniqueness = ONE / (1 + self.recent.count(fingerprint))
            plans = h.metadata.get("plan", ())
            involved = sorted({c for plan in plans for c in currencies(plan["symbol"]) if c not in view.cash_currencies})
            required = {}
            if plans:
                plan = plans[0]
                base, quote = currencies(plan["symbol"])
                currency = quote if plan["side"] == "BUY" else base
                amount = Decimal(plan["quantity"]) * Decimal(plan["limit_price"]) if plan["side"] == "BUY" else Decimal(plan["quantity"])
                required[currency] = amount
            notional = sum((amount * view.marks[currency] for currency, amount in required.items()), ZERO)
            edge = economics.net_edge if economics else ZERO
            regime = decimal_value(regime_fit.get(h.strategy_id, ZERO))
            correlation = max((abs(signed_decimal(correlations.get((a, b), ONE if a == b else ZERO)))
                for a in involved for b in view.active_assets), default=ZERO)
            efficiency = edge * equity / notional if notional > ZERO and equity > ZERO else ZERO
            score = edge + Decimal("0.001") * uniqueness + Decimal("0.001") * regime + Decimal("0.00001") * efficiency - Decimal("0.002") * correlation
            scores[h.hypothesis_id] = score
            # Existing DecisionEngine's float return/confidence contract is preserved
            # at this dimensionless boundary; balances/prices/scoring stay Decimal.
            selling = h.side is HypothesisSide.SELL
            signal = self.engine.evaluate(asset=h.symbol.split("_")[0], timestamp=at,
                expected_return=float(-economics.expected_return if selling else economics.expected_return) if economics else 0,
                expected_cost=float(economics.expected_cost) if economics else 0,
                risk_penalty=float(economics.risk_penalty) if economics else 0,
                uncertainty_penalty=float(economics.uncertainty_penalty) if economics else 0,
                confidence=float(economics.confidence) if economics else 0,
                currently_long=selling, evidence=h.evidence,
                metadata={"hypothesis_id": h.hypothesis_id, "execution_authorized": False,
                    "interpretation": "SCENARIO_PAPER_ONLY", "eligible_for_v51": False})
            signals[h.hypothesis_id] = signal
            records.append((h, fingerprint, uniqueness, regime, correlation, notional, involved, required, signal, economics))
        records.sort(key=lambda r: (-scores[r[0].hypothesis_id], r[0].hypothesis_id))
        cash = {c: view.inventory.free(c) for c in view.inventory.balances}
        exposure = {c: view.inventory.balances[c] * view.marks[c] for c in view.inventory.balances if c not in view.cash_currencies}
        active = set(view.active_assets)
        positions, turnover = len(view.active_ids), ZERO
        approved, rejected = [], []
        with self.journal.atomic():
            for h, fingerprint, uniqueness, regime, correlation, notional, involved, required, signal, economics in records:
                reason = hard_reason
                reducing = h.side is HypothesisSide.SELL
                if reason is None:
                    if h.hypothesis_id in self.decided:
                        reason = "DUPLICATE_HYPOTHESIS"
                    elif not economics or notional <= ZERO:
                        reason = "UNCALIBRATED_OR_UNSIZED_HYPOTHESIS"
                    elif economics.net_edge <= ZERO or signal.decision not in {Decision.BUY_CANDIDATE, Decision.EXIT}:
                        reason = "DECISION_ABSTAINED"
                    elif any(amount > cash.get(c, ZERO) for c, amount in required.items()):
                        reason = "CAPITAL_EXHAUSTED"
                    elif not reducing and any(c in active for c in involved):
                        reason = "ASSET_CONFLICT"
                    elif positions >= limits.max_positions:
                        reason = "POSITION_CAPACITY"
                    elif turnover + notional > limits.max_turnover * equity:
                        reason = "TURNOVER_LIMIT"
                    elif decimal_value(h.metadata.get("market_spread_fraction", "0")) > limits.max_spread:
                        reason = "SPREAD_LIMIT"
                    elif decimal_value(h.metadata.get("depth_slippage_fraction", "0")) > limits.max_slippage:
                        reason = "SLIPPAGE_LIMIT"
                    elif not reducing and any(exposure.get(c, ZERO) + notional > limits.max_asset_weight * equity for c in involved):
                        reason = "CONCENTRATION_LIMIT"
                    elif not reducing and sum(exposure.values(), ZERO) + notional > limits.max_gross_exposure * equity:
                        reason = "GROSS_EXPOSURE_LIMIT"
                    elif not reducing and sum((cash[c] * view.marks[c] for c in view.cash_currencies if c in cash), ZERO) - notional < limits.min_cash_buffer * equity:
                        reason = "CASH_BUFFER_LIMIT"
                admitted = reason is None
                if admitted:
                    approved.append(h)
                    positions += 1
                    turnover += notional
                    for c, amount in required.items():
                        cash[c] -= amount
                    for c in involved:
                        exposure[c] = max(ZERO, exposure.get(c, ZERO) + (-notional if reducing else notional))
                        active.add(c)
                else:
                    rejected.append(RejectedHypothesis(h, reason))
                record = {"hypothesis_id": h.hypothesis_id, "strategy_id": h.strategy_id,
                    "approved_for_simulation": admitted, "reason": reason or "APPROVED_SIMULATION_ONLY",
                    "score": str(scores[h.hypothesis_id]), "net_edge": str(economics.net_edge) if economics else None,
                    "uniqueness": str(uniqueness), "regime_fit": str(regime), "regime_available": bool(regime_fit),
                    "correlation_penalty": str(correlation), "notional_quote": str(notional),
                    "decision": signal.decision.value, "execution_authorized": False}
                identity = sha256(canonical_json([record, at.isoformat(), view.inventory.stamp.lineage]).encode()).hexdigest()
                self.journal.append("arbitration:" + identity, "ARBITRATION", at, record)
                self.recent.append(fingerprint)
                self.decided.add(h.hypothesis_id)
            self.recent = self.recent[-128:]
            self.journal.checkpoint("arbiter", {"recent": self.recent, "decided": sorted(self.decided)})
        from types import MappingProxyType
        return ArbitrationResult(tuple(approved), tuple(rejected), MappingProxyType(scores), MappingProxyType(signals))
