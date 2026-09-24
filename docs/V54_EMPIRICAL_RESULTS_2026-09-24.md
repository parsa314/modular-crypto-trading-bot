# v0.54 CoinEx empirical results — 2026-09-24

## Provenance and status

- GitHub Actions run: [36024720614](https://github.com/parsa314/modular-crypto-trading-bot/actions/runs/36024720614)
- Artifact ID: `10818957812` (`v54-evidence-36024720614-1`)
- Artifact ZIP SHA-256: `6973d2d667a47b0e6d7ed91ef7becb18720d96a9d3e1637f10fb2c04a3dd378a`
- Frozen scientific source SHA: `f234a85028ed2d86cd3bc672e7491f72b0245b9b`
- Execution run ID: `20260924T160441Z`
- Fresh audit report SHA-256: `8a2dc306bdfb37e83eb8af6585d2caf906f209cb09b7248a3a6250bfe1282179`
- Replay audit report SHA-256: `355a2ee73f26113d63ac9f96c6e62da1551c267ba09474b2ce9c01b1cedce835`
- Five of five symbols completed; no blocked symbols. Fresh and replay return codes are zero. All frozen dataset frame and schema hashes match on replay; cross-symbol evidence and aggregate economic metrics agree. The descriptive global feature-health diagnostics contain small serialization differences and should not be called byte-identical.
- Machine status: `WORKFLOW_COMPLETE`; empirical coverage status: `V54_EMPIRICAL_COMPLETE`; scientific promotion and execution authorization: **false**.
- The dataset covers approximately 2026-05-22 through 2026-09-24. The frozen audit uses 1h decisions and 4h context, expanding walk-forward training, one-bar purge, 240-row test steps, and 1,680 out-of-sample periods per symbol.

## Main result: ALL model under the frozen cost assumptions

Net total returns are compounded over each symbol's out-of-sample evaluation. Sharpe and drawdown are computed at the preregistered 24 bps round-trip base cost. These are simulated strategy metrics, not realized exchange trades.

| Symbol | Net 0 bps | Net 24 bps | Net 36 bps | Sharpe 24 bps | Max drawdown 24 bps | Period profit factor 24 bps | Turnover sum |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTC/USDT | +1.14% | **−49.87%** | −64.73% | −9.87 | −52.11% | 0.719 | 584 |
| DOGE/USDT | +31.33% | **−47.29%** | −66.63% | −5.10 | −53.80% | 0.836 | 760 |
| ETH/USDT | −24.73% | **−58.54%** | −69.25% | −9.12 | −61.54% | 0.723 | 496 |
| SOL/USDT | −29.28% | **−66.27%** | −76.72% | −10.51 | −66.36% | 0.711 | 616 |
| XRP/USDT | +44.13% | **−30.72%** | −52.00% | −2.45 | −46.58% | 0.913 | 610 |

All five ALL-model results are negative at the 24 bps base cost. Two are positive with zero cost, indicating that trading friction changes the economic conclusion. Each run reports full time exposure for the ALL variant. The turnover totals are position-change units over 1,680 evaluation periods, not counts of executed live trades. Actual exchange spread, fees, fill quality, and liquidity may differ from these fixed assumptions.

## Incremental feature-family audit at 24 bps

`promotion_candidate` is the preregistered **relative feature** rule: positive median `Sharpe(ALL) − Sharpe(DROP_family)` and positive increments in at least three completed symbols. `paired_return_support` additionally tests the sign of paired net-return differences; it does **not** require every symbol's 95% interval to exclude zero.

| Feature family | Median Sharpe increment | Positive symbols / 5 | Paired support | Structural feature candidate |
|---|---:|---:|---|---|
| BASE | +0.474 | 3 | Yes | Yes |
| HTF (4h context) | +3.437 | 5 | Yes | Yes |
| ICHIMOKU | +0.623 | 4 | Yes | Yes |
| BROOKS | −0.774 | 2 | No | No |
| SMC_ICT | −0.629 | 1 | No | No |

HTF has the strongest relative contribution, but its positive paired 95% bootstrap intervals exclude zero for only BTC and ETH. The confidence intervals for the other three symbols cross zero. The Ichimoku confidence intervals cross zero for all five symbols. The relative feature candidates are **not** profitable models and must not be represented as authorization for paper or live trading.

## Scientific decision and next experiment

**Decision:** v0.54 empirical and reproducibility gates are complete. The ALL trading policy **fails the net-return economic test at 24 bps on every symbol**. Preserve the negative result. No paper or live execution is authorized.

**Proposed separate v0.55 hypothesis:** A training-only calibrated, cost-aware abstention/no-trade rule may reduce turnover while preserving whatever predictive information exists in the HTF and Ichimoku families. Pre-register the candidate rule, thresholds and model-selection set **before** inspecting a fresh later holdout. Compare against the frozen v0.54 ALL and simple HTF/Ichimoku baselines at 0/24/36 bps, with turnover, exposure, net return, drawdown and uncertainty; include a no-trade baseline so merely doing nothing is not mistaken for alpha. Do not tune thresholds on the v0.54 OOS periods and relabel them as independent evidence.

This direction is motivated by transaction-cost research on cost-aware execution filters (Bysik & Ślepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting*, 2026, https://arxiv.org/abs/2606.00060) and turnover control (Khubiev et al., *Finance-Grounded Optimization For Algorithmic Trading*, 2025, https://arxiv.org/abs/2509.04541). These studies motivate a new test; they do not establish that this project's next variant will be profitable.

## Reproduction boundary

The GitHub Artifact includes `feature_audit.json`, `feature_audit_replay.json`, `run_status.json`, five frozen `CSV.gz` inputs, five manifests and logs. GitHub Artifact retention is 90 days, so retain the ZIP independently for thesis archiving. The workflow pins the frozen scientific source and writes only research artifacts; it does not update scientific code or enable trading.
