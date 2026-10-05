# Production Convergence — Phase 1 — 2026-10-05

این مرحله یک مسیر مهندسی قابل آزمون می‌سازد. موفقیت آن شواهد Alpha،
Forward، Paper، Testnet یا مجوز معامله نیست. `LIVE_EXECUTION=false` و
`PAPER_EXECUTION=false`؛ registry واقعی Promotion خالی است.

## Repository forensic audit — before changes

`main=876e229a8d3e73f2e9d646d4126c8ce08e4c11b4`؛ پایه انتخابی PR #109
با SHA `8d94096d8d9a1d066a30ecabb8134b55581a539f`، نه main و نه mega-merge.
هر ۹ workflow پایه روی همان head موفق بود. ۶۹ PR باز و Issue #78 در
snapshot اولیه مشاهده شد؛ بازبودن PR به معنای قبول علمی نیست.
شاخه مستقل: `integration/production-convergence-20261005`.
AGENTS، README، canonical status، traceability، منابع V58/V59، V52،
MASTER/Live/PPO، تست‌ها و قراردادهای workflow پیش از تغییر بررسی شدند.
فهرست SHAها در `evidence/production_phase1/source_audit.json` موجود است.

| COMPONENT | CURRENT_IMPLEMENTATION | SOURCE_BRANCH | SCIENTIFIC_STATUS | ENGINEERING_STATUS | KNOWN_DEFECT | ACTION_REQUIRED |
|---|---|---|---|---|---|---|
| Native strategies/features | 10 confluences + visible Ichimoku, causal snapshots | #109 / V58 source #93 | Development; not promoted | Tested native implementation | Identity omitted data version | Selective identity/metadata correction, no strategy retuning |
| Tournament | Expanding folds; sklearn train pipeline; validation isotonic | #100 → #109 | Old development evidence retained | Executable | Late labels/complete temporal contract missing; duplicate check coupled to timestamp | Availability/interval purge; independent uniqueness; adversarial isolation tests |
| V59 risk | Seven immutable limits, 5% kill | #109 | Research gate | Tested | Independent config/hash from execution | Canonical immutable constitution |
| MASTER L0 | Frozen config, deployment gates | #103 `822a81866582fa63d1f83ead83b8608662fa1cf1` | No promoted model | Source tests passed separately | 10% configured DD | Selective config port, tighten to canonical 5%; preserve old frozen branch |
| PPO simulator | Offline causal next-open environment | #101 → #103 | Challenger, not primary | Synthetic/smoke engineering | Independent 12% ceiling | Preserve simulator; shared pure config, reject relaxed values |
| Live controller | PPO-specific single-symbol controller | #102 → #103 | Authenticated execution NOT_VERIFIED | Private gate closed | Cannot consume V59 intent; no native protection | Do not port controller/private transport; generic Fake-only OMS replaces new path |
| Execution journal | WAL/FULL sync, flock, deterministic reservation | #103 | Engineering | Source reviewed | Separate account/receipt transactions in old controller | Selective journal port; atomic CAS settlement and protection recovery |
| V52 atomicity | Executable BBO, atomic fills, idempotency | Merged #75 `b252dde46e0a97e443967ed872310ec68d7e1a63` | Paper engineering, no new authorization | Proven principles/tests retained | No generic production intent | Reuse principles, not parallel service |
| V51 overlap | Research simultaneous conflict arbitration | Existing lineage | Research only | Existing tests retained | Not central production portfolio arbiter | Phase-1 cash/exposure reservation only; full correlation arbiter Phase 2 |
| Promotion artifact | Research fold/seed identity | Legacy PPO runtime | NOT_PROMOTED | No canonical promoted loader | Arbitrary run selection | Exact artifact hashes, reviewed registry and scope checks |
| Protective exits | No confirmed native stop path | Legacy Live branch | NOT_VERIFIED | Missing | Filled position depends on process | Durable Fake FSM + unknown-state halt + simulated emergency flatten |
| Prospective collection | Separate #105 repair/evidence | main-based repair | V25 invalid continuity; V15 deployment blocked | Distinct PR | Forward evidence not restored by Phase 1 | Keep scientific statuses; do not backfill, activate or merge |
| Cloud state/WS/monitoring | Local journal only | Multiple branches | NOT_VERIFIED | Incomplete production infrastructure | No distributed lock or independent watchdog | Not implemented here; Phase 2 |

## Frozen responsibility boundary

```text
V58 reviewed features/strategies → V59 causal candidates and research gates
    → validated promotion/artifacts + portfolio capacity decision
    → immutable ProductionDecisionEnvelope → sealed ApprovedOrderIntent
    → generic OMS → exact Fake transport → receipt reconciliation
    → atomic journal + protective FSM → confirmed Fake protection/evidence
```

Consumer `execution.oms` imports no V59, V58, model, torch or PPO code.
`FinalResearchDecision.action=ADMITTED_RESEARCH_SIMULATION` retains its research
meaning. The separate trusted bridge requires all scientific gates and explicit
engineering promotion; the default registry cannot authorize any intent.
Fake approval is injected only for fixture tests/replay and cannot reach a private
transport. No real keys are read, no CCXT private client is constructed.

## Compatibility and intentionally changed contracts

`execution.py` becomes a package; its original paper simulator is kept as
`execution/legacy.py`, with only the relative import adjusted. Lazy exports
preserve existing callers. Existing V52 and service behavior are unchanged.
Reviewed MASTER journal/config and offline environment are selectively ported;
PPO/controller/exchange/CLI are not blindly imported.

New risk hashes, temporal schema and `NATIVE_EVENT_ID_V2` require **new** evidence
records. Historical registration, identifiers, datasets and negative findings are
never edited or relabeled. Native identity now includes direction, entry time and
data version. The original frozen native study refuses changed source hashes;
this is correct behavior, not a regression to bypass.

Primary evaluation is expanding chronological walk-forward. Equal-clock groups
stay indivisible. Train outcome/publication intervals must end strictly before
the earliest Validation/Test information start minus configured time embargo;
Validation labels must be available before Test information start minus embargo.
Insufficient admissible data skips the fold rather than silently reverting to
row-only splitting. Row-only mode remains explicitly ENGINEERING_FIXTURE-only.

## Scope limits

Phase 1 is single-active-protective-group per symbol, unleveraged long-only USDT
spot, IOC entry, quote-denominated fixture fees. It includes shared-account cash,
asset/gross/turnover/stop-risk limits; it does not certify correlation allocation,
multi-instance cloud leadership or actual venue protective API semantics.
Emergency halt has **no automatic resume endpoint**; manual/2FA operational
resume remains unavailable, which is stricter than allowing unsafe resume.
No new strategy, performance threshold, training run or model promotion occurs.

## Reproduction

```bash
python -m pip install -e '.[dev,data]' 'pyyaml>=6,<7'
python -m compileall -q research_bot scripts
python -m pytest -q tests/test_risk_constitution.py tests/test_live_ledger.py tests/test_convergence*.py tests/test_phase1_end_to_end.py tests/v59/test_convergence*.py
python -m pytest -q
python -m research_bot.v59.convergence_replay --output artifacts/phase1-new-replay
PYTHONPATH=. python scripts/audit_convergence_native_events.py --input-root /path/to/pinned/binance-2024-history --output artifacts/phase1-new-native-audit
```

Replay output is create-only. The optional native audit reads only checksum- and
quality-verified official 2024 cached archives; it runs no tournament or training.
It is not prospective evidence and never consumes sealed CoinEx/Kraken holdouts.

پایان‌نامه: این مدارک به فصل روش تحقیق/معماری و بخش اعتبار مهندسی فصل نتایج
مربوط‌اند؛ نباید در جدول سودآوری یا شواهد Forward شمارش شوند.
