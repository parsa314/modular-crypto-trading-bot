# Forward Paper risk cap alignment — 2026-11-05

Scope: **only** `research_bot.forward_paper_v14.ForwardPaperRunner`, not the independently stacked strategy/PAPER engines, finance PR #149, or production broker.

The original frozen runner config declared 0.5% risk fraction, 12% peak drawdown and 100% gross exposure at the `RiskEngine` constructor. These were inconsistent with the owner's stricter risk constitution.

Implemented:
- New paper-only position sizing risk fraction defaults to **0.25%**; any value above it is rejected.
- Paper pre-trade drawdown hard ceiling becomes **5%** (previous 12%).
- Paper pre-trade gross exposure hard ceiling becomes **70%** (previous 100%).
- Maximum single-asset weight remains the stricter **25%**; a config above the constitutional ceiling **35%** is rejected.
- Position sizing includes an estimated entry/exit fee and slippage plus one quoted spread in the stop-distance denominator; fills remain hypothetical, not exchange executions.
- A configuration requesting broader per-trade risk, peak drawdown, gross exposure or asset weight is rejected rather than silently clamped.
- Existing risk-reducing exit handling remains intact.

**Not yet complete / no false claim of full risk compliance:**

1. There is no persistent verified UTC day-open equity anchor and **no 2% daily-loss halt in this runner**; a later scoped change with PostgreSQL restart/concurrency tests must implement it before treating Paper risk as complete.
2. This strategy uses conceptual ATR stop-distance **for sizing** but does not submit or simulate an independently reliable server-side stop order. A gap may exceed estimated loss. Do not claim a guaranteed 0.25% maximum realized loss.
3. Cross-asset marks can fall back to stored average prices for positions not refreshed in the current process, which is **not a safe live portfolio MTM**; address before multi-asset production-like Paper promotion.
4. Paper accounting still has float-based persistence in this older runner and should not be confused with finance G1 / Decimal ledger evidence.
5. This PR is not a new economic test and does not promote Ichimoku, four-strategy convergence, ML or RL.
6. No real exchange connectivity, DEMO orders, or live-money orders are exercised.

Validation:
- `pytest -q tests/test_forward_paper_v14.py`
- `pytest -q` and the existing v52 workflow on the exact PR head/merge ref.
- Preserve previous (possibly failing) scientific findings.

Gate verdict pending measured CI: **partial PAPER risk alignment only**. Financial G1 = BLOCKED; real-money LIVE = OFF; full Paper-risk conformity = NOT YET VERIFIED.
