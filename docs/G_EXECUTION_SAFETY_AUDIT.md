# G — Execution safety audit

Scope: course integration against verified main SHA `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`. Default research flow terminates in NoOrderTransport; course processing rejects injected paper/exchange transports. No model, candidate, passing test, environment token, dashboard action or documentation state grants execution permission.

Public Bitpin access has an allowlisted GET transport and no authentication, POST, credential or real-order implementation. Private reads, refresh/token expiry, deposits/networks and actual order submission/reconciliation are unsupported, not mocked as production features. Hence private token-expiry/order-timeout correctness is not certified. Stop-limit simulation is unsupported. No real exchange connectivity or MT5 account was exercised.

OfflineOrderSimulator is `OFFLINE_REPLAY_NOT_PAPER`. It has only fixture balances and pure state, no socket/HTTP/session objects. Approval requires an external boolean for deterministic offline replay; this is not a transferable live authorization. NoOrderTransport remains the orchestrator sink even after an offline fill. Fill evidence here is synthetic.

Safety evidence includes nonfinite forecast/risk rejection; unknown edge/confidence and missing PIT/FX/tail inputs abstention; deterministic conflict/capital projection through existing RiskEngine; global-in-store immutable event identity; reserved Grid inventory; depth depletion/queue evidence; failed/partial arbitrage halting; restore conservation; and bounded dashboard projections. Candidate-level NO_TRADE records retain input/projection and reason codes.

Limits are unchanged: main DD12%, gross100%, asset35%, turnover50%, spread35bps, slippage25bps, CVaR3.5%. The prompt's 0.25%/70%/5% examples are not silently substituted. No claim is made that all existing repository execution adapters satisfy this course-specific isolation; the legacy explicitly selected execution/testnet paths need their own approval/audit.

Open risks: real public schema evolution; book timestamp/sequence and minimum-notional unknown; shared artifact-store configuration and stale-lock recovery; lack of calibrated historical order-book queue/latency data; unknown model utility; absence of production account reconciliation and authorized real-order lifecycle. These block promotion. Engineering tests do not constitute independent financial-risk approval.
