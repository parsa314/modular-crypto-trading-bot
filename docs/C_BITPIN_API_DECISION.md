# Bitpin public research data — API decision record

Date: 2026-10-10 UTC. Component classification: **ENGINEERING_EXTENSION**. This document describes public research ingestion, not a trading connection or authorization. No private account was accessed and no order was submitted. Economic or prospective strategy evidence has not been collected by these tests.

## Existing implementation and selective reconstruction

The existing unmerged PR #80 was inspected at immutable commit `3ef544788066b4f6851f8c0261681563c5c95c35`:

| Existing source | Verified Git blob | Finding |
| --- | --- | --- |
| `research_bot/bitpin_public.py` | `dbf5d08a71ba5620ae1b3e48662681ec99e2c9f6` | Isolates public GETs, injects request fixtures and validates positive prices; converts financial data to floats, accepts multiple guessed envelopes, defaults to `.market`, and substitutes local receipt time for a missing exchange timestamp. |
| `research_bot/bitpin_depth.py` | `75ac9416af8bf0b36d885bf5c957c88ada668de8` | Reuses `DepthSnapshot`; inherits the timestamp fallback and lacks verified sequence provenance. |

The useful concept is public ingestion separated from execution with a fixture-injectable port. These files were not blindly merged or represented as already present in the selected base branch. The new `research_bot/course/bitpin.py` binds the course research input contract without changing risk, execution authorization or evidence-ledger authority. It shares `MarketMetadata`, `BookSnapshot` and `UnsupportedCapability` from `course/contracts.py`. Existing production/scientific transports retain their original behavior.

## Current official documentation inspection

Official source: <https://docs.bitpin.ir/v1/>. Inspection used explicit public GETs, including the site's dynamically loaded Docusaurus assets. The HTML pages return a navigation shell, so endpoint specifications were extracted from the corresponding official JavaScript content assets, not inferred from course notebooks or third-party clients.

The current deprecation notice states that some v1 paths move to v2 before **31 Mordad 1405**. Its actual listed paths concern wallet withdrawals, networks, addresses and deposits. None of the four public market-data paths below appears in that table. This observation is not a guarantee of future API availability.

| Capability | Official documented URL | Documented response / limit | Implementation |
| --- | --- | --- | --- |
| Markets | `GET https://api.bitpin.org/api/v1/mkt/markets/` | JSON list: symbol, base, quote, tradable and decimal precisions | Immutable market observations; IRT explicitly TOMAN, IRR explicitly RIAL. |
| Tickers | `GET https://api.bitpin.org/api/v1/mkt/tickers/` | JSON list: symbol, price, low, high, Unix-second timestamp; 80/minute | Decimal values, explicit venue timestamp, duplicate/nonfinite/future timestamp rejection. |
| Order book | `GET https://api.bitpin.org/api/v1/mth/orderbook/{symbol}/` | Object with bids/asks as `[price, quantity]`; at most 20 levels; 60/minute | Immutable observations, strict ordering and spread checks; **DATA_UNAVAILABLE** for decisions requiring exchange timestamp and sequence. |
| Candles | `GET https://api.bitpin.org/api/v1/mkt/candles/?symbol=...&from=...&to=...&res=...` | JSON list OHLCV, `ts` seconds, `time` milliseconds and `resolution`; max 10,000; general public limit 300/minute/IP | Fixed-duration resolutions only; strict returned-resolution agreement, timestamp unit agreement and closed candles. |
| Complete market metadata | No documented minimum-notional field established in inspected page | Precision is not an independently verified tick increment or minimum notional | `UnsupportedCapability`; no invented defaults. |
| Server time | No dedicated official endpoint established | HTTP Date or receipt time is not a verified exchange clock | `UnsupportedCapability`; no synthetic clock synchronization. |
| Authentication/private reads/execution | Outside this read-only adapter | The existence of a course function does not establish a current API contract | `UnsupportedCapability` via `require_capability`; no authentication, credential or order methods. |

The official candle page warns that unsupported `res` values can silently fall back to `30m`. Returned `resolution` is therefore mandatory and must exactly match the request. Implemented fixed durations are `1m, 5m, 15m, 30m, 1h, 3h, 4h, 6h, 1d, 1w`; documented `1M` is explicitly unsupported until calendar-month semantics are implemented. No timeframe is silently approximated.

### Content provenance

Exact raw asset bytes obtained from the official host have these SHA-256 digests:

| Official asset below `https://docs.bitpin.ir/v1/assets/js/` | Topic | SHA-256 |
| --- | --- | --- |
| `fbcf3205.4312cecc.js` | markets | `cb95cb2a864438d5eabb8af23a1e2a29f117d8ca5f5fda2f5ebd84bc0a7b5518` |
| `69847496.2c74aebf.js` | tickers | `26528c55e7d3d86f61ebca4c69d70fd113c0b9299381318b9e7e5c22530cefe8` |
| `31ca1c6b.a81db356.js` | order book | `8f6ac55bf9a1c7a44ecf38ad6b7cb7b4f68065219cc55382d7f4b6b86239fab7` |
| `edbc154c.76804bf2.js` | candles | `8455237925c3cb47c9f6fc115c4a6c4e78a400a2baf54311d1298e3df8a2fcea` |
| `f3a252d5.1ae75669.js` | deprecations | `bd966db13770319eda47fb3a7f59f829591d70dbe491f15208d0c89c0983a50f` |
| `1a032c34.2d88d5b1.js` | general rate limit | `3c44ee6ca2a914651c2db5a893e127378cfeb4461b701d2b787060d182863f4f` |
| `39565daf.c359b338.js` | HTTPS/pagination | `ca0771bd1e69d7ad2dedc6241ccf337a039fa7f4a507db56d7bdce0771c54e41` |

These are documentation-content hashes. They are not exchange-data hashes or proof of a successful live endpoint read. No live market-data smoke test forms part of the offline acceptance suite.

## Design decisions, limits and rollback

1. **Strict public GET allowlist.** HTTPS host is exactly `api.bitpin.org`; four documented paths only; candle query keys restricted; redirects disabled; no environment token read, Authorization header, arbitrary host or private endpoint. Alternative generic authenticated exchange client rejected for this research-only path. Rollback: remove this opt-in adapter, leaving original transports unchanged.
2. **Preserve missing provenance.** The documented book response omits both exchange timestamp and sequence. `BookObservation.exchange_timestamp` and `sequence_id` remain `None`. `to_snapshot()` raises instead of manufacturing a valid `BookSnapshot`. Trade-off: live order-book strategies using this REST schema remain blocked; inspection and artifact hashing are possible. Acceptance requires a separately documented, captured and validated timestamp/sequence source before decision use.
3. **Do not invent executable metadata.** The market page reports decimal precision and discusses `price_increment`, but its example does not establish the complete increment/minimum-notional contract. `get_market_metadata()` remains unsupported. Simulator tests can use explicitly versioned fixture metadata, which cannot be passed off as current exchange constraints.
4. **Current receipt is availability.** Historical candles fetched today are available today. Their opening/closing time is not rewritten into past first-seen availability. Forming candles are excluded; future-use guards remain necessary in the receiving strategy. This source is outside the frozen v0.51 venue/data contract and does not amend prospective evidence.
5. **Retries confined to reads.** Timeout, network failure and HTTP 500/502/503/504 permit bounded GET retry with exponential delay and jitter. HTTP 429 honors seconds/date Retry-After. Long waits open the circuit and defer work rather than ignore the provider deadline. Error bodies and exception details are not emitted. Per-adapter serialization and scheduling obey selected endpoint limits. Cross-process/IP-wide coordination is not implemented; shared-IP deployments must centrally budget the general 300/minute ceiling.
6. **No downstream authorization.** Strategy, model, risk, ledger and execution components are not created or replaced here. An injected fixture transport is a trusted testing dependency, not a security sandbox for arbitrary Python code. API keys cannot add capabilities to this adapter. Authentication/session expiry cannot be claimed tested because authentication is deliberately unavailable.

Financial values use `Decimal` parsed directly from JSON, including numeric OHLC fields. Book snapshots are not sorted, deduplicated or repaired silently. Source hashes bind exact raw public response bytes. No last price or midpoint is claimed to be an executable fill price.

## Fresh local verification

Command executed in `/workspace/course-integration/work` with the installed isolated test tools:

```text
PYTHONPATH=/tmp/course-test-tools python -m ruff check research_bot/course/bitpin.py tests/test_course_bitpin.py
All checks passed!
PYTHONPATH=/tmp/course-test-tools python -m mypy --follow-imports=skip --ignore-missing-imports research_bot/course/bitpin.py
Success: no issues found in 1 source file
PYTHONPATH=/tmp/course-test-tools python -m pytest tests/test_course_bitpin.py -q
............................                                             [100%]
28 passed in 0.06s
```

The mypy result is scoped to the adapter; imports were skipped and it is not a whole-repository type-check claim. Tests cover documented schema rejection, Decimal fidelity, timestamp availability/staleness, missing book provenance, book ordering/invalid sizes, candle closure/timeframe fallback, unsupported calendar resolutions, rate limiting, long Retry-After deferral, timeout retry/circuit opening, redacted errors, unsupported authenticated/order capabilities even with an environment token, host/path/query restrictions, absence of constructor I/O and clock regression. Fixtures use no live keys, balances or orders.

Status: **IMPLEMENTED → TESTED** for documented public fixture ingestion. Integration uses the shared research input contracts; **EVIDENCE_VERIFIED** here means documentation and offline engineering evidence only. Full executable Bitpin metadata and decision-quality order-book ingestion remain **INTEGRATION_BLOCKED** until their missing provider contracts are established. Live connectivity, economic performance, scientific promotion and prospective evidence remain unverified. PAPER/LIVE flags are unchanged.

## Pure microstructure diagnostics added to the same research boundary

`research_bot/course/microstructure.py` is an **ENGINEERING_EXTENSION**, independent of the provider adapter. It consumes the shared `BookSnapshot` and first applies `validate_at(decision_time)`; missing sequence, unknown quality, future availability and stale books all block calculation. Consequently the current timestamp-free/sequence-free Bitpin REST observation cannot be silently passed into this module. Documented prospective snapshots or explicitly labeled simulator fixtures are required.

`calculate_microstructure()` returns immutable Decimal metrics with snapshot identity and units. Definitions for selected levels are:

- Spread: best ask minus best bid. Relative spread divides by the best-quote midpoint; spread bps multiplies by 10,000.
- Top imbalance: `(best_bid_size - best_ask_size) / (best_bid_size + best_ask_size)`.
- Multi-level imbalance: the corresponding ratio of total base quantities on each side.
- Microprice: `(best_ask * best_bid_size + best_bid * best_ask_size) / (best_bid_size + best_ask_size)`.
- Depth-weighted price: sum of `price * base_quantity` across both selected sides divided by their total base quantity. This two-sided descriptive statistic is not an executable price.
- Liquidity concentration: HHI of quote-notional shares over all selected bid and ask levels, `sum((level_notional / total_notional)^2)`.
- Quote age: exact Decimal elapsed seconds since exchange timestamp, including microseconds.

`executable_cost()` accepts a requested **base quantity**, consumes visible asks for BUY and visible bids for SELL, checks positive finite Decimal quantity, step size and requested best-quote minimum notional, and returns observed-depth VWAP, fillable/unfilled base amounts, visible quote notional, positive adverse slippage versus the best executable quote, and distance to the marginal consumed quote. It never extrapolates missing depth. Its status is always `SNAPSHOT_ESTIMATE_NOT_ORDER_OR_FILL`. These values are static estimates before fees, latency, queue effects, other traders and actual broker acceptance.

The implementation deliberately does not call `stateful.quote_conversion()` for BUY because that function accepts a **quote-currency budget**, whereas this estimator accepts a **base quantity**. Conflating those interfaces would produce incorrect buy sizing. Both modules use the same validated book and price/quantity units. No risk engine, portfolio reservation or execution transport was added.

`short_horizon_volatility()` raises `UnsupportedCapability` without verified return history and a sampling protocol. `causal_price_impact()` raises without execution history and a causal impact protocol. Marginal visible-depth distance is not labeled empirical or causal market impact.

Additional fresh verification:

```text
PYTHONPATH=/tmp/course-test-tools python -m ruff check research_bot/course/microstructure.py tests/test_course_microstructure.py
All checks passed!
PYTHONPATH=/tmp/course-test-tools python -m mypy --follow-imports=skip --ignore-missing-imports research_bot/course/microstructure.py
Success: no issues found in 1 source file
PYTHONPATH=/tmp/course-test-tools python -m pytest tests/test_course_microstructure.py -q
......................                                                   [100%]
22 passed in 0.07s
```

The 22 cases include independent hand calculations, one-level selection, explicit level counts, correct mirrored BUY/SELL book consumption, incomplete depth, zero best-level slippage, snapshot provenance and temporal rejection, quantity precision/minimum notional, unsupported historical/causal claims and deterministic replay. No test establishes live liquidity, historical capacity, realized fills or profitable trading. Rollback removes the two opt-in diagnostic files and this documentation addition; canonical engine contracts and scientific records remain unchanged.
