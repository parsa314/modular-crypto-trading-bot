from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from .config import V59Config
from .contracts import PortfolioState, SignalCandidate
from .hashing import stable_hash


@dataclass(frozen=True)
class LiquiditySnapshot:
    symbol: str
    observed_at: datetime
    bid: float
    ask: float
    depth_notional_10bps: float
    source: str
    source_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        for name in ("bid", "ask", "depth_notional_10bps"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not float(self.bid) < float(self.ask):
            raise ValueError("bid must be strictly below ask")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source is required")
        if not isinstance(self.source_hash, str) or not self.source_hash.strip():
            raise ValueError("source_hash is required")

    @property
    def mid(self) -> float:
        return (float(self.bid) + float(self.ask)) / 2.0

    @property
    def spread_bps(self) -> float:
        return (float(self.ask) - float(self.bid)) / self.mid * 10_000.0


@dataclass(frozen=True)
class ExecutionAssumptions:
    taker_fee_bps_per_side: float = 5.0
    slippage_stress_bps_per_side: float = 2.0
    latency_stress_bps_per_side: float = 1.0
    impact_coefficient_bps: float = 8.0
    max_impact_bps_per_side: float = 50.0
    max_depth_participation: float = 0.20
    min_fill_fraction: float = 0.80
    max_liquidity_age_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "taker_fee_bps_per_side",
            "slippage_stress_bps_per_side",
            "latency_stress_bps_per_side",
            "impact_coefficient_bps",
            "max_impact_bps_per_side",
            "max_liquidity_age_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("max_depth_participation", "min_fill_fraction"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0,1]")


@dataclass(frozen=True)
class ExecutionCostEstimate:
    event_id: str
    reference_notional: float
    max_fillable_notional: float
    fill_fraction: float
    spread_bps_round_trip: float
    fee_bps_round_trip: float
    slippage_bps_round_trip: float
    latency_bps_round_trip: float
    impact_bps_round_trip: float
    total_round_trip_bps: float
    liquidity_pass: bool
    source_classification: str
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id is required")
        for name in (
            "reference_notional", "max_fillable_notional", "fill_fraction",
            "spread_bps_round_trip", "fee_bps_round_trip",
            "slippage_bps_round_trip", "latency_bps_round_trip",
            "impact_bps_round_trip", "total_round_trip_bps",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.fill_fraction > 1:
            raise ValueError("fill_fraction cannot exceed one")
        if not isinstance(self.source_classification, str) or not self.source_classification.strip():
            raise ValueError("source_classification is required")
        if not isinstance(self.snapshot_hash, str) or not self.snapshot_hash.strip():
            raise ValueError("snapshot_hash is required")


def estimate_execution_cost(
    *,
    candidate: SignalCandidate,
    portfolio: PortfolioState,
    liquidity: LiquiditySnapshot,
    config: V59Config,
    assumptions: ExecutionAssumptions | None = None,
) -> ExecutionCostEstimate:
    assumptions = ExecutionAssumptions() if assumptions is None else assumptions
    if liquidity.symbol != candidate.symbol:
        raise ValueError("liquidity symbol does not match candidate")
    if liquidity.observed_at > candidate.decision_at:
        raise ValueError("future liquidity snapshot is forbidden")
    age = (candidate.decision_at - liquidity.observed_at).total_seconds()
    if age < 0 or age > assumptions.max_liquidity_age_seconds:
        raise ValueError("liquidity snapshot is stale")
    if portfolio.timestamp != candidate.entry_time:
        raise ValueError("portfolio state must be stamped at candidate entry time")

    constitution = config.constitution
    equity = float(portfolio.equity)
    entry = float(candidate.entry_price)
    stop = float(candidate.stop_price)
    stop_fraction = abs(entry - stop) / entry

    spread_rt = float(liquidity.spread_bps)
    fee_rt = 2.0 * assumptions.taker_fee_bps_per_side
    slippage_rt = 2.0 * assumptions.slippage_stress_bps_per_side
    latency_rt = 2.0 * assumptions.latency_stress_bps_per_side
    fixed_cost_fraction = (spread_rt + fee_rt + slippage_rt + latency_rt) / 10_000.0

    risk_budget = equity * constitution.risk_per_trade
    risk_based_notional = risk_budget / max(stop_fraction + fixed_cost_fraction, 1e-12)
    current_asset = float(portfolio.asset_exposure.get(candidate.symbol, 0.0))
    asset_capacity = max(0.0, constitution.max_asset_weight * equity - current_asset)
    gross_capacity = max(0.0, constitution.max_gross_exposure * equity - float(portfolio.gross_exposure))
    cash_capacity = max(0.0, float(portfolio.cash) - constitution.min_cash_buffer * equity)
    turnover_capacity = constitution.max_turnover_per_step * equity
    reference_notional = min(
        risk_based_notional,
        asset_capacity,
        gross_capacity,
        cash_capacity,
        turnover_capacity,
    )
    reference_notional = max(0.0, float(reference_notional))

    capacity = float(liquidity.depth_notional_10bps) * assumptions.max_depth_participation
    max_fillable = min(reference_notional, capacity)
    fill_fraction = 0.0 if reference_notional <= 0 else max_fillable / reference_notional

    participation = 0.0
    if liquidity.depth_notional_10bps > 0:
        participation = max_fillable / float(liquidity.depth_notional_10bps)
    impact_per_side = min(
        assumptions.max_impact_bps_per_side,
        assumptions.impact_coefficient_bps * math.sqrt(max(participation, 0.0)),
    )
    impact_rt = 2.0 * impact_per_side
    total_rt = spread_rt + fee_rt + slippage_rt + latency_rt + impact_rt
    liquidity_pass = reference_notional > 0 and fill_fraction >= assumptions.min_fill_fraction

    snapshot_hash = stable_hash(
        {
            "symbol": liquidity.symbol,
            "observed_at": liquidity.observed_at,
            "bid": liquidity.bid,
            "ask": liquidity.ask,
            "depth_notional_10bps": liquidity.depth_notional_10bps,
            "source": liquidity.source,
            "source_hash": liquidity.source_hash,
        }
    )
    return ExecutionCostEstimate(
        event_id=candidate.event_id,
        reference_notional=reference_notional,
        max_fillable_notional=max_fillable,
        fill_fraction=fill_fraction,
        spread_bps_round_trip=spread_rt,
        fee_bps_round_trip=fee_rt,
        slippage_bps_round_trip=slippage_rt,
        latency_bps_round_trip=latency_rt,
        impact_bps_round_trip=impact_rt,
        total_round_trip_bps=total_rt,
        liquidity_pass=liquidity_pass,
        source_classification="OBSERVED_BID_ASK_DEPTH_WITH_STRESS_COMPONENTS",
        snapshot_hash=snapshot_hash,
    )
