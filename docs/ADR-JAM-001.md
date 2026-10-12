# ADR-JAM-001: hypothesis contract before decision evidence

Status: accepted for independent engineering diagnostics, 2026-10-11.

`SignalEvidence` contains a decision; an indicator rule is not a calibrated
forecast. Keep `research_bot/contracts.py` and `DecisionEngine.evaluate()` intact.
Use `research_bot/hypothesis.py`, because a new `contracts/hypothesis.py` cannot
be imported through the repository's existing flat `contracts.py` module.

`HypothesisCandidate` stores a typed family/side, explicit decision timestamp,
strictly later execution bound, input stamps, parameters and reasons. Nested
containers are detached and immutable. Identity hashes the canonical event,
parameters, provenance and derived diagnostics; equivalent timezone representations
produce the same identity. Baselines also hash their close-price input sequence.
Replay uses an explicit event time; supplying another decision time is another
event, not a prospective first-seen backfill.

There are deliberately no constant expected-return, cost, penalty or confidence
fields. `hypothesis_adapter.evaluate_hypothesis()` calls the existing decision
engine with zero abstention sentinels and unavailable-calibration flags. It
requires `NO_TRADE`; a threshold override that admits zero-confidence input is
rejected. This bridge records decision evidence and does not calibrate or promote
the hypothesis. A future calibrated forecast needs its own protocol and adapter.

Acceptance evidence: contract immutability, PIT rejection, deterministic identity,
all three baseline rules and adversarial threshold tests in
`tests/test_jamalian_phase_one.py`. No new transport or execution authorization.
