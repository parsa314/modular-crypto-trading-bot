# Reviewed PAPER migration entry point

Install this reviewed repository (`python -m pip install -e .`), then run:

```bash
python main.py --journal /tmp/demo-migration.sqlite --cycles 80
python main.py --journal /tmp/demo-migration.sqlite --cycles 90
```

You can copy this main.py over the uploaded demo's main.py after preserving the
original archive. It imports the reviewed repository, not the old immediate-fill
or random-HPT execution path. Keep this README beside it.

The run is bounded, synthetic, PAPER only. HPT and real-feed mode are disabled.
Default initial capital is exactly 10000 USDT; IRT is not created. Zero trades
under unchanged admission thresholds are expected and do not imply a profitable
or scientifically validated strategy. Existing legacy journal rows are preserved;
new exact restart state uses `complete_demo_migration_v2` in additive tables.

See [audit and migration](../../docs/COMPLETE_DEMO_BOT_AUDIT_AND_MIGRATION.md) in
the reviewed repository for schema, fee units, limits and remaining work.
