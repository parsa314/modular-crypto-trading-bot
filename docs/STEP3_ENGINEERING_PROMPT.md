# IDE continuation prompt — Step 3 only

Audit `AGENTS.md`, canonical navigation, active PR #115 and
`docs/COMPLETION_LAYER_AUDIT_AND_MIGRATION.md` before editing. The actual runnable
layer is `research_bot/completion/`; no verified `complete_demo_bot` source is
available. Preserve existing dataclass fields and DecisionEngine/RiskGate paths.

Continue ONLY the offline event-driven execution hardening described in
`docs/ADR-COMP-008_LOW_LIQUIDITY_EXECUTION.md`. Use composition, Python >=3.10,
strict Pydantic external validation, Decimal money/prices/quantities and the
existing custom deterministic event queue. Do not add live exchange transport,
raise risk ceilings, change frozen v0.51 or relabel synthetic/historical data as
prospective. RESEARCH_ONLY=true, scoped PAPER=true, LIVE=false.

First audit the existing tests rather than recreate implemented components:

- `execution/event_driven_simulator.py`: lifecycle, reservations, shared
  snapshot depth, paced submission, actual ACK time, fees, seeded latency,
  cancellation/expiry, atomic journal rollback and restart.
- `execution/impact_estimator.py`: explicit uncalibrated k, daily volatility,
  quote 24h volume, correct inverse, lot/minimum constraints and remaining edge.
- `execution/liquidity_gate.py`: stamped bounded book, ASK entry/BID exit for
  BUY, BID entry and inventory reduction for spot SELL, freshness and hard caps.
- `execution/cost_preflight.py`: input/parameter/result audit in the shared
  SQLite journal; REDUCE requires a new hypothesis and risk evaluation.

State the problem/options/evidence/decision/trade-offs/contract impact,
acceptance and rollback before any major new choice. Treat default 10% eligible
depth, 100ms pacing and any k as sensitivity assumptions until independently
validated on held-out venue observations. Do not double-count book VWAP/spread
and a parametric residual impact cost. Do not assert profitability or novelty
from software tests.

Acceptance commands from repository root:

```bash
python -m unittest discover -s tests -p 'test_completion*.py' -q
python -m scripts.verify_completion_coverage --output /tmp/step3-coverage.json --threshold 80
python -m scripts.run_completion_paper --journal /tmp/new-step3.sqlite --cycles 80
python -m scripts.run_completion_paper --journal /tmp/new-step3.sqlite --cycles 90
```

Require >80% executable statement coverage in each completion module, exact
close/reopen vs cold replay, and fresh repository CI/firewall results on the
reviewed commit. Keep negative evidence. Changes to policy hashes require a new
journal/experiment and invalidate prior graduation. Publish on the authorized
research branch with a concrete reviewable PR; do not merge or deploy implicitly.
