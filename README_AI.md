# Executable Primary + ML + PPO architecture

This track implements the owner's four-phase instruction dated 2026-10-17.
Demo and paper execution only. Earlier research artifacts retain their original
meaning; this experiment uses new EMA20/50 entries rather than v1–v6 signals.

## Actual results

| Phase | Measurement | Outcome |
| --- | --- | --- |
| 1 | MT5Forge installed; actual Linux probe: 0 connected demo sessions, 0 EURUSD H1 candles, 0 submitted orders | Connection not available on this host |
| 2 | 1,045 EMA events; validation Precision@top_decile 0.333333; raw/ML Sharpe −10.287140 / −3.803946 | FAIL against precision >0.60 |
| 3 | PPO 100,352 actual steps; fixed 2% Sharpe −3.804297; PPO 0 allocated trades, 0% return, undefined Sharpe | FAIL; fixed fraction retained |
| 4 | 15 independent ML/PPO refits, 1,505,280 PPO steps, 5 OOS paths; PBO 0.0; PPO DSR undefined | REJECTED |

Reports: [MT5](results/mt5_connection.json), [ML](results/ml_filtered_backtest.json),
[sizing](results/rl_vs_fixed_sizing.json), [CPCV](results/final_validation.json).
Zero return volatility makes Sharpe and DSR undefined; null is deliberately
preserved. PBO 0.0 does not establish a profitable PPO strategy. Across the five
CPCV paths, fixed-sizing Sharpe ranges from −3.9753 to −3.0655; PPO stays in cash.
The JSON includes individual fold and path measurements.

## Install and run

The full MT5Forge stack requires Python 3.12 or later; the terminal bridge
requires Windows. ML, PPO and CPCV were actually executed on Linux/Python 3.12.

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[ai,dev,validation,mt5]'
python scripts/test_mt5_connection.py --symbol EURUSD --timeframe H1 --count 100
python -m scripts.train_primary_ml
python -m src.rl.train_signal_ppo --steps 100000
python -m scripts.run_cpcv --workers 2 --steps 100000
```

Before the first command that connects to MT5, configure `MT5_DEMO_LOGIN`,
`MT5_DEMO_PASSWORD`, `MT5_DEMO_SERVER`, and `MT5_DEMO_TERMINAL_PATH` securely on
the Windows host. The bridge pins those explicit selectors, verifies Demo
before/after terminal operations, and excludes forming candles. Its simulated
order method uses real broker `order_check`, reports CHECKED_ONLY and never
claims a fill. No `order_send` is enabled by this new broker.

`MT5Broker` exposes async methods while serializing the process-global terminal
SDK on one worker. The script prints all actual received OHLCV rows and refuses
to report a successful 100-candle fetch when the response is shorter.

## Measured implementation

The seven classifier features are `atr_ratio`, `volume_ratio`, `rsi`,
`macd_hist`, `regime`, `hour_of_day`, and `signal_confidence`. Their exact units
are recorded in `models/meta_labeler_v1.metadata.json`. The 3-state BTC daily
HMM is calibrated before 2019 and filtered forward; only completed daily
context is available to an M15 decision. Barriers use the next actual open,
profit 2×ATR14, stop 1×ATR14 and 20 M15 candles. Double touches lose; timeouts
are omitted from binary fitting but remain eligible for execution.

Train is 2022–2023; validation is 2024. Five-fold PurgedKFold removes actual
event-interval intersections plus 5-bar purge/embargo margins. The classifier
and 90th-percentile admission threshold fit training data only. Every held
training event, including timeouts, receives an OOF prediction. The native
XGBoost artifact is `models/meta_labeler_v1.json`.

`PrimarySignal` emits only classifier-approved EMA crossovers. PPO consumes
71 actual approved OOF training trades; its validation comparison uses the
same 35 approved events for both policies. Observations contain entry-time
account state, ML probability, ATR and regime. Both policies use actual
intratrade closed-price marks and identical costs. The reward is trailing
30-calendar-day daily-NAV Sharpe less the drawdown penalty. Capital is flat
between nonoverlapping event decisions; future outcomes and price paths are
never exposed in an observation. The checkpoint is `models/ppo_agent.zip`.

Each of the 15 CPCV splits refits XGBoost, its inner-OOF threshold and a fresh
100k-step PPO. Purge margins are 75 minutes (5 M15 bars); whole calendar dates
are grouped conservatively. Episodes reset across disconnected training
segments. Positions close within each permitted held group. Five OOS paths
are reconstructed without pooling repeated days as independent observations.
PBO describes selection between fixed 2% and PPO; the reported PBO is the mean
of five path-wise CSCV values. DSR is computed separately per path and its
minimum would be reported if all values were defined. Earlier project-wide
search multiplicity is not fully counted. These dates have been inspected
previously, so this is retrospective evidence rather than a fresh holdout.

## Verification and dependencies

```bash
python -m pytest -q tests/test_mt5_broker.py tests/test_primary_ml.py tests/test_signal_rl.py tests/test_real_cpcv.py
python -m scripts.verify_real_bot_artifacts --phase 2
python -m scripts.verify_real_bot_artifacts --phase 3
python -m scripts.verify_real_bot_artifacts --phase 4
```

The verification script reloads actual model files, reproduces measurements,
checks hashes and OOF admission, and verifies all 15 PPO archives and their
training/test separation. It performs no training or broker connections.
[GitHub CI](https://github.com/parsa314/modular-crypto-trading-bot/actions/workflows/real-bot-engineering.yml)
has separate broker, ML, PPO and CPCV jobs. Contract CI does not establish a
successful Windows terminal connection.

Reviewed library releases: MT5Forge 1.0.3, XGBoost 3.4.1, hmmlearn 0.3.3,
Gymnasium 1.4.0, Stable-Baselines3 2.9.0 and purgedcv 0.1.11. Core dependencies
and platform markers live in `pyproject.toml`. The dedicated ML CI uses the
CPU XGBoost wheel; both variants expose the same native model interface.

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Schulman, J., Wolski, F., Dhariwal, P., Radford, A., & Klimov, O. (2017).
  *Proximal policy optimization algorithms*. https://doi.org/10.48550/arXiv.1707.06347
- Bailey, D. H., & López de Prado, M. (2014). The deflated Sharpe ratio.
  https://doi.org/10.3905/jpm.2014.40.5.094
- Bailey, D. H., Borwein, J. M., López de Prado, M., & Salehipour, A. (2017).
  The probability of backtest overfitting. https://doi.org/10.21314/JCF.2016.322
- Software: https://pypi.org/project/mt5forge/1.0.3/ and
  https://pypi.org/project/purgedcv/0.1.11/
