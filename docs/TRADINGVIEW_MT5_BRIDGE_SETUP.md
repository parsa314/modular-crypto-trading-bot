# TradingView -> MetaTrader 5 DEMO Bridge Setup

This bridge connects TradingView alerts to the project's existing Python
execution layer and then to a **MetaTrader 5 DEMO account**.

It does not change the final target architecture: the canonical bot remains a
cryptocurrency-exchange trading/research system. MT5 is a temporary
forward/execution-validation venue.

## Data path

    TradingView alert()
          |
          | HTTPS POST /webhooks/tradingview/<route-token>
          v
    FastAPI bridge on Windows/VPS
          |
          v
    JSON validation + restart idempotency journal
          |
          v
    MT5DemoExecutor
          |
          +--> DEMO-account check
          +--> symbol allowlist
          +--> fresh bid/ask
          +--> spread gate
          +--> max notional
          +--> lot/contract conversion
          +--> server-side SL/TP validation
          +--> order_check()
          v
    order_send() -> MT5 DEMO server

The webhook responds HTTP 202 before MT5 execution completes. Execution results
or errors are written to the append-only journal.

## 1. Windows / VPS requirements

Use a Windows machine or Windows VPS with:

- MetaTrader 5 terminal installed;
- the desired broker DEMO account logged in;
- algorithmic/expert trading allowed in the terminal/account;
- Python compatible with the project;
- the repository checked out on branch
  engineering/mt5-demo-validation.

Install:

    python -m pip install -e ".[dev,mt5]"

## 2. Generate a dedicated webhook route token

Do not reuse an exchange key, MT5 password, email password or any other account
credential.

Generate a dedicated random routing token:

    python -c "import secrets; print(secrets.token_urlsafe(32))"

Keep it private. If it is exposed, rotate it.

## 3. Environment configuration

PowerShell example:

    $env:TV_MT5_BRIDGE_ENABLED="1"
    $env:TV_WEBHOOK_ROUTE_TOKEN="<generated-route-token>"

    $env:TV_ALLOWED_SYMBOLS="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT"

    $env:TV_MT5_SYMBOL_MAP_JSON='{"BTC/USDT":"BTCUSD","ETH/USDT":"ETHUSD","SOL/USDT":"SOLUSD","XRP/USDT":"XRPUSD","DOGE/USDT":"DOGEUSD"}'

    $env:MT5_LOGIN="<demo-account-number>"
    $env:MT5_PASSWORD="<demo-password>"
    $env:MT5_SERVER="<exact-demo-server-name>"
    $env:MT5_TERMINAL_PATH="C:\Program Files\MetaTrader 5\terminal64.exe"

    $env:MT5_MAX_ORDER_NOTIONAL="5000"
    $env:MT5_MAX_SPREAD_BPS="35"

    $env:TRADINGVIEW_ENFORCE_SOURCE_IP="1"
    $env:TRADINGVIEW_TRUST_PROXY="0"

Start in DRY-RUN first:

    $env:MT5_DEMO_SUBMIT_ENABLED="0"

The exact MT5 symbol names are broker-specific. BTC may be BTCUSD, BTCUSDm,
BTCUSD.a, BTCUSD#, or something else. Inspect Market Watch and set the mapping
explicitly.

## 4. Start the bridge

    python scripts/run_tv_mt5_bridge.py

Local health check:

    http://127.0.0.1:8000/health

The runner binds to 127.0.0.1:8000 by default.

## 5. HTTPS exposure

TradingView must reach a public HTTP/HTTPS endpoint on port 80 or 443. The
local bridge may remain on 127.0.0.1:8000 behind an HTTPS reverse proxy or
secure tunnel.

Public webhook shape:

    https://YOUR_HOST/webhooks/tradingview/YOUR_ROUTE_TOKEN

Do not expose MT5 credentials in the URL or alert body.

TradingView currently publishes these webhook source IPs:

- 52.89.214.238
- 34.212.75.30
- 54.218.53.128
- 52.32.178.7

The bridge checks them by default. If TLS is terminated by a reverse proxy,
configure the proxy correctly and only then set:

    $env:TRADINGVIEW_TRUST_PROXY="1"

Do not trust X-Forwarded-For from arbitrary public clients unless the bridge is
reachable only through your trusted proxy.

## 6. TradingView setup

TradingView webhook alerts require 2-factor authentication on the TradingView
account.

Add:

    tradingview/mt5_demo_bridge_test.pine

to Pine Editor and add it to a chart.

Create an alert:

- Condition: TradingView -> MT5 DEMO Bridge Transport Test
- Trigger: Any alert() function call
- Webhook URL:
  https://YOUR_HOST/webhooks/tradingview/YOUR_ROUTE_TOKEN

The Pine script generates valid JSON dynamically.

For an existing Pine strategy, copy the three helper functions:

- f_iso_time()
- f_event_id()
- f_payload()

and call f_payload() only when the real entry condition is confirmed.

## 7. Dry-run first

Keep:

    MT5_DEMO_SUBMIT_ENABLED=0

Trigger one TradingView transport test.

Expected webhook response:

    ACCEPTED_DRY_RUN

Inspect:

    GET /bridge/recent

The journal should contain a RECEIVED row and no MT5 order should exist.

## 8. Enable MT5 DEMO orders

Only after the dry-run path is correct:

    $env:MT5_DEMO_SUBMIT_ENABLED="1"

Restart the bridge process.

Now a valid TradingView BUY/SELL webhook is submitted only to an MT5 DEMO
account. The MT5 adapter will refuse real/non-demo accounts.

## 9. Webhook JSON contract

Example:

    {
      "event_id": "BTCUSDT-20261005T140000Z-LONG",
      "symbol": "BTC/USDT",
      "action": "BUY",
      "quantity": 0.01,
      "reference_price": 62000,
      "stop_loss": 61000,
      "take_profit": 64000,
      "event_time": "2026-10-05T14:00:00Z",
      "strategy_version": "ICHIMOKU_ICT_BROOKS_V59",
      "model_version": "champion-001",
      "metadata": {
        "timeframe": "4h"
      }
    }

Required action values in this first bridge stage are:

- BUY
- SELL

The executable MT5 order price is taken from the fresh broker bid/ask, not from
TradingView reference_price. The TradingView price remains decision provenance.

## 10. Important current limitation

This first bridge stage handles new BUY/SELL entry intents with broker-side
stop loss and optional take profit. It deliberately does not interpret a SELL
message as "close my long" because that behavior is unsafe on hedging accounts.

Signal-based CLOSE / REDUCE requires a dedicated position-reconciliation layer
that closes only bot-owned positions by ticket/magic. Until that layer is
implemented, exits are:

- broker-side SL/TP; or
- the bot's existing separately controlled execution path.

This is deliberate fail-closed behavior, not a missing shortcut.

## 11. Scientific logging

Never pool MT5 broker/CFD performance with crypto-exchange Spot/Perpetual
performance without a declared cross-venue experiment.

Keep:

- TradingView event_id and event_time;
- strategy/model version;
- TradingView decision reference price;
- MT5 executable price/fill price;
- broker/server;
- symbol mapping;
- order/deal ticket;
- costs/slippage;
- outcome timestamp;
- execution_domain.

These records can feed the shadow-learning experience ledger only after their
outcomes become causally available.
