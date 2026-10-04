# Agent Operating Contract — Repository Routing and Source of Truth

> **Effective date:** 2026-09-13  
> **Project:** MSc AI cryptocurrency trading-bot research  
> **Primary repository:** `parsa314/modular-crypto-trading-bot`

This file exists specifically to prevent future coding/research agents from entering repository-selection loops, duplicating fixes, flattening scientific lineage, or treating the public deployment shell as the scientific codebase.

## 1. Single Source of Truth

`parsa314/modular-crypto-trading-bot` is the **canonical scientific and engineering repository** for the MSc trading-bot project.

All substantive development belongs here, including:

- research code and experiment logic;
- strategy research (Ichimoku, market structure, ICT/SMC-derived causal hypotheses, etc.);
- feature engineering and model research;
- backtesting, OOS validation and statistical testing;
- risk, portfolio and CVaR research;
- ML / deep-learning / reinforcement-learning modules;
- paper-trading research logic when scientifically authorized;
- thesis evidence, reproducibility assets and defense documentation;
- the authoritative project dependency definition (`pyproject.toml`).

**Do not create a second scientific implementation in another repository unless the owner explicitly asks for it.**

## 2. Mandatory Current-State Navigation

Before changing scientific code or interpreting project status, read:

1. this file;
2. `docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md`;
3. the immutable preregistration/scientific SHAs referenced by that status record;
4. the active research PR when work is prospective.

Historical `PROJECT_STATUS_*`, results and defense documents are evidence snapshots. They do not automatically override the canonical current-state navigation record.

## 3. Relationship to `parsa314/miladchicomobot`

`parsa314/miladchicomobot` is a **lightweight public deployment / monitoring shell**, not the canonical research codebase.

Its purpose is to expose a small fail-closed FastAPI service for deployment verification and public monitoring. It may intentionally contain only a subset of dependencies and endpoints.

Therefore:

- do **not** treat missing scientific modules in `miladchicomobot` as evidence that the main research code was deleted;
- do **not** port the entire scientific project into `miladchicomobot` merely because Railway currently deploys that repository;
- do **not** use `miladchicomobot` as the authoritative dependency manifest for the thesis project;
- do **not** infer the state of the scientific project from the deployment shell alone;
- when a runtime dependency is mirrored into `miladchicomobot`, it is only to keep the public shell deployable.

## 4. Current Railway Routing

As of 2026-09-13, the Railway service named `thesis-trading-bot-v08` is sourced from:

`parsa314/miladchicomobot` → branch `main`

This is a deployment-routing fact, **not** a statement that `miladchicomobot` is the main project repository.

Before changing Railway source routing, verify the current Railway service configuration and obtain explicit approval for any migration/repointing action.

## 5. Intentional Fail-Closed Behavior

The current deployment is intentionally research-only and fail-closed.

The following state is intentional unless a later scientific gate explicitly changes it:

- `LIVE_EXECUTION = false`
- `PAPER_EXECUTION = false`
- forward-paper execution disabled
- no exchange-order endpoint enabled
- Kraken holdout sealed

A missing or locked execution endpoint is **not automatically a bug**. Inspect the actual source and scientific gate before attempting to change such behavior.

Do not enable live or paper execution merely to make a smoke test pass.

### Owner-requested operator CLI — 2026-10-04

The owner explicitly requested LIVE capability in this repository on 2026-10-04.
`research_bot.live_runtime` is a separate, explicitly invoked spot execution CLI;
its default is public-data preview. See `docs/LIVE_EXECUTION_FA.md` for the account,
artifact and risk contract. This scoped engineering change does not promote a
scientific model, unseal Kraken, change research-service flags, or start/deploy a
trading process. Preserve the research service's intentional execution lock.

## 6. Dependency Authority

For the scientific bot, use the dependency declarations in this repository as authoritative.

Core runtime includes the project stack around CCXT, NumPy, Pandas, scikit-learn, FastAPI, Uvicorn and Psycopg. Optional ML/deep/RL stacks are declared as extras and should be installed only when their corresponding research path is being exercised.

Do not add large ML frameworks to a lightweight deployment image merely because they may be used in future research.

## 7. Keep the Two Crypto Projects Separate

The owner also has a separate **Crypto Intelligence Studio / fundamental research platform**. It is not this MSc trading-bot repository.

Do not import its database schema, market-coverage requirements, pre-listing engine, whale intelligence pipeline or Railway database incidents into this trading-bot project unless an explicit integration task requires it.

A previously observed Railway PostgreSQL `No space left on device` / `57P03 recovery mode` incident was found while inspecting the broader Railway environment. It must **not** be assumed to be the canonical trading-bot database failure without verifying the exact service and `DATABASE_URL` routing.

Infrastructure errors must be attributed to the exact service/database before trading or research logic is changed.

## 8. Scientific Branch Lineage Is Intentionally Stacked

The scientific lineage through v0.50/v0.51 is not fully flattened into `main`. `main` and `deploy/research-v50` are materially diverged histories.

This is not permission to perform a mega-merge.

Future agents must distinguish:

- moving branch head;
- frozen preregistration head;
- canonical empirical/scientific head;
- artifact/run provenance;
- deployment shell head.

Proven engineering hardening may be reconciled selectively into `main` with regression tests. Scientific model/threshold/data-window/holdout changes must remain under the experiment-specific governance path.

## 9. Agent Decision Procedure

When continuing this project:

1. Start in `parsa314/modular-crypto-trading-bot` for scientific/research/trading-bot work.
2. Read `README.md`, this `AGENTS.md`, `docs/CANONICAL_RESEARCH_STATUS_2026-09-13.md`, and the relevant experiment/workflow before editing code.
3. Verify the latest commit, PR and CI state; do not assume an older chat-reported version is current.
4. Treat `miladchicomobot` as a deployment shell unless the owner explicitly changes repository governance.
5. Separate code, dependency, database, Railway and external-provider causes before patching.
6. Preserve `LIVE_EXECUTION=false` and `PAPER_EXECUTION=false` unless explicit authorization and the project governance gate both permit otherwise.
7. Never rewrite or discard negative experimental evidence merely to obtain a passing result.
8. Prefer reproducible fixes in the canonical repository over ad-hoc changes made only in a running environment.
9. Never label infrastructure blockage as a scientific rejection and never substitute an offline smoke test for required prospective evidence.
10. For v0.51, honor first-seen evidence rules: missed/late bars cannot be backfilled and relabeled as prospective.

## 10. Anti-Loop Rules

A future agent must **not** repeatedly:

- search for the "real" bot inside `miladchicomobot` after this repository has been identified;
- recreate dependencies already declared in the canonical project;
- interpret a fail-closed endpoint as a missing-feature regression without checking governance;
- blame CoinMarketCap or another provider for an unrelated database/storage failure;
- migrate databases or change Railway source repositories simply to clear an error without first identifying the root cause;
- duplicate the same research implementation across both repositories;
- attempt a blind merge of hundreds of scientific commits to make `main` look current;
- treat a moving PR head as interchangeable with a frozen scientific SHA.

If repository roles or scientific state appear inconsistent with this document, **verify current GitHub and Railway state first**, then update this contract and the canonical status record as part of the same change.

## 11. Governance Summary

```text
parsa314/modular-crypto-trading-bot
    = CANONICAL SCIENTIFIC / ENGINEERING REPOSITORY
    = source of truth for research, models, experiments, thesis evidence

parsa314/miladchicomobot
    = PUBLIC DEPLOYMENT / MONITORING SHELL
    = intentionally lightweight and fail-closed

Crypto Intelligence Studio
    = SEPARATE FUNDAMENTAL-RESEARCH PROJECT
    != this MSc trading-bot codebase

Railway deployment source
    = may point to the shell for operational reasons
    != project source of truth
```

**Evidence Before Opinion. Reproduce before promoting. Attribute infrastructure failures before changing scientific logic.**
