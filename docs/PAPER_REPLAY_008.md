# Historical paper replay 008 — engineering prototype

This is a closed-bar historical replay, not an online paper session, broker connection, validated ML model or release. Four unchanged strategy prototypes and their existing unit tests come from commit `ae65f99e092aa0f631675f02626003438d2f5dfe` (stacked PRs #128–#132); their lineage remains separate. The confluence hypothesis is not evidence of performance or prior approval for scientific promotion.

Run from a checkout with dependencies installed:

```sh
python -m pip install -e '.[dev]'
python -m research_bot.paper_replay_008 \
  --csv /path/to/btcusdt-h1.csv --state /path/to/isolated-paper.sqlite \
  --as-of '2024-12-05T00:00:00+00:00' \
  --source 'Binance Vision BTCUSDT M15 aggregated H1, source commit cda9e5ff47d0f40cb62b6734c784317d2f15e405' \
  --fee-bps 10 --spread-bps 3 --slippage-bps 2
```

The CSV must contain `timestamp,open,high,low,close,volume`, ascending unique UTC hourly open times, at least 91 closed consecutive bars, and valid OHLCV. It is not a source authentication mechanism: the caller must obtain and verify a genuine dataset and provide its provenance. The workflow uses the prior branch's parquet and compares its SHA-256 with the included manifest before aggregating four actual M15 candles per hour. The first observation time is unavailable. Historical candle-close availability is an assumption and never prospective evidence.

The current model is a **cash-only baseline**, not a trained or calibrated model. The signal confluence is recorded, but is not converted into an invented forecast or approval. Thus the real CLI run abstains; a synthetic test injects a decision candidate solely to exercise the later closed-bar paper-fill path. Spread is the full bid/ask spread in bps, split evenly over each side; fee and slippage are bps per fill and are assumptions, not measured costs.

The SQLite ledger stores exact Decimal strings, checks risk independently, requires the fill timestamp after the decision, commits account and fill together, rejects conflicting duplicate IDs and reloads state after restart. These are prototype engineering controls, not proof of safety for a real account. The current runner makes one decision per invocation, does not support a trained ML model, does not implement daily-loss anchoring or proven risk-reducing exits under a halt, and a crash after fill but before evidence persistence can require manual recovery. It has not demonstrated a real-market paper fill. Fee provenance F01 and OKX precision F02 for broker paths remain blocked, independently of this paper experiment.
