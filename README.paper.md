# Public-data paper simulator (24/7 VPS/laptop)

**NO authenticated exchange operations; no live orders; no real API keys.**
This is a paper accounting engine, NOT the release of v3. The v3 development
evaluation did not pass its data/validation prerequisite gate, so the frozen
fallback is the ONLY strategy callable here.

## Run

Python 3.11+:

```sh
python -m pip install -r requirements-paper.txt
chmod +x start.sh
./start.sh                         # public Binance read-only live WebSocket
./start.sh config.replay.yaml      # 168-hour offline replay of included verified Parquet
```

The default mode opens only `wss://data-stream.binance.vision:443` combined
**closed 1h klines**. Bootstrap is **public** `GET https://data-api.binance.vision/api/v3/klines`;
never call order APIs, never supply credentials. Clock must be UTC-aware.
A VPS or always-awake laptop must keep the process running. On network failure
the engine deliberately **halts** and writes its persisted kill latch;
**do not configure automatic systemd Restart=always** (it will refuse to
restart until an operator has reconciled state and deliberately reset it).
Start under a process supervisor with `Restart=no` and external alerting
that watches `FATAL_HALTED` in the JSONL log. Manual intervention is required
after any halt. Paper logs are in `logs/YYYY-MM-DD.jsonl` and state is
in `logs/paper_state.json`. Do not put state/log files in Git.

Offline replay includes real historical BTCUSDT 15m candles verified against
all 36 Binance Vision publisher SHA256s. It resamples FOUR actual M15 candles
per closed UTC hour. Missing hours FAIL CLOSED, never interpolated.
The pre-existing parquet excludes actual quote volume; the replay uses
`sum(base_volume * M15 low)` as a mathematically conservative LOWER BOUND
of true quote volume, not a fabricated exact 24-hour volume.
One symbol cannot form a meaningful cross-sectional quintile, hence replay
stays in **cash** intentionally. For multi-asset simulations, use public WS
rather than pretending BTC-only OHLCV has other coins.

## Scheduling and signal

Every **closed hourly candle**: compute momentum 28-day and Wilder RSI(14),
update 24h Binance quote-volume and mark spot NAV. RSI <30 engages the
mean-reversion sleeve; >70 disengages it; intermediate RSI retains state.
Every day at **00:00 UTC**: daily NAV/risk status event. Each **Monday 00:00 UTC**:
produce next-cycle targets for a **weekly rebalance**. Paper fills occur after
the following hourly candle closes (deliberate 1h feed latency). Portfolio
targets combine 50% high 28-day momentum quintile and 50% oversold RSI
state, with per-asset hard cap 10%; surplus remains noninterest-bearing USDT.
The strategy refuses to form quintiles with fewer than **5 liquid assets**.
Only a symbol with 24h official quote volume >5M USDT may be selected.

## Broker model and safety

Spot long-only; 50,000 USDT initial cash by default; never margin or leverage.
Commission 10bps each side, **estimated** slippage 5bps BTC/ETH,
15bps among remaining top-20 liquid symbols, 40bps otherwise; total
nominal side costs 15/25/50bps. These tier proxies are NOT realized spreads,
actual liquidity fills, or guaranteed market impact. A broker simulation
cannot truthfully claim exchange-observed fill slippage. Position limit 10%
of NAV, daily drawdown stop if >5%, peak-to-date stop if >15%,
persistent hard stop after any stale/gapped/malformed data or API disconnect.
Kill stops new trading; it does NOT automatically close existing positions.
The full JSONL daily audit includes signals, simulated fills, risk and NAV.

This prototype is designed for a **single process/machine**; do not run two
instances with the same state file. It does not provide multi-host locks or
crash-consistent financial-grade order reconciliation, and is not suitable for
live-money accounts. Online simulated fills are at next observed hourly close,
not latency-free real tick execution. No empirical profitability is implied.

## CI

`python -m pytest -q tests/test_paper_trading.py` tests cash, 50/50
allocation/caps, fee/slippage, next-bar schedule, protective halts, replay
provenance, and deliberate v3 rejection. No network access in unit tests.
Historical/noncommercial dataset attribution: Binance Vision, CC BY-NC-SA 4.0.
Never use the archive as a commercial licensed data feed.
