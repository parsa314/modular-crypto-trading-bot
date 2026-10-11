# Canonical signal contract — engineering follow-up

Repository: `parsa314/modular-crypto-trading-bot`. Base main SHA freshly rechecked: `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`. This work is an additive local continuation of the previous reviewed source package; no remote branch, commit, PR or CI is claimed. The previous package is preserved.

## Corrections to the supplied architecture report

- `evidence_ledger_v17.py` is a static milestone/claim registry, not an appendable per-hypothesis runtime ledger. Course decisions use existing TrialRecord/append_trial with immutable decision/event artifacts. Rewriting milestone history for each hypothesis would damage provenance.
- `venue_adapter_v19.py` is an order-book-depth normalization helper, not a generic venue-port framework. This integration exports the already tested ResearchDataPort and verified Bitpin implementation under `research_bot/exchanges/`, avoiding a competing protocol/client.
- Main contains neither the proposed named cognitive classes nor every capability those names suggest. Presence of old risk/validation modules does not establish full meta-cognition, portfolio optimization or validation completeness.
- The frozen v51 experiment is closed H4 OHLCV on CoinEx/OKX/KuCoin for five named assets. Grid/depth/Bitpin/three-leg arbitrage cannot be inserted into that evidence series by registration alone. v51 is unchanged. Adding a different strategy universe/data contract requires a separately registered experiment; any compatible arbitration amendment must be approved/frozen before evidence collection.
- “Live” in an inventory means code exists, not permission to trade. Authenticated Bitpin reads remain unsupported here; credentials and guessed private endpoint formats are not introduced.

## Implemented interfaces

| Requested item | File / actual component | Status |
|---|---|---|
| Bitpin canonical adapter path | `research_bot/exchanges/bitpin_adapter.py` reuses `course.bitpin.BitpinReadOnlyAdapter` | Public-read implementation reused, typed gateway added |
| Shared exchange ports | `research_bot/exchanges/contracts.py` | Existing types re-exported, no competing schema |
| Research execution sink | `BitpinResearchGateway.execution` | NoOrderTransport; cannot inject execution in constructor |
| Explicit simulation | `research_bot/exchanges/simulated.py` | Existing OfflineOrderSimulator re-exported; isolated from sink |
| MovingAverageCrossoverHypothesis | `research_bot/course/hypotheses.py` | Existing 50/150 source rule; list[SignalEvidence] |
| RSI_MA_ConfluenceHypothesis | Same | Existing RSI/MA rule; candle proxy labelled ENGINEERING_EXTENSION |
| GridInventoryStateMachine | Same | Read-only view of existing prepared GridStrategyStateMachine; list[SignalEvidence] |
| Registry | `course_hypothesis_registry` | Disabled by default; grid requires explicitly supplied prepared machine |
| Orchestrator entry | `ResearchTradingOrchestrator.process_research_signals` | Reuses candidate integration, existing Decision/Risk/TrialRecord and NoOrder sink |
| Versioned parameter manifest | `course_experiment_manifest` | Existing ExperimentManifest, actual parameters/config hashes/reference docs, explicit fees/slippage |

No indicator, wallet, Grid lifecycle, RiskEngine, HTTP client or ledger is duplicated. Public generator output is the existing SignalEvidence. The candidate envelope inside metadata is an internal lossless representation needed for capital/event provenance, not an independent decision contract. Metadata also explicitly exposes event/strategy/config identity, capital requirements, invalidation conditions, earliest execution time and HYPOTHESIS status. Output metadata is recursively immutable. The signal and its internal envelope are checked for equality before passing existing gates, so altering probability, forecast or decision alone is rejected.

Generation does not submit orders, reserve balances, change Grid state or fit models. An enabled Grid view only reads a READY plan with the exact prepared snapshot and decision time; a changed snapshot or active plan is rejected. Enabling a generator is explicit research participation, not admission, paper or live authorization. Unknown expectancy/confidence remain zero/adverse admission rather than invented values; all three fixture outputs abstain.

ExperimentManifest has no RESEARCH_ONLY enum member. BACKTEST is used to describe offline research; notes explicitly deny paper/live authorization. The caller supplies actual run fee/slippage assumptions; the helper does not infer execution costs or declare them optimal. Notes distinguish consumed parameter records from packaged reference configuration documents. Custom configuration provenance remains the experiment owner's responsibility.

## Usage through the existing orchestrator

```python
from research_bot.course.hypotheses import MovingAverageCrossoverHypothesis
from research_bot.orchestrator import ResearchTradingOrchestrator

# closed_frame must satisfy the existing candle PIT/quality contract.
generator = MovingAverageCrossoverHypothesis(enabled=True)  # explicit research only
signals = generator.generate(closed_frame, symbol="BTC/USDT", venue="FIXTURE",
                             decision_time=decision_time)
records = ResearchTradingOrchestrator().process_research_signals(
    signals, decision_time=decision_time, risk_snapshot=risk_snapshot,
    run_id=run_id, git_commit=verified_sha, dataset_hash=dataset_hash,
    artifact_dir=shared_immutable_artifact_directory)
# execution_authorization remains DENIED_RESEARCH_ONLY; no transport is activated.
```

The frozen v17 strategy_lab evaluation is not automatically changed or retrained. Registry integration occurs at the existing orchestrator seam, preserving historical strategy lab experiments and thresholds. Broader feature/strategy registration needs its own new experiment configuration.

## Validation, failure conditions and rollback

`tests/test_course_signal_interfaces.py` checks existing contract types; point-in-time rejection; disabled defaults; zero generation effects on candle/Grid state; snapshot substitution; NoOrder binding despite environment token; signal/envelope mismatch; all three generators through the existing orchestrator/registry; and canonical manifests. Fresh full-suite/lint/type/build evidence is in the delivery evidence directory. Tests are engineering evidence, not alpha evidence.

Critical acceptance remains INTEGRATION_BLOCKED for decision-quality Bitpin book timestamp/sequence/metadata, private API and real-market/prospective/CPCV/economic validation. Unchanged earlier limitations remain in the A–J report. No automatic promotion, v51 amendment or risk-limit change is made.

Rollback this follow-up by restoring orchestrator.py to the previous package version and removing exchanges/, course/hypotheses.py and the new test/document. Full rollback to main uses the previous migration report. No scientific record or external account state needs reversal. Patch manifests identify the exact base for both follow-up-only and cumulative patches.

## Fresh verification result

Final integrated suite: **407 passed, 1 skipped, 3 warnings in 66.57 seconds**. The PostgreSQL test remains skipped because TEST_DATABASE_URL is not configured. Focused canonical-interface/integration suite: **35 passed**. Ruff and scoped Mypy (six follow-up files) pass. A freshly built wheel loads from outside the source tree with disabled generators and a NoOrder gateway; its relevant source bytes match the working files. `dataclasses.asdict` serializes the immutable SignalEvidence metadata into a detached mutable artifact without changing the original.

Controlled smoke: **3 SignalEvidence outputs → existing orchestrator → 3 NO_TRADE artifacts and TrialRecords**; existing Grid state/wallet unchanged. The existing ExperimentManifest and all three public signals were serialized successfully. Evidence is under `evidence/followup/` in the ZIP. Previous A–J report and root evidence describe the preceding delivery, not this fresh test run. No actual API/account, prospective experiment or economic result was exercised.
