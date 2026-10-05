# Phase 2A / Task 1 — PostgreSQL journal and offline migration

## Scope and authorization

این تغییر فقط اولین وظیفه Phase 2A است: PostgreSQL واقعی برای مسیر مهندسی
V59 → generic OMS → **Fake Exchange**. شروع Paper، اجرای Testnet، Stop واقعی،
Monitoring و تغییر آستانه‌های علمی جزو این تغییر نیستند.
`LIVE=false / PAPER=false / TESTNET=false / REAL_ORDER_SUBMISSION=false`.
Registry علمی همچنان خالی است. PostgreSQL یا CI سبز شواهد سودآوری نیستند.

پایه: PR #110، `fde6e758bfab244e40055778aa84c42cf454f295`.
شاخه: `infrastructure/postgres-phase2a-20261005`؛ بدون merge یا تغییر main.
`AGENTS.md`، canonical status، قرارداد Phase 1، SQLite journal، OMS،
protective FSM و مسیر PostgreSQL قدیمی V52 قبل از تغییر بررسی شدند.

## Architecture and data map

| SQLite V1 / producer | PostgreSQL V1 / consumer | Guarantee |
|---|---|---|
| live_metadata | convergence_journals + convergence_schema | Exact identity; version and schema source hash; one journal/account |
| live_decisions | convergence_decisions | Durable unique decision reservation |
| live_orders | convergence_orders | Durable unique client ID; same transition/fill invariants |
| live_state | convergence_account_state | Canonical JSON is authoritative; includes cash, protection and risk latches |
| state.positions | convergence_positions | Atomic read projection, never a separate sizing engine |
| cumulative order result | convergence_fill_revisions | Idempotent receipt revisions; not invented exchange trade IDs |
| live_candles | convergence_candles | Immutable chronological bars; no gaps/revisions |
| Explicit health writer | convergence_heartbeats | Aware monotone timestamp; not a watchdog or leadership lease |

`PostgresLedger` implements the existing journal seam; OMS and protection code
do not change and remain model-agnostic and Fake-only. No implicit environment
DSN discovery exists in the backend. Only explicit `ENGINEERING_REPLAY` identity
is accepted, before connecting. No new API-key access or private transport.

`update_order` commits receipt, account JSON, positions and receipt revision in
one transaction. The journal metadata row is locked with `FOR UPDATE`, including
when account state has not yet been inserted. Compare-and-swap checks the exact
previous canonical JSON. Receipt validation is shared with SQLite, preserving
its scientific/financial behavior. Nested migration operations use savepoints
inside one outer transaction; a later failure rolls back every imported row.

The dedicated connection owns a nonblocking account-scoped PG advisory lock
across an entire OMS cycle. A second connection cannot acquire it. SIGKILL or
backend disconnect releases it server-side. No automatic reconnect, reacquire,
resubmit or resume exists. This coordinates cooperating clients against **one
database**; it is not a claim of exchange-side fencing, multi-cloud HA, TTL lease
or authenticated execution safety. A lost connection requires reconciliation.

## Run the isolated test database

Python 3.11+ and Docker Compose v2:

```bash
python -m pip install -e '.[dev,data]' 'pyyaml>=6,<7'
docker compose -f compose.postgres-test.yml up -d --wait
export CONVERGENCE_TEST_DATABASE_URL='postgresql://convergence_test:local-test-only@127.0.0.1:55432/convergence_test'
export TEST_DATABASE_URL="$CONVERGENCE_TEST_DATABASE_URL"
python -m pytest -q tests/test_convergence_postgres.py tests/test_postgres_authorization.py tests/test_execution_hardening_postgres_v52.py
python -m pytest -q
docker compose -f compose.postgres-test.yml stop
```

Compose binds only to loopback and uses a named volume and known **test-only**
credentials. Never expose it publicly, reuse these credentials for deployment,
or point these tests at an existing database. Tests refuse remote/non-test DSNs.
The old V52 test uses `TEST_DATABASE_URL`, not `BOT_TEST_DATABASE_URL`; configuring
the actual variable makes its atomic-settlement regression run instead of skip.
When no dedicated test DSN is supplied, offline suites explicitly skip the PG
service tests. The added CI job requires both DSNs and a PostgreSQL 16 service.

## Controlled migration

First stop every source writer. Preserve an offline source backup and any WAL
files while preparing it; use a SQLite-consistent backup rather than copying a
live `.sqlite` file alone. CLI uses only `CONVERGENCE_TEST_DATABASE_URL`; ordinary
`DATABASE_URL` is never consulted.

```bash
PYTHONPATH=. python scripts/migrate_convergence_sqlite_to_postgres.py \
  --source /absolute/path/to/offline-engineering.sqlite \
  --journal-id engineering-migration-001 --writers-stopped
```

Source opens read-only with a consistent WAL-aware transaction, integrity check,
expected schema and duplicate-key rejection. A cooperative source executor's
flock prevents migration. The operator must still stop noncooperative writers;
flock cannot prove their absence. Identity must match the target. Existing target
data is never replaced. The canonical snapshot hash makes repeat import a no-op;
a changed source snapshot fails closed. Original cumulative revision history
does not exist in SQLite and is **not manufactured**: only its latest receipt
can be imported. Pending/UNKNOWN orders, unprotected positions and halt reasons
are preserved. They require reconciliation; migration does not clear a gate.

Rollback before commit: PostgreSQL rolls back all imported records. The initialized
empty journal metadata can remain, permitting a reviewed retry. After commit:
retain the unchanged source backup, stop every PG consumer and review which state
is authoritative before selecting a backend. There is no automatic switch back
to an older SQLite state and no dual-write service. Post-import updates must never
be lost by replaying the original snapshot. No production database was migrated.

## Acceptance and limitations

- Required: actual PG atomic rollback/CAS, unique reservation race, SIGKILL,
  reopen, identity pinning, schema pinning, advisory exclusion, migration
  idempotency/rollback/source preservation and causal Fake end-to-end evidence.
- Actual native CoinEx/OKX/Binance protective capability/acknowledgement remains
  **NOT_VERIFIED**. Software stops cannot satisfy that gate.
- Monitoring, leader TTL/fencing, WebSockets, production DB backup/restore,
  cloud storage failure drills and scientific promotion remain separate work.
- Proposed Sharpe 0.5 or two-week Paper thresholds are not adopted. Existing
  scientific gates are unchanged; Phase 2C requires a separate explicit protocol.

پایان‌نامه: فصل روش تحقیق (persistence، idempotency و recovery) و بخش اعتبار
مهندسی فصل نتایج؛ این آزمون‌ها در جدول Alpha یا شواهد Forward شمارش نمی‌شوند.
