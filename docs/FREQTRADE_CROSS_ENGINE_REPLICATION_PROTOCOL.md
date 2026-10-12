# P2: Independent Freqtrade cross-engine replication protocol

Status: DESIGN_ONLY / NOT_RUN. No validated comparison or profitability claim.
Canonical experiment: TEN_RULES_1, S1 Ichimoku Trend, BTC_USDT 1h, RESEARCH_ONLY.

## Methodological distinction

Cross-engine replication tests implementation consistency, not statistical
cross-validation, scientific promotion, or proof of profitable performance.
An independent Freqtrade implementation must not import the canonical strategy's
predictions as a supposed independent comparator.

## Matching the actual frozen S1 strategy

- Exactly the same checksummed source CSV and manifest, time boundaries,
  UTC availability, missing-data policy, symbol, and warmup.
- Reference: docs/ten/ADR-TEN-01_RULES_AND_COSTED_REPLAY.md.
  G = close strictly above BOTH visible/trailing Ichimoku cloud lines AND
  Tenkan >= Kijun. Fresh S1 cross requires previous Tenkan < previous Kijun.
  Exit if close below Kijun OR visible cloud bottom.
- Independent 10,000 USDT cash account; allocation <=10%; max one long;
  no leverage or averaging; stop=2%, take=4%, max holding=48 hours.
  Fee 0.001 and adverse slippage 0.0002 per side are declared scenario costs,
  not observed broker fill costs.
- CRITICAL CLOCK DIFFERENCE: Canonical S1's earliest admissible open is the
  FIRST source bar open STRICTLY LATER than decision.available_at (closed hour
  plus the modeled 2-second availability lag). Freqtrade backtesting normally
  uses next candle open on a close signal. Its default may be one source-bar
  boundary too early; timing must be aligned before comparing trade results.
- Record and compare canonical STOP_FIRST, gap stop, terminal residual
  liquidation valuation and Freqtrade fill assumptions separately.
- Visible cloud and its displacement must be aligned without lookahead.
  Using shifted future Senkou directly may invalidate parity; startup=52 alone
  does not establish the displaced cloud warmup is sufficient.
- Freqtrade per-trade stoploss=-0.15 does not mean max portfolio drawdown=15%
  and is not the registered S1 stop=2%.
- No lookahead-analysis PASS without sufficient triggered signals:
  Freqtrade explicitly warns insufficient signals can yield false negatives.

## Required stages and outputs

1. Export exact canonical S1 features with data timestamps and available_at.
2. Implement an independently defined Freqtrade S1 signal source, using only
   completed/available Ichimoku values.
3. Align every admitted bar and compare Tenkan, Kijun, visible cloud, NaNs,
   fresh crosses and exit predicates. Emit first differing event/time.
4. Run prefix-future perturbation and Freqtrade lookahead-analysis plus
   recursive-analysis, recording which actual signals they assessed.
5. Align execution clock, costs, account cash, exits, and risk policy. If a
   particular Freqtrade fill convention cannot mirror the canonical replay,
   report ENGINE_SEMANTICS_DIFFER rather than claiming matching PnL.
6. Generate feature_signal_parity.json and execution_semantics_diff.json with
   full dataset/protocol/source versions and reproducible seeds.
7. Keep untouched OOS and prospective evidence separate. No same-test tuning.

Acceptance: exact bar/entry/exit Boolean parity for the mutually eligible
observations, with ALL mismatches reported rather than a generic ±5% tolerance.
Execution differences may remain explicitly documented. Agreement between
implementations does not prove alpha.

Status: no independent Freqtrade implementation, no common verified dataset
comparison, no empirical cross-engine report; P2 is NOT_RUN.

## Official sources
- https://www.freqtrade.io/en/stable/lookahead-analysis/
- https://www.freqtrade.io/en/stable/recursive-analysis/
- https://docs.freqtrade.io/en/stable/strategy-customization/
- https://docs.freqtrade.io/en/latest/backtesting/
