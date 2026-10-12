"""Phase-one registry, adapter and operational evidence recording.

Grid/MACD/triangle prototypes retained in legacy modules are not admitted here.
The operational ledger never imports evidence_ledger_v17 or arbitration_v51.
"""
from __future__ import annotations

from dataclasses import asdict
import json

from .hypothesis import HypothesisCandidate, canonical_json
from .operational_event_ledger import OperationalResearchEventLedger
from .orchestrator import ResearchTradingOrchestrator, PipelineOutcome
from .risk import RiskSnapshot


PHASE_ONE_STRATEGIES = {"JAMALIAN_MA_CROSS_V3", "JAMALIAN_RSI_MR_V3", "JAMALIAN_RSI_MA_V3"}


class JamalianPhaseOneResearch:
    def __init__(self, events: OperationalResearchEventLedger,
                 orchestrator: ResearchTradingOrchestrator | None = None):
        self.events = events
        self.orchestrator = orchestrator or ResearchTradingOrchestrator()

    def record(self, candidate: HypothesisCandidate, *,
               risk_snapshot: RiskSnapshot | None = None) -> PipelineOutcome:
        if candidate.strategy_id not in PHASE_ONE_STRATEGIES:
            raise ValueError("phase-one strategy not registered")
        outcome = self.orchestrator.process_candidate(candidate, risk_snapshot=risk_snapshot)
        identity = candidate.hypothesis_id
        self.events.append(event_id=identity + ":hypothesis", kind="HYPOTHESIS",
            timestamp=candidate.timestamp, payload=candidate.to_dict())
        # Store a detached JSON record of actual decision output, not a fabricated
        # outcome or a frozen scientific milestone.
        payload = json.loads(json.dumps({"signal": asdict(outcome.signal),
            "pipeline_status": outcome.status,
            "risk": asdict(outcome.risk) if outcome.risk is not None else None,
            "execution_authorized": False, "eligible_for_v51": False}, default=str, allow_nan=False))
        self.events.append(event_id=identity + ":decision", kind="DECISION",
            timestamp=candidate.timestamp, payload=payload)
        return outcome

    def export(self) -> dict:
        ledger = self.events.export()
        decisions = [r["payload"] for r in ledger["records"] if r["kind"] == "DECISION"]
        return json.loads(canonical_json({"schema": "jamalian-phase-one-v3",
            "mode": "RESEARCH_ONLY", "paper_execution": False, "live_execution": False,
            "scientific_status": "HYPOTHESIS_NOT_ECONOMICALLY_VALIDATED",
            "experiment_id": ledger["experiment_id"], "event_ledger": ledger,
            "records": decisions, "advanced_research_gate": "WITHHELD_PENDING_BASELINE_PROTOCOL"}))
