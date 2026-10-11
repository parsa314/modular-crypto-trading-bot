# E — Verified test coverage and explicit limits

The authoritative result is `evidence/final-full-tests.log`; tests use controlled data, not real funded accounts. Full repository type checking, GitHub CI, token-backed APIs, broker fills and statistical significance are not inferred from unit tests.

| Requirement | Actual test files / boundary |
|---|---|
| Lookahead, PIT, indicator prefix invariance | test_course_strategies.py, test_course_labels.py, test_course_integration.py |
| Train-only fitting, purge/embargo, holdout barrier | test_course_evaluation.py |
| Exchange schemas, rate limiting, timeout/circuit | test_course_bitpin.py (public GET fixture only) |
| Token expiry / private ambiguous submission | NOT IMPLEMENTED: private API unsupported; no claim of certification |
| Order idempotency/state/partial/balances | test_course_stateful.py (offline only) |
| Duplicate immutable event/revision/cross-run | test_course_integration.py |
| Grid reservations, inventory, breakout/recenter | test_course_stateful.py |
| Arb direction/fees/failed legs/residual inventory | test_course_stateful.py |
| Market stale/sequence/unit correctness | test_course_bitpin.py, test_course_microstructure.py, test_course_strategies.py |
| Risk bypass, capital projection/FX time/currency aliases | test_course_integration.py |
| Research-only guarantee | test_course_integration.py; dashboard tests check flags cannot authorize |
| Restart/replay | test_course_stateful.py; runner export/import state equality |
| Existing regression | Existing tests; database integration skipped without TEST_DATABASE_URL |
| Config lineage and packaged defaults | test_course_configuration.py, test_course_strategies.py; wheel smoke verification |
| Dashboard leakage/unknown schema/read-only projection | test_course_monitoring.py |

Limit fills require explicit synthetic queue evidence; a quote snapshot alone never proves a historical fill. End-to-end real Bitpin/MT5 account tests, observed provider idempotency and live reconciliation are outside implemented capability. Fail-closed and no-order isolation apply to this integration; validation does not claim proof over every future code change or all disconnected legacy transports.
