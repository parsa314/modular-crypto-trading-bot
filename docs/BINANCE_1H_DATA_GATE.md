# Binance BTC_USDT 1h: today's narrow data gate

Only Binance public spot BTC_USDT hourly bars are ingested. The existing challenger's frozen internal 4h features are derived from these 1h bars; no new venue, strategy bundle, model or risk limit is introduced.

```bash
python -m scripts.prepare_binance_1h --symbol BTCUSDT --years 3 --out data/historical/BTC_USDT_1h_closed.csv
python -m scripts.run_four_systems_research --journal /tmp/four-system.sqlite --output /tmp/four-system-report.json
python -m scripts.run_four_systems_research --csv data/historical/BTC_USDT_1h_closed.csv --journal /tmp/historical-four-system.sqlite --output /tmp/historical-report.json
```

Direct `python scripts/prepare_binance_1h.py` is also supported. --start and --end are UTC dates referring to candle-open dates; --end includes that day's complete bars, never future/current bars. Defaults are three calendar years ending at the latest available complete hour. The one-hour range is continuous and must be fully returned: duplicates, gaps (including exactly 2h), disorder, empty/nonprogress pages, wrong schemas, nonfinite OHLCV, invalid geometry and bad time alignment fail. Nothing is sorted or deduplicated to conceal these errors. There is no --allow-gaps bypass.

Binance's inclusive close_time (:59:59.999) is checked against open_time+1h-1ms, then converted to the exact next UTC hour. ISO output retains fractional available_at precision. No API key or requests dependency is required. Bounded transient retries exclude authentication/geographic 4xx errors. An existing different CSV is not silently overwritten; a SHA256 sidecar identifies the CSV and canonical API page payloads.

Historical klines do not establish original observation latency. available_at is explicitly modeled close+delay, labelled DECLARED_CLOSE_PLUS_LAG_NOT_OBSERVED; real downloaded_at is separate. This is HISTORICAL_BACKFILL, not prospective evidence or verified historical first-seen availability. The reader requires timestamp_close and validates the sidecar digest before renaming to the existing internal timestamp contract.

Reports keep RESEARCH_ONLY=true, PAPER_EXECUTION=false, LIVE_EXECUTION=false, DSR/PBO NOT_RUN. Insufficient fit/calibration support yields FAILED_INSUFFICIENT_DATA with a nonzero exit; invalid/missing CSV yields FAILED_INVALID_DATA. No missing CSV is replaced with synthetic data. The synthetic invocation remains an explicitly labelled engineering regression, not market-performance evidence.

The independent GitHub workflow binance-1h-data-research attempts the public download, runs historical research only after a valid CSV exists and uploads logs/data-gate.json plus available CSV/manifest/reports/journal. Provider blockage is reported as data infrastructure failure, not strategy rejection; artifact/job outcome does not confer graduation. Local sandbox network/write restrictions are separate from provider availability. Economic matrices, DSR/PBO and new training remain deferred until this data gate passes.
