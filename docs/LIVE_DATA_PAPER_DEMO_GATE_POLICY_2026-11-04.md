# Live Exchange Data, Forward Paper, and Official DEMO Gate Policy — 2026-11-04

Status: **PROPOSED / engineering implementation pending**. This document authorizes a development scope; it is not evidence that a collector, demo session, broker credential, CI job, or order has run.

Applies to the canonical repository `parsa314/modular-crypto-trading-bot` and to Issue #126. It supersedes conflicting *current instructions* that wrongly require a passed production financial gate merely to read public market data or simulate orders from a real-time feed. It does **not** rewrite old experiment manifests, unseal research holdouts, bypass risk controls, or change the existing monitoring service.

## Owner intent and boundaries

Owner request (2026-11-04): connect the bot to **authentic exchange-origin market data**, download provider history where authorized, observe live market changes, generate trades according to its implemented strategies, and test the bot in forward-paper and the exchange's **official simulated environment** where available. Do not confuse Kaggle/third-party backtests with first-seen live evidence. The owner has previously recorded generic live-trading permission in `AGENTS.md` (2026-10-12), but **this work item is specifically about safe real-feed/PAPER/DEMO engineering and does not instruct an agent to submit real-money orders or change production flags**.

## Environment contract: independent gates

| Mode | Exchange data | Sends venue orders? | Allowed while production G1 financial gate is BLOCKED? | Required controls |
|---|---|---|---|---|
| `HISTORICAL_REAL` | Provider-origin OHLCV/trades fetched from documented exchange endpoints or documented publisher archives | No | **Yes** | Provider, endpoint, response time, checksums, coverage, missingness and availability-time manifest |
| `LIVE_FEED_READ_ONLY` | Live public exchange REST/WebSocket, including order book / trades / closed OHLCV | No | **Yes** | Public-only adapter, reconnect/gap detection, raw event archive and first-seen clock |
| `FORWARD_PAPER_REAL_FEED` | Same live provider feed; simulated fills/account only | **No real or demo orders** | **Yes**, explicitly separate from G1 approval | Isolated paper wallet, reproducible decision logs, spread/fees/slippage, risk veto, state recovery and NO_TRADE |
| `DEMO_READ_ONLY` | Official exchange sandbox/demo account using **demo-scoped credentials** | Private *reads* only | **Yes** | Verified venue demo routing, scope, balance/precision/authentication readback, no order methods |
| `DEMO_ORDER_TEST` | Official exchange simulated trading environment | **Demo orders only** | **Conditionally yes**, after *independent DEMO safety gate D0* passes; G1 production approval not required | Verified sandbox isolation and credentials, capped virtual order size, durable client ID/intent, risk veto, reconciliation, crash/timeout drills, audit and kill switch |
| `LIVE_REAL_MONEY` | Production exchange with actual capital | **Real orders** | **No** | All production financial/OMS/risk/credential readiness, scientific evaluation and separate scoped operator release |

**Not equivalent:** local paper fills are **not** official exchange demo fills; an official demo balance is **not** real equity; exchange-origin history is **not** live first-seen evidence; a successful CI test is **not** demonstrated profitability.

## D0 — minimal official-DEMO order safety gate

Before the first actual order sent to an official DEMO endpoint, require all:

1. Confirm the selected venue **offers an official simulated trading API for the intended market and account**; no invented Bitpin/Nobitex testnet capability. Prefer existing OKX Demo adapter/runbook when compatible; follow the exchange's verified latest docs.
2. Check demo-specific keys, host/header/sandbox mode, and server-side account type *before any private trading call*. Reject production credentials, fallback credentials, uncertain routing, unsupported instruments and account types. Never expose secrets in code/CI/logs.
3. Separate DEMO transport, database, account, API keys and operator identity from LIVE. Default deny when environment identity cannot be proven.
4. Require explicit DEMO-only enablement, symbol allowlist, bounded virtual notional, independent stop/risk veto, quantity/minimum validation, persisted unique client intent **before sending**, and status reconstruction/reconciliation **after** ambiguous network failures or restarts.
5. Demonstrate in tests: submit ACK, partial fill, cancel, delayed fee in non-quote asset, missing/zero filled amount, duplicate client ID, restart recovery, uncertain submit, terminal history, halted account and cross-process mutation. Document remaining defects; an unresolved defect affecting the chosen demo path blocks **DEMO_ORDER_TEST** but **not** live public data or isolated paper simulation.
6. Obtain owner-scoped DEMO test instructions (venue, demo account, allowed markets and virtual-order budget) before order creation. The owner's generic permission to trade live does not convert an unknown account into a verified demo account.

Production G1 remains **BLOCKED** while the known fee/liability and committed-intent-to-send/recovery issues remain unresolved. D0 is a separate controlled test boundary, not a relabeling of G1 or permission to merge unsafe finance code.

## Data acquisition and provenance

- Prefer authenticated provider-origin sources only where permissions and provider capabilities warrant it; public market-data APIs ordinarily need no trading key.
- Each immutable raw response should record venue, market type (spot/futures), symbol mapping, timeframe, UTC event/close timestamp, download/receive/first-seen timestamps, API endpoint/version, pagination ranges, source account mode, checksum and requested lookback.
- WebSocket: timestamp receipts with a UTC clock, detect sequence gaps/outages, reconnect and backfill separately. **Backfill is historical context**, never retrospectively labeled first-seen prospective.
- Features and labels must use `available_at` for every consumed data element. A TradingView/Kaggle reupload without provider provenance must be labeled `THIRD_PARTY_UNVERIFIED`, not `EXCHANGE_VERIFIED`.
- Do not touch sealed Kraken holdouts, future bars or test windows reserved by preregistration.
- Use real downloaded market bars for economic backtests, plus distinct first-seen live-feed evidence for forward evaluation. Synthetic fixtures remain suitable only for engineering tests.

## New delivery sequence for Codex

1. Re-read `AGENTS.md`, `README.md`, canonical status, Issue #126, and active PRs #145/#148/#149/#151. Map code and gaps; do not blind-merge stacked branches.
2. Implement isolated **public live-data ingestion** (provider native REST + WebSocket as appropriate) behind mode `LIVE_FEED_READ_ONLY`. Persist immutable raw data and observability, then prove genuine provider responses in a separately marked integration run (not through fixtures).
3. Bridge the already implemented strategy candidate layer to the isolated `FORWARD_PAPER_REAL_FEED` simulator: causal closed candles, canonical timestamps, NO_TRADE, deterministic event/order IDs, fees and capital accounting, risk veto and restart state.
4. Integrate official `DEMO_READ_ONLY` preflight for a supported venue. If and only if D0 succeeds, add a dedicated `DEMO_ORDER_TEST` workflow with virtual-money caps and read-back reconciliation.
5. Keep **all** LIVE real-money order adapters/flags OFF and separate; do not modify `research_bot/service.py` research-monitoring deployment firewall to smuggle in execution. Deploy a separate, explicitly named worker only after review.
6. In parallel, continue G1 finance defect corrections in their proper PR/branch, without gating read-only feed collection or local forward paper.
7. Deliver a reproducible proof bundle for each mode: `source SHA / provider / endpoint / UTC first seen / data SHA256 / sample timestamps / test command / CI run / actual broker-demo readback or NOT_CONNECTED / errors / permission state`.
8. Update Issue #126 with verified results only, including the difference between implemented, locally tested, CI-tested, real-feed observed and actual exchange DEMO orders observed.

## Mandatory machine-readable flags

- `MARKET_DATA_MODE=HISTORICAL_REAL|LIVE_FEED_READ_ONLY` (not an order permission)
- `EXECUTION_MODE=OFF|FORWARD_PAPER_REAL_FEED|DEMO_READ_ONLY|DEMO_ORDER_TEST|LIVE_REAL_MONEY`
- `LIVE_REAL_MONEY_ENABLED=false` by default; no mode inference from presence of API keys.
- Use explicit `exchange_environment=public|demo|production`, `order_destination=none|simulator|demo|production`, `demo_verified`, `risk_gate_passed` and `execution_is_real_money` in logs and reports.

These names are **target contracts**, not claims that current application code already supports the flags. Do not flip or reuse legacy flags merely to satisfy a test.

## Gate outcome at policy creation

- **Public real exchange history and live feed: AUTHORIZED FOR IMPLEMENTATION**; provider connectivity not yet shown by this document.
- **Forward paper on real live feed: AUTHORIZED FOR IMPLEMENTATION**; cannot be called live-forward evidence until first-seen samples exist.
- **Official exchange DEMO: authorized for read-only preflight; demo order tests conditional on D0.**
- **Production G1: BLOCKED. G3 economic alpha: INSUFFICIENT_EVIDENCE. Real-money order submission: NOT REQUESTED IN THIS CHANGE.**

**Evidence Before Opinion. Public data does not spend money; simulated orders must never route to a real-money account.**
