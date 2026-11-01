# PARSA-AUTOTRADER V1 implementation matrix

Snapshot: 2026-11-01. Research baseline PR #148 head `359282a6f7bba700abb7269c4f9ce6723513b8df`; financial baseline PR #145 head `81071995fd7c5a27830d92780801cd0783e108d9`; financial changes in PR #149. This is not deployment approval or economic evidence.

Dependency: versioned market data → causal setup → research-only execution scenarios → evidence. The separate financial line is risk → OMS → provisional accounting → reconciliation. These are not integrated or released. PR #149 is stacked on #145; PR #148 is stacked on #147. Preserve both histories; do not use the research simulator as a production ledger.

| Requirement | Existing implementation | File | Gap | Priority | Test / evidence | Status |
|---|---|---|---|---|---|---|
| Point-in-time market data | M15 constituent validation and complete H1/H4 aggregation | `research_bot/quant_002_data.py` | Five-asset verified data, first-seen and durable lineage incomplete | P0 | `tests/test_quant_002_data.py` | PARTIAL |
| Ichimoku contract | Kijun pullback research setup | `research_bot/quant_001_ichimoku.py` | Other setup families and integration incomplete | P0 | `tests/test_quant_001_ichimoku.py` | PARTIAL |
| ICT / SMC / Brooks | Baseline detectors and research adapters | `research_bot/strategies/`, `research_bot/strategy_research_012.py` | Complete causal lifecycle, exits and integration not established; older Brooks lineage requires leakage repair | P0 | `tests/test_strategy_research_012.py` | PARTIAL |
| Regime and labels | Research implementations | `research_bot/strategy_regime_012.py`, `research_bot/strategy_labels_012.py` | Full PIT evaluation and calibrated inference missing | P1 | Corresponding strategy tests | PARTIAL |
| Offline execution | Isolated OHLCV L1 event scenarios | `research_bot/quant_002_l1.py` | Quote/order-book data, defensible partial fills and multi-strategy integration missing | P0 | `tests/test_quant_002_l1.py`, PR #148 CI | PARTIAL |
| Financial ledger | PostgreSQL provisional cumulative principal and unresolved fee liability on financial line | `research_bot/durable_risk.py`, `docs/ADR_PAPER_011_PRINCIPAL_FEE.md` | Direct-store bypass; identified append-only fee events, late/foreign fees and independent reconciliation unresolved | P0 | `tests/test_p0_correctness_postgres.py`, PR #145/#149 CI | BLOCKED |
| Broker precision and identity | Existing adapter work | `research_bot/okx_demo_transport.py` | Exact quantity end-to-end and real account provenance unresolved | P0 | Independent adapter and account tests required | BLOCKED |
| Paper application | Isolated cash baseline and journal on financial line | `research_bot/paper_replay_008.py` | Financial/execution safety gates and autonomous integration not established | P0 | `tests/test_paper_replay_008.py` and financial CI | BLOCKED |
| ML and decision | Existing research and earlier ML/RL studies | `research_bot/` | Accepted provenance, calibration, connected inference and model registry lifecycle unestablished | P1 | Reproducible train/validation/inference tests required | BLOCKED |
| Economic validation | Previously observed January 2023 BTC development scenario | `scripts/quant_002_historical.py`, `docs/MASTER_QUANT_002_PROTOCOL.md` | Independent locked OOS, multi-strategy experiments and uncertainty evidence missing | P1 | PR #148 historical artifact; not an OOS result | INSUFFICIENT_EVIDENCE |
| Release and operations | Existing repository packaging | `pyproject.toml` | V1 commands, runtime integration, operator procedures and independent acceptance incomplete | P1 | AT01–AT18 not yet established | BLOCKED |

Historical CI is specific to its recorded heads and merge refs. The observed sample and hypothetical execution assumptions do not establish an economic edge. No account identity, live/demo order, live release or continuous paper operation is established.
