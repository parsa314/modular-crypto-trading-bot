# Repository audit and ten-strategy upgrade

Date 2026-10-12. Inspected branch baseline ef46b513af079dd38d6a91a331d64c1353030680; main fa31e6f5ceb09f4f14ae13fef50706f2e10e1835. Draft PR 115.
Coverage of audit: all 354 tracked paths inventoried; 104 locally present research Python modules (17,224 lines) read/AST-parsed for class/function structure; additional published Binance ingestion and R1 documents checked remotely. Detailed behavioral inspection focused on contracts, orchestrator, DecisionEngine, risk, hypothesis adapter, SQLite journal, existing replay/metrics/graduation, trial registry, robustness inference, archive ingestion, strategy lab, service flags and CI. This is repository-wide inventory and targeted source audit, not a claim that every historical notebook/document line received manual review.

## Architecture and findings

| Area | Existing seam / state | Upgrade direction |
|---|---|---|
|Canonical decision|HypothesisCandidate -> process_candidate -> zero-confidence NO_TRADE; hypothesis is not a forecast|Preserve; use no placeholder expected_return|
|Risk|RiskEngine and independent completion limits; static data are not exit liquidity|Cap replay allocation at .10 and ES at .035; no new Kelly risk inflation|
|Research vs execution|service flags false; separately guarded Nobitex TESTNET/OKX demo transports exist|New package imports no venue/write transport; do not claim the entire repo has no order code|
|Journal|completion_events/checkpoints; digest + unique(experiment,event)|Reuse immutable canonical events for hypotheses and clearly hypothetical replay records|
|Validation|robustness_v11 block bootstrap; jamalian ValidationHarness has explicit legacy IID caveats|New paper-referenced return diagnostics; unknown search/dependence remains blocked|
|Four-system legacy|38 causal features, logistic/isotonic ablations; no net replay matrix|Retain v1; add independently namespaced R1-derived ten-rule detector/replay|
|Strategy lab|Existing v17 rule/meta-label research and prior results|Include in prior-search inventory; not an independent untouched historical universe|
|v0.50/v0.51|Negative v0.50 result; v0.51 frozen/prospective rules; Kraken sealed|No edits, mega-merge, holdout reopening or relabelled backfill|
|Data|Current CSV was not supplied/tracked; public API prior run returned HTTP451|Try official public checksummed spot monthly archives, no credentials or synthetic replacement|
|CI|Canonical Python3.11 and regression/coverage/firewall checks|Add ten-rule tests/coverage plus independently labelled real-data workflow|
|Models/deployment|Deep/RL extras and lightweight public shell are separate|Baseline evidence before heavy models; no Railway/source migration|

## Literature checked and permitted inference

[Bailey et al. PBO author PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), section 2.2: CSCV selects IS winner and evaluates relative OOS rank, rank/(M+1). It is a selection diagnostic on performance, not a classifier accuracy metric or economic readiness certificate. [Bailey/LdP DSR author PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), Eq.(2)/Appendix A.3: selection adjustment needs trial count/dispersion and return moments; prior omitted trials and serial dependence cannot be fixed by relabelling Sharpe.
[Official Binance archive documentation](https://github.com/binance/binance-public-data): public spot archives have checksums and switch timestamp units to microseconds from January2025. New ingestion verifies mandatory checksum, bounded ZIP structure, exact closed UTC hour and entire monthly coverage; no silent sorting/dedup.
Earlier primary-paper scope/abstract/full-text limits remain in docs/r1/FOUR_SYSTEMS_R1_REVIEW.md. No broad formal finding that every SMC/ICT rule is unprofitable is inferred. Operational detectors do not demonstrate institutional activity. Ten strategies are hypotheses; finding zero, some or all failures is a valid result.

## Main deliverables and execution

See ADR-TEN-01_RULES_AND_COSTED_REPLAY.md and ADVANCED_RESEARCH_UPGRADE_PROMPT.md.
From the repository root on this PR:
```bash
python -m scripts.run_ten_strategies_research --synthetic --journal /tmp/ten/synthetic.sqlite --output /tmp/ten/synthetic.json
python -m scripts.prepare_binance_archive_1h --out data/historical/BTC_USDT_1h_archive_closed.csv
python -m scripts.run_ten_strategies_research --csv data/historical/BTC_USDT_1h_archive_closed.csv --journal /tmp/ten/historical.sqlite --output /tmp/ten/historical.json
python -m scripts.verify_ten_strategies_coverage --output /tmp/ten/coverage.json
```
Actual data blockage is recorded separately. No local workspace edits are assumed: current environment is read-only, so publication uses the explicitly authorized GitHub connector and CI verifies disk artifacts. No new credentials, network permission bypass or execution authorization.
