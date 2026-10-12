# ADR-JAM-002: independent portfolio resource feasibility

Status: accepted for offline engineering diagnostics, 2026-10-11.

The v0.51 experiment remains frozen. Do not import, extend or register course
strategies in `overlap_arbitration_v51`. `portfolio_conflict.py` is independent,
requires an explicit experiment identifier and admits only phase-one LONG
single-symbol hypotheses for **offline capital planning**. Admission does not
assert net edge, validated alpha, decision approval or execution authorization.

Use one explicit quote currency and `Decimal` for equity, available cash,
quote-valued gross exposure and reservations. Available cash already excludes
previous reservations. Gross exposure includes prior commitments. Caller-supplied
proposal amounts include fees/contingencies and must come from a separate sizing
protocol, not an indicator default. Cross-currency portfolios and multi-leg
strategies are unsupported.

Rank all proposals by explicit event timestamp then deterministic identity before
filtering position capacity. Recheck remaining cash for every admission; update
cash and gross exposure together. Reject duplicate proposals, existing identities,
symbol conflicts, neutral/future/stale events and breached limits. Return tuples
and a new immutable state. Callers must carry that state forward; the arbiter
does not persist reservations, manage a broker wallet or resolve live concurrency.

Do not implement net-edge ranking on fabricated forecast constants. More policies
and advanced strategy families require a separate preregistered experiment.

Acceptance evidence: counterexamples for aggregate overspending and filtering
before ranking, repeat-reservation tests, input-order invariance and seeded
capital-invariant scenarios in `tests/test_jamalian_phase_one.py`.
