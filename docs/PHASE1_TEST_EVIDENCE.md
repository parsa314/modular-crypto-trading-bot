# Phase 1 — fresh engineering evidence

Authoritative machine evidence: `evidence/production_phase1/verification.json`,
`source_audit.json`, `native_contract_audit.json`, `fake_replay.json` and test logs.
No synthetic output counts as paper/forward/alpha evidence. Source hash manifest
ties these records to exact Python/test/workflow contents; Git source commits are
recorded separately to avoid a self-referential hash of the evidence file itself.

Fresh local result: **145 focused passed; 759 full passed; 0 failed; 1 skipped;
12 subtests passed**. The skip is PostgreSQL integration without a configured
test database. Existing Starlette TestClient deprecation is the sole warning.
Compileall, 37 workflow YAML parses, risk/config hashes and static secret hygiene
passed. Native schema audit: 43,920 pinned 2024 bars → 17,788 unique events,
no economic rerun. Fake crash drills: 3; actual order submissions: 0.

| Requirement | Fresh test source |
|---|---|
| Canonical limits / immutable / hash / strict parsing | tests/test_risk_constitution.py |
| Ledger lock / transaction / idempotency / credential hygiene | tests/test_live_ledger.py |
| Late-label / long-horizon / unique IDs / complete times | tests/v59/test_convergence_event_times.py |
| Expanding / interval purge / no future / train scaler / validation calibration / test isolation | tests/v59/test_convergence_isolation.py; test_tournament_intervals.py |
| Causal prefix / identity mutation / sealed holdout | tests/v59/test_native_strategy.py |
| Promotion absent/unpromoted/scope/corrupt artifacts/risk/uncertainty veto | tests/test_convergence_bridge.py |
| Normal / duplicate / early-expired / stale / price / minimum-precision / crash-ambiguous / account CAS | tests/test_convergence_oms.py |
| Fill/partial/ACK loss/restart/TP-stop/unknown/bad coverage/deadline/flatten crash | tests/test_convergence_protection.py |
| Fake market → actual causal features → all scientific gates → journal → confirmed protective ACK | tests/test_phase1_end_to_end.py |

The E2E test actively forbids socket connection. It uses an explicit fixture
model/regime/calibration/promotion registry, no arbitrary fold/seed. PPO is not
imported by the executor. Local full regression includes historical V52/V58/V59
tests without rewriting old scientific findings. The PostgreSQL test can skip
only when BOT_TEST_DATABASE_URL is absent; no database drill is claimed from skip.

The native contract audit regenerates 2024 events from verified cached official
archives. It checks identity, temporal metadata and global uniqueness, without
economic scoring. Historical label availability remains an assumption.

Exact run commands are in PRODUCTION_CONVERGENCE_PHASE1.md. Compilation,
workflow parsing, configuration hash checks, diff hygiene and static secret scan
are recorded. CI status must refer to the new PR head; green parent CI alone is
never sufficient to mark a changed tree PASS.
