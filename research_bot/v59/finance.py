from __future__ import annotations

import math
from statistics import mean

from .config import V59Config
from .contracts import (
    Direction,
    EconomicDecision,
    FinancialDecision,
    GateStatus,
    PortfolioState,
    SignalCandidate,
)


def historical_cvar95(returns: tuple[float, ...]) -> float | None:
    if len(returns) < 20:
        return None
    losses = sorted((-float(r) for r in returns), reverse=True)
    tail_n = max(1, math.ceil(0.05 * len(losses)))
    tail = [max(0.0, value) for value in losses[:tail_n]]
    return float(mean(tail))


def financial_gate(
    candidate: SignalCandidate,
    economics: EconomicDecision,
    state: PortfolioState,
    config: V59Config,
) -> FinancialDecision:
    if candidate.event_id != economics.event_id:
        raise ValueError("event identity mismatch between economics and finance")
    if state.timestamp != candidate.entry_time:
        raise ValueError("portfolio state must be stamped at exact candidate entry time")
    constitution = config.constitution

    def veto(reason: str, *, cvar: float | None = None, stop_risk: float = 0.0) -> FinancialDecision:
        return FinancialDecision(
            event_id=candidate.event_id,
            status=GateStatus.VETO,
            approved_notional=0.0,
            risk_budget=float(state.equity * constitution.risk_per_trade),
            stop_risk_fraction=float(stop_risk),
            projected_asset_weight=float(state.asset_exposure.get(candidate.symbol, 0.0) / state.equity),
            projected_gross_exposure=float(state.gross_exposure / state.equity),
            cvar95=cvar,
            drawdown=float(state.drawdown),
            reason=reason,
        )

    if economics.status is not GateStatus.PASS:
        return veto("UPSTREAM_GATE_NOT_PASSED")
    if candidate.direction is Direction.SHORT:
        return veto("SHORT_NOT_AUTHORIZED_PHASE1")
    if state.drawdown >= constitution.drawdown_kill:
        return veto("DRAWDOWN_KILL")

    cvar = historical_cvar95(state.recent_returns)
    if cvar is not None and cvar > constitution.max_cvar95:
        return veto("CVAR95_BREACH", cvar=cvar)

    entry = float(candidate.entry_price)
    stop = float(candidate.stop_price)
    stop_distance = abs(entry - stop) / entry
    cost_fraction = float(config.round_trip_cost_bps) / 10_000.0
    effective_stop_risk = stop_distance + cost_fraction
    if effective_stop_risk <= 0:
        return veto("INVALID_STOP_RISK", cvar=cvar)

    risk_budget = float(state.equity * constitution.risk_per_trade)
    requested_notional = risk_budget / effective_stop_risk

    current_asset = float(state.asset_exposure.get(candidate.symbol, 0.0))
    asset_capacity = max(0.0, constitution.max_asset_weight * state.equity - current_asset)
    gross_capacity = max(0.0, constitution.max_gross_exposure * state.equity - state.gross_exposure)
    cash_capacity = max(0.0, state.cash - constitution.min_cash_buffer * state.equity)
    turnover_capacity = constitution.max_turnover_per_step * state.equity

    approved = min(requested_notional, asset_capacity, gross_capacity, cash_capacity, turnover_capacity)
    if approved <= 0:
        if cash_capacity <= 0:
            reason = "INSUFFICIENT_CASH_BUFFER"
        elif asset_capacity <= 0:
            reason = "MAX_ASSET_WEIGHT"
        elif gross_capacity <= 0:
            reason = "MAX_GROSS_EXPOSURE"
        else:
            reason = "TURNOVER_LIMIT"
        return veto(reason, cvar=cvar, stop_risk=effective_stop_risk)

    projected_asset_weight = (current_asset + approved) / state.equity
    projected_gross = (state.gross_exposure + approved) / state.equity
    if projected_asset_weight > constitution.max_asset_weight + 1e-12:
        raise RuntimeError("internal asset-weight projection violated constitution")
    if projected_gross > constitution.max_gross_exposure + 1e-12:
        raise RuntimeError("internal gross-exposure projection violated constitution")

    return FinancialDecision(
        event_id=candidate.event_id,
        status=GateStatus.PASS,
        approved_notional=float(approved),
        risk_budget=risk_budget,
        stop_risk_fraction=float(effective_stop_risk),
        projected_asset_weight=float(projected_asset_weight),
        projected_gross_exposure=float(projected_gross),
        cvar95=cvar,
        drawdown=float(state.drawdown),
        reason="FINANCIAL_CONSTITUTION_PASSED",
    )
