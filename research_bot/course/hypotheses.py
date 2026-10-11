"""SignalEvidence interfaces over audited course implementations, not a new core.

All generators remain unapproved hypotheses. Grid shares the existing state
machine and never changes its wallet or lifecycle during hypothesis generation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import pandas as pd

from ..contracts import (
    ExecutionMode,
    ExperimentManifest,
    ResearchStatus,
    SignalEvidence,
)
from .contracts import BookSnapshot, HypothesisCandidate, aware, digest
from .stateful import GridStrategyStateMachine
from .strategies import CourseStrategy


class SignalHypothesisGenerator(Protocol):
    def generate(
        self,
        frame: pd.DataFrame | BookSnapshot,
        *,
        symbol: str,
        venue: str,
        decision_time: datetime,
    ) -> list[SignalEvidence]: ...

    @property
    def parameter_record(self) -> Mapping[str, Any]: ...


class _FrozenMetadata(Mapping[str, Any]):
    """Immutable public mapping with a detached dataclasses.asdict projection."""

    __slots__ = ("_values",)
    _values: Mapping[str, Any]

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_values", values)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("IMMUTABLE_SIGNAL_METADATA")

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        return {key: deepcopy(value, memo) for key, value in self._values.items()}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMetadata(
            MappingProxyType({key: _freeze(item) for key, item in value.items()})
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_signal(candidate: HypothesisCandidate) -> SignalEvidence:
    signal = candidate.to_signal_evidence()
    return replace(
        signal,
        metadata=_freeze(
            {
                **signal.metadata,
                "event_id": candidate.event_id,
                "strategy_id": candidate.strategy_id,
                "strategy_version": candidate.strategy_version,
                "research_status": ResearchStatus.HYPOTHESIS.value,
                "config_hash": candidate.config_hash,
                "capital_requirements": candidate.capital_requirements,
                "invalidation_conditions": candidate.invalidation_conditions,
                "earliest_execution_time": candidate.earliest_execution_time,
                "execution_authorization": "DENIED_RESEARCH_ONLY",
            }
        ),
    )


def candidates_from_signals(
    signals: Sequence[SignalEvidence],
) -> list[HypothesisCandidate]:
    """Validate the complete canonical projection before entering existing gates.

    Public forecasts and metadata cannot silently diverge. Legacy arbitrary
    SignalEvidence without a course envelope is rejected rather than guessed.
    """
    candidates = []
    for signal in signals:
        if not isinstance(signal, SignalEvidence):
            raise TypeError("CANONICAL_SIGNAL_EVIDENCE_REQUIRED")
        payload = signal.metadata.get("course_candidate")
        if payload is None:
            raise ValueError("MISSING_COURSE_CANDIDATE_ENVELOPE")
        if not isinstance(payload, Mapping):
            raise TypeError("INVALID_COURSE_CANDIDATE_ENVELOPE")
        candidate = HypothesisCandidate(**dict(payload))
        if signal != _canonical_signal(candidate):
            raise ValueError("SIGNAL_CANDIDATE_PROJECTION_MISMATCH")
        candidates.append(candidate)
    return candidates


class _RuleHypothesis:
    strategy_id: str

    def __init__(
        self, config_path: str | Path | None = None, *, enabled: bool | None = None
    ) -> None:
        self._rule = CourseStrategy(self.strategy_id, config_path, enabled=enabled)

    @property
    def parameter_record(self) -> Mapping[str, Any]:
        return _freeze(
            {
                "strategy_id": self.strategy_id,
                "config_hash": self._rule.config_hash,
                "enabled": self._rule.enabled,
                "parameters": self._rule.parameters,
                "approval": "NOT_APPROVED_FOR_PROMOTION",
            }
        )

    def generate(
        self,
        frame: pd.DataFrame | BookSnapshot,
        *,
        symbol: str,
        venue: str,
        decision_time: datetime,
    ) -> list[SignalEvidence]:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("CANDLE_FRAME_REQUIRED")
        return [
            _canonical_signal(c)
            for c in self._rule.generate(
                frame, symbol=symbol, venue=venue, decision_time=decision_time
            )
        ]


class MovingAverageCrossoverHypothesis(_RuleHypothesis):
    strategy_id = "sma_crossover"


class RSI_MA_ConfluenceHypothesis(_RuleHypothesis):
    strategy_id = "rsi_moving_average"


class GridInventoryStateMachine:
    """Read-only hypothesis view of one existing GridStrategyStateMachine.

    Configure/prepare/reserve/replay the supplied state machine through its
    existing interface; generation has no order, reservation or state effects.
    """

    strategy_id = "grid"

    def __init__(
        self, state_machine: GridStrategyStateMachine, *, enabled: bool = False
    ) -> None:
        if (
            not isinstance(state_machine, GridStrategyStateMachine)
            or type(enabled) is not bool
        ):
            raise TypeError("EXISTING_GRID_AND_EXPLICIT_BOOL_REQUIRED")
        self._machine = state_machine
        self._enabled = enabled

    @property
    def parameter_record(self) -> Mapping[str, Any]:
        config = asdict(self._machine.config)
        return _freeze(
            {
                "strategy_id": self.strategy_id,
                "config_hash": digest(config),
                "enabled": self._enabled,
                "parameters": config,
                "approval": "NOT_APPROVED_FOR_PROMOTION",
            }
        )

    def generate(
        self,
        frame: pd.DataFrame | BookSnapshot,
        *,
        symbol: str,
        venue: str,
        decision_time: datetime,
    ) -> list[SignalEvidence]:
        aware(decision_time)
        if not self._enabled:
            return []
        if not isinstance(frame, BookSnapshot):
            raise TypeError("VALIDATED_BOOK_REQUIRED")
        frame.validate_at(decision_time)
        if self._machine.state != "READY":
            raise ValueError("GRID_READY_HYPOTHESIS_REQUIRED")
        candidate = self._machine.to_candidate()
        if (
            candidate.symbol != symbol
            or candidate.venue != venue
            or candidate.decision_time != decision_time
            or candidate.feature_snapshot_id != frame.snapshot_hash
        ):
            raise ValueError("GRID_PLAN_DECISION_SNAPSHOT_MISMATCH")
        return [_canonical_signal(candidate)]


def course_hypothesis_registry(
    *, grid: GridStrategyStateMachine | None = None, enabled: bool = False
) -> dict[str, SignalHypothesisGenerator]:
    if type(enabled) is not bool:
        raise TypeError("EXPLICIT_ENABLE_BOOL_REQUIRED")
    result: dict[str, SignalHypothesisGenerator] = {
        "sma_crossover": MovingAverageCrossoverHypothesis(enabled=enabled),
        "rsi_moving_average": RSI_MA_ConfluenceHypothesis(enabled=enabled),
    }
    if grid is not None:
        result["grid"] = GridInventoryStateMachine(grid, enabled=enabled)
    return result


def course_experiment_manifest(
    *,
    generators: Mapping[str, SignalHypothesisGenerator],
    experiment_id: str,
    created_at: datetime,
    git_commit: str,
    dataset_id: str,
    random_seed: int,
    fee_bps: float,
    slippage_bps: float,
) -> ExperimentManifest:
    """Record fixed config documents and consumed parameters in the existing type.

    BACKTEST describes offline research because the canonical enum has no
    RESEARCH_ONLY member. It never means paper or exchange authorization.
    """
    aware(created_at)
    if (
        len(git_commit) != 40
        or any(c not in "0123456789abcdef" for c in git_commit)
        or not experiment_id
        or not dataset_id
        or type(random_seed) is not int
        or not all(math.isfinite(v) and v >= 0 for v in (fee_bps, slippage_bps))
    ):
        raise ValueError("INVALID_EXPERIMENT_PROVENANCE")
    config_root = Path(__file__).resolve().parent / "configs"
    configs = {
        p.name: json.loads(p.read_text()) for p in sorted(config_root.glob("*.json"))
    }
    records = {
        name: dict(generator.parameter_record) for name, generator in generators.items()
    }
    notes = json.dumps(
        {
            "research_status": ResearchStatus.HYPOTHESIS.value,
            "execution_authorization": "DENIED_RESEARCH_ONLY",
            "parameter_records": records,
            "versioned_reference_config_documents": configs,
            "paper_execution": False,
            "live_execution": False,
            "evidence_class": "ENGINEERING_EXTENSION_NOT_PREREGISTERED",
        },
        default=lambda v: dict(v) if isinstance(v, Mapping) else str(v),
        sort_keys=True,
        allow_nan=False,
    )
    return ExperimentManifest(
        experiment_id,
        created_at,
        git_commit,
        dataset_id,
        "COURSE_RULES_AND_GRID",
        "NONE",
        random_seed,
        ExecutionMode.BACKTEST,
        fee_bps,
        slippage_bps,
        notes=notes,
    )
