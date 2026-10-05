# LIVE Execution Integration — Fail-Closed Architecture

Date: 2026-10-05

Status: **architecture integrated / real-money order submission remains disabled**

## Purpose

The trading bot now has an explicit LIVE-readiness governance layer instead of
representing LIVE only as a boolean flag.  The objective is to make the path
from research -> PAPER -> TESTNET -> LIVE review auditable and impossible to
skip accidentally.

## Implemented gates

The new `research_bot.live_readiness` module evaluates:

1. scientific promotion passed;
2. forward PAPER execution reconciled;
3. persistent order reconciliation validated;
4. one canonical risk constitution;
5. TESTNET failure drills passed;
6. balance reconciliation validated;
7. explicit operator approval.

The first four gates are required before TESTNET review.  All seven are
required before the system can become a `LIVE_REVIEW_CANDIDATE`.

## Critical safety invariant

Even when every readiness gate is true:

`live_execution_authorized = false`

This is intentional.  Readiness is not authorization.  A separate reviewed
release must explicitly change the execution contract after scientific,
operational, reconciliation, security and financial-risk evidence exists.

## Existing execution components reused

- `research_bot.execution`: deterministic PAPER execution and idempotency;
- `research_bot.execution_hardening_v52`: fresh executable quote, atomic
  settlement and persistent duplicate protection;
- `research_bot.order_reconciliation_v52`: durable client order identity,
  ambiguous-submit state and reconnect reconciliation.

## Next engineering milestone

Before any private adapter is considered, build and test a TESTNET-only adapter
with injected credentials, fixed-precision monetary arithmetic, persistent
order-intent storage, ambiguous-submit recovery, partial-fill aggregation,
venue fee reconciliation, balance reconciliation and kill-switch drills.

No real-money endpoint or exchange-secret handling is added by this change.
