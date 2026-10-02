from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .hashing import stable_hash


@dataclass(frozen=True)
class ComponentSpec:
    component_id: str
    kind: str
    maturity: str
    enabled: bool
    promotion_required: bool
    role: str


DEFAULT_COMPONENTS = (
    ComponentSpec("STRATEGY_V58_BASE", "STRATEGY", "MIGRATED", True, False, "DETERMINISTIC_SIGNAL"),
    ComponentSpec("CONFLUENCE10", "STRATEGY", "MIGRATED", True, False, "DETERMINISTIC_SIGNAL"),
    ComponentSpec("FVG_ICT_TSI_MTF", "STRATEGY", "MIGRATED", True, False, "DETERMINISTIC_SIGNAL"),
    ComponentSpec("LOGISTIC", "MODEL", "BASELINE", True, False, "PROBABILITY"),
    ComponentSpec("HIST_GRADIENT_BOOSTING", "MODEL", "BASELINE", True, False, "PROBABILITY"),
    ComponentSpec("DIFF_LSTM", "MODEL", "CANDIDATE", False, True, "TEMPORAL_PROBABILITY"),
    ComponentSpec("TCN", "MODEL", "CANDIDATE", False, True, "TEMPORAL_PROBABILITY"),
    ComponentSpec("PATCHTST", "MODEL", "CANDIDATE", False, True, "TEMPORAL_PROBABILITY"),
    ComponentSpec("ITRANSFORMER", "MODEL", "CANDIDATE", False, True, "TEMPORAL_PROBABILITY"),
    ComponentSpec("TIMESFM", "MODEL", "CANDIDATE", False, True, "FOUNDATION_PRIOR"),
    ComponentSpec("MOIRAI", "MODEL", "CANDIDATE", False, True, "FOUNDATION_PRIOR"),
    ComponentSpec("CHRONOS", "MODEL", "CANDIDATE", False, True, "FOUNDATION_PRIOR"),
    ComponentSpec("CONFORMAL_SELECTIVE", "UNCERTAINTY", "PHASE1", True, False, "ABSTENTION"),
    ComponentSpec("FINANCIAL_CONSTITUTION", "RISK", "PHASE1", True, False, "INDEPENDENT_VETO"),
    ComponentSpec("RECURRENT_SAC", "RL", "CANDIDATE", False, True, "ALLOCATION_ONLY"),
    ComponentSpec("PPO", "RL", "CANDIDATE", False, True, "ALLOCATION_ONLY"),
    ComponentSpec("TD3", "RL", "CANDIDATE", False, True, "ALLOCATION_ONLY"),
)


class ComponentRegistry:
    def __init__(self, components: Iterable[ComponentSpec] = DEFAULT_COMPONENTS) -> None:
        values = tuple(components)
        ids = [row.component_id for row in values]
        if len(ids) != len(set(ids)):
            raise ValueError("component ids must be unique")
        self._components = {row.component_id: row for row in values}

    def get(self, component_id: str) -> ComponentSpec:
        return self._components[component_id]

    def enabled(self) -> tuple[ComponentSpec, ...]:
        return tuple(row for row in self._components.values() if row.enabled)

    def snapshot(self) -> dict:
        rows = [asdict(row) for row in sorted(self._components.values(), key=lambda x: x.component_id)]
        return {
            "components": rows,
            "registry_hash": stable_hash(rows),
        }
