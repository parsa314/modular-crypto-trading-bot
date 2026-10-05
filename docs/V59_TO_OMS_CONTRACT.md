# V59 → Production contract

تولیدکننده مورد اعتماد: `v59/production_adapter.py`؛ مصرف‌کننده مستقل:
`execution/oms.py`. اجرای Paper/Testnet/Live در Phase 1 مجاز نیست.

Bridge verifies the chained research ledger, exact candidate/prediction/final
bindings, uncertainty non-abstention, economic/financial PASS, required research
gates enabled, sufficient CVaR history, portfolio timestamp/balance consistency,
shared capacity, manifest creation availability and venue/symbol/timeframe scope.
Registry membership, explicit promotion state, purpose and exact artifact bytes
are mandatory. Default registry is empty even if manifest claims PROMOTED.

`FrozenModelManifest` hashes model, scaler, feature schema, training dataset,
validation protocol, risk constitution, Git commit, strategy version, authorized
scope, promotion evidence, creation time and state. Strict JSON rejects duplicate,
unknown and nonfinite fields. Artifact verification reads three bounded regular
files, checks SHA-256, and never deserializes arbitrary pickle. Fixture manifests
have ENGINEERING_REPLAY purpose; no production artifact is promoted.

`PortfolioDecision` freezes shared-account capacity approval and state/risk hash.
`ProductionDecisionEnvelope` contains the complete research gate payload and
portfolio decision; its hash enters immutable `ApprovedOrderIntent`.
Intent contains account/event, venue/symbol/timeframe, strategy/model, all gate
hashes, portfolio and feature IDs, decision/creation/validity times, approved
notional, entry/stop/target, cost budget, side, LIMIT/IOC and engineering purpose.
No research action is relabeled LIVE. Trusted L0 signs the intent with HMAC;
L2 never receives the signer key. The OMS verifies scope and recalculates limits
against current account state, quote, fees and existing allocations.

`intent_id = SHA256(canonical(event,portfolio,venue,symbol,side,version))`.
`client_order_id = C + SHA256(canonical(account,event))[:30]` is deliberately
stable even if a caller changes size/price/portfolio after reservation: changed
payload cannot buy a second order after restart. No UUID or `str(dict)` hashing.

## Event temporal meanings

| Field | Definition |
|---|---|
| decision_at / timestamp | Closed information cutoff for candidate decision |
| entry_time | Earliest permitted future entry reference; strictly after decision |
| feature_available_at | Availability of causal feature snapshot, at/before decision |
| information_start | Start of event/label information interval, at/before decision |
| event_end_time | Closed-bar TP/SL/TIMEOUT observation end, at/after entry |
| information_end | End of information consumed for outcome, at/after event end |
| label_available_at | Actual/declared outcome publication time, at/after information end |

Native historical labeling uses closed-bar availability as an **assumption**;
it is not an observed prospective publication record. Feature dependency history
is represented by the causal prefix hash, not a future observation. Global event
ID uniqueness is mandatory independent of timestamp or strategy. New native ID
includes causal snapshot/config, venue/symbol/strategy, version, direction,
decision/entry times and data version. Existing historical IDs remain frozen.

## Failures

Missing promotion, scope/hash mismatch, stale decision, audit tamper, uncertainty
abstention, economic/risk/portfolio veto produce no intent. Unknown submit never
retries. The Phase-1 gateway must be exactly the local Fake class; a mode string
or subclass/private client cannot impersonate it.
