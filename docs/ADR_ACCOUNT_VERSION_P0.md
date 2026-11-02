# P0: account-version invalidation for durable risk approvals

Status: **candidate mitigation, not release authorization**. Applies only to
the existing fixture-governed PostgreSQL TESTNET research implementation. No
live/demo transaction or broker identity is validated by this ADR.

The original governor issued an approval bound to the account snapshot hash.
That hash did not change when another connection updated the OMS directly.
Consequently a persisted, unapplied fill could coexist with a still-consumable
approval referencing the old snapshot.

Account rows are locked by `SELECT ... FOR UPDATE`. Every account update now
advances its database-enforced state version. Reservation inserts advance that
version in the same transaction, and other reservation and fee-liability
mutations additionally invalidate reconciliation. The approval records the
version **after** its own reservation is inserted. Consumption locks and
compares that version; an older or pre-migration approval without a version
fails closed. Multiple concurrently issued approvals may therefore cause earlier
approvals to be rejected and require reapproval.

The order table does not contain account identity. An OMS insert/update/delete
invalidates all account anchors at the same venue in the same transaction.
This is conservative and must not be mistaken for account attribution.
First installation quarantines accounts for venues with pre-existing orders;
existing reservations/fee liabilities are quarantined when their guards are
first installed. Admission and consumption reject missing or disabled guards.
Observations check for orphan records outside rollover as well.

Under ordinary PostgreSQL READ COMMITTED operation, updating the same account
row serializes risk admission with the invalidation. If the mutation commits
first, the old approval is rejected. If admission takes the account lock first,
the mutation waits or PostgreSQL detects a deadlock and aborts a transaction;
aborted work must be rolled back and reevaluated. Triggers are transactional and
crash rollback must not leave an invalidation detached from its OMS write.
The acceptance suite includes independent spawned processes for stale order
mutation, waiting on the account lock, crash before commit, and an explicit
SERIALIZABLE conflict followed by reevaluation in a fresh transaction. Results
must be read from the exact CI revision; listing a test is not a passing result.

This does **not** solve the window between committed intent consumption and the
external send, authenticate the database role/schema, prevent privileged DDL or
TRUNCATE bypass, establish exchange execution IDs, provide an append-only fee
journal, support multi-currency fees, or prove account provenance. A production
boundary must fence every writer, authorize the final transmission, handle
ambiguous sends, and be independently verified under concurrent PostgreSQL
connections and crash injection. A successful CI suite is not proof of live
safety. The engineering, broker, paper and live release gates remain BLOCKED.

The product policy explicitly includes distinct LIVE capability and approval
states, but this revision reports `LIVE_NOT_IMPLEMENTED`: there is no verified
production broker boundary or authenticated scoped approval authority. A
caller-created readiness Boolean cannot grant execution. In the future,
activation needs independently verified account, financial, risk, execution
and observation evidence plus time-limited, revocable operator authorization
bound to the exact account, venue, instruments, limits and code/config version.
