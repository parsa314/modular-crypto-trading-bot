# V59 Stage 5 — Execution Model Calibration Protocol

Date: 2026-10-02  
Mode: STRICT SCIENTIFIC RESEARCH  
PAPER_EXECUTION = FALSE  
LIVE_EXECUTION = FALSE

## Objective

Turn Stage-3 execution-cost stress assumptions into a calibratable, testable model without pretending that a deterministic fixture is real evidence.

## Calibration sample

Each prospective fill sample records:

```text
sample_id
timestamp
side
reference_mid
fill_price
notional
depth_notional_10bps
spread_bps
fee_bps_per_side
source_hash
```

Derived quantities:

```text
participation = notional / depth_notional_10bps

adverse_fill_bps =
  BUY  -> (fill - mid) / mid
  SELL -> (mid - fill) / mid

residual_bps =
max(0, adverse_fill_bps - spread_bps / 2)
```

## Model

The Stage-5 calibration candidate is intentionally simple:

```text
residual_bps =
base_bps
+ impact_coefficient_bps × sqrt(participation)
```

The split is chronological. The later block is validation data.

No random shuffle is allowed.

## Promotion gate

Default minimums:

- 100 total samples
- 20 validation samples
- validation MAE <= 5 bps
- validation 90th-percentile underprediction <= 8 bps
- validation underprediction rate <= 50%

A calibration report can be:

- INSUFFICIENT_SAMPLE
- CALIBRATION_CANDIDATE_REJECTED
- CALIBRATION_CANDIDATE_PASSED

Only the final state is eligible to produce candidate execution assumptions.

Even then, real-money execution remains disabled.

## Deterministic fixture versus real evidence

`calibration-demo` generates a known synthetic relation with base=2 bps and impact coefficient=8 bps.

Its purpose is to prove that:

- chronological splitting works
- parameter fitting works
- validation metrics work
- report hashing works
- assumption promotion is gated

It does **not** count as real empirical calibration.

## CLI

Deterministic integrity demo:

```bash
python -m research_bot.v59 calibration-demo \
  --output results/v59-stage5
```

Prospective CSV calibration:

```bash
python -m research_bot.v59 calibrate-execution \
  --csv prospective_fills.csv \
  --run-id exec-cal-001 \
  --output results/calibration
```

## Think-tank review

### Data Professor
The split is correctly chronological. Real samples must come from a prospective evidence stream and retain source hashes.

### Financial Engineering PhD
Calibration is now separated from the cost model. Real promotion should compare fitted costs against realized fills and tail underestimation.

### Economics PhD
Transaction-cost behavior can shift across regimes. After enough data, regime-conditioned calibration may be tested as an ablation rather than assumed.

### Computer Engineer
Calibration artifacts are independent from runtime execution. Preserve this separation.

### Python Engineer
The estimator is intentionally simple and dependency-light. Complexity should only increase if validation evidence supports it.

### AI PhD
Now that data and execution protocols exist, a model/strategy tournament can be built without giving deep models an unrealistic cost advantage.

### Mathematics PhD
Chronological validation and explicit underprediction metrics are appropriate. Confidence intervals and block-bootstrap uncertainty should be added to later empirical calibration reports.

### Al Brooks Specialist
Short-horizon breakout strategies should be evaluated under calibrated friction because costs can erase small edges.

### Ichimoku Specialist
Longer-horizon Ichimoku setups may tolerate cost better; strategy comparisons must still use the same execution contract.

### ICT Specialist
Sweep/FVG setups often occur during changing liquidity. Execution cost should be conditioned only after sufficient prospective evidence.

### SMC Specialist
Liquidity concepts remain signal features; fill calibration remains an execution measurement problem.

### Research Writer
Fixture calibration must be labeled engineering validation, not market evidence.

## Joint decision

Next blocker:

```text
MODEL_STRATEGY_TOURNAMENT
```

Real prospective calibration data remains a parallel evidence requirement.

No profitability, paper-trading or live-readiness claim is authorized.
