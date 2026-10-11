"""Pure snapshot diagnostics, ENGINEERING_EXTENSION; no fill or impact claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .contracts import BookSnapshot, UnsupportedCapability, decimal

ZERO = Decimal(0)
ONE = Decimal(1)
BPS = Decimal(10000)


@dataclass(frozen=True)
class MicrostructureMetrics:
    venue: str
    symbol: str
    quote_unit: str
    snapshot_hash: str
    decision_time: datetime
    bid_levels_used: int
    ask_levels_used: int
    spread: Decimal
    relative_spread: Decimal
    spread_bps: Decimal
    top_of_book_imbalance: Decimal
    multi_level_imbalance: Decimal
    microprice: Decimal
    depth_weighted_price: Decimal
    liquidity_concentration_hhi: Decimal
    quote_age_seconds: Decimal
    provenance: str = "ENGINEERING_EXTENSION:SNAPSHOT_DIAGNOSTICS_V1"


def calculate_microstructure(
    book: BookSnapshot, decision_time: datetime, *, levels: int = 5
) -> MicrostructureMetrics:
    """Base-quantity imbalance; notional HHI over selected levels on both sides.

    Depth-weighted price uses base-quantity weights across both sides; it is
    explicitly not an executable price. ``microprice`` uses opposite-side
    weighting of the two best quote prices. No temporal smoothing is inferred.
    """
    book.validate_at(decision_time)
    if type(levels) is not int or not 1 <= levels <= 1000:
        raise ValueError("INVALID_DEPTH_LEVEL_COUNT")
    bids, asks = book.bids[:levels], book.asks[:levels]
    bid, bid_size = bids[0]
    ask, ask_size = asks[0]
    spread = ask - bid
    mid = (bid + ask) / 2
    bid_total = sum((size for _, size in bids), ZERO)
    ask_total = sum((size for _, size in asks), ZERO)
    sizes = bid_total + ask_total
    notionals = tuple(price * size for price, size in (*bids, *asks))
    total_notional = sum(notionals, ZERO)
    elapsed = decision_time - book.exchange_timestamp
    age = (
        Decimal(elapsed.days * 86400 + elapsed.seconds)
        + Decimal(elapsed.microseconds) / 1000000
    )
    return MicrostructureMetrics(
        venue=book.market.venue,
        symbol=book.market.symbol,
        quote_unit=book.market.quote_unit,
        snapshot_hash=book.snapshot_hash,
        decision_time=decision_time,
        bid_levels_used=len(bids),
        ask_levels_used=len(asks),
        spread=spread,
        relative_spread=spread / mid,
        spread_bps=spread / mid * BPS,
        top_of_book_imbalance=(bid_size - ask_size) / (bid_size + ask_size),
        multi_level_imbalance=(bid_total - ask_total) / sizes,
        microprice=(ask * bid_size + bid * ask_size) / (bid_size + ask_size),
        depth_weighted_price=total_notional / sizes,
        liquidity_concentration_hhi=sum(
            ((amount / total_notional) ** 2 for amount in notionals), ZERO
        ),
        quote_age_seconds=age,
    )


@dataclass(frozen=True)
class ExecutableCostEstimate:
    venue: str
    symbol: str
    side: Literal["BUY", "SELL"]
    base_currency: str
    quote_currency: str
    quote_unit: str
    snapshot_hash: str
    decision_time: datetime
    requested_base_quantity: Decimal
    fillable_base_quantity: Decimal
    unfilled_base_quantity: Decimal
    depth_complete: bool
    visible_quote_notional: Decimal
    executable_vwap: Decimal
    best_quote: Decimal
    marginal_quote: Decimal
    slippage_bps: Decimal
    slippage_quote: Decimal
    marginal_depth_distance_bps: Decimal
    provenance: str = "ENGINEERING_EXTENSION:VISIBLE_DEPTH_ESTIMATE_V1"
    status: str = "SNAPSHOT_ESTIMATE_NOT_ORDER_OR_FILL"


def executable_cost(
    book: BookSnapshot,
    side: Literal["BUY", "SELL"],
    base_quantity: Decimal,
    decision_time: datetime,
) -> ExecutableCostEstimate:
    """Consume visible asks to buy or visible bids to sell a base quantity.

    This differs from ``stateful.quote_conversion``: its BUY input is a quote
    budget, whereas this input is a base quantity. No balance reservation,
    actual fill, fee currency, latency prediction or future depth is assumed.
    Partial visible depth is reported with its unfilled quantity, never filled
    by extrapolating the last quote. The caller retains risk/authorization.
    """
    book.validate_at(decision_time)
    if side not in {"BUY", "SELL"}:
        raise ValueError("INVALID_COST_SIDE")
    if not isinstance(base_quantity, Decimal):
        raise TypeError("QUANTITY_REQUIRES_DECIMAL")
    decimal(base_quantity, positive=True)
    if base_quantity % book.market.step_size:
        raise ValueError("ORDER_PRECISION_MISMATCH")
    quotes = book.asks if side == "BUY" else book.bids
    best = quotes[0][0]
    if base_quantity * best < book.market.minimum_notional:
        raise ValueError("MINIMUM_NOTIONAL_BREACH")
    remaining, notional, marginal = base_quantity, ZERO, best
    for price, size in quotes:
        take = min(remaining, size)
        notional += take * price
        remaining -= take
        marginal = price
        if remaining == ZERO:
            break
    filled = base_quantity - remaining
    vwap = notional / filled
    sign = ONE if side == "BUY" else -ONE
    slippage_quote = sign * (notional - filled * best)
    return ExecutableCostEstimate(
        venue=book.market.venue,
        symbol=book.market.symbol,
        side=side,
        base_currency=book.market.base,
        quote_currency=book.market.quote,
        quote_unit=book.market.quote_unit,
        snapshot_hash=book.snapshot_hash,
        decision_time=decision_time,
        requested_base_quantity=base_quantity,
        fillable_base_quantity=filled,
        unfilled_base_quantity=remaining,
        depth_complete=remaining == ZERO,
        visible_quote_notional=notional,
        executable_vwap=vwap,
        best_quote=best,
        marginal_quote=marginal,
        slippage_bps=sign * (vwap / best - ONE) * BPS,
        slippage_quote=slippage_quote,
        marginal_depth_distance_bps=sign * (marginal / best - ONE) * BPS,
    )


def short_horizon_volatility(book: BookSnapshot) -> Decimal:
    """A single snapshot cannot establish a return series or sampling protocol."""
    raise UnsupportedCapability(
        "VERIFIED_RETURN_HISTORY_AND_SAMPLING_PROTOCOL_REQUIRED"
    )


def causal_price_impact(book: BookSnapshot) -> Decimal:
    """Visible-depth distance is not an empirical causal impact estimate."""
    raise UnsupportedCapability("EXECUTION_HISTORY_AND_CAUSAL_IMPACT_PROTOCOL_REQUIRED")
