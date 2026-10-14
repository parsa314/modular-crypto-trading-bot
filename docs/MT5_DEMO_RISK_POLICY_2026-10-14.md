# MT5 DEMO Financial Veto — 2026-10-14

Status: **ENGINEERING / DEMO-ONLY / NOT LIVE-READY**.
Implemented in `research_bot/mt5_demo_risk_gate.py` and invoked in `DirectMT5StrategyRunner.evaluate_once` **after** signal/model/position analysis and **before** the durable intent journal or any MT5 order submission.

## Fixed controls

- Baseline requested risk: 0.25% of account equity per proposed position; existing direct-strategy config hard-limits the declared risk to 1% (more conservative than the owner's 2% upper bound).
- Absolute gate cap: 2% of observed equity inclusive of stop-distance risk **plus a stated 50 bps round-trip cost stress**. The 50 bps are a **scenario assumption, NOT an empirical fill estimate**.
- Account-level daily kill: reject new entries when observed equity is <=90% of an independently observed **same UTC day's opening equity**.
- No missing, naive, nonfinite or stale timestamps/data accepted; missing or stale daily equity anchor is a hard veto.
- Demo-mode enforcement, broker stop-distance checks, fresh quotes and broker `order_check` remain the responsibility of the pre-existing MT5 Demo executor.
- NO_TRADE, model veto, risk veto and existing-position management remain independent.
- Existing position exits are not blocked by the new-entry veto: the safety gate sits only on new entries.

## Anchor protocol

The direct agent accepts two nonsecret environment variables:

`MT5_DEMO_DAY_START_EQUITY` — account equity actually observed and verified at the start of the current UTC session.
`MT5_DEMO_DAY_START_DATE_UTC` — date in `YYYY-MM-DD` corresponding to the observed equity.

Both must be provided for **DEMO submission**. Never invent a day-start balance, copy a later equity measurement back to midnight, or reuse an old day's value. If automated trustworthy collection is not operating at the UTC boundary, the demo agent will safely **refuse new orders** until a valid source is available.

Setting the date and number alone is not proof of independent verification: broker-stamped daily equity provenance and a durable anchor store still need an operational integration and tests before promoting this feature to a production or LIVE route.

## Limitations (must not be hidden)

- Not an account-wide persistent, broker-reconciled kill switch across multiple independent workers. Distributed locking, cross-agent exposure accounting and account/history reconciliation remain P0 work.
- A server-side stop is necessary but not sufficient: gaps, spread expansion, rejected/partially filled exits and liquidation can result in losses greater than the modeled stop risk or the owner's 2% cap.
- The new gate does NOT automatically flatten positions when drawdown reaches 10%; it vetoes **new entries**. Existing positions require their separate broker-side protections and liquidation policy.
- Trading without a trustworthy start-of-day equity anchor is not enabled by a `LIVE_PERMISSION=GRANTED` statement; owner's intent and operational readiness are different facts.
- The native terminal is a Windows application; web/mobile MT5 is not a replacement for the Python/desktop execution bridge.
- No broker login, live/demo order, neural model fit, backtest profit estimate or money movement is performed by this code change.

## Test evidence

`pytest -q tests/test_mt5_demo_risk_gate.py tests/test_mt5_direct_strategy.py`

Scenarios: exact 10% daily drawdown boundary, missing/stale anchor, large position stop+cost exceedance, NaN/Inf, short stop direction, denied submission before journaling, successful fake demo submission, idempotent signal replay and timeouts.

Fake broker fixtures are permitted for **engineering unit tests**, and must never be represented as real market trading results.

## Scientific evidence protocol

Real returns require genuine source-traceable market prices, contract specs, fees, bid/ask, fill history, timestamps and model/source/data hashes. Use purged expanding walk-forward as the deployment-replay primary analysis and CPCV as a robustness examination. A fully untouched final chronological holdout plus prospective demo evidence are still prerequisites for promoting any model. Both success and negative findings must remain registered.

## 2026-10-14 Addendum: cooperating single-host global DEMO admission

`research_bot/mt5_global_demo_kill_switch.py` stores account-keyed state in a durable SQLite file (serialized schema initialization and `BEGIN IMMEDIATE` transactional admission) and **holds a single writer transaction across each cooperating worker's demo order submission**. It rejects new entries when same UTC-day observed equity is at or below 90% of its first persisted day-start anchor, or after **three consecutive execution exceptions**, or when an order acknowledgement is partial/uncertain. Any latched halt persists across new days and reconnects, and there is intentionally no automatic reset. The broker account is identified by the verified DEMO login/server supplied by the adapter; a missing database path/account ID or non-DEMO trade mode blocks new entries. Other account identities have separate account-keyed ledgers.

`MT5_DEMO_GLOBAL_KILL_DB` configures the **same absolute local path on all cooperating MT5 DEMO workers** (default `results/mt5_demo_global_kill.sqlite3`). Operators must provision the durable directory and persistent local storage. SQLite locking is **NOT** a distributed lock across independent VPS hosts or a guarantee on unreliable network filesystems; workers outside this code can still place unmanaged orders. Broker-provided day-start equity remains an **externally asserted input** and requires an independent broker-stamped historical account ledger. No global flattening is implemented, and close-position emergency paths continue independently. Exits must be reconciled directly with MT5 positions/orders after errors, partial fills or process crashes. This is **DEMO ONLY**, not live trading certification.

For this reason the code remains in a DRAFT PR until exact-head CI and **broker-reconciled, cross-process and crash-recovery integration tests** pass. Do not equate a 10% *observed equity* threshold with a loss guarantee. The previous individual 2% model risk cap is not a substitute for lot/contract/currency-converted broker risk.
