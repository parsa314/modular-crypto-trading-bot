# Four independent PIT signal arms — Engineering prototype, 2026-10-14

**Status:** preliminary research-only event generators, not trained AI, not production strategies, not a positive return claim. New package in `research_bot/strategies` because the installed repository currently uses `research_bot` (it has no `src/agents` tree).

## What is implemented

- `ichimoku_strategy.py`: 9/26/52 Ichimoku midpoints, Senkou cloud shifted forward on the chart but safely accessed with `shift(26)` for current decision; an *actual new* TK cross, cloud breakout or Kijun reclaim is needed to emit a candidate. Chikou confirmation compares the present close to a **past** high/low, never a future price.
- `ict_strategy.py`: 20-closed-bar swing liquidity-sweep proxy, followed within an 8-bar window by an intervening-structure close (MSS), then optional displacement and DST-aware London/NY session confirmation. The sweep is not direct evidence of a stop hunt.
- `smc_strategy.py`: a 12-bar break-of-structure signal with an opposing-candle order-block **proxy**; a confirmed 3-candle gap plus displacement adds the third score point. OB detection does not prove institutional orders, and retest/mitigation is not yet implemented.
- `al_brooks_strategy.py`: EMA20 trend-context approximation, mechanically strong signal bar, five-bar breakout/follow-through. Not a complete Brooks discretionary setup dictionary; further pattern-specific ablations are required.
- `confluence.py`: exactly four independent style votes, each 0..3, equal weights 0.25 each and normalized total `4*sum(weight*score)` (max 12); threshold >=7 **and** >=3 agreeing nonzero arms, veto contradictory BUY/SELL, otherwise HOLD.
- `common.py`: strict OHLCV and `timestamp`/`available_at` checks, aware `as_of`, no duplicate/invalid/nonfinite rows, and exclusion of not-yet-available market observations.
- `scripts/evaluate_four_strategy_signals.py`: actual CSV input and optional externally supplied SHA256; prints causal signals and hash, NO trading, NO PnL estimate.

## Reproducible interface

CSV must contain `timestamp,available_at,open,high,low,close,volume`. `timestamp` is candle OPEN time, and `available_at` is the first time the CLOSED candle could be observed. For M15, `available_at` cannot be before candle close. The current code validates `available_at >= timestamp` only; source adapters MUST additionally prove the correct candle duration and source latency before this becomes accepted point-in-time market evidence.

```bash
python -m pytest -q tests/test_four_independent_strategies.py
python scripts/evaluate_four_strategy_signals.py --csv /path/to/verified_M15_BTCUSDT.csv --as-of 2024-12-31T23:59:00Z --expected-sha256 ACTUAL_64_HEX
```

Never pretend the CLI did an out-of-sample backtest or that a supplied CSV is real without venue/source provenance. No BTC/USDT 2022–2024 dataset has yet been retrieved and independently verified for THIS PR. Unit-test OHLCV fixtures are explicitly **synthetic solely to test software invariants**. Any real dataset must include vendor, venue, symbol, bar bounds, time-zone, timestamp semantics, publication latency, gap/duplicate statistics, hash and rights to use. Do not fill missing observations with invented bars.

## Immediate scientific gaps and rejection conditions

1. Confluence components are NOT calibrated probabilities; 7/12 and 0.25 are predeclared **hypotheses**, not optimized or profitable constants.
2. There is no exit, stop/target barrier event, fees, execution simulator, margin model, portfolio position accounting, partial fill, broker connectivity, online RL, walk-forward, CPCV or proof of positive alpha in this package.
3. Four styles share price-derived information, so independence of votes is **conceptual, not statistical**. Do ablations, nested training, uncertainty calibration and explicit correlation tests before giving weights significance.
4. Advanced versions from PRs #115, #122 and #123 exist in other lineages. Before final merge, review and integrate their causal setup detectors rather than maintain redundant engines indefinitely.
5. Run leakage regressions on true available timestamps and exchange-specific M15/4H alignment. For time-based event labels use stop-first when candles contain both TP and SL.
6. Route any research signal through an independent risk/OMS gate only after a separate audited promotion. This branch contains **no order submission code**.

**Acceptance:** passing deterministic tests is necessary but not sufficient. Real-data data-integrity reports, multi-asset out-of-sample performance after realistic cost stress, walk-forward and independent prospective demo proof remain pending.
