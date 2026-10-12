# AI Cryptocurrency Trading Bot Research — Machine Learning, Deep Learning & Reinforcement Learning

**Evidence-driven MSc research platform for an AI cryptocurrency trading bot using financial machine learning, deep learning, portfolio risk management and reproducible out-of-sample validation.**

> **Scientific contract:** Evidence Before Opinion  
> **Execution status:** `RESEARCH_ONLY / PAPER_OFF / LIVE_OFF`  
> **Latest completed scientific result:** `v0.50 — V50_NONOVERLAP_FAILURE_SUPPORTED`  
> **Active prospective hypothesis:** `v0.51 — overlap-conflict arbitration`  
> **Current v0.51 state:** `BLOCKED / SCIENTIFIC_DECISION_DEFERRED` — canonical prospective evidence has not yet been collected.  
> **Research claim:** no strategy is claimed as guaranteed profitable alpha.

> **Agent / repository routing:** This repository is the **canonical scientific and engineering source of truth**. `parsa314/miladchicomobot` is a lightweight public deployment/monitoring shell, not the main research codebase. Any coding or research agent must read [`AGENTS.md`](AGENTS.md) before changing repository roles, Railway routing, execution flags, dependencies, or scientific logic.

## Canonical current state

The uploaded complete_demo_bot now has a verified offline PAPER migration:
`python -m scripts.run_demo_migration --journal /tmp/demo-migration.sqlite --cycles 80`.
See [actual-source audit and migration](docs/COMPLETE_DEMO_BOT_AUDIT_AND_MIGRATION.md).
HPT remains disabled; existing REAL journal tables are a compatibility view of
the exact namespaced event ledger. No live execution is added.

The owner's 2026-10-11 **Section 2 completion layer** authorizes an independent
offline PAPER simulator under `research_bot/completion/`. Its four-generator
synthetic example uses SQLite and has no exchange-order transport. The original
service and v0.51 execution gates above retain their existing state. Research
PAPER requires a separate recorded graduation decision; synthetic results cannot
graduate. See [audit, usage and migration](docs/COMPLETION_LAYER_AUDIT_AND_MIGRATION.md)
and [architecture decisions](docs/COMPLETION_LAYER_ADRS.md).
Step 3 now uses a shared 10% displayed-depth budget and 100ms simulated submission
spacing. These are uncalibrated stress assumptions; corrected Decimal impact and
spot liquidity preflight are described in
[ADR-COMP-008](docs/ADR-COMP-008_LOW_LIQUIDITY_EXECUTION.md).
Use a new journal file when upgrading the execution policy.

```bash
python -m scripts.run_completion_paper --journal /tmp/completion.sqlite --cycles 80
python -m scripts.run_completion_paper --journal /tmp/completion.sqlite --cycles 90
python -m scripts.verify_completion_coverage --output /tmp/completion-coverage.json
```

Use [`docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md`](docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md) as the current navigation record. It distinguishes:

- the moving default branch and deployment surface;
- immutable scientific/preregistration SHAs and artifacts;
- stacked research branches/PRs;
- the public deployment shell;
- infrastructure failures versus scientific failures.

The later scientific lineage is intentionally not flattened into `main` with a destructive mega-merge because preregistration, empirical execution and evidence provenance must remain auditable.

## What this project is

This repository is a research program for building and falsifying an **AI/ML crypto trading system** under realistic market constraints. It covers strategy discovery, causal feature engineering, meta-labeling, temporal/deep challengers, portfolio risk, failure attribution, prospective validation and defense-grade reproducibility.

The research question is deliberately stricter than “does the backtest look good?”:

> **Can an AI cryptocurrency trading hypothesis survive leakage controls, realistic costs, model-selection bias, regime change, portfolio overlap, CVaR/drawdown limits and genuinely fresh evidence?**

Negative, rejected and blocked experiments are preserved because they are part of the thesis evidence trail.

## Research architecture

`Literature & Hypothesis → Causal/PIT Data → Strategy Events → ML/Calibration → Purged Validation → Failure Attribution → Search-Aware Statistics → External/Future Evidence → Portfolio Arbitration & MTM/CVaR Risk → PAPER Review → Thesis Defense`

Core principles:

- point-in-time / closed-bar feature construction;
- next-open execution semantics where applicable;
- realistic fees, slippage and cost stress;
- development / validation / test separation with purge + embargo;
- simple baselines before complex ML;
- multi-seed or perturbation evaluation where stochasticity is material;
- ablation and incremental-value tests;
- moving-block bootstrap and multiple-testing controls;
- CPCV / PBO / Deflated-Sharpe-style diagnostics where applicable;
- mark-to-market portfolio risk, correlation and CVaR constraints;
- immutable manifests, artifact digests and dependency provenance;
- no same-test rescue tuning;
- PAPER/LIVE execution remains fail-closed until a separate promotion path is satisfied.

## v0.50 — latest completed scientific result

Canonical decision: **`V50_NONOVERLAP_FAILURE_SUPPORTED`**.

Canonical provenance:

- workflow run `34707823108`;
- scientific head `1dd0b1fe506fc51ceec4ff8934b77090f86b6cc2`;
- preregistration head `9b3f8d1bda650cc93af7b6cc69f162ae30d64cac`;
- artifact `10302830689`;
- digest `sha256:da5813a8c031f9da6cc940fda942efc846ca536dbd952f4930584c30732373e9`.

The frozen v0.50 diagnostic found that the **earliest-first non-overlap transformation** was the stage satisfying the preregistered broad-harm attribution rule. This is a mechanistic diagnosis, not proof that any replacement arbitration rule is profitable.

## v0.51 — active prospective question

v0.51 compares the frozen earliest-first rule against `SIMULTANEOUS_MAX_EXPECTED_R_NO_PREEMPTION` using genuinely prospective data while keeping predictor, threshold, costs, exits, Financial Governor, venues, assets and risk limits fixed.

Current governance:

- original preregistration `d8ee4576aaf55750dd5910cc0d3b2efcbba3f5b2`;
- valid predictor-identity amendment `4838c0d98c408d358929b0767676a5ac024bd8dd`;
- evidence-integrity addendum `da5dcf5cd3568a92c75871016f000917c9380955`;
- repaired prospective start `2026-09-13T12:00:00Z`;
- public closed 4h OHLCV from CoinEx, OKX and KuCoin;
- BTC, ETH, SOL, XRP and DOGE;
- Kraken sealed;
- PAPER/LIVE disabled.

The current v0.51 PR records an infrastructure block: GitHub-hosted jobs failed before workflow steps with `runner_id=0`, and a separate Railway collector could not be provisioned under the current resource limit. Therefore **no v0.51 scientific result exists yet**. Missed bars cannot later be relabeled as prospective because the first-seen evidence rule is strict.

## Historical integrated snapshot

[`docs/PROJECT_STATUS_2026-09-11.md`](docs/PROJECT_STATUS_2026-09-11.md) is retained as a dated v0.25-era evidence snapshot. It is historical evidence, not the current authorization/status record.

## Model and strategy families investigated

The program has evaluated or instrumented logistic/Ridge baselines, random forests and gradient boosting, temporal/deep challengers, calibrated competing-risk models, Ichimoku, ICT/SMC/Brooks-inspired causal event families, order-flow/microstructure hypotheses and portfolio-ranking/arbitration layers. Reinforcement learning remains gated until simpler signal/allocation hypotheses survive stronger fresh-evidence tests.

Source-derived trading concepts are treated as **hypothesis generators**, never as proof of alpha.

## Research / defense navigation

Jamalian course integration is an independent **HYPOTHESIS / ENGINEERING-ONLY**
research path. See the [source audit and integration report](docs/JAMALIAN_INTEGRATION_REPORT.md)
and [revised integration specification](docs/JAMALIAN_INTEGRATION_PROMPT.md).
The active interface is now the [V3 phase-one correction](docs/JAMALIAN_V3_REVIEW.md):
immutable hypotheses from MA Cross / RSI / RSI+MA, an abstention adapter, a separate
operational event ledger and offline capital feasibility arbitration. Advanced
prototypes are excluded from this interface pending a separate baseline protocol.
The three phase-one generators emit `HypothesisCandidate`; decision evidence is
created by the abstention adapter. Earlier engineering prototypes and the offline
event simulator remain available for regression coverage. The frozen v0.51
experiment is unchanged; no economic validation or PAPER/LIVE authorization is implied.

- [`AGENTS.md`](AGENTS.md) — repository and execution-governance contract
- [`docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md`](docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md) — canonical current scientific state
- [`docs/PROJECT_STATUS_2026-09-11.md`](docs/PROJECT_STATUS_2026-09-11.md) — historical integrated snapshot
- [`docs/RESEARCH_TRACEABILITY_MATRIX.md`](docs/RESEARCH_TRACEABILITY_MATRIX.md) — research-to-code/status matrix
- [`docs/DEFENSE_EVIDENCE_INDEX.md`](docs/DEFENSE_EVIDENCE_INDEX.md) — examiner question → evidence map
- [`docs/RESEARCH_CHANGELOG.md`](docs/RESEARCH_CHANGELOG.md) — stage-by-stage research updates
- [`docs/AI_CRYPTO_TRADING_BOT_RESEARCH.md`](docs/AI_CRYPTO_TRADING_BOT_RESEARCH.md) — project overview

## Install

Core development environment:

```bash
python -m pip install -e '.[dev]'
pytest -q
```

Optional research stacks are explicit and are not installed into the lightweight deployment runtime unless needed:

```bash
python -m pip install -e '.[ml]'
python -m pip install -e '.[deep]'
python -m pip install -e '.[rl]'
```

## Narrow Binance data gate

The [BTC_USDT 1h preparation script and gate](docs/BINANCE_1H_DATA_GATE.md) validates exact closed-bar boundaries, complete pagination and a SHA256 sidecar before research. Historical availability is explicitly modeled, not prospective evidence. Run `python -m scripts.prepare_binance_1h --years 3`, then pass its CSV to `scripts.run_four_systems_research`. Orders remain disabled; DSR/PBO remain NOT_RUN.

## Reproducibility rule

The independent [four-system causal/calibration challenger](docs/FOUR_SYSTEMS_LITERATURE_AND_UPGRADE.md)
adds closed 1h/4h Ichimoku, ICT/SMC and Brooks-inspired features, purged chronological
train/calibration/test ablations, optional training-only PCA and Decimal ES size
ceilings. It emits hypotheses into the existing research abstention pipeline and
SQLite journal; this new experiment remains **RESEARCH_ONLY / PAPER_OFF / LIVE_OFF**.
The [ADR](docs/ADR-COMP-010_CAUSAL_FOUR_SYSTEMS.md) and
[source audit catalog](docs/FOUR_SYSTEMS_SOURCE_CATALOG.csv) distinguish checked
methodology from unverified performance claims. Run its offline synthetic example:

```bash
python -m scripts.run_four_systems_research --journal /tmp/four-system.sqlite --output /tmp/four-system-report.json
```

Every promotable experiment preserves source SHA, frozen hypothesis/config, data provenance, dependency environment, dataset/artifact fingerprints, validation-only selection logic, fresh evaluation evidence, cost/risk assumptions, machine-readable decision output and explicit PAPER/LIVE authorization flags.

A successful workflow means the experiment executed according to contract. It does **not** automatically mean the strategy is economically valid.

## Thesis / defense labels

- **TESTED** — executed under a frozen protocol
- **REJECTED** — tested and failed a scientific gate
- **BLOCKED** — infrastructure/data/reproducibility prevented a valid read
- **CHALLENGER / HYPOTHESIS** — promising but not externally/forward validated
- **PREREGISTERED / PROSPECTIVE** — design frozen before admissible future outcomes
- **DATA_UNAVAILABLE** — evidence is not fabricated or silently proxied

## Safety

Predictive models do not directly authorize exchange execution. Portfolio/risk controls are independent. The canonical current deployment is research/monitoring only, with PAPER and real-money LIVE execution disabled. Nothing in this repository is financial advice or a guarantee of returns.

## Repository governance

- [Agent operating contract](AGENTS.md)
- [Contribution guidelines](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Citation metadata](CITATION.cff)
- [Research PR template](.github/PULL_REQUEST_TEMPLATE.md)

---

Maintained by **Milad Parsanezhad** as an academic financial-ML, AI cryptocurrency trading-bot and quantitative-research project.
