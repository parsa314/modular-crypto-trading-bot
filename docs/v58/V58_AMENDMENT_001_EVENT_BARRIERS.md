# V58 Amendment 001 — Decision/Entry Separation and Primary Barriers

**Date:** 2026-09-25  
**Outcome inspection before amendment:** none for V58  
**Training authorization:** false  
**Paper/live execution:** false

## Correction

The freeze-1 schema incorrectly required a realized next-bar entry and its derived stop/target inside the immutable decision-time event. The next open is not known at decision time. V58 therefore stores:

1. an immutable `DecisionEvent` containing the decision timestamp, decision-time ATR and feature identity; and
2. an append-only `EnteredEvent`, created only when the next executable bar opens, containing modeled entry and realized barrier prices.

This is a temporal-contract correction, not a result-driven strategy change.

## Pre-registered primary barrier policy

The same policy is used for ARM_A–ARM_E to avoid giving a feature family a different outcome definition:

- risk distance: `max(1.5 × Wilder ATR14 at decision close, 0.0005 × next-open entry)`;
- target: `3R` from entry;
- horizon: 30 bars including the entry bar;
- same-bar TP/SL: `STOP_FIRST`;
- stop gap: fill at adverse bar open;
- favorable target gap: conservatively fill at target;
- no barrier by bar 30: `TIMEOUT` at that close;
- dataset ends before bar 30: `RIGHT_CENSORED`, never relabelled as TIMEOUT.

Exact intrabar resolution time is unavailable from OHLC data; `resolved_at` is the exit-bar timestamp under the repository bar timestamp convention. MFE/MAE are not claimed by this amendment.

This amendment removes the numeric-barrier blocker only. Exact dataset bytes, holdout identity, five-arm signal definitions and remaining pre-training tests remain blocking.
