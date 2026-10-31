# PAPER-011: provisional cumulative principal and fee liabilities

Status: PROTOTYPE / BLOCKED. Scope: synthetic engineering validation only.

The current OMS receives cumulative snapshots, not independently identified
executions. The tuple (account, venue, client order, previous cumulative quantity,
new cumulative quantity) identifies a **posting interval**, not an exchange
execution. It must not be presented as a venue trade ID. A primary key rejects
duplicate interval records. The account row, reservation and order are locked in
that order. The principal posting, account projection and reservation update are
committed in the same database transaction. A crash before commit rolls them all
back. Reconciliation after commit sees the persisted cumulative watermark.

When fill quantity and average price are known but fee denomination is not USDT,
the gross cash and position effect is provisional; the fee liability is explicitly
UNRESOLVED, the account remains unreconciled, and the reservation remains open.
Neither the numeric fee placeholder nor a successful account observation proves
the fee was zero. This projection is not a final spendable balance or PnL. New risk
admission remains blocked until verified reconciliation.

This limited change does not make initial OMS snapshot persistence and principal
posting one transaction: the gap is recoverable only through reconciliation and
is still a release blocker. Existing historical rows are not backfilled into the
new posting table; their cumulative reservation watermark remains authoritative.
No source supplies immutable per-execution or fee-event identifiers here. Late
terminal fee corrections, append-only signed fee events, non-quote fees, rebates,
lot allocation, fee provenance, fully atomic OMS + posting persistence and all
Decimal decision boundaries remain unimplemented. These cases remain blocked.
Account observations with unresolved liabilities must never mark the account
reconciled. This ADR does not establish broker, Demo, or scientific evidence.
