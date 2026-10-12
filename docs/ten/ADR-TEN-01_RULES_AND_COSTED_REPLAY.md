# ADR-TEN-01: ten independent costed rule experiments

Date 2026-10-12. Baseline ef46b513af079dd38d6a91a331d64c1353030680. Status: implementation design frozen; historical evaluation DIAGNOSTIC, not clean preregistration. Governance RESEARCH_ONLY / PAPER_OFF / LIVE_OFF. Existing funded completion simulation remains separately scoped.

## Exact entry and exit rules

Let G mean close strictly above both visible/trailing cloud lines and Tenkan >= Kijun. A fresh upward cross additionally requires previous Tenkan < previous Kijun. All rules use closed 1h events available at the decision. Higher 4h context is implemented for inspection, not silently added as a trading filter.

| ID | Entry while flat | Close-triggered exit |
|---|---|---|
|S1|G and fresh TK cross|close below Kijun or cloud bottom|
|S2|G and confirmed continuation BOS up|not G or any confirmed downward structure break|
|S3|G and bullish OB retest|OB invalidation/expiry or close below Kijun|
|S4|G and active bullish FVG created now/previous bar|FVG fully mitigated/invalidated/expired or close below Kijun|
|S5|G and confirmed low sweep|close below sweep candle low or Kijun|
|S6|G and Brooks two-bar bullish reversal proxy|close below EMA20 or Kijun|
|S7|G and BOS up and FVG created now|downward structure break, bound gap invalidation/mitigation/expiry or below cloud|
|S8|G and OB retest and low sweep|bound OB invalidation/expiry or below cloud|
|S9|BOS up and OB retest, no G|downward structure break or bound OB invalidation/expiry|
|S10|G and at least two of OB retest, FVG created now, low sweep, Brooks reversal|below cloud or bound trigger invalidation; FVG mitigation also exits|

All admitted comparisons are the same ten fixed rules; no parameter search/winner retuning. Equality conventions follow the R1 snapshot. BOS distinguishes continuation from initial break and CHoCH; exits conservatively accept any downward break. S6/S10 intentionally use the R1 two-bar reversal, not a falsely named authentic Brooks L2/H2. S10 binds latest zone confirmation; ties BROOKS > SWEEP > FVG > OB. OB touch is a closed-bar overlap with close above zone high. Zones keep their entry-bound identity; future nearest-zone changes do not switch an existing trade's anchor.

Every column represents its own hypothetical 10,000 USDT account, one long position, allocation <=10%, no averaging/leverage. These accounts are not a combined funded portfolio. Common barriers: stop 2%, take 4%, known maximum holding 48 hours. No canonical limit is raised. Existing 3.5% empirical ES and 12% drawdown ceilings are enforced as scenario screens; historical-close ES is not measured future exit liquidity/portfolio CVaR.

## Decision choices

Problem: previously supplied table was not an executable specification (cross vs regime, old zone vs latest zone, unspecified timing/costs).
Options: reuse mutable v1 proxies; modify frozen experiments; isolate a versioned event/replay package. Choose independent research_bot/ten_strategies using the R1 operational detector definitions and canonical immutable HypothesisCandidate.
Evidence: existing DecisionEngine adapter requires zero-confidence hypotheses and no fill. Existing completion journal has additive events, integrity digests and unique event identity. Supplied placeholder returns do not estimate economic value.
Trade-off: additional package and strict rare events can yield sparse/zero columns. Zero events are retained; no softening after results.
Impact/security: process_candidate remains unchanged and always non-actionable. No exchange/write transport is imported. The OHLC ledger uses TEN_REPLAY_* records, not actual orders/fills. Research replay is a counterfactual experiment, not PAPER graduation.
Acceptance: prefix/future perturbation, confirmed pivots, zone origin/confirmation, unavailable current 4h bars, exact Decimal cash flows, strict after-availability opens, double-touch stop-first, gap stop, maximum holding, no canonical fill and idempotent journal.
Rollback: stop using TEN_RULES_1 namespace; remove only new modules/CLI. Keep run reports and previous v1/R1 evidence immutable.

## Costs and clock

Fee .001 per side and adverse slippage .0002 per side are declared scenario parameters; round-trip shorthand is approximately .0024, but actual fees/costs are applied to each transaction's notional, not subtracted from every hourly return or used as a placeholder forecast.
Entry budget B produces quantity B/[O*(1+s)*(1+f)]. Exit cash is quantity*P*(1-s)*(1-f). Monetary arithmetic uses Decimal with precision 36. Data prices enter as validated numeric representations; this is an OHLC historical scenario, not venue lot/precision matching.
A close-triggered action executes only at the first source-bar open strictly later than its decision available_at. A +2s lag therefore cannot fill at the immediately preceding boundary. Risk history excludes bars unavailable at that open. Hard barriers use OHLC ranges, stop first if both touched, a gapped stop exits at the worse observed open, take profit receives no optimistic gap improvement. Intrabar physical fill times are unknown; barrier resolution is logged when the bar becomes available. A precommitted 48h timeout exits at that boundary's open.
Terminal residual inventory is marked at liquidation-cost value; no fictitious final-close trade is inserted. Records are clearly hypothetical OHLC replay with no depth/queue/partial-fill claim.

## Return matrix and rejection screens

T x 10 is aligned hourly net ACCOUNT returns including idle cash, actual replay entry/exit costs and mark-to-liquidation-value inventory. CSV identity, protocol and source/runner identities namespace the journal. CLI writes a lock file before replay; a changed lock cannot silently overwrite the same output namespace. JSON/Markdown, matrix SHA256, closed trade cash flows and remaining inventory are emitted. Synthetic and historical runs have separate labels.

PBO uses daily complete right-labelled 24h intervals, eight CSCV blocks and two-day guards at both ends of each block (covering maximum holding). Bailey relative OOS rank is rank/(M+1); ties use average ranks and average across tied IS maxima. A constant strategy/split aborts the statistic instead of dropping a variant. At least 40 guarded days per block are a project support floor, not a universal sufficiency theorem.
DSR Eq.(2) uses net daily returns, per-period Sharpe, skew, Pearson kurtosis, sample count and trial Sharpe dispersion. Family-only IID diagnostics may be emitted after 1,000 daily observations; official dsr_status remains NOT_RUN because prior search/dependence assumptions are unbound. PBO is family diagnostic, not proof of clean search. The old ValidationHarness's legacy approximations are not silently reused as a formal certificate.

Rejection gates R1-R8 below are this new experiment's proposed operational checklist, not claims that an unspecified prior R1-R8 standard was replicated:
R1 synthetic is not market evidence; R2 clean preregistered/untouched data required; R3 >=100 closed trades per strategy; R4 positive net performance required; R5 measured execution costs required; R6 complete search and valid DSR assumptions required; R7 PBO available and <=.10; R8 prospective first-seen and portfolio risk validation required. Positive historical replay cannot pass the unbound gates or automatically become PAPER. Negative net result is FAIL; positive but unbound result is INCONCLUSIVE.
