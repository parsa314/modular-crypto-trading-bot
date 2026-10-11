"""Adapt course hypotheses to the canonical decision/risk/trial registry."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts import Decision
from ..execution import NoOrderTransport
from ..risk import RiskSnapshot
from ..trial_registry_v17 import TrialRecord, append_trial
from .contracts import HypothesisCandidate, aware, decimal, digest

if TYPE_CHECKING:
    from ..orchestrator import ResearchTradingOrchestrator


def _safe_input(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NONFINITE:" + str(value)
    if isinstance(value, dict):
        return {key: _safe_input(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_input(item) for item in value]
    return value


def process_batch(
    orchestrator: ResearchTradingOrchestrator,
    *,
    candidates: list[HypothesisCandidate],
    decision_time: datetime,
    risk_snapshot: RiskSnapshot,
    run_id: str,
    git_commit: str,
    dataset_hash: str,
    artifact_dir: str | Path,
    capital_available: dict[str, Decimal] | None = None,
    capital_reserved: dict[str, Decimal] | None = None,
    capital_prices: dict[str, Decimal] | None = None,
    capital_price_available_at: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    """One simultaneous group; no future arrivals, outcomes or pre-emption.

    Unknown course expectancy remains abstention. Capital reservations here are
    proposal reservations only. Actual simulated balances live in the simulator.
    """
    if not isinstance(orchestrator.execution_engine, NoOrderTransport):
        raise RuntimeError("RESEARCH_NO_ORDER_TRANSPORT_REQUIRED")  # noqa: TRY004
    aware(decision_time)
    if not all((run_id, git_commit, dataset_hash)):
        raise ValueError("MISSING_RUN_PROVENANCE")
    if len({c.event_id for c in candidates}) != len(candidates):
        raise ValueError("DUPLICATE_EVENT")
    if any(c.decision_time != decision_time for c in candidates):
        raise ValueError("SAME_ENTRY_GROUP_REQUIRED")
    available = {k: decimal(v) for k, v in (capital_available or {}).items()}
    reserved = {k: decimal(v) for k, v in (capital_reserved or {}).items()}
    prices = {k: decimal(v, positive=True) for k, v in (capital_prices or {}).items()}
    price_times = capital_price_available_at or {}
    if any(
        k != k.upper() or not k.isalnum()
        for k in (*available, *reserved, *prices, *price_times)
    ):
        raise ValueError("NONCANONICAL_CAPITAL_CURRENCY")
    for value in price_times.values():
        aware(value)
        if value > decision_time:
            raise ValueError("POINT_IN_TIME_CAPITAL_PRICE_UNAVAILABLE")
    if any(
        value > available.get(k, Decimal(0)) or k not in prices or k not in price_times
        for k, value in reserved.items()
    ):
        raise ValueError("INVALID_CAPITAL_RESERVATION")
    group_hash = digest(
        [asdict(c) for c in sorted(candidates, key=lambda c: c.event_id)]
    )
    prepared = []
    for candidate in candidates:
        adapted = candidate.to_signal_evidence()
        signal = orchestrator.decision_engine.evaluate(
            asset=adapted.asset,
            timestamp=adapted.timestamp,
            expected_return=adapted.expected_return,
            expected_cost=adapted.expected_cost,
            risk_penalty=adapted.risk_penalty,
            uncertainty_penalty=adapted.uncertainty_penalty,
            confidence=adapted.confidence,
            currently_long=False,
            evidence=adapted.evidence,
            metadata=dict(adapted.metadata),
        )
        reasons = list(signal.reasons)
        admissible = signal.decision is Decision.BUY_CANDIDATE
        if candidate.expected_edge is None or candidate.confidence is None:
            reasons.append("UNESTIMATED_EDGE_OR_CONFIDENCE")
            admissible = False
        if candidate.direction != 1:
            reasons.append("FLAT_OR_SHORT_NOT_SUPPORTED_BY_SPOT_PIPELINE")
            admissible = False
        pair = candidate.symbol.replace("_", "/").split("/")
        if len(pair) != 2:
            reasons.append("MISSING_CANONICAL_BASE_QUOTE")
            admissible = False
        elif (
            any(not part.isalnum() or part != part.upper() for part in pair)
            or pair[0] == pair[1]
        ):
            reasons.append("NONCANONICAL_BASE_QUOTE")
            admissible = False
        if candidate.data_quality != "VALID":
            reasons.append("DATA_UNAVAILABLE")
            admissible = False
        if (
            candidate.evidence_available_at is None
            or candidate.source_observed_at is None
        ):
            reasons.append("MISSING_POINT_IN_TIME_PROVENANCE")
            admissible = False
        if (
            not candidate.capital_requirements
            or not any(amount > 0 for _, amount in candidate.capital_requirements)
            or any(
                k not in prices or k not in price_times
                for k, _ in candidate.capital_requirements
            )
        ):
            reasons.append("MISSING_CAPITAL_OR_ACCOUNT_CURRENCY_VALUATION")
            admissible = False
        if len(risk_snapshot.recent_returns) < 20:
            reasons.append("INSUFFICIENT_TAIL_RISK_EVIDENCE")
            admissible = False
        prepared.append((candidate, signal, reasons, admissible))
    # Stable causal policy for this new course experiment, NOT v0.51 arbitration.
    prepared.sort(key=lambda item: (-item[1].net_alpha, item[0].event_id))
    used_markets: set[str] = set()
    selected_notional_by_market: dict[str, Decimal] = {}
    external_reserved_notional = sum(
        (
            amount * prices.get(currency, Decimal(0))
            for currency, amount in reserved.items()
        ),
        Decimal(0),
    )
    records: list[dict[str, Any]] = []
    for candidate, signal, reasons, admissible in prepared:
        market = candidate.symbol.replace("_", "/").split("/")[0]
        arbitration = "INELIGIBLE"
        if admissible and market in used_markets:
            reasons.append("SAME_ASSET_CONFLICT")
            admissible = False
        required: dict[str, Decimal] = {}
        for currency, amount in candidate.capital_requirements:
            required[currency] = required.get(currency, Decimal(0)) + amount
        projected_notional = sum(
            (
                amount * prices.get(currency, Decimal(0))
                for currency, amount in required.items()
            ),
            Decimal(0),
        )
        proposed_total = projected_notional + sum(
            (
                amount * prices.get(currency, Decimal(0))
                for currency, amount in reserved.items()
            ),
            Decimal(0),
        )
        added_weight = (
            float(proposed_total) / risk_snapshot.equity
            if risk_snapshot.equity > 0
            else float("inf")
        )
        asset_added = (
            float(
                projected_notional
                + selected_notional_by_market.get(market, Decimal(0))
                + external_reserved_notional
            )
            / risk_snapshot.equity
            if risk_snapshot.equity > 0
            else float("inf")
        )
        projected_snapshot = replace(
            risk_snapshot,
            gross_exposure=risk_snapshot.gross_exposure + added_weight,
            asset_weight=risk_snapshot.asset_weight + asset_added,
            turnover=risk_snapshot.turnover + added_weight,
        )
        risk = orchestrator.risk_engine.evaluate(projected_snapshot)
        if not risk.approved:
            reasons.extend(risk.reasons)
            admissible = False
        if admissible and any(
            amount
            > available.get(currency, Decimal(0)) - reserved.get(currency, Decimal(0))
            for currency, amount in required.items()
        ):
            reasons.append("CAPITAL_RESERVATION_CONFLICT")
            admissible = False
        if admissible:
            used_markets.add(market)
            selected_notional_by_market[market] = (
                selected_notional_by_market.get(market, Decimal(0)) + projected_notional
            )
            for currency, amount in required.items():
                reserved[currency] = reserved.get(currency, Decimal(0)) + amount
            arbitration = "SELECTED_RESEARCH_ONLY"
        elif (
            "SAME_ASSET_CONFLICT" in reasons
            or "CAPITAL_RESERVATION_CONFLICT" in reasons
        ):
            arbitration = "CONFLICT_REJECTED"
        record: dict[str, Any] = {
            "run_id": run_id,
            "trial_id": run_id + ":" + candidate.event_id,
            "event_id": candidate.event_id,
            "strategy_id": candidate.strategy_id,
            "strategy_version": candidate.strategy_version,
            "git_commit": git_commit,
            "dataset_hash": dataset_hash,
            "config_hash": candidate.config_hash,
            "model_version": None,
            "decision_timestamp": decision_time.isoformat(),
            "evidence_available_at": candidate.evidence_available_at.isoformat()
            if candidate.evidence_available_at
            else None,
            "snapshot_hash": candidate.feature_snapshot_id,
            "candidate_set_hash": group_hash,
            "arbitration_decision": arbitration,
            "risk_decision": asdict(risk),
            "execution_authorization": "DENIED_RESEARCH_ONLY",
            "simulated_order_id": None,
            "outcome": "NO_TRADE",
            "rejection_reasons": reasons,
            "candidate": asdict(candidate),
            "economic_evidence": "NOT_EVALUATED",
            "capital_proposal": {k: str(v) for k, v in reserved.items()},
            "risk_snapshot_input": _safe_input(asdict(risk_snapshot)),
            "risk_snapshot_projected": _safe_input(asdict(projected_snapshot)),
            "risk_limits": asdict(orchestrator.risk_engine.limits),
            "capital_available": available,
            "capital_reserved_input": capital_reserved or {},
            "capital_prices_account_currency_per_unit": prices,
            "capital_price_available_at": price_times,
            "capital_valuation_assumption": "CALLER_SUPPLIED_PIT_FX_NO_LIVE_ACCOUNT_AUTHORITY",
        }
        records.append(record)
    # Existing trial registry points to immutable full decision artifacts.
    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".course-artifact.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("ARTIFACT_STORE_BUSY_OR_STALE") from exc
    try:
        events = root / "events"
        events.mkdir(exist_ok=True)
        for record in records:
            event_path = events / (digest(record["event_id"]) + ".json")
            event_raw = json.dumps(
                record["candidate"], default=str, sort_keys=True, allow_nan=False
            ).encode()
            if event_path.exists():
                if event_path.read_bytes() != event_raw:
                    raise ValueError("IMMUTABLE_EVENT_REVISION_REJECTED")
            else:
                event_fd = os.open(
                    event_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                with os.fdopen(event_fd, "wb") as handle:
                    handle.write(event_raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            name = digest([run_id, record["event_id"]]) + ".json"
            target = root / name
            raw = json.dumps(
                record, default=str, sort_keys=True, allow_nan=False
            ).encode()
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("IMMUTABLE_EVENT_REVISION_REJECTED")
            else:
                artifact_fd = os.open(
                    target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                # An interrupted artifact is quarantined, never overwritten.
                with os.fdopen(artifact_fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            append_trial(
                root / "trials.jsonl",
                TrialRecord(
                    trial_id=record["trial_id"],
                    git_commit=git_commit,
                    dataset_fingerprint=dataset_hash,
                    feature_family="COURSE_RULE_BASELINE",
                    model_family="NONE",
                    hyperparameters={
                        "config_hash": record["config_hash"],
                        "event_id": record["event_id"],
                    },
                    random_seed=None,
                    forecast_horizon="PREREGISTRATION_REQUIRED",
                    decision_rule="COURSE_SAME_TIME_RESEARCH_ONLY_V1",
                    cost_model={
                        "expected_cost": str(record["candidate"]["expected_cost"])
                    },
                    train_window="NOT_TRAINED",
                    validation_window="NOT_EVALUATED",
                    holdout_window=None,
                    return_path_artifact=str(target),
                    status="HYPOTHESIS",
                ),
            )
    finally:
        os.close(fd)
        lock.unlink()
    return records
