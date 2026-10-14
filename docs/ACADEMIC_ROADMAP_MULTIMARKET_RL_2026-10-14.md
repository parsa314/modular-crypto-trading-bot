# Academic Multi-market Trading Program — Verified Engineering Plan (2026-10-14)

## Scope and constraints

Canonical scientific repository: `parsa314/modular-crypto-trading-bot`; current default branch `main` is not the aggregate of all historical research branches. Do **not** mega-merge stacked scientific histories or rewrite negative experiments. The proposed end state spans crypto spot/perpetuals, FX and equities, but each market needs **its own calendars, venues, execution rules, financing/margin, data rights, tick/lot precision and broker currency conversion**. Options are NOT requested for the first executable release.

Training markets and execution markets must not be conflated. Crypto cannot have 2000–2008 history. Use 2000 onward only for assets with real licensed/traceable history; BTC data may begin later, and newer altcoins later still. A Kaggle upload does not by itself prove point-in-time availability. No simulated OHLCV is allowed to support market-performance claims. Synthetic fixtures are allowed exclusively for deterministic unit tests and must be labeled so.

## Engineering selection

**Chosen stack B:** Python 3.11; pandas/NumPy/scikit-learn for PIT features and baselines; PyTorch and Gymnasium/Stable-Baselines3 optional RL extras; MetaTrader5 official Python module with a Windows terminal for DEMO; CCXT/venue-specific REST/WebSocket adapters for crypto, never a generic API-compatibility promise. PostgreSQL, optional TimescaleDB for candles/events, Redis streams (not non-durable Pub/Sub) for messaging when persistent recovery is needed; start with durable in-process/SQL event-journal instead of Kubernetes. Deploy data/model processes separately from Windows MT5 terminal. Explicit modes: BACKTEST, OFFLINE_SIM, DEMO/TESTNET, and LIVE with separate credentials, endpoints, roles and release gates.

## Framework-specific hypotheses

- Ichimoku: time-causal Tenkan/Kijun, correctly shifted **current-known** cloud values (do not accidentally access forward Senkou), high-timeframe gating and cloud-width regime features.
- ICT: timed sweep, displacement, market structure shift and non-repainting FVG revisit; UTC session windows with DST-aware London/NY time conversions.
- SMC: point-in-time break of structure, origin/mitigation of order blocks and objective invalidation.
- Brooks: signal bar, follow-through, microchannel, trend pullback, range fade and failed breakout in deterministic candle geometry; distinguish confirmed late pivots from available pivots at signal time.

Evaluate all four independent raw arms, pairwise combinations and ALL_FOUR on **same event cohort** and realistic fees; abstention is a valid action. A proposed confluence score >=7/12 is a **pre-registered hypothesis**, not a production proof or a universally profitable threshold. RSI/MACD confirmations must pass ablations rather than being assumed to improve outcomes.

## ML and RL gates

1. Immutable label/event schema: `event_id, strategy_arm, venue, asset, decision_at, available_at, entry_at, stop, target, label_end_at, data_hash, model_hash`. Exit labels: TP/SL/TIMEOUT with stop-first intrabar resolution when ambiguous.
2. Baselines first: raw strategy, always-flat/no-trade, buy-and-hold where comparable, cost-adjusted logistic, gradient boosting, HGB, optional LightGBM/XGBoost. Train-only transforms and independent validation-only calibration.
3. Time-series challengers (only with support): LSTM/TCN/Transformer; compare not only Brier/LogLoss but **net performance at fixed capital and cost**.
4. PPO as an **experimental** RL policy for portfolio sizing/admission AFTER supervised and risk baselines. Compare A2C, DQN where action space permits, and SAC for continuous allocation. Frozen champion is not automatically replaced by online replay.
5. Reward based on realized portfolio net return minus explicit transaction/impact/turnover/Drawdown/ES penalties, with broker/venue constraints in the environment. Agents cannot modify or bypass the independent risk gate. Do not claim backtest profitability from invented fills.

## Validation design

Primary: strictly chronological expanding walk-forward with label-end purging, training-only imputation, out-of-sample cost scenario stress and a sealed final forward segment. Secondary: CPCV `N=6, k=2` as a distributional robustness diagnostic (15 splits and 5 assembled paths where data support exists); CPCV allows training later blocks to score earlier blocks, so it is **not a chronological deployment replay**. Search-history multiplicity must be recorded before Deflated Sharpe Ratio; probability of backtest overfitting requires an actual trial matrix (never fabricated). Test multiple time regimes, assets, volatility, fees and capacity. Negative/insufficient-support gates are first-class outcomes.

## Economic gates (desired, not achieved)

The owner's **aspirational** criteria: OOS Sharpe >1.5, MDD <20%, Profit Factor >1.3, PBO <0.3, Win Rate >55%, with a confidence interval for incremental net utility and realistic costs. These thresholds **do not replace** risk vetoes, capacity/accounting, idempotency, credential isolation or forward DEMO monitoring. Benchmark selection must be preregistered and market-specific; win rate alone is economically insufficient.

**Financial constitution for first DEMO release:** default 0.25% declared stop risk (owner's requested 2% is the absolute cap, not the default), entry veto at >=10% daily equity drawdown, no automatic capital risk increase, kill-switch independently enforced. A modeled stop is not a guaranteed worst-case loss. No DEMO/TESTNET receipt establishes real-money readiness.

## Staged implementation with measurable deliverables

- P0: separate real/current data from stale/missing observations; repair source/artifact ingestion; actual venue metadata and credential-mode checks; maintain exact SHAs.
- P1: complete four-arm event generators and matched-cohort event/ablation reports; tests for leakage and STOP_FIRST.
- P2: portfolio accounting, Decimal order state, full order lifecycle/reconciliation, multi-worker capital competition and risk vetoes.
- P3: train/validate baselines with real historical source manifests; if sample support is inadequate report INCONCLUSIVE.
- P4: train PPO/SAC challengers, ablations, multi-seed, walk-forward and CPCV with a frozen trial ledger.
- P5: evaluate with MT5 Windows DEMO and official Nobitex TESTNET when operator/broker data/credentials are available; no real account assumed and no fake fills.
- P6: document with architecture, runbooks, references, results manifest, DOI provenance, pytest and CI; release only on evidence-backed acceptance.

## Reproducibility and literature (reviewed metadata)

1. Pippas, N., Ludvig, E. A., & Turkay, C. (2025). *The Evolution of Reinforcement Learning in Quantitative Finance: A Survey*. ACM Computing Surveys 57(11), 295. https://doi.org/10.1145/3733714 . Survey of 167 publications; no universal PPO-superiority theorem.
2. Tay, X. H., & Lim, S. M. (2024). *Deep Reinforcement Learning in Cryptocurrency Trading: A Profitable Approach*. Journal of Telecommunications and the Digital Economy, 12(3), 126–147. https://doi.org/10.18080/jtde.v12n3.985 . Study reports DQN ahead of tested A2C/RPPO with negative ROI for the latter two **within that study** (do not generalize as a market law).
3. Abbade, L. R., & Costa, A. H. R. (2026). *Realistic Market Impact Modeling for Reinforcement Learning Trading Environments*. arXiv preprint https://arxiv.org/abs/2603.29086 . Preprint; relative algorithm ranking changes with execution cost model.
4. Bailey, D. H., & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 .
5. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Relevant CPCV/purging framework; avoid a blanket claim that CPCV replaces chronological validation.
6. Official platform/API references: https://www.mql5.com/en/docs/python_metatrader5 ; https://apidocs.nobitex.ir/ ; https://testnetapi.nobitex.ir (environment URL, not a source of empirical results); https://stable-baselines3.readthedocs.io/en/master/modules/ppo.html .

**Evidence grading:** literature pointers are references, not bot-performance evidence. No new real-data experiment, capital deployment, financial-performance or live orders are claimed by this roadmap.
