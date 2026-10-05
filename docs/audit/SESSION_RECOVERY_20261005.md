# Interrupted V1–V58 audit: recovery checkpoint

This records the last carried-forward execution state from the earlier audit session. It is a recovery note, **not delivery of the local source code or completion of the rebuild**.

## Source and delivery state

- Base: PR #90, `09979266ebd7ba1466f13e8c01d8fe7857ea0e57`.
- Original workspace: `/workspace/trading-bot-audit`.
- Local branch: `audit/v1-v58-market-runtime-20261001`.
- Last reported local commit: `9945e4a2f1cd73729192d1b7dea3cf06887137b4`.
- Last reported local tree: `06d6b189e6a9bf1848eef1ef497999262e96f219`.
- Stage-1 source was committed locally; it has **not been verified as uploaded to GitHub**. The branch search returned no matching remote audit branch.
- Stage-2 components were still untracked. Recover them before replacing, resetting or cleaning the original workspace.
- These hashes identify local objects; their presence in GitHub is not claimed.

## Last recorded verification

The carried-forward record reports **868 passed, 1 skipped, 12 subtests passed**, in 55.53 seconds. Actual PostgreSQL integration ran. The one skip concerned optional Torch initialization. Dependency consistency and source compilation were also recorded as passing.

```bash
TEST_DATABASE_URL='postgresql://postgres:@/postgres?host=/workspace/audit-pgdata' \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest -x -q
```

The raw outputs and source cannot currently be read, so this is a historical execution record, not a fresh rerun or independent current verification. The new components need their own integrated test run.

## Work reported locally

Stage 1 corrected temporal data/label availability, probability validation, order/fill reconciliation, cash and inventory accounting, drawdown and risk calculations, and closed-candle handling. It added public spot-data adapters for CoinEx, Binance and Nobitex, causal four-style feature proxies, portable calibrated ML artifacts, and durable observation identity.

The V1–V58 audit distinguishes inspected source from unavailable history. No named V55–V57 source was established; those versions must not be called tested.

Stage 2 developed explicit OBSERVE / PLAN_ONLY / OFFLINE_REPLAY contracts, exact Decimal lot/tick handling, three separate venue wallets, cumulative-fill accounting, unsigned order templates with submission blocked, causal structure/context events, and fixed statistical diagnostics. Final coordinator/replay integration remains pending. The multidisciplinary council consisted of technical review perspectives provided by AI agents, not credentialed human professionals.

## Economic results retained

The fixed development protocol used 184 test observations per available venue. CoinEx model Brier loss was 0.2392910031 versus 0.2198566634 for the training-frequency control; Nobitex was 0.2517197003 versus 0.2335984393. Both ML admission paths made zero trades.

The rules control had 17 hypothetical trades on each venue. Net return after prescribed costs was approximately -0.127811% for CoinEx and -0.133313% for Nobitex. These results do not demonstrate profitable alpha. Keep the outcomes and do not retune the same heldout observations.

Binance public retrieval returned HTTP 451. No alternate route or restriction bypass was used. Fresh October-2 inference on the other two venues returned NO_TRADE. No authenticated exchange requests or actual orders were sent by this audit.

## Current interruption and next action

On resumption on October 5, the cloud status reported a running machine with **offline connectivity and no execution capabilities**. Waiting roughly 15 minutes did not restore filesystem or command tools. This prevented source recovery, fresh tests and transfer of local code. The requested uninterrupted two-hour work period cannot be claimed.

Restore the original workspace, verify the commit/tree and untracked files, finish risk/replay integration, and rerun the full suite with PostgreSQL. Then upload the actual source and raw evidence, read back the remote tree, and inspect hosted CI. Preserve frozen research refs and changes made in other branches during the interruption.

This checkpoint is published on a separate documentation branch based on PR #90. It does not change main, later work, research promotion decisions, or execution authorization.
