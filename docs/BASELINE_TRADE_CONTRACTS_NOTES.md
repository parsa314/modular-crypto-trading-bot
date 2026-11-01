# Baseline trade contracts — local research draft

Scope: wrap the existing ICT, SMC and Al Brooks baseline detectors; never
modify their original code, weights or thresholds. This is not a replacement
for the requested full advanced engines or a scientifically accepted policy.

Source: `research_bot/strategies/ict_strategy.py` blob
`50a02bc82b4230de98cd4ae4d44a47294ea453b7`, SMC blob
`03047417a3da3ddfb7d9b3e063f27dfca913f672`, Brooks blob
`e22647dca9efb31f186f3fd913bd9702c4352e22` at base
`359282a6f7bba700abb7269c4f9ce6723513b8df`. Existing logic is invoked through
the validated, contiguous and closed `strategy_research_012.validated_prefix`.

The adapter is a separate **research-only hypothesis**: for baseline BUYs it
specifies a stop proxy using (ICT) the past sweep low whose age the detector
returns, (SMC) the existing opposing-candle OB-proxy low, or (Brooks) the
completed signal-bar low. No institutional order flow is inferred. The target
multiple, entry deadline and exit horizon must be supplied through an explicit
caller-declared `ResearchExitPolicy`, with identity and a freeze timestamp; no
new default parameters or claim of independent preregistration is introduced.

For the first open strictly after decision, the stop is invalid if entry <= stop;
the target is later computed from the actual hypothetical entry and the supplied
R multiple. Late entry, invalid/stale data, risk veto and unverified execution
policy cancel the hypothesis. Spot SELL is NO_TRADE for new entries, never a
short. All candidate outputs are `execution_eligible=false`. The ordinal
baseline score is not a probability. Liquidity stress, market regime and the
economic value of the strategies are not inferred from this adapter.

Feed identity, SHA and first-seen are caller assertions, not authenticated here.
Detector arithmetic and OHLC proxies are floating point; this is not a financial
ledger or an exchange precision contract. Inputs stop at the as-of cutoff; no
future bar, next-open price, execution or label is used to form the candidate.
The output is a mapping with matching top-level candidate fields, not an
instance of `SignalCandidate`; its invalidation is a structured object. It is
NOT currently accepted by the Ichimoku-only #148 L1 simulator. Bars which
cannot yet have closed are removed before availability/price validation so an
incomplete future bar cannot mutate a past decision; a malformed timestamp
itself still fails closed because its event-time identity cannot be established.
No broker, OMS, financial store, ML model, configuration, Holdout or external
service was touched. Full multi-timeframe lifecycle, SMC mitigation, ICT event
ordering/decay, advanced Brooks H1/H2, independent scientific comparisons and
source provenance remain outstanding. Synthetic tests do not demonstrate edge.
