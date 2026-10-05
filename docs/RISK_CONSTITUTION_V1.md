# Canonical Risk Constitution V1

منبع: `research_bot/execution/constitution.py`؛ alias قدیمی
`v59.config.FinancialConstitution` همین نوع immutable را مصرف می‌کند.

| Field | Fraction | Meaning |
|---|---:|---|
| risk_per_trade | 0.0025 | 0.25% of equity, including sealed round-trip cost in stop sizing |
| max_asset_weight | 0.35 | Single-asset maximum |
| max_gross_exposure | 0.70 | Account gross maximum |
| max_daily_loss | 0.02 | New-exposure kill at 2% daily marked-equity loss |
| drawdown_warning | 0.03 | Warning flag |
| drawdown_kill | 0.05 | New-exposure kill at 5% peak-to-current equity loss |
| max_cvar95 | 0.035 | Historical loss tail gate; no future-loss guarantee |
| min_cash_buffer | 0.05 | Minimum unallocated equity fraction |
| max_turnover_per_step | 0.70 | Per-step notional ceiling |

Version and **all** effective fields are hashed using sorted compact canonical
JSON, numeric fractions and SHA-256. Bool, NaN, Inf, nonnumeric, unknown/duplicate
JSON keys and any relaxed ceiling fail. Raising the minimum cash buffer is
stricter; lowering it is relaxation. Warning must precede kill.

Runtime can use a stricter complete immutable profile. Its effective hash must
match research, manifest, intent and journal; a nominal V1 hash cannot conceal
different runtime limits. Governance and RL configuration expose a derived risk
hash; defaults equal V1. Their stricter warning is capped at 60% of tighter DD.
Models provide data only, never configuration. Frozen Python objects are not a
security sandbox against arbitrary operator code.

V59 Finance enforces 5% DD and allocation/CVaR ceilings. Daily account start and
actual current equity are checked again by OMS because the historical V59
PortfolioState does not carry daily accounting. The offline PPO simulator is
a challenger heuristic, not a promoted controller or proof of daily-risk control.

Execution/equity gaps may overshoot a threshold. Kill is a new-entry veto, not a
promise that realized losses cannot exceed 5%. Permanent safety latches cannot
be cleared by a later clean quote or research PASS.
