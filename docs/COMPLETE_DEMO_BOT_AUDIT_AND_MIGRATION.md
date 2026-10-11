# Supplied complete_demo_bot: verified audit and PAPER migration

Audit date: 2026-10-11. Uploaded archive SHA256:
`79a1adf35e3bb3c598dca3261ef77753c0698e351c8ec83bad4e93fbc7f921b0`.
This source supersedes the earlier report's **missing-demo** limitation. Source
inspection is not certification of economic performance or a trained model.

## Findings from actual files

| File / location | Reproduced finding | Migration response |
|---|---|---|
| `liquidity/impact_estimator.py:105–134` | Inverse size formula applies `V/P` twice. With P=100, V=10000, sigma=.02, k=1, requested=2, hard cap=25bps and target=20bps, it recommends 2; the correct target size is 1. NaN quantity returns OK. | Use validated Decimal estimator; do not silently execute REDUCE. |
| `execution/simulator.py:54–83` | ACK immediately followed by full market fill; no funded-inventory check. An unfunded SELL fills. | Existing completion simulator enforces reservations, balances, new-book latency and partial fills. |
| `main.py:193–197,256–262` | Grid/Tri bypass DecisionEngine abstention. A triangular cycle is executed as a BTC_USDT single order, with no funded IRT cycle. | Canonical DecisionEngine remains authoritative; never reinterpret MULTI_LEG as one pair. |
| `main.py:299–304` | TP/SL exits bypass decision, risk and liquidity gates. Cash can become negative; position accounting clamps naked sells. | Replacement entry point imports none of these execution paths. Automatic TP/SL is not implemented in this migration. Future exits must enter the hypothesis/gate pipeline. |
| `models/train_hpt.py:31–33` | Feature prefix includes `closes[t+1]`, so horizon-1 target return is already observed. Longer-horizon label is one return, not cumulative forward return. One 70/30 split is described as walk-forward. | `research_bot/hpt_data.py` builds causal features, cumulative future labels and purged expanding train/validation/test folds. This is a dataset repair, not a training result. |
| `strategies/hpt_hypothesis.py` | Random model initialization can produce hypotheses without verified trained weights; checkpoint errors are swallowed. | HPT stays disabled. No torch dependency is imported by the migrated runtime. |
| `core/contracts.py`, `journal/trade_journal.py` | Actual contracts differ from the canonical repository. Journal contains four REAL-based tables. | Test fixtures preserve the supplied contracts and journal verbatim; explicit adapters preserve their shape rather than replacing canonical contracts. |

The original eight liquidity tests pass. Independent failure reproductions above
show why those tests alone do not establish fail-closed behavior. The supplied
Gaussian head already has a positive variance floor; the earlier pasted snippet's
missing-floor criticism does **not** apply to that line in this archive. Loss
shape/finite checks, calibration and checkpoint provenance still need work.
Neither the claimed 95% thesis failure rate nor Q1/profit claims are established.

## Runtime and scope

```bash
python -m scripts.run_demo_migration --journal /tmp/demo-migration.sqlite --cycles 80
python -m scripts.run_demo_migration --journal /tmp/demo-migration.sqlite --cycles 90
python -m scripts.verify_completion_coverage --output /tmp/demo-coverage.json
```

The thin replacement `examples/complete_demo_bot/main.py` calls this entry point.
Install the reviewed repository before copying that main.py into the supplied
demo. Old raw modules remain archived for audit and are not imported by this
entry point. This is an explicit migration, not an assertion that the original
unmodified main.py is now safe.

`RESEARCH_ONLY=true`, scoped offline `PAPER_EXECUTION=true`, `LIVE_EXECUTION=false`.
The original service firewall and frozen scientific artifacts remain unchanged.
`--live-feed` and unbounded `--cycles 0` are refused: the supplied feed's stale
fallback is not adopted. Real-feed research PAPER needs verified market evidence,
calibrated statistics and the existing graduation protocol.

The example keeps the actual demo's initial 10000 USDT, 10% order/exposure cap,
three open orders, 3% daily loss cap and 30-second cooldown. Canonical limits
also apply (including 35bps spread and 25bps combined extra slippage). The
cooldown uses persisted simulated fill time, not wall-clock sleeps. The engine
policy revision `DEMO_COMPATIBILITY_V2` invalidates old-policy certificates and
checkpoints; use a new experiment namespace rather than reinterpreting old runs.

Four generators run: RSI+MA, EMA, Grid and Triangular. They are canonical reviewed
generators, not identical replacements for the supplied heuristics. Uncalibrated
RSI/EMA abstain. In the all-cash default scenario Grid's maximum confidence is
below the unchanged DecisionEngine threshold; therefore an 80-frame run can
legitimately have **zero fills and zero return**. This is not a performance test
or a graduation result. A separately funded synthetic test exercises actual
approval, cost charging, fills and journal projection without changing defaults.

No IRT is implicitly added to the default USDT wallet. The new demo admission
bridge refuses multi-leg execution until every leg has a funded admission path.
The independent completion runner still has its tested sequential, non-atomic
multi-leg simulator and residual-inventory handling. This migration does not
claim that the demo integration is ready for triangular execution.

## Cost integration and journal semantics

After portfolio/DecisionEngine approval, `DemoExecutionAdmission` applies the
demo limits and the Decimal cost/liquidity preflight. Its volatility, volume and
k are explicitly **uncalibrated synthetic inputs**, persisted in configuration;
they cannot authorize graduated real-data research. Approved additional impact
is charged adversely in each simulated fill and logged separately from the
base slippage and fees. Combined slippage must remain <=25bps. A REDUCE result
is rejected; new sizing requires a new hypothesis and risk evaluation.

For a resting limit there is no currently executable depth. Displayed depth in
the price band is only a preflight liquidity proxy; the simulator keeps the
original limit and can fill only on a later eligible book. This assumption is
logged and makes no fill-probability guarantee. Square-root impact is treated as
an explicit additional stress component, not proof of independent empirical
costs; calibration must prevent double counting book slippage.

`DemoJournalProjection` verifies the supplied four table layouts and adds no
columns to them. It projects every order transition (including PARTIALLY_FILLED),
fills, arbitration and equity into those tables. Exact Decimal string amounts,
fee assets, events, reservations and restart checkpoints remain authoritative in
the namespaced completion tables in the same SQLite file. REAL projections are
lossy. Legacy fee summaries use quote equivalents; base-denominated fees are
not charged again to cash. Only the configured quote's orders are projected;
other currencies remain in the exact ledger. Missing confidence stays NULL.

Original rows remain intact, including equity timestamp collisions. Namespaced
order identity collisions reject the projection instead of overwriting history.
Projection writes and their cursor commit atomically; they can be retried after
a source-frame commit. Unknown schema, missing provenance, quote drift or
disappearing events fail closed. Existing old demo rows are **not** enough to
reconstruct exact balances: the new namespace starts a separate declared
experiment. Resume works from new exact checkpoints, not from old REAL rows.

## Verification and research limits

Tests use the real uploaded contract and journal fixtures, and cover original
row preservation, partial/final transitions, fee units, collision/rollback,
unfunded and over-limit paths, cost charging, post-cost alpha, frozen limits,
multileg refusal, disabled HPT and resumed-versus-cold replay. HPT data tests
change future prices while asserting past features stay unchanged, check
cumulative horizon labels and disjoint purged evaluation folds.

Coverage reports measure executable statements, not scientific correctness.
This delivery does not train PyTorch, validate neural calibration, establish DSR
or PBO on real data, or enable live execution. Actual command results, source
hashes and CI evidence accompany the review artifacts.

Rollback: retain the original archive; stop the migrated entry point, keep its
namespaced exact ledger for audit, and return to the prior reviewed branch.
Do not run the unsafe original execution flow merely to undo this migration.
