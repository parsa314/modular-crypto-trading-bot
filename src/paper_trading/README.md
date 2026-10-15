# Local paper trading

Runs a local spot USDT book from Binance **public market data**, or replays the
repository's Parquet file. There is no account connection, order API, API key,
shorting, borrowing, or live execution transport.

From the repository root on Linux/macOS (Windows: WSL), with Python 3.10+:

```bash
./start.sh
```

The first run creates `.venv-paper` and installs the project's `paper` extra.
Subsequent runs reuse it. An existing environment can be selected with
`PAPER_PYTHON=/absolute/path/to/python ./start.sh`; install `pip install -e '.[paper]'`
into that environment first. Keep the machine awake and connected.
The process runs until interrupted, an error occurs, or a risk limit trips.

Offline replay, requiring no network after dependency installation:

```bash
./start.sh --config src/paper_trading/config.replay.yaml
```

This example reads only the selected 2022 dates from the existing BTC Parquet,
with prior data for warmup. It does not open the research holdout or run v3
validation. Replay ends at EOF; it is not a 24/7 market feed. Separate state and
log locations prevent mixing live observations with replay:

```bash
./start.sh --config src/paper_trading/config.replay.yaml \
  --state-file /tmp/paper-demo/state.json --log-dir /tmp/paper-demo/logs \
  --max-events 200
```

## Configuration and strategies

Edit `config.yaml` before starting a new paper ledger. All paths are relative to
the repository root. Changing the strategy, universe, mode, costs, or risk settings
requires a separate `state_file`; existing state rejects a configuration mismatch.

The live default is `auto`: a recorded PASS selects v3, FAIL selects hybrid, and
a missing verdict selects observation with no trades. A recorded failure of the
data prerequisite gate also selects the experimental hybrid paper fallback;
it is not interpreted as measured economic underperformance. No v3 PASS or FAIL is
invented. To exercise an experimental strategy independently, explicitly choose
`hybrid`, as the replay example does. Hybrid uses 50% of target capital for the top quintile of 28-day
momentum and 50% for a Wilder RSI(14) sleeve: enter below 30, retain until above
70. Signals use closed daily bars and are calculated once per UTC hour. Each
asset's combined target is capped at 10%; excess allocation stays in cash.

| `strategy.name` | Rebalance time |
| --- | --- |
| `hybrid` | Monday 00:00 UTC, weekly |
| `momentum` | Every day at 00:00 UTC |
| `observe` | No trades; prices, portfolio and errors are logged |
| `auto` | Recorded v3 PASS → `v3`; recorded FAIL → `hybrid`; missing/unknown verdict → `observe` |
| `v3` | Tuesday 00:00 UTC using the preceding Sunday close, preserving its full Monday delay |

Rebalancing runs on the first observation in the scheduled UTC hour. A missed
hour is not replayed later. The v3 adapter requires an explicit PASS in
`strategy.result_file` and a complete, sourced historical catalog in
`strategy.catalog_file`. Catalog format: `{"complete":true,"rows":[...]}`;
rows require `symbol`, `listed_at`, nullable `delisted_at`, and `source` as in the
research module. Every active catalog symbol must be covered by the configured
feed before v3 can rank the universe. Missing prerequisites stop startup or
execution. This infrastructure does not create a scientific verdict or open any
Test-3 data.

## Costs and risk

Every simulated side pays a 10 bp fee. Cost tiers are recalculated from the
**mean quote volume of the latest 30 fully closed UTC days** in the configured
universe: BTC/ETH are Tier 1; other ranks 1–20 Tier 2; the rest Tier 3. At least
30 consecutive daily volumes are required for each traded asset. Neither
today's market-cap rank nor future volume is used.

For each batch, the live feed fetches public bids/asks, calculates displayed-depth
VWAP for the simulated size and uses the worse of that impact and the tier floor
(5/15/40 bp). Spread and depth impact are measured relative to that snapshot's
mid; the floor is not added a second time. Fees apply to the simulated fill
notional. Quotes older than 10 seconds and insufficient displayed depth stop
execution. Displayed liquidity does not guarantee a real fill or model latency
and queue changes; the broker only simulates it.

Parquet has no historical order book: slippage uses the tier floor and is labeled
`parquet_model_only`. The bundled file also has no quote-volume field, so replay
uses `base_volume × close` as an explicit quote-volume proxy. Live mode uses
Binance's quote volumes. No replay impact is labeled as measured slippage.

- Daily drawdown **above 5%** from the UTC day's equity high, or total drawdown
  **above 15%** from the ledger's equity high, persists a halt and exits.
- A 10% position limit applies after transaction costs. Price appreciation beyond
  the limit triggers a trim on the next market observation.
- New purchases require rolling 24-hour quote volume **strictly above 5M USDT**.
  Risk exits are permitted for an existing illiquid position.
- Missing/stale prices, malformed data, insufficient depth, REST failure,
  unexpected WebSocket closure or a 30-second receive timeout halt the process.
  The connection rotates deliberately after 23 hours to precede Binance's
  24-hour connection limit. An unexpected outage does not reconnect and trade.

A halt stops new activity and leaves the simulated positions in the saved book;
it does not invent liquidation fills during an outage. Restarting the same state
preserves the halt. To begin a fresh paper ledger after reviewing the cause,
choose a new `--state-file` and log directory. Do not delete the old record.

## Persistence and logs

`var/paper/live_state.json` is saved atomically. A process lock prevents two
writers. Restarts retain cash, units, cost basis, P&L, risk highs, strategy state
and the last hourly/rebalance slots. Replayed timestamps are skipped, preventing
the same paper rebalance from being submitted twice.

Every observation writes a full portfolio snapshot to
`logs/paper/YYYY-MM-DD.jsonl`, with signals, fills, cost tier, fee, measured/model
impact, risk values and stop reason. Pending log events are recovered on restart;
a crash between appending and acknowledging can repeat a log event with the
**same sequence number**. Deduplicate by `sequence` when reading a ledger's logs.
Portfolio trades are committed once in the atomic state. State and logs must be
on persistent local storage; they are excluded from Git.

## Run after logout or reboot

Example `/etc/systemd/system/paper-trading.service` (replace user and paths):

```ini
[Unit]
Description=Local paper trading with public Binance data
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=paperbot
WorkingDirectory=/opt/modular-crypto-trading-bot
ExecStart=/opt/modular-crypto-trading-bot/start.sh
KillSignal=SIGINT
Restart=no
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now paper-trading
journalctl -u paper-trading -f
```

Automatic restart is deliberately disabled for data/risk failures. Check the
JSONL halt reason before starting a new ledger. Exit codes: 0 EOF/event limit,
1 data/execution error, 2 risk/persisted halt, 130 operator interrupt.

Run the focused engineering checks with:

```bash
python -m pip install -e '.[paper,dev]'
PYTHONPATH=src:. python -m pytest -q tests/test_paper_trading.py
```

These checks assess infrastructure behavior and accounting; they do not
establish alpha, strategy profitability, or live readiness.
