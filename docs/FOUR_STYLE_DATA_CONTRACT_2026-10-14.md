# Data/availability contract (layer 1 of 5)

This PR only creates a fail-closed OHLCV/availability barrier and tests, extracted from PR #127.

**Important:** `timestamp` is candle open and `available_at` indicates completed-bar availability. Values after the evaluation `as_of` are excluded. The contract does not verify that venue data genuinely arrived at that timestamp and does not on its own verify a 15-minute candle close or live network latency.

`test_no_lookahead_bias_future_contamination_ignored` injects absurd future prices; `test_mutated_unavailable_bar_must_not_repaint_history` mutates the latest incomplete/unavailable bar. In both cases the as-of dataset must remain identical. This is a necessary but not sufficient test against future leakage.

CI reports missing-line coverage for the focused subset **and** shows exactly why any full-suite tests are skipped. A skipped test is never silently equated with a pass. No broker orders, training or backtest claims.
