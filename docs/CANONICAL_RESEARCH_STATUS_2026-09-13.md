# Canonical Research Status — 2026-09-13

This file is the default navigation record for the current scientific and engineering state of the MSc crypto-trading-bot project. It exists to stop agents, reviewers and deployment tooling from confusing the moving default branch, the stacked scientific lineage, and the public deployment shell.

## Repository roles

- Canonical scientific/engineering repository: `parsa314/modular-crypto-trading-bot`.
- Default branch: `main`.
- Public deployment shell: `parsa314/miladchicomobot`.
- The public shell is not the scientific source of truth.
- Later research stages are intentionally preserved in stacked branches/PRs; do not flatten them with a blind mega-merge.

See `AGENTS.md` before changing repository routing, scientific lineage, execution flags or Railway source configuration.

## Safety state

Current authorized mode is:

`RESEARCH_ONLY / PAPER_OFF / LIVE_OFF / KRAKEN_SEALED`

A successful CI run, backtest, research artifact or service healthcheck does not authorize PAPER or LIVE execution and does not prove profitable alpha.

### Engineering addendum — 2026-10-04

The owner requested a separately invoked LIVE spot CLI. The implementation and
operator contract are described in [LIVE_EXECUTION_FA.md](LIVE_EXECUTION_FA.md).
This authorization is for the requested execution capability; no actual order
or deployment was performed while implementing it. The research service remains
locked, and scientific results, promotion decisions and sealed holdouts are
unchanged. The historical scientific provenance below remains a dated snapshot.

The owner's subsequent MASTER v3 protocol begins with Stage 0 only. Its foundation
initializes an immutable config and pending progress ledger. On the Stage 0 branch,
the legacy operator CLI blocks private modes until later verified promotion gates;
no economic metric is marked passed. See [MASTER_V3_STAGE0_FA.md](MASTER_V3_STAGE0_FA.md).

## Latest completed scientific result — v0.50

Canonical decision:

`V50_NONOVERLAP_FAILURE_SUPPORTED`

Canonical provenance:

- workflow run: `34707823108`
- scientific head: `1dd0b1fe506fc51ceec4ff8934b77090f86b6cc2`
- frozen preregistration head: `9b3f8d1bda650cc93af7b6cc69f162ae30d64cac`
- artifact ID: `10302830689`
- artifact digest: `sha256:da5813a8c031f9da6cc940fda942efc846ca536dbd952f4930584c30732373e9`

The v0.50 diagnostic reproduced the frozen Expected-R pipeline and isolated the earliest-first non-overlap transformation as the stage satisfying the preregistered broad-harm attribution rule across the consumed development folds. This is a mechanistic failure-attribution result. It is not evidence that removing overlap controls, using post-hoc ranking, or enabling trading will be profitable.

## Active scientific question — v0.51

v0.51 tests whether a causal same-entry conflict arbiter can improve the frozen earliest-first non-overlap control without changing the predictor, admission threshold, costs, exits, Financial Governor, venues, assets or risk limits.

Frozen comparison:

- `A0`: earliest-first, no pre-emption.
- `A1`: `SIMULTANEOUS_MAX_EXPECTED_R_NO_PREEMPTION`.
- A1 may only rank candidates executable at exactly the same entry time while flat.
- later arrivals cannot pre-empt an active trade.
- realized outcome, future path and future arrivals are forbidden from the priority key.

Current provenance and amendments:

- original preregistration: `d8ee4576aaf55750dd5910cc0d3b2efcbba3f5b2`
- frozen calendar: `6a8fa49de2d1befa9c17049aa61e13da20a028eb`
- predictor-identity amendment: `4838c0d98c408d358929b0767676a5ac024bd8dd`
- evidence-integrity addendum: `da5dcf5cd3568a92c75871016f000917c9380955`
- repaired prospective start: `2026-09-13T12:00:00Z`
- current PR: `#62` (`research/v51-overlap-arbitration-empirical`)
- current PR head when this record was prepared: `2f16d7b2538919048aa5f11a559b6539461b820c`
- the PR narrative records empirical implementation snapshot `7e54b0487e46ae2e7f85892b06860df12c64925e`; do not confuse that frozen/reference snapshot with the moving PR head.

Data contract:

- public closed 4h OHLCV only
- venues: CoinEx, OKX, KuCoin
- assets: BTC, ETH, SOL, XRP, DOGE
- five immutable 30-day blocks; 150-day design horizon
- a prospective bar is admissible only if first observed no later than 60 minutes after close
- late backfill remains context only and cannot be upgraded to prospective evidence

Current v0.51 state:

`V51_READINESS_IMPLEMENTED / PROSPECTIVE_EVIDENCE_NOT_YET_CANONICALLY_COLLECTED / SCIENTIFIC_DECISION_DEFERRED`

No terminal v0.51 economic result has been inspected and no v0.51 scientific decision exists.

## Current infrastructure blockers for v0.51

The active PR records two infrastructure blockers rather than scientific failures:

1. GitHub-hosted Actions jobs have failed before executing workflow steps with `runner_id=0`; a minimal independent runner smoke reproduced the no-runner condition.
2. A separate Railway collector service could not be provisioned because of the current plan resource limit. Existing thesis/fundamental services were deliberately not repurposed.

These conditions must not be relabeled as strategy failure or model failure. Conversely, deterministic offline tests are engineering evidence only and must not be substituted for the canonical prospective evidence ledger.

Because the first-seen rule is strict, missed bars cannot later be backfilled and called prospective. Any future evidence read must prove its first-seen timestamps and support requirements before economic metrics are inspected.

## Branch-lineage warning

`main` and `deploy/research-v50` are materially diverged histories. A repository audit on 2026-09-13 found the research/deployment lineage hundreds of commits ahead of `main` while also behind it by later default-branch commits.

Therefore:

- do not resolve the divergence with a blind mega-merge;
- preserve preregistration/empirical boundaries and artifact provenance;
- port proven engineering fixes selectively with regression tests;
- keep canonical scientific SHAs immutable in documentation;
- treat moving branch heads separately from frozen scientific heads.

## Engineering reconciliation policy

Known engineering hardening from the v0.50 audit may be selectively reconciled into `main` when the change is scientific-result-neutral and covered by regression tests. Examples include fail-closed non-finite risk/execution handling and point-in-time multi-asset join correctness.

Changes to model logic, thresholds, labels, data windows, holdout access, overlap policy, PAPER/LIVE permissions or v0.51 prospective evidence rules require their own scientific governance and must not be smuggled into engineering reconciliation.

## Default navigation rule for future agents

When determining the state of the project, use this order:

1. `AGENTS.md` for repository and execution governance.
2. this file for current canonical navigation.
3. immutable experiment/preregistration SHAs and canonical artifacts for scientific claims.
4. current PRs for active prospective work.
5. Railway/public shell only for deployment-health facts.

Historical documents remain evidence and should not be rewritten merely because the current status advanced. If a historical document conflicts with this navigation record, treat it as a dated snapshot rather than current authorization.
