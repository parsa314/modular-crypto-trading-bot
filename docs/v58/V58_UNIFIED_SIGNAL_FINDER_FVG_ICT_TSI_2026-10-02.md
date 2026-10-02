# V58 Unified Signal Finder — FVG/ICT/TSI + Confluence10

Date: 2026-10-02  
Status: research-only signal discovery; PAPER/LIVE disabled.

## Scope

This revision integrates the supplied deterministic FVG/ICT/TSI strategy with the ten previously registered V58 Ichimoku + ICT + SMC + Al-Brooks-inspired hypotheses.

The signal finder is intentionally upstream of AI and finance:

```text
OHLCV
  -> deterministic strategy candidates
  -> AI probability / calibration / uncertainty
  -> expected utility + transaction costs
  -> finance-AI handoff
  -> independent financial veto / sizing
  -> shared-capital portfolio research
```

No strategy module is allowed to create a real order.

## Supplied FVG/ICT/TSI family

Frozen core rules:

- Three-candle bullish FVG: candle-3 low > candle-1 high.
- Three-candle bearish FVG: candle-3 high < candle-1 low.
- Middle candle must pass the displacement-body threshold.
- HTF/LTF mappings are configurable, with primary examples 1H -> 5m and 4H -> 15m.
- TSI = 25 / 13 / 13.
- Confirmation modes:
  - `tsi`
  - `structure`
  - `either` (default)
  - `both`
- Structure break is deterministic: close beyond the previous N-bar high/low.
- Price must enter the FVG.
- Midpoint rejection can be mandatory.
- Entry reference is the immediate next LTF bar open after the closed signal bar.
- FVGs are single-use once a signal is emitted.
- FVG age is capped in HTF bars.
- Stop modes:
  - `midpoint`
  - `zone_edge`
  - `swing`
- Target is R:R-based.
- STOP_FIRST is mandatory for same-bar stop/target ambiguity.

## Timestamp hardening

The uploaded standalone script used right-labelled resampling. V58 instead interprets input timestamps as **bar-open timestamps**, which matches normal exchange OHLCV conventions.

Resampled bars remain labelled by their open. Therefore:

```text
signal_time = signal_bar_open + LTF duration
entry_time  = next_bar_open
signal_time == entry_time
```

This makes the close[t] -> open[t+1] contract explicit and avoids a one-bar timestamp-label ambiguity.

## Financial authority

Signal discovery stores entry/stop/target geometry but **does not size capital**.

Position sizing belongs to the downstream financial engine. The standalone research backtest keeps a conservative sizing implementation for reproducibility, with:

- risk per trade <= 0.25%
- max single-asset notional <= 35% equity
- max gross exposure ceiling <= 70%
- drawdown kill <= 5%
- CVaR95 ceiling <= 3.5%
- fees and slippage included in stop-risk budgeting
- gap-stop handling
- STOP_FIRST ambiguity

Bearish FVG signals are detected and logged, but the current V58 spot portfolio does not authorize short execution. They are labelled:

`SIGNAL_ONLY_BEARISH_SHORT_NOT_AUTHORIZED_BY_CURRENT_V58_SPOT_PORTFOLIO`

## Combined registry

The unified finder contains:

1. Ten `C10_*` four-framework hypotheses.
2. `FVG_ICT_TSI_MTF`.

The FVG family has research dimensions for:

- HTF/LTF pair
- confirmation mode
- stop mode
- R:R

These dimensions must be recorded as separate trials during tournament testing. They must not be tuned after viewing the same test outcomes.

## Commands

### Standalone supplied-family backtest: 1H -> 5m

```bash
python fvg_ict_strategy.py BTCUSDT_1m.csv --htf 1h --ltf 5min
```

### 4H -> 15m

```bash
python fvg_ict_strategy.py BTCUSDT_1m.csv --htf 4h --ltf 15min
```

### TSI only

```bash
python fvg_ict_strategy.py BTCUSDT_1m.csv \
  --htf 1h \
  --ltf 5min \
  --confirmation tsi
```

### TSI OR structure break

```bash
python fvg_ict_strategy.py BTCUSDT_1m.csv \
  --htf 1h \
  --ltf 5min \
  --confirmation either
```

### TSI AND structure break

```bash
python fvg_ict_strategy.py BTCUSDT_1m.csv \
  --htf 1h \
  --ltf 5min \
  --confirmation both
```

### Stop-mode experiments

```bash
--stop-mode midpoint
--stop-mode zone_edge
--stop-mode swing
```

Standalone outputs:

- `fvg_ict_trades.csv`
- `detected_fvgs.csv`
- `equity_curve.csv`
- `fvg_ict_signals.csv`

### Unified V58 signal finder

```bash
python -m research_bot.v58 signal-scan \
  --csv BTCUSDT_1m.csv \
  --symbol BTC/USDT \
  --htf 1h \
  --ltf 5min \
  --confirmation either \
  --stop-mode zone_edge \
  --output results/signal-scan
```

Outputs:

- `combined_signals.csv`
- `confluence10_signals.csv`
- `fvg_ict_tsi_signals.csv`
- `detected_fvgs.csv`
- `signal_registry.json`
- `signal_scan_summary.json`

## Backtest metrics

The standalone FVG family reports:

- Win Rate
- Net PnL
- Return
- Profit Factor
- Average R
- Max Drawdown
- Total Costs
- Fee Costs
- Slippage Costs

## Scientific protocol

The new family is a hypothesis, not evidence of profitability.

Testing should preserve a complete trial registry for at least:

- 1H -> 5m
- 4H -> 15m
- confirmation = tsi / structure / either / both
- stop = midpoint / zone_edge / swing

That is 24 pre-declared FVG experiment cells before any R:R sensitivity analysis. Multiple-testing/search-aware controls must account for all attempted variants.

PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE
