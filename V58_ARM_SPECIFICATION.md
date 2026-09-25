# V58 ARM Specification

Status: `FROZEN_PRE_OUTCOME`  
Code: `research_bot/v58/generators.py`  
Feature schema: `58.1`

All candidates are long-only research observations formed at a closed H4 bar and executable no earlier than the next open. Chikou is excluded.

| Arm | Deterministic candidate definitions |
|---|---|
| ARM_A | S6 prior-20 breakout with positive six-bar EMA200 slope; current high excluded from prior high. |
| ARM_B | B1 causal cloud cross; B2 above-cloud TK continuation; B3 contemporaneous Kijun pullback/rejection. |
| ARM_C | Sweep/reclaim within six bars, displacement, relative-volume gate and bullish MSS within three bars; FVG state recorded. |
| ARM_D | D1 trend continuation, D2 breakout, D3 failed breakout, D4 range reversal, D5 pullback continuation proxies. |
| ARM_E | Same-timestamp presence of A, B, C and D; contributing correlated families retained. |

Identity hashes venue, symbol, timeframe, event close timestamp, Arm, subtype, direction and feature-schema version. Duplicate IDs fail safely.
