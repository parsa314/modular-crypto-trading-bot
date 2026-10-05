# Think Tank Decision — TESTNET Execution Gateway

Date: 2026-10-05

## Decision

The next engineering step after the LIVE-readiness gate is a **sandbox-only
TESTNET execution gateway**, not real-money LIVE activation.

## Council synthesis

- Data / statistics: preserve point-in-time evidence and do not create a new
  scientific claim from execution plumbing.
- Financial engineering / mathematics: use fixed-precision `Decimal` at the
  monetary boundary and enforce venue minimums before submission.
- Risk / economics: the independent risk engine keeps veto power; a kill-switch
  blocks venue I/O.
- Python / software engineering: persist order intent before network submission,
  use deterministic client identities and explicit state transitions.
- Trading specialists (Ichimoku, ICT/SMC, Al Brooks): strategy logic must remain
  upstream; no strategy is allowed to bypass risk/execution governance.
- Research / thesis governance: TESTNET engineering evidence is not alpha
  evidence and cannot authorize LIVE trading.
- Operations / accounting: ambiguous network outcomes require reconciliation;
  blind resubmission is forbidden.

## Implemented in this step

`research_bot/testnet_execution.py` adds:

- TESTNET-only mode enforcement;
- sandbox transport enforcement;
- readiness-gate enforcement;
- risk-veto enforcement;
- Decimal quantity quantization;
- minimum quantity/notional checks;
- durable order-intent store protocol;
- PostgreSQL TESTNET order ledger;
- submit-before-ACK state persistence;
- ambiguous-submit state;
- duplicate client-order blocking;
- partial/terminal reconciliation;
- client-order and venue-order identity checks.

## Venue decision

OKX Demo Trading is the preferred first external TESTNET integration target
because it provides an explicit demo trading environment.  The transport layer
in this commit remains venue-neutral so exchange-specific API mapping can be
tested independently before credentials are introduced.

## Safety boundary

This change contains **no production API key**, **no LIVE endpoint**, and
**no real-money authorization**.  The existing deployment firewall remains
unchanged.
