"""Trusted producer: V59 audit -> L0 seal. Execution never imports V59."""
from dataclasses import asdict
from datetime import timedelta

from ..execution.constitution import RISK_CONSTITUTION_V1
from ..execution.intent import ApprovedOrderIntent, FrozenModelManifest, IntentAuthority, digest, utc
from .contracts import Direction, GateStatus
from .hashing import stable_hash
from .hashing import canonical_json
from ..execution.production_contracts import ProductionDecisionEnvelope, PortfolioDecision
from ..execution.promotion import PromotionGate


def adapt_research_decision(*, engine, final, candidate, prediction, manifest: FrozenModelManifest,
                            authority: IntentAuthority, account_id: str, timeframe: str,
                            now, promotion_gate: PromotionGate | None = None, valid_for=timedelta(seconds=30)):
    """Bridge a fully audited decision only for offline engineering execution.

    A PASS research decision is necessary, never sufficient for live authority.
    This producer is trusted application code and must not be called by L2.
    """
    now = utc(now)
    if not candidate.entry_time <= now < candidate.entry_time + valid_for:
        raise ValueError('DECISION_NOT_EXECUTABLE_NOW')
    if not engine.ledger.verify():
        raise ValueError('DECISION_LEDGER_INTEGRITY_FAILURE')
    if (final.status is not GateStatus.PASS or final.action != 'ADMITTED_RESEARCH_SIMULATION'
            or final.event_id != candidate.event_id or prediction.event_id != candidate.event_id
            or candidate.direction is not Direction.LONG):
        raise ValueError('RESEARCH_DECISION_NOT_ADMITTED')
    if (manifest.model_id != prediction.model_id or manifest.model_version != prediction.model_version
            or manifest.strategy_version != candidate.strategy_version
            or candidate.symbol not in manifest.approved_symbols or candidate.venue not in manifest.approved_venues
            or timeframe not in manifest.approved_timeframes):
        raise ValueError('MANIFEST_SCOPE_MISMATCH')
    risk = engine.config.constitution
    if manifest.created_at > now:
        raise ValueError('PROMOTION_MANIFEST_NOT_YET_AVAILABLE')
    approval = (promotion_gate or PromotionGate()).evaluate(manifest, risk_hash=risk.sha256)
    if not approval.approved or approval.purpose != 'ENGINEERING_REPLAY':
        raise ValueError('SCIENTIFIC_PROMOTION_NOT_AUTHORIZED')
    if final.risk_constitution_hash != risk.sha256:
        raise ValueError('RISK_CONSTITUTION_HASH_MISMATCH')
    if not (engine.config.require_ai_gate and engine.config.require_calibration
            and engine.config.require_economic_gate and engine.config.require_financial_gate):
        raise ValueError('RESEARCH_GATE_OVERRIDE_FORBIDDEN')
    input_types = {'SIGNAL_CANDIDATE', 'MODEL_PREDICTION', 'UNCERTAINTY_ASSESSMENT',
                   'ECONOMIC_DECISION', 'FINANCIAL_DECISION', 'FINAL_RESEARCH_DECISION',
                   'PORTFOLIO_STATE', 'EXECUTION_COST_ESTIMATE'}
    records = [r for r in engine.ledger.records if r['payload'].get('event_id') == candidate.event_id
               and r['record_type'] in input_types]
    by_type = {r['record_type']: r['payload'] for r in records}
    if len(by_type) != len(records):
        raise ValueError('DUPLICATE_RESEARCH_AUDIT_RECORD')
    for name, obj in [('SIGNAL_CANDIDATE', candidate), ('MODEL_PREDICTION', prediction),
                      ('FINAL_RESEARCH_DECISION', final)]:
        if stable_hash(by_type.get(name)) != stable_hash(asdict(obj)):
            raise ValueError('DECISION_AUDIT_BINDING_MISMATCH')
    economics, finance = by_type['ECONOMIC_DECISION'], by_type['FINANCIAL_DECISION']
    if (economics['status'] is not GateStatus.PASS or finance['status'] is not GateStatus.PASS
            or final.approved_notional != finance['approved_notional']
            or by_type['UNCERTAINTY_ASSESSMENT']['abstain'] is not False or finance['cvar95'] is None):
        raise ValueError('UPSTREAM_GATE_NOT_PASSED')
    state = by_type['PORTFOLIO_STATE']
    if (state['timestamp'] != candidate.entry_time or state['cash'] < 0
            or abs(sum(state['asset_exposure'].values())-state['gross_exposure']) > 1e-8
            or abs(state['cash']+state['gross_exposure']-state['equity']) > 1e-8):
        raise ValueError('PORTFOLIO_GATE_REJECTED')
    capacity = min(state['cash']-state['equity']*risk.min_cash_buffer,
                   state['equity']*risk.max_gross_exposure-state['gross_exposure'],
                   state['equity']*risk.max_asset_weight-state['asset_exposure'].get(candidate.symbol, 0.))
    portfolio = PortfolioDecision(account_id, candidate.event_id, stable_hash(state), risk.sha256,
        final.approved_notional, final.approved_notional <= capacity+1e-8, 'SHARED_ACCOUNT_CAPACITY_V1')
    if not portfolio.approved:
        raise ValueError('PORTFOLIO_GATE_REJECTED')
    portfolio_id = portfolio.decision_id
    by_type['PORTFOLIO_DECISION'] = asdict(portfolio)
    envelope = ProductionDecisionEnvelope(candidate.event_id, final.audit_hash, risk.sha256,
        manifest.sha256, approval.evidence_hash, portfolio_id, canonical_json(by_type).decode(), now, approval.purpose)
    intent = ApprovedOrderIntent(account_id=account_id, event_id=candidate.event_id,
        symbol=candidate.symbol, venue=candidate.venue, timeframe=timeframe,
        strategy_id=candidate.strategy_id, model_id=prediction.model_id,
        decision_hash=envelope.sha256,
        risk_hash=risk.sha256, manifest_hash=manifest.sha256,
        decision_at=candidate.decision_at, valid_from=candidate.entry_time,
        valid_until=candidate.entry_time + valid_for, approved_notional=final.approved_notional,
        limit_price=candidate.entry_price, stop=candidate.stop_price, target=candidate.target_price,
        round_trip_cost_bps=economics['round_trip_cost_bps'], portfolio_decision_id=portfolio_id,
        feature_snapshot_id=candidate.feature_snapshot_id, created_at=now)
    # Keep the full boundary evidence, not only its digest. Repeated adaptation
    # at the same cutoff is idempotent; later edits require a distinct event.
    for kind, value in [('PORTFOLIO_DECISION', portfolio), ('PRODUCTION_DECISION_ENVELOPE', envelope)]:
        existing = [r for r in engine.ledger.records if r['record_type'] == kind
                    and r['payload'].get('event_id') == candidate.event_id]
        if existing:
            if len(existing) != 1 or stable_hash(existing[0]['payload']) != stable_hash(asdict(value)):
                raise ValueError('EVENT_ALREADY_ENVELOPED_DIFFERENT_DECISION')
        else:
            engine.ledger.append(record_type=kind, payload=value, recorded_at=now)
    return authority.seal(intent)
