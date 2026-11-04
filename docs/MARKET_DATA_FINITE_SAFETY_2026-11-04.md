# Market-data finite-value safety patch — 2026-11-04

## Scope and baseline

- Base: `7db5643f24bb713ba1df2abb9a2989f73d56a8c6` (canonical `main`), isolated engineering change.
- This is *not* a scientific v0.51 amendment, alpha improvement, testnet/live permission, or promotion of any pending finance PR.
- Existing experiment SHAs, trade costs, target labels, execution endpoints, portfolio thresholds and model weights remain unchanged.

## Verified code-level failure modes

1. Public ticker `NaN`/infinite quote volume or bid/ask could bypass simple inequality checks or enter rankings.
2. `NaN`/infinite market eligibility fractions were not explicitly rejected, because comparisons with `NaN` return false.
3. Candle-audit logic counted missing values, but could fail to classify infinite OHLCV fields as abnormal.
4. A bad last candle could slip under the permitted overall anomaly fraction and still reach the fast scanner.

## Changes

- Require finite nonnegative quote volume, spread, missing fraction and abnormal fraction at the eligibility gate.
- Convert invalid provider values to unknown instead of treating them as valid market observations.
- Count nonfinite OHLCV values as abnormal in both audit and scanner.
- Reject latest-candle anomalies at the fast-scan boundary rather than silently generating a research candidate from an earlier bar.
- Add synthetic deterministic regression tests; no new market data or financial performance claims.

## Verification required before merge

```bash
python -m pytest -q tests/test_market_data_safety.py tests/test_live_eligibility_v09.py tests/test_fast_scanner_v08.py tests/test_market_discovery_v08.py
python -m pytest -q tests/
```

Review GitHub Actions checks and the PR diff. Do not promote or merge this safety patch merely on the existence of tests; a passing clean CI run and independent review are still required.
