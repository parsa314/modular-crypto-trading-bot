# Research-to-Code Traceability Matrix

Date: 2026-09-11

This file connects the thesis research program to executable repository modules, immutable evidence and the next admissible scientific gate. Engineering completion, local validation and scientific promotion are reported separately.

## Current research-to-code map

| Research phase | Current code/artifact | Status | Acceptance evidence still required |
|---|---|---|---|
| Audit / provenance | prior audits + v0.8-v0.24 regression suites + experiment manifests | ACTIVE / VERIFIED INFRASTRUCTURE | preserve original artifacts, dependency environment, source SHA and data fingerprints |
| Dynamic Universe | `market_discovery.py`, `universe.py` | IMPLEMENTED / VALIDATING | historical membership/entity resolution |
| Coverage | `CoverageReport` | IMPLEMENTED PARTIAL | trustworthy market-cap denominator |
| Fast Scanner | `fast_scanner.py` | IMPLEMENTED / VALIDATING | larger-universe throughput artifact |
| Cross-sectional Ranking | `cross_sectional_v06.py`, v0.10/v0.11, corrected v0.19 audit | ACTIVE | broader venue/capacity replication |
| Ichimoku / pattern | `ichimoku_advanced.py`, v0.17 strategy lab, v0.19-v0.20 tournaments | ACTIVE / CHALLENGERS ONLY | fresh evidence after frozen selection; no direct alpha claim |
| Source-derived MTF strategies | v0.19 30-candidate + v0.20 42-candidate tournament | TESTED / NO PROMOTION | fresh holdout/forward evidence for any newly frozen hypothesis |
| Derivatives | v0.12 external holdout | EXTERNAL HOLDOUT TESTED — NO INCREMENTAL EDGE | higher-quality independent flow source |
| Microstructure | `microstructure_v13.py`, `forward_microstructure_v19.py`, Phase-Q evidence | MEASUREMENT INFRASTRUCTURE VERIFIED / FORWARD SAMPLE MATURITY GATED | prospective maturity gate and later predictive ablation |
| Multi-venue trade-flow PIT contract | fixed 60s window, unique-venue coverage, staleness/clock-skew gates | IMPLEMENTED / SMOKE VERIFIED | mature prospective quality artifact |
| Cost-aware abstention | `decision.py`, v0.18/v0.19 | ECONOMIC RISK REDUCTION OBSERVED / STATISTICAL GATE FAILED | new untouched replication with complete trial registry |
| On-chain | `point_in_time.py` contract | CONTRACT READY | real provider/history |
| Whale | specification | NOT IMPLEMENTED | wallet/entity evidence |
| Fundamental | point-in-time contract | NOT IMPLEMENTED | provider/lag audit |
| Tokenomics | specification | NOT IMPLEMENTED | unlock history |
| Sentiment/News | point-in-time contract | NOT IMPLEMENTED | source pipeline |
| ML baselines | v0.10-v0.12 + v0.23r | OOS TESTED / FRAMEWORK HARDENED | incremental economic evidence on a fresh hypothesis/window |
| Broad tabular meta-labeling | v0.21/v0.23r/v0.24 | TESTED — `NO_META_MODEL_PROMOTED` | fresh externally/forward validated uplift with search-aware controls |
| Family-specific meta-labeling | `strategy_family_meta_v24b.py`, workflow `34578494059` | SHADOW TESTED — `NO_FAMILY_META_PROMOTION` | exact frozen artifact replay + fresh external/future evidence |
| Frozen-model artifact identity | `frozen_snapshot_v24d.py` + v0.24b artifact `10190676664` | V0.24C BLOCKER IDENTIFIED / V0.24D PRE-REGISTERED | exact binary replay under sklearn 1.9.1 environment, then external triangulation |
| DL temporal challengers | `deep_temporal_v22.py`, `temporal_meta_v24c.py`, run `34582340510` | VALIDATION CHALLENGERS / NOT PROMOTED | fresh external/future-time test; both frozen champions currently exceed 5% validation DD ceiling |
| Vision / multimodal | v0.22 research track | RESEARCH CHALLENGER / GATED | independent incremental value before ensemble eligibility |
| Regime | `regime.py`, v0.11/v0.12/v0.18/v0.19 + future unsupervised context | RETROSPECTIVE / CONTEXT ONLY | fresh pre-registered replication; unsupervised regime discovery cannot claim alpha by itself |
| RL | safe contract + v0.24c evidence ladder | GATED BEHIND PORTFOLIO ALLOCATOR | valid state/action/reward contract + portfolio allocator PASS + multi-seed fresh OOS evidence |
| Risk | `risk.py`, `portfolio_mtm_v24c.py` | MTM / CORRELATION / CVaR ENGINE IMPLEMENTED | fresh candidate must pass 5% MTM DD, CVaR and exposure gates |
| Portfolio | v0.24b realized-overlap + v0.24c MTM engine | STRONGER RESEARCH ENGINE / NO FILTER PROMOTION | fresh dual-venue/future validation and capacity study |
| Backtest | `backtest.py`, v0.11-v0.24 | STRONG RESEARCH INFRASTRUCTURE | complete search registry + CPCV/PBO/DSR for next promotable chain |
| Search-aware statistical audit | `evidence_ledger_v17.py`, `trial_registry_v17.py`, v0.24 gates | PROTOCOL IMPLEMENTED / PARTIALLY EXECUTED | CPCV path distribution + PBO/DSR on final frozen chain |
| Paper Trading | `execution.py`, `forward_paper_v14.py`, `persistence.py`, API | ENGINEERING COMPLETE / FORWARD COLLECTION ACTIVE | candidate-specific fresh promotion gate before any replacement |
| Forward evidence | v0.15/v0.16 + Phase-Q | ACTIVE / SAMPLE DEPENDENT | satisfy pre-registered elapsed-time and independent-observation gates |
| External fresh-evidence ladder | `evidence_ladder_v24c.py` | IMPLEMENTED / PLAN A BLOCKED BY EXACT REPRODUCTION | v0.24d exact snapshot replay; if blocked/insufficient route to future-time Plan B |
| Formal forward review | v0.16 evaluation + defense artifact | IMPLEMENTED / SAMPLE INSUFFICIENT | pre-registered gate + sufficient independent returns |
| Thesis / defense synthesis | v0.17 synthesis + Chapter 4 drafts + `PROJECT_STATUS_2026-09-11.md` | IMPLEMENTED / CONTINUOUSLY UPDATED | final evidence freeze, figures/tables and defense deck after next fresh-evidence cycle |
| Dashboard | `/dashboard`, `/paper/*` | IMPLEMENTED | refinement only |
| Testnet | execution-mode contract | READINESS GATED | evidence-backed promoted strategy + provider/testnet configuration |
| Live readiness | LIVE fails closed | BLOCKED BY EVIDENCE CONTRACT | search-aware survivor + fresh external/future replication + forward PAPER + testnet + explicit approval |

## Evidence milestones

- **v0.10:** purged live-universe OOS tournament; no learned model promoted.
- **v0.11:** multi-seed robustness/bootstrap/FDR; no learned model promoted; Ichimoku regime dependence became a hypothesis.
- **v0.12:** external derivatives holdout; decision `NO_INCREMENTAL_DERIVATIVES_EVIDENCE`.
- **v0.13:** post-v0.12 BTC/ETH true-basis/finer-flow forward collection begins.
- **v0.14/v1.0-rc1:** completed-4h PAPER observer, independent risk, PostgreSQL persistence and dashboard; LIVE disabled.
- **v0.15:** immutable scheduled prospective evidence with pre-registered elapsed-time/observation/fill minimums.
- **v0.16:** formal forward aggregation, snapshot-independence filtering and defense artifact generation; sample remained insufficient.
- **v0.17:** thesis evidence synthesis plus search-aware statistical audit protocol. Separate Ichimoku strategy lab preserved S6 as research/forward observation only and rejected IRGC-S.
- **v0.18:** cost-aware/regime experiments reduced some economic risk relative to baselines but confidence intervals crossed zero; no promotion.
- **v0.19 source-derived tournament:** 30 candidates across 1m/5m/15m/1h/4h/1d; lower timeframes were heavily damaged by modeled 24 bps round-trip friction; no strategy promoted.
- **v0.19 corrected audit / microstructure:** PIT window, exact quote-notional depth, unique-venue counting, configured-universe materialization, staleness and clock-skew gates verified. Measurement-health evidence only.
- **v0.20 42-candidate tournament:** three 4h candidates cleared internal validation gates. Frozen internal winner `H4_S6_BREAKOUT` replicated on OKX with 592 trades, PF `1.8615`, expectancy `+0.4555R`, but MDD `-5.5751%` breached the frozen 5% ceiling. Decision: no promotion.
- **v0.21/v0.23r:** event-level labeled ML/meta-learning rebuilt with strict feature deny-list, purged chronological panels, validation-only model/threshold selection and fail-closed runtime gate.
- **v0.24 pooled strategy-aware meta-labeling:** 4,979 events / 953 test events; frozen Ridge filter improved some local mean-R/PF/win-rate metrics but reduced total return, failed breadth, paired-bootstrap, PBO/DSR and 36 bps stress. Decision `NO_META_MODEL_PROMOTED`.
- **v0.24b family meta + overlap portfolio:** workflow `34578494059`, artifact `10190676664`, digest `sha256:0384391db85922309e7b67e0e0481f2cbf8795cf069ee48f146e36ba857525db`. Base shadow portfolio `+10.84%`, PF `1.3875`, DD `-3.70%`; family-filtered portfolio `-2.33%`, PF `0.7957`, DD `-5.44%`, hard kill triggered. Decision `NO_FAMILY_META_PROMOTION`.
- **v0.24c external gate:** primary Bybit transport returned HTTP 403; changed-mechanism OKX fallback was used. Both stopped before external economic scoring because exact reconstruction of `H4_D1_OB_BOS_RISK` selected 46 archived validation events instead of the frozen 47. Status `BLOCKED`, not strategy failure.
- **v0.24c temporal tournament:** workflow `34582340510`, artifact `10192231684`, digest `sha256:f5eef115839c5c1cec3419ad4e35fe47ffd4674d099997abdd1278ae022b8cb4`. Frozen validation challengers: TCN for S6 and LSTM for D1-OB. Neither promoted; validation MDDs exceeded the 5% ceiling and no previous terminal test was scored.
- **v0.24d pre-registration:** exact byte-verified v0.24b model/dataset artifact, locked persistence environment, exact archived replay, then dual-venue OKX+KuCoin cross-sectional triangulation with MTM/CVaR/correlation risk and 24/36/60 bps stress. Even a dual-venue PASS remains pre-future-time evidence.

## Evidence labels

`NOT_TESTED`, `DATA_UNAVAILABLE`, `UNVERIFIED`, `HYPOTHESIS`, `CHALLENGER`, `TESTED`, `REJECTED`, `BLOCKED`, `DISCOVERY_CANDIDATE`, `VALIDATED_OOS`, `EXTERNAL_HOLDOUT_NEGATIVE_RESULT`, `FORWARD_PAPER_HYPOTHESIS`, `SEARCH_AWARE_SURVIVOR`, `FORWARD_REPLICATED`, `MEASUREMENT_INFRASTRUCTURE_VERIFIED_FORWARD_SAMPLE_IMMATURE`.

No module can transition to `VALIDATED_OOS` from a unit test, synthetic smoke run, a single backtest, validation-only uplift, or PAPER execution alone. No candidate can transition to `SEARCH_AWARE_SURVIVOR` without a complete trial registry and appropriate correction for model/strategy search. Negative, blocked and inconclusive results are retained as first-class scientific evidence.

## 2026-10-05 Production Convergence Phase 1 addendum

The historical rows above remain evidence snapshots. New canonical engineering
path: V59 native research → hash-bound promotion/portfolio envelope → generic
Fake-only OMS → atomic local journal → confirmed protective Fake lifecycle.

| Component | Implementation | Evidence status | Remaining gate |
|---|---|---|---|
| Risk constitution | execution/constitution.py; MASTER and RL config bindings | ENGINEERING_TESTED, fractions and no relaxation | Scientific model/operational promotion still absent |
| Event-time validation | v59/tournament.py; native_dataset.py; native identity V2 | ENGINEERING_TESTED, real 2024 contract audit only | Fresh preregistered economic evaluation; never reuse old results as new protocol |
| Research-production bridge | production_adapter.py + immutable envelope/manifest/intent | ENGINEERING_FIXTURE_ONLY | Empty real promotion registry; no live authority |
| Generic OMS | execution/oms.py + ledger.py | FAKE_REPLAY_TESTED, model agnostic | Authenticated adapter + infrastructure validation |
| Protective lifecycle | execution/protection.py + venue-labelled Fake seams | FAKE_ACK_RESTART_FAULT_TESTED | Native venue protection NOT_VERIFIED |
| Phase 2A PostgreSQL persistence | execution/postgres_ledger.py + postgres_schema.sql; tests/test_convergence_postgres.py | ENGINEERING_REPLAY_ONLY, actual DB atomicity/recovery | Production deployment/native exchange protection not verified |
| Offline SQLite → PostgreSQL migration | execution/postgres_migration.py + explicit CLI | Identity-pinned, transactional, source read-only, no-overwrite | Operator must stop source writers; no operational backend switch authorized |
| Production infrastructure | PostgreSQL/WS/watchdog/correlation portfolio | NOT_IMPLEMENTED_IN_PHASE1 | Phase 2 after Phase-1 review |

See PRODUCTION_CONVERGENCE_PHASE1.md and PHASE1_TEST_EVIDENCE.md for exact scope,
source SHAs, evidence hashes and reproduction. LIVE=false and PAPER=false.
