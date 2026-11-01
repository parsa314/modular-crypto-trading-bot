# MASTER-GATE-QUANT-002 — L1 research protocol

Research only; financial PR #145 and release remain BLOCKED. Reference SHA
`db04326c07ab080149509e5d5d52e4639013c70d` / PR #147. The existing
`paper_replay_008.py` and `paper_ledger_008.py` are a separate Paper prototype;
neither is a simulator evidence source, nor is this research simulator an OMS,
broker, financial ledger, order authority, or paper-trading observation.

Fixed before any new simulation: Binance Vision BTCUSDT spot M15 source commit
`cda9e5ff47d0f40cb62b6734c784317d2f15e405`, raw parquet SHA256
`f7bf84ccaff343dbb627c17e6245ae6f2488f940a3ed6867efd262115ea9d401`.
Candidate development window: 2023-01-01 to 2023-02-01 UTC, with earlier bars
only as required for warm-up. This already-observed dataset is NOT an independent
holdout. Existing v0.51 and sealed Kraken research artifacts remain untouched.
Missing 15m observations are enumerated, not filled. Derived H1/H4 bars require
all four/sixteen exact contiguous source bars. No historical first-seen evidence
is available: close-time availability is an explicit optimistic timing assumption.

The frozen candidate contract is `ICH_KIJUN_PULLBACK_V1`. One spot long at most;
entry is the first H1 open strictly after decision, within two hours, and above
the frozen stop. No leverage or averaging down. Starting research NAV 10000 USDT;
risk target at stop at most 0.25% of equity after modelled cost, per-asset exposure
at most 35%, gross at most 70%. Stop-gaps can exceed the risk target. Daily
loss 2% and peak drawdown 5% halt new entry; no automatic hard-halt release.

L1 assumes H1 open/high/low/close, no actual quote, depth, queue or partial fills.
A touched limit is not a proven fill. Same-bar stop+target is AMBIGUOUS; separate
stop-first and target-touch-assumed scenarios are sensitivity bounds, neither an
observed path or guarantee. Gap-through-stop is priced at the adverse bar open.
No assumption replaces a missing observed event with a fake market observation.

Cost scenarios are assumed round-trip basis points; spreads/slippage are price
adjustments and commission is debited separately (no double-counting):

| scenario | fees rt bps | spread rt bps | slippage rt bps | total |
| --- | ---: | ---: | ---: | ---: |
| theoretical | 0 | 0 | 0 | 0 |
| low | 10 | 6 | 8 | 24 |
| mid | 10 | 12 | 14 | 36 |
| stress | 10 | 18 | 22 | 50 |

All components are hypotheses, not historical account fees or observed spreads.
Exchange tick, lot and historical minimum notional are unknown; model output is
therefore conditional and cannot establish executable quantities. Buy-and-hold
is disclosed as a 100%-exposure comparator rather than a risk-matched baseline.
Actual historical execution occurs only after targeted causality/risk tests pass;
statistics are descriptive only, undefined quantities remain N/A, and insufficient
or dependent observations cannot prove an edge. No Walk-Forward/PBO/DSR is invented.
