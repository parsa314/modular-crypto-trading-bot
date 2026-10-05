# Nobitex TESTNET Integration Runbook

Date: 2026-10-05

## Venue decision

Nobitex is the primary Iranian execution venue for the thesis TESTNET path.

Reasons:
- official API documentation;
- official TESTNET web environment: `https://testnet.nobitex.ir`;
- official TESTNET API: `https://testnetapi.nobitex.ir`;
- deterministic `clientOrderId` support;
- order status and cancellation APIs;
- public market metadata and precision/minimum-order settings;
- existing repository reconciliation logic for Nobitex.

Versland is not selected for automated execution because no stable public
trading API/Testnet contract is currently available to this project.

## Safety boundary

The adapter is hard-locked to `https://testnetapi.nobitex.ir`.

It accepts only:
- `NOBITEX_TESTNET_ENABLED=true`
- `NOBITEX_TESTNET_TOKEN`

There is no fallback to `NOBITEX_API_TOKEN` or `https://api.nobitex.ir`.

## Read-only preflight

Workflow:
`.github/workflows/nobitex-testnet-readonly-preflight.yml`

Required repository secret:
`NOBITEX_TESTNET_TOKEN`

The preflight:
1. reads public BTC/USDT orderbook data;
2. reads `/v2/options` for amount precision and minimum notional;
3. authenticates with `/users/wallets/list`;
4. returns only wallet count, not balances;
5. performs no create/cancel/update operation.

## TESTNET order contract

Market orders use:
- `POST /market/orders/add`;
- `execution=market`;
- deterministic `clientOrderId`;
- amount serialized as a string;
- expected/reference price included to constrain adverse market execution.

After submission:
- numeric order id is persisted;
- `POST /market/orders/status` is authoritative for reconciliation;
- network ambiguity never triggers blind resubmission;
- `POST /market/orders/update-status` is followed by a status fetch;
- partial fills are preserved after cancellation.

## Promotion sequence

Read-only credential preflight
→ tiny TESTNET order
→ partial-fill/cancel drill
→ timeout-after-send drill
→ restart + PostgreSQL reconciliation
→ balance conservation audit
→ multi-symbol TESTNET forward run
→ scientific review

This runbook does not authorize LIVE trading.
