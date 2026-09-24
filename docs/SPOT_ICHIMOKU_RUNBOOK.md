# Spot Ichimoku bot: executable research path

## 1. Install

Use Python 3.11 in a clean virtual environment at the repository root:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest tests.test_spot_ichimoku_bot -v
```

## 2. Run a spot-only historical study

```bash
python -m scripts.run_spot_ichimoku_bot backtest --symbol BTC/USDT --bars 600 --output artifacts/btc_spot.json
```

This fetches public CoinEx spot candles; no API key is needed. Signals use only
closed four-hour candles. A hypothetical trade fills at the next bar's open;
no short positions are possible. The account starts with 10,000 USDT, allocates
up to 20% on entry and models 10 bps fees plus 2 bps slippage per fill.
The output records individual fills, ending cash/base asset, marked equity and
drawdown. It is a historical **in-sample strategy check**, not proof of alpha:
the rule was previously developed and the same period must not be called a
prospective holdout. Candle OHLCV alone cannot prove attainable execution.

## 3. Observe new signals without orders

Provide a PostgreSQL database connection in the shell environment and run:

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME'
python -m scripts.run_spot_ichimoku_bot observe --symbol BTC/USDT --bars 280 --output artifacts/btc_observation.json
```

The command uses the existing `ForwardPaperRunner`, current CoinEx public
four-hour candles and order-book quotes. It requires durable PostgreSQL state
to deduplicate bars across process restarts. Run it shortly after each 4-hour
bar closes, for example at 00:02, 04:02, 08:02, 12:02, 16:02 and 20:02 UTC.
An old signal or stale quote is rejected. This mode records observations only;
it sends no exchange orders and does not simulate a fill.

## 4. Current execution gate

`LIVE_EXECUTION=false` and `PAPER_EXECUTION=false` remain the project's
scientific state. The older v0.54 Ridge audit must not be wired to exchange
orders: it generated short positions from spot data and failed the net-return
cost test on all five studied assets. This runner has no private API key or
live-order route. Actual money execution requires a distinct audited exchange
adapter, market filters, persisted order reconciliation, independent risk
limits, validated out-of-sample and forward evidence, and explicit scientific
promotion. Setting a flag cannot override that gate.

Never commit database credentials. For persistent scheduling, arrange your
scheduler to run the observe command on the bar-close times above, with its
working directory at the repository root and `DATABASE_URL` set securely.
