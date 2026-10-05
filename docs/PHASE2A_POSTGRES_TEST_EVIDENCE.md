# Phase 2A task 1 — fresh database engineering evidence

Date: 2026-10-05. Base: `fde6e758bfab244e40055778aa84c42cf454f295` / PR #110.
Backend: actual PostgreSQL **16.15**, Docker `postgres:16-alpine`, isolated named
test volume; Python 3.11.16. Every exchange interaction remains Fake-only.

| Requirement | Evidence | Status |
|---|---|---|
| Compose/service startup | Actual local Docker daemon, healthy PostgreSQL container | PASS |
| PG schema and identity pinning | Version/source-hash rejection, immutable identity, unique account/journal | PASS |
| Atomic settlement/CAS | Receipt/account/positions transaction; conflict and late-write failure rollback | PASS |
| Idempotency | Unique durable claim, receipt revision dedup, two concurrent connections | PASS |
| Advisory exclusion | Two sessions; exception release; killed process releases lock | PASS (one database, cooperating executors) |
| Process crash | SIGKILL with committed claim + uncommitted account update; fresh process recovers commit only | PASS |
| Connection failure | Own backend terminated; no implicit reconnect/retry; absent reservation remains absent | PASS |
| Database container restart | Committed reservation and halt persist; fresh Python connection; duplicate rejected | PASS |
| Causal end-to-end | Fake closed bars → native features → V59 gates → signed intent → existing OMS → PG → Fake confirmed protection | PASS |
| Entry/protection ACK ambiguity | DB reopen, same IDs, no duplicate submit; partial quantity matches fill | PASS |
| Protection absence | Deadline preserved; Fake emergency flatten gives zero tracked exposure and permanent halt | PASS (Fake only) |
| SQLite migration | Read-only snapshot, unchanged source, same pending/UNKNOWN state, idempotency and changed-source rejection | PASS |
| Migration rejection/rollback | Active source lock, wrong identity, nonempty target, duplicate JSON, invalid final account projection | PASS |
| Existing V52 PostgreSQL regression | Real configured TEST_DATABASE_URL; rollback and duplicate fill settlement | PASS, no skip |
| Package schema | Wheel built; embedded SQL bytes equal source exactly | PASS |
| Config/compile/YAML | Same risk hash; compileall; 38 workflows + Compose parse | PASS |
| Scientific gates | No model trained/promoted, no threshold retuning, no sealed holdout read | Unchanged |
| Native protective venue API | No private call/order/key access | NOT_VERIFIED |
| Cloud deployment/backup restore/HA fencing | Not performed | NOT_IMPLEMENTED |

Focused database/authorization/V52: **28 passed**, zero skips/failures.
Full repository with actual PostgreSQL enabled: **787 passed**, **12 subtests
passed**, **0 skipped**, **0 failed**. One existing Starlette/TestClient dependency
deprecation warning; no test failure. Raw logs and source/config hashes are in
`evidence/production_phase2a/`.

Exact commands are in `docs/POSTGRESQL_PHASE2A.md` and the dedicated
`production-postgres-phase2a.yml` CI job. The ordinary offline Phase-1 workflow
still explicitly skips DB-dependent cases when a test service is absent; it does
not substitute a mock or turn those skips into database acceptance evidence.

This passes **Phase 2A task 1** only. Entire Phase 2A remains PARTIAL until its
remaining approved tasks are verified. PAPER=false, LIVE=false, TESTNET=false;
actual promotion registry remains empty. Native stops are not replaced by
software-only stops. No production database or exchange account was modified.
