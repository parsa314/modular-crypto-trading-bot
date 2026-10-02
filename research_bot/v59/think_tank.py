from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .hashing import stable_hash


@dataclass(frozen=True)
class ExpertRole:
    role_id: str
    priority: str
    question: str


@dataclass(frozen=True)
class StageFinding:
    finding_id: str
    severity: str
    summary: str


ROOM = (
    ExpertRole("DATA_PROFESSOR", "DATA_INTEGRITY", "Is the evidence point-in-time, reproducible and leakage-free?"),
    ExpertRole("FINANCIAL_ENGINEERING_PHD", "RISK_ECONOMICS", "Are costs, sizing, exposure and tail risk realistic?"),
    ExpertRole("ECONOMICS_PHD", "REGIME_MACRO", "Are regime and macro assumptions economically defensible?"),
    ExpertRole("COMPUTER_ENGINEER", "ARCHITECTURE", "Are module boundaries observable, resilient and deployable?"),
    ExpertRole("PYTHON_ENGINEER", "SOFTWARE_QUALITY", "Are interfaces typed, deterministic and testable?"),
    ExpertRole("AI_PHD", "MODEL_RISK", "Does the model beat simpler baselines out-of-sample and calibrate uncertainty?"),
    ExpertRole("MATHEMATICS_PHD", "STATISTICS", "Are inference, multiplicity and uncertainty mathematically defensible?"),
    ExpertRole("AL_BROOKS_TRADER", "PRICE_ACTION", "Are trend/range/breakout/failed-breakout rules deterministic?"),
    ExpertRole("ICHIMOKU_TRADER", "ICHIMOKU", "Are trend, Kijun, TK and Kumo states causal and scale-consistent?"),
    ExpertRole("ICT_TRADER", "ICT", "Are sweep, displacement, MSS/CISD and FVG sequences correctly ordered?"),
    ExpertRole("SMC_TRADER", "SMC", "Are structure, liquidity and imbalance rules explicit and testable?"),
    ExpertRole("RESEARCH_WRITER", "SCIENTIFIC_EVIDENCE", "Can every claim be reproduced and supported by an artifact?"),
)


SEVERITY_ORDER = {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def prioritize(findings: Iterable[StageFinding]) -> dict:
    rows = tuple(findings)
    ordered = sorted(
        rows,
        key=lambda row: (SEVERITY_ORDER.get(row.severity, 99), row.finding_id),
    )
    top = ordered[0] if ordered else None
    decision = (
        "STOP_AND_REPAIR_BLOCKER"
        if top and top.severity == "BLOCKER"
        else "CONTINUE_WITH_HIGHEST_PRIORITY_REPAIR"
        if top
        else "ADVANCE_STAGE"
    )
    payload = {
        "room": [role.__dict__ for role in ROOM],
        "findings": [row.__dict__ for row in ordered],
        "decision": decision,
        "next_priority": None if top is None else top.finding_id,
    }
    payload["decision_hash"] = stable_hash(payload)
    return payload
