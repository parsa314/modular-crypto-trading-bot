# ADR proposal: durable fee evidence and venue precision

Status: PROPOSED / ENGINEERING BLOCKED. This document is a design target, not evidence that the migration or broker contract exists.

## Context

An order lifecycle and its economic settlement are distinct. A terminal filled order may lack fee evidence. Missing fee, an explicit zero and a confirmed nonzero amount must not be conflated. Historical numeric zero has no inherent provenance.

## Required design

- Persist immutable fee events keyed by account, venue, order, source and a stable venue fee/trade identity. Store exact numeric amount, currency, observation and recording times, schema version and a hash of canonical event fields. A hash establishes content integrity, not source authenticity.
- The durable fee evidence states are UNKNOWN, CONFIRMED_ZERO and CONFIRMED_AMOUNT. Foreign-currency valuation is separately unresolved until a validated conversion is supplied. Do not infer CONFIRMED_ZERO for legacy rows.
- Apply a confirmed fill and its gross cash/position effect exactly once, even when the fee is unknown. Keep economic settlement unresolved, block new risk and retain the reservation. A known fee is charged once in the same transaction as its event application; repeated event IDs with different payloads cause conflict.
- A terminal order retains its lifecycle while later economic evidence is recorded. OMS persistence, risk reservation and accounting must be updated transactionally; do not reapply fill, fee or cash on restart or duplicate delivery. Existing terminal-state guards and database triggers need an additive, reviewed change; a Python-only change is insufficient.
- The migration must be additive, idempotent and validated against old rows in isolated PostgreSQL. Unknown legacy zero must remain UNKNOWN; migration failure must roll back. A production migration is not authorized.
- Order-level and trade-level fee reports must not be summed without proof of independent events. Rebates and non-quote fees remain unresolved until venue-specific semantics and conversions are verified.

## Current partial implementation

The branch applies gross fill effects provisionally for unpriced fee denomination and keeps the risk reservation open. It still lacks the three-state fee event ledger, terminal-fee completion, historic migration and verified recovery. Legacy fee fields still contain numeric zero placeholders and cannot establish confirmed-zero provenance. Account reconciliation remains fail-closed. This partial guard is not financial completion.

## OKX

Order submission is blocked with OKX_PRECISION_UNVERIFIED, including before durable approval consumption. It remains blocked until the exact CCXT 4.5.77 outgoing payload, approved Decimal, venue precision, limits and failure handling have been verified without a network request and independently reviewed. This is a functional restriction, not a successful Demo connection.

## Validation gates

Real isolated PostgreSQL migration, duplicate/conflict and crash recovery tests, independent workers, existing risk/OMS regression, SDK wire-serialization tests and independent review remain mandatory. Recovery CI or synthetic fixtures alone cannot validate broker identity, trading performance or release.
