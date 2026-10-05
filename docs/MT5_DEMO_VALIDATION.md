# MT5 DEMO Forward-Execution Validation

## Purpose

The canonical bot remains a cryptocurrency-exchange research/trading system.
MetaTrader 5 is added only as a **DEMO forward/execution validation venue** so
the existing strategy, ML inference, risk gate, order lifecycle and learning
pipeline can be exercised without risking real capital or requiring a supported
crypto-exchange account.

This does **not** authorize live trading and does not change the canonical
execution flags:

- LIVE execution remains OFF.
- Existing PAPER authorization remains unchanged.
- MT5 real-money accounts are explicitly refused by code.
- Exchange adapters remain the intended production destination after separate
  scientific and operational promotion.

## Architecture

    Exchange historical/public data
              |
              v
    Feature / strategy / ML / calibration
              |
              v
    Decision + independent risk gate
              |
              +--------------------------+
              |                          |
              v                          v
    Existing exchange path        MT5 DEMO adapter
    (canonical destination)       (validation venue)
                                         |
                                         v
                                  Broker DEMO server
                                         |
                                         v
                              execution/fill observations

Strategy and AI logic are not rewritten in MQL5. The Python decision engine
produces the same venue-neutral ExecutionRequest. The MT5 adapter translates
canonical symbols and requested quote notional into broker-specific symbols and
lots.

## Why MT5 DEMO is the primary automated validation venue

The official MetaTrader 5 Python integration exposes account information,
market data, order pre-checking and order submission. The adapter therefore can
validate a real execution path while remaining on virtual funds.

TradingView Paper Trading is useful for manual/visual forward testing and Pine
strategy reports. TradingView alerts can also send webhooks to an external
service. However, TradingView currently states that automated Pine Strategy
trading on brokerage accounts is not available. Therefore TradingView must not
be treated as the primary programmatic order-execution API for this project.

## Safety invariants implemented in code

research_bot/execution_adapters/mt5_demo.py enforces:

1. The connected account trade mode must be DEMO.
2. Trading and expert trading must be allowed by the terminal/account.
3. Canonical symbols must be explicitly allowlisted.
4. Canonical symbols are mapped explicitly to broker symbols.
5. The executable bid/ask tick must be fresh.
6. Crossed/non-positive quotes are rejected.
7. Spread above the configured threshold is rejected.
8. Requested notional is capped before submission.
9. Base-asset quantity is converted to broker lots using trade_contract_size.
10. Volume is rounded DOWN to volume_step, never up.
11. Broker minimum/maximum volume bounds are enforced.
12. A server-side stop loss is required by default.
13. Stop/target direction and broker stop-distance constraints are checked.
14. order_check must pass before order_send.
15. Real/non-demo accounts fail closed even when submission is enabled.
16. Demo submission itself defaults OFF and requires explicit opt-in.
17. Duplicate client_order_id values are refused within the running process.

Persistent restart reconciliation remains a separate engineering gate before
this adapter can be considered production-grade.

## Installation/runtime

Run MT5 execution on a Windows machine or Windows VPS with the MetaTrader 5
terminal installed and logged into a DEMO account.

Install project dependencies plus the optional MT5 extra:

    python -m pip install -e ".[dev,mt5]"

Do not store account credentials in the repository. If runtime login is needed,
use environment variables:

    MT5_LOGIN
    MT5_PASSWORD
    MT5_SERVER
    MT5_TERMINAL_PATH

A connection-only safety smoke:

    python scripts/mt5_demo_smoke.py \
      --canonical-symbol BTC/USDT \
      --venue-symbol BTCUSD

This validates the account and exits without sending an order.

A DEMO order requires two independent opt-ins:

    set MT5_DEMO_SUBMIT_ENABLED=1

and:

    python scripts/mt5_demo_smoke.py \
      --canonical-symbol BTC/USDT \
      --venue-symbol BTCUSD \
      --side BUY \
      --quantity 0.01 \
      --reference-price 60000 \
      --stop-loss 59000 \
      --take-profit 62000 \
      --submit-demo

The exact broker symbol may be BTCUSD, BTCUSDm, BTCUSD.a or another broker
variant. Never assume the mapping; inspect the selected DEMO broker first.

## Scientific evidence contract

MT5 DEMO data is a separate venue/domain. Every forward observation should keep:

- canonical symbol;
- venue symbol;
- broker/server;
- DEMO trade mode;
- decision timestamp;
- executable tick timestamp;
- model/strategy version;
- dataset/feature version;
- git commit;
- requested quantity/notional;
- contract size and submitted lots;
- bid/ask/spread;
- SL/TP;
- order/deal ticket;
- fill price;
- implementation shortfall;
- eventual realized outcome.

Do **not** combine MT5 DEMO returns with exchange Spot/Perpetual returns as if
they came from the same market.

## Learning policy

For the AI learning stage, DEMO executions should initially operate in
**shadow-learning mode**:

1. the currently frozen/champion model makes the decision;
2. execution and subsequent outcome are appended to an immutable experience
   ledger;
3. candidate models may retrain from prior admissible observations;
4. the candidate is scored prequentially / walk-forward against the frozen
   champion;
5. no same-trade or same-window outcome may update a model before its result is
   known;
6. the candidate cannot replace the champion without a predefined promotion
   gate covering costs, drawdown, calibration, stability and multiple assets.

This lets the thesis demonstrate adaptive/online-learning mechanics without
silently turning recent demo outcomes into leakage or self-confirming evidence.

## Validation ladder

Recommended order:

1. deterministic unit tests;
2. historical exchange backtests with realistic costs;
3. existing internal paper simulator;
4. MT5 DEMO forward execution;
5. TradingView visual/Pine forward comparison where useful;
6. cross-venue robustness analysis;
7. exchange testnet/sandbox when legally/operationally available;
8. real exchange execution only under a separate future promotion and safety
   authorization.

The original exchange bot remains the target architecture throughout this
ladder.
