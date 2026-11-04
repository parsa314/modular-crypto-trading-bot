# Terminal cumulative-fill evidence — safety increment

Parent finance head: `a962b5dd62dd47bb089b08011571614eddc621ed`.
Scope: OKX Demo and Nobitex TESTNET parsing only. Absence of a venue-reported
cumulative fill must not be silently rewritten as an explicit zero, especially
when a terminal state may release risk reservations. Explicit zero remains valid.

- OKX absent `filled`: `AmbiguousTransportOutcome`, never a terminal-zero snapshot.
- Nobitex absent `matchedAmount`: `NOBITEX_MATCHED_AMOUNT_UNAVAILABLE`, no reconciled terminal snapshot.
- Added synthetic regression cases with both missing and blank fields.
- No changes to live routing, credentials, execution enablement, risk rules or
  historic accounting state. The independently reported *non-quote fee with
  zero fill* defect, late fees and in-flight dispatch/recovery race remain OPEN.

Verification required: targeted tests on the exact commit, repository regression,
PostgreSQL 16 tests, and real CCXT SDK compatibility. Synthetic tests alone are
not evidence of exchange integration. Financial release gate stays BLOCKED.
