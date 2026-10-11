"""Auditable historical pilot assessment; never portfolio/order authorization.

ENGINEERING_EXTENSION. Numeric study choices belong to a committed protocol,
not to universal profitability rules. Existing evaluator/ledger are reused.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from .evaluation import (
    EvaluationConfig,
    average_uniqueness,
    economic_metrics,
    paired_comparison,
)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values in original order; arbitrary dependence.

    Missing/unsupported tests must be supplied as 1, never removed from the
    preregistered family. Input p-values must themselves be valid/justified.
    """
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or not len(p) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("INVALID_P_VALUE_FAMILY")
    order = np.argsort(p, kind="stable")
    adjusted = np.minimum(1, np.maximum.accumulate(p[order] * np.arange(len(p), 0, -1)))
    result = np.empty_like(p)
    result[order] = adjusted
    return result.tolist()


def assess_event_panel(events: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Consume matured directional course events, charging costs exactly once.

    Label replay already signs gross_return by direction; multiplying by side
    again would turn losing short events into gains. This API consumes that
    signed return and checks the explicit contract rather than re-signing it.
    Event summaries are not an account equity curve or a capacity estimate.
    """
    result: dict[str, Any] = {
        "economic_confirmation": "ECONOMIC_CONFIRMATION_DEFERRED",
        "execution_authorization": "DENIED_RESEARCH_ONLY",
        "return_unit": "DIRECTIONAL_PNL_PER_ENTRY_NOTIONAL",
        "portfolio_risk_compliance": "NOT_EVALUATED_NO_FUNDED_PORTFOLIO",
        "pbo": {"status": "NOT_APPLICABLE", "reason": "NO_COMPLETE_REGULAR_COMPARABLE_RETURN_PANEL"},
        "dsr": {"status": "NOT_APPLICABLE", "reason": "IRREGULAR_EVENT_CLOCK_AND_UNKNOWN_EFFECTIVE_TRIAL_COUNT"},
        "cpcv": {"status": "NOT_APPLICABLE", "reason": "FIXED_RULE_PILOT_NO_FITTED_MODEL_CPCV_CLAIM"},
        "promotion_blockers": ["HISTORIC_AVAILABILITY_ASSUMED", "COST_SCENARIOS_NOT_MEASURED",
                               "NO_INDEPENDENT_OOS_OR_FIRST_SEEN_FORWARD_EVIDENCE",
                               "NO_EXECUTABLE_DEPTH_CAPACITY_OR_SHORT_FINANCING_VALIDATION",
                               "NO_FUNDED_PORTFOLIO_RISK_REVIEW", "STATIONARITY_AND_DEPENDENCE_ASSUMPTIONS_UNVERIFIED"],
        "events": len(events), "scenarios": {}, "comparisons": {},
    }
    if events.empty:
        result.update(pilot_result="INCONCLUSIVE_NO_MATURED_EVENTS", p_one_sided=1.0)
        return result
    required = {"gross_return", "direction", "round_trip_cost", "net_return",
                "entry_time", "exit_time", "event_id"}
    if not required.issubset(events):
        raise ValueError("INCOMPLETE_SIGNED_EVENT_CONTRACT")
    if events.event_id.duplicated().any() or not events.entry_time.is_monotonic_increasing:
        raise ValueError("DUPLICATE_OR_NONCHRONOLOGICAL_EVENTS")
    gross = events.gross_return.astype(float).to_numpy()
    if not np.isfinite(gross).all() or not events.direction.isin([-1, 1]).all():
        raise ValueError("INVALID_DIRECTIONAL_EVENTS")
    if not events.round_trip_cost.astype(float).eq(0).all() or not np.array_equal(gross, events.net_return.astype(float)):
        raise ValueError("COST_MUST_NOT_BE_PRECHARGED_OR_DOUBLE_COUNTED")
    if not (events.exit_time > events.entry_time).all():
        raise ValueError("INVALID_EVENT_DURATION")
    result["short_events"] = int(events.direction.eq(-1).sum())
    result["uniqueness"] = average_uniqueness(events)
    for cost in protocol["roundtrip_cost_bps"]:
        net = gross - float(cost) / 10000
        result["scenarios"][str(cost)] = economic_metrics(
            net, np.ones(len(net)), cvar_alpha=protocol["cvar_tail_fraction"])
    reference = gross - protocol["reference_cost_bps"] / 10000
    for block in protocol["bootstrap_blocks_in_events"]:
        config = EvaluationConfig(bootstrap_block=block, bootstrap_runs=protocol["bootstrap_runs"],
                                  random_seed=protocol["bootstrap_seed"])
        result["comparisons"][str(block)] = paired_comparison(reference, np.zeros(len(reference)), config)
    primary = result["comparisons"][str(protocol["primary_bootstrap_block"])]
    result["p_one_sided"] = primary.get("p_one_sided", 1.0)
    result["pilot_result"] = (
        "CONDITIONAL_NEGATIVE_PILOT" if primary.get("ci", [0, 0])[1] < 0
        else "INCONCLUSIVE_PENDING_FAMILY_CORRECTION")
    return result


def finalize_family(panels: Sequence[dict[str, Any]], protocol: Mapping[str, Any]) -> None:
    """Annotate a complete fixed family without changing any execution state."""
    if len(panels) != protocol["family_size"]:
        raise ValueError("INCOMPLETE_PREREGISTERED_FAMILY")
    adjusted = holm_adjust([p["p_one_sided"] for p in panels])
    for panel, p_adjusted in zip(panels, adjusted):
        panel["holm_adjusted_p"] = p_adjusted
        supported = bool(panel["comparisons"]) and all(
            comparison.get("ci", [-1, 1])[0] > 0 for comparison in panel["comparisons"].values())
        if supported and p_adjusted <= protocol["family_alpha"]:
            panel["pilot_result"] = "CONDITIONAL_POSITIVE_PILOT"
        elif panel["pilot_result"] == "INCONCLUSIVE_PENDING_FAMILY_CORRECTION":
            panel["pilot_result"] = "INCONCLUSIVE"
