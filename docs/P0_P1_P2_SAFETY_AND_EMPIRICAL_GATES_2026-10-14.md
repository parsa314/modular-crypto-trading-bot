# Gate ledger: owner+supervisor P0/P1/P2/P3 (2026-10-14)

This is a **safety and evidence ledger**, not a release announcement. Repo default main's `RESEARCH_ONLY` service/firewall remains authoritative. **NO real-money trading or live credentials.** All newly added broker code is DEMO-only.

## P0 — main-line convergence

- New PR #134 was built directly on latest canonical `main` commit `7db5643f24bb713ba1df2abb9a2989f73d56a8c6` using 45 source-vetted MT5 paths. Main's `research_bot/service.py`, scientific decisions, README and governance unit tests were not overwritten.
- PR #113 historical divergent lineage remains preserved; never force-merge 123 commits or replace main's canonical negative results.
- Stage is **draft and not merged** until exact-final-commit tests, safety review and explicit handoff; a conflict-free diff is necessary but not enough.

## P1 — DEMO emergency mode

- Single-host SQLite shared risk state latches after >=10% observed same-day equity drawdown, 3 consecutive broker execution exceptions, or uncertain fill. It blocks new entries across cooperating same-host processes.
- New `mt5_demo_emergency.py` makes one **best-effort** pass over exact-ticket bot-owned positions on explicitly allowlisted symbols; every close intent and broker response is persisted. Dry-run by default. Needs an already-latched kill, independently verified DEMO account, and explicit `allow_demo_closes=True`. Never acts on real accounts or foreign-magic positions; cannot promise successful flattening after disconnect.
- **NOT COMPLETE:** cross-machine coordinator, PostgreSQL authenticated role-backed state, broker-wide pending-order cancel and position enumeration across symbols and terminals, a true independent watchdog, deposit-adjusted certified day-start equity, actual DEMO terminal recovery/prospective validation and human safety review. Market gaps/rejected closes may cause losses beyond target.

## P2 — order/position reconciliation

- `mt5_demo_reconciliation.py` compares complete source journal with freshly queried broker managed positions and pending orders, returning an immutable hashed verdict. Missing broker data, stale timestamps, unresolved local intents, partial/unknown fills, active broker orders or unverified prior broker trades veto new entries. Never auto-reissues uncertain orders.
- The MT5 DEMO runner invokes reconciliation inside its risk admission before a new order can reach the broker, records receipt, and latches stop on any disagreement.
- **NOT COMPLETE:** broker deal-history replay, SL/TP realized-close linking, exact quantity/currency/commission comparison, multi-host single OMS ledger, incident alerts to separate service, crash-resumption reconciliation proof on a real MT5 DEMO terminal. Conservative false-positive halts are intentional.

## P3 — BTCUSDT M15 empirical falsification (NOT STARTED; blocked by P0/P1/P2)

- Required real data: **BTCUSDT Binance spot 15m 2022-01-01..2024-12-31 UTC**, with venue archive ZIP SHA256 and companion official .CHECKSUM for each of 36 months; no invented/counterfeit candles; log gaps, duplicates, close availability, and source hash.
- Reproducible baseline terms proposed by supervisor: fees 0.1% per side + slippage 0.05% **per side (assumption to pre-register)**, one FULL completed M15 candle decision delay then next bar executable price; explicitly stress both stop-first ambiguous candles and next-bar spread/volatility. Compare same time window and capital with Buy&Hold, same trade-count seeded Random, and MA Crossover baseline.
- Must pre-register signal threshold and all cost/calibration choices; require strict train/test chronology and untouched final holdout; never optimize until positive in-sample then claim holdout win. Evaluate net CAGR/Sharpe/MDD/CVaR and confidence intervals. If no cost-adjusted OOS improvement versus baselines, STOP and preserve negative outcome. NO RL before a valid positive and risk-controlled walk-forward result.
- At this checkpoint a local fetch attempt for Binance public archive failed due to **the execution environment's DNS resolution**. It is not proof Binance data is unavailable. No real data or market-performance backtest has been executed. Do not invent any numbers.

## Human signoff

Before merging any execution-capable branch, require independent review of DEMO-only checks, transactional order semantics, recovery on network failure, uncertainty lock, unbounded existing-position risk, and currency/contract multipliers. Even a green CI does not waive this review.
