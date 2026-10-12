# Canonical bridge engineering report

Evidence state: ENGINEERING. Economic/prospective collection remains BLOCKED.

Base: 7db5643f24bb713ba1df2abb9a2989f73d56a8c6.
Pre-implementation freeze: a4302666178d8727f27d6da06e3b49eb166a7e55.
ADR and manifest were committed before bridge source was written. Their exact byte
hashes are checked by the bridge. No frozen v50/v51 file was changed.

## Delivered
- Canonical DetectorEvent input DTO -> existing SignalEvidence/DecisionEngine.
- Existing ResearchTradingOrchestrator.process_detector_event journal-consumer seam.
- No second Decision/ResearchStatus or execution/risk engine.
- PIT timestamps, explicit entry-after-decision, version/asset/venue validation.
- Finite forecasts, nonnegative penalties, scored-object identity.
- Unestimated forecasts explicitly abstain; numeric placeholders are not estimates.
- Sealed-window and unrecovered-raw-data vetoes, immutable event identity.
- Fixture-only engineering path, no exchange calls/orders/position sizing.

## Fresh local validation
Python 3.12; installed repository `.[dev]` dependencies.
`python -m pytest tests/test_cognitive_bridge.py tests/test_v08_orchestrator.py tests/test_trial_registry_v17.py tests/test_evidence_ledger_v17.py -q`: 48 passed.
`python -m pytest -q`: 234 passed, 1 skipped, 3 warnings, 46.69s.
`git diff --check`: PASS. `python -m compileall -q research_bot/cognitive_core research_bot/orchestrator.py`: PASS.
Initial full-suite collection failed for absent ccxt/fastapi; installing declared
repository dependencies resolved it. No source change was made to hide that error.
Warnings: two pre-existing pytest class-collection warnings and Starlette/httpx deprecation.
Remote CI has not been verified. Local full-suite pass is not an economic result.

## Gates and limits
Contract/PIT bridge tests PASS. Detector correctness NOT_RUN (not implemented here).
Risk/execution bypass prevention PASS for the new journal-only seam.
Economic n=0, cost_model=CC_COST_RT_BPS_v1, mean_net=null, PBO=NOT_RUN,
DSR=NOT_RUN_SEARCH_HISTORY_INCOMPLETE. Economic/leakage gates for a real event
study INCONCLUSIVE. No v51 prospective result; no guaranteed alpha.

Historical hashes/calendar are inherited from the exact pinned V58 manifest;
raw bytes were not recovered or newly verified. The future March1-July29 2027
window is a reservation, not collected evidence or a global untouchedness audit.
No collector/scheduled task was created. The complete global search count is unknown.
This source-checkout/editable-install increment does not claim wheel packaging.
Trial-registry schema remains unchanged; no nonexistent economic trial was appended.
Journal consumer persistence, detectors, real dataset recovery, prospective collection,
model training and PBO/DSR computation are not implemented by this bridge increment.

Single next step: recover and verify the five pinned historical files and freeze
a data-admission amendment before allowing any real-data detector study.
