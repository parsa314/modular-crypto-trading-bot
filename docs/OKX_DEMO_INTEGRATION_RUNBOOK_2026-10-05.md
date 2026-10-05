# OKX Demo Trading Integration Runbook

Date: 2026-10-05

## Scope

This runbook covers the first real external execution adapter for the thesis
system. It is **Demo/Testnet only**. It does not authorize production trading.

Official OKX Demo Trading uses the OKX API with simulated trading enabled. With
CCXT, `set_sandbox_mode(True)` must be called immediately after constructing
the exchange object, before any other exchange call.

## Safety contract

The adapter accepts only these credential names:

- `OKX_DEMO_API_KEY`
- `OKX_DEMO_API_SECRET`
- `OKX_DEMO_API_PASSPHRASE`

It also requires:

- `OKX_DEMO_ENABLED=true`

There is deliberately no fallback to generic `OKX_API_KEY` or production
credentials.

Never commit credentials, `.env` files, API keys, secrets, or passphrases.

## Current capability

`research_bot/okx_demo_transport.py` provides:

1. sandbox-first CCXT initialization;
2. read-only authenticated preflight;
3. OKX market precision/minimum discovery;
4. market order submit with deterministic client identity;
5. conservative ambiguous-network handling;
6. reconciliation by venue order ID or client order ID;
7. partial/final fill mapping;
8. fee/currency mapping;
9. cancel followed by authoritative fetch/reconciliation.

## Required evidence before a Demo order

A Demo submission must still pass the existing `TestnetExecutionGateway`:

- TESTNET readiness review eligible;
- independent RiskDecision approved;
- risk kill-switch false;
- quantity/minimum checks;
- durable order intent persisted before submit.

The adapter itself cannot bypass these conditions.

## Credentialed preflight

After creating a Demo Trading API key in OKX, export only the demo-specific
variables in the runtime environment. Then instantiate
`OKXDemoTransport.from_env()` and run `private_preflight("BTC/USDT")`.

Expected evidence:

- sandbox = true;
- market_loaded = true;
- authenticated_balance_response = true;
- no create/cancel order call during preflight.

## Failure drill sequence

Before any broader Demo experiment, validate these cases:

1. submit ACK;
2. partial fill;
3. cancel with residual quantity;
4. reconnect and fetch by venue order ID;
5. ambiguous timeout after submit;
6. fetch by deterministic client order ID after ambiguous outcome;
7. duplicate client ID blocked by the durable ledger;
8. fee and fill reconciliation;
9. process restart with PostgreSQL ledger;
10. kill-switch veto before venue I/O.

## Promotion boundary

Passing this runbook is evidence for TESTNET execution engineering only. It is
not alpha evidence and it does not set `LIVE_EXECUTION=true`.
