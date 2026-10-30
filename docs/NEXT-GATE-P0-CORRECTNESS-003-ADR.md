# P0 correctness contract (ENGINEERING only)

Base: ae85db473e392cda3726d32ac6bccb07af861892. Main and scientific definitions remain unchanged.

1. ReconciledOrder financial values are Decimal; NUMERIC is unbounded to preserve
   venue precision. JSON financial values are decimal strings. Legacy numeric
   constructors are accepted through Decimal(str(value)); lost upstream float
   digits cannot be recovered. Producers must use original decimal strings.
2. OMS update is compare-and-swap on state_version under a row lock. Identity,
   cumulative quantity, cumulative fee and terminal-state invariants are checked
   against the persisted row, not a worker's stale copy. Conflict requires reload
   and reconciliation, never blind retry of an order.
3. Stateless OperationalRiskEngine evaluates research inputs only. Execution
   requires a persisted intent-bound RiskApproval; the gateway refuses Boolean
   approvals. PostgreSQL reserves risk and consumes approval with intent creation
   in one transaction before network I/O. An unknown send retains its reservation.
4. Account anchors are immutable source observations. Unknown sources remain
   ANCHOR_UNVERIFIED. Only explicitly configured reconciliation-source identities
   may supply anchors; tests use an explicitly synthetic source. This is a trust
   boundary, not proof of an actual broker connection. No actual broker source is
   configured by this increment.
5. Account-row locking serializes anchor changes, hard/daily halts, approvals and
   reservations. The 2% daily and 5% drawdown policies stay unchanged. A hard halt
   requires explicit audited authorization plus a fresh reconciled account read
   before release. UTC is the only supported trading-day timezone. Cash flows
   adjust day and peak capital baselines; unexplained flows block admission.
6. Pending BUY notional and stop/cost risk count towards aggregate limits. Pending
   SELL quantity prevents a short. Partial fills retain conservative exposure and
   residual reservations until a terminal reconciliation is accounted for. Timeout
   never frees risk. Financial reconciliation is idempotent on cumulative values.
7. Migrations are additive. Legacy NUMERIC values are not rewritten. Rollback
   requires a quiescent ledger with no active approvals/orders, an exported backup,
   and data-preserving retention of evidence. Never downgrade Decimal data to float.

Compatibility: baseline tests are archived with hashes. Assertions are preserved;
test fixtures that supplied unbound Boolean approvals must be migrated to the
new durable contract. Those fixture changes will be listed explicitly. No
global mutable state or automatic test-database discovery is used by production.

Release remains BLOCKED pending independently observed Demo identity, fills,
reconciliation and recovery. No new science, models, strategies or live orders.
