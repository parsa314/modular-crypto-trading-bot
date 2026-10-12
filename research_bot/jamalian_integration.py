"""Independent research registry and evidence export, outside frozen v0.51."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json

from .contracts import SignalEvidence, EvidenceStamp, ResearchStatus
from .event_driven_simulator import EventDrivenExecutionSimulator
from .jamalian_data import currencies
from .strategies.jamalian_hypotheses import InventoryState
from .evidence_ledger_v17 import validate_ledger
from .orchestrator import ResearchTradingOrchestrator, PipelineOutcome
from .risk import RiskSnapshot


HYPOTHESIS_REGISTRY = {
    "JAMALIAN_RSI_MA_V1": {"source": "Python_6:cell11,cell22", "family": "RSI_MA"},
    "JAMALIAN_MACD_RSI_EMA_V1": {"source": "Python_5:cell16", "family": "MACD_RSI_EMA"},
    "JAMALIAN_GRID_V1": {"source": "Python_6:cell14", "family": "GRID"},
    "JAMALIAN_TRIANGLE_V1": {"source": "Python_6:cell18", "family": "TRIANGLE"},
}


def inventory_from_simulator(simulator: EventDrivenExecutionSimulator,
                             symbol: str, at: datetime) -> InventoryState:
    """Construct PIT free inventory only after actual simulated fill/cancel events."""
    simulator.advance(at)
    base, quote = currencies(symbol)
    free_base, free_quote = simulator.available_balance(base), simulator.available_balance(quote)
    payload = json.dumps([symbol, at.isoformat(), str(free_base), str(free_quote),
        len(simulator.events)])
    stamp = EvidenceStamp("offline_simulator_inventory", at, at,
        lineage=sha256(payload.encode()).hexdigest(), status=ResearchStatus.HYPOTHESIS)
    return InventoryState(free_base, free_quote, stamp, symbol)


class JamalianResearchLedger:
    """Deduplicated run-local ledger. Export uses a new file, never overwrites.

    Historical evidence_ledger_v17 milestones remain immutable. This ledger
    records engineering hypotheses and does not create prospective outcomes.
    """
    def __init__(self, orchestrator: ResearchTradingOrchestrator | None = None):
        validate_ledger()
        self.orchestrator = orchestrator or ResearchTradingOrchestrator()
        self._rows: dict[str, dict] = {}

    def record(self, signal: SignalEvidence, *, risk_snapshot: RiskSnapshot | None = None) -> PipelineOutcome:
        hypothesis = signal.metadata.get("hypothesis_id")
        if hypothesis not in HYPOTHESIS_REGISTRY:
            raise ValueError("unregistered Jamalian hypothesis")
        identity = signal.metadata.get("signal_id")
        if not isinstance(identity, str) or len(identity) != 64:
            raise ValueError("signal identity required")
        outcome = self.orchestrator.process_hypothesis(signal, risk_snapshot=risk_snapshot)
        row = {"signal": asdict(signal), "pipeline_status": outcome.status,
            "risk": asdict(outcome.risk) if outcome.risk is not None else None,
            "execution_authorized": False, "eligible_for_v51": False}
        # Strict JSON also rejects NaN/inf before a record can become evidence.
        encoded = json.dumps(row, sort_keys=True, default=str, allow_nan=False)
        record = json.loads(encoded)
        record["record_sha256"] = sha256(encoded.encode()).hexdigest()
        if identity in self._rows and self._rows[identity] != record:
            raise ValueError("signal identity reused with different evidence")
        self._rows[identity] = record
        return outcome

    def export(self) -> dict:
        # Detached copies prevent caller mutation of recorded evidence.
        return json.loads(json.dumps({"schema": "jamalian-research-v1",
            "mode": "RESEARCH_ONLY", "paper_execution": False, "live_execution": False,
            "scientific_status": "HYPOTHESIS_NOT_ECONOMICALLY_VALIDATED",
            "records": list(self._rows.values())}, allow_nan=False))
