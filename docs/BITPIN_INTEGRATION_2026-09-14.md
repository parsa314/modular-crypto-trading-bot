# Bitpin integration — read-only first

Date: 2026-09-14

## Scope

This integration adds Bitpin as an Iranian-market data venue while preserving the canonical project safety state. It does not enable PAPER or LIVE execution and does not add a real order-submission path.

## Public endpoints implemented

- `GET /mkt/currencies/`
- `GET /mkt/markets/`
- `GET /mkt/tickers/`
- `GET /mth/orderbook/{SYMBOL}/`
- `GET /mth/matches/{SYMBOL}/`

The API base defaults to `https://api.bitpin.market/api/v1` and can be overridden with `BITPIN_API_BASE_URL`. This is deliberate because Bitpin API hostnames and deprecation state may change.

## Execution-safety bridge

`fetch_bitpin_depth()` maps Bitpin public order-book data into the existing `DepthSnapshot` contract. This means downstream paper-research logic can keep using executable-side prices (ask for a buy, bid for a sell), quote-age checks, spread/depth diagnostics and fail-closed validation without adding any live exchange write capability.

## Authenticated endpoints intentionally NOT enabled

Bitpin authentication, wallets, user orders/trades, create-order and cancel-order remain outside the active adapter. The current project safety gate is research-only. Credentials must never be committed to GitHub.

When governance later authorizes authenticated integration, credentials should be injected only as secrets/environment variables:

- `BITPIN_API_KEY`
- `BITPIN_API_SECRET`
- optionally issued access/refresh tokens if the selected client flow requires them

The authentication flow observed in current Bitpin community SDKs uses `/usr/authenticate/` with `api_key` and `secret_key`, and `/usr/refresh_token/` with a refresh token. These must be revalidated against Bitpin's current official documentation at activation time because the official docs currently carry an API deprecation warning.

## What is not needed now

No API key or secret is required for the public market-data adapter. Do not send secrets in chat, source code, README files, issue bodies or screenshots.
