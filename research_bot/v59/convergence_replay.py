"""Offline audited V59 -> generic OMS -> local journal failure drill.

All market, model and calibration inputs here are explicitly fixtures. No new
strategy or trained model is introduced; this command proves engineering only.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from ..execution.constitution import RISK_CONSTITUTION_V1 as R
from ..execution.intent import FrozenModelManifest, IntentAuthority, digest
from ..execution.ledger import LiveLedger
from ..execution.oms import OrderManagementSystem
from ..execution.replay_exchange import EngineeringReplayExchange
from ..execution.promotion import PromotionGate
from .contracts import Direction, ModelPrediction, PortfolioState, Regime, SignalCandidate, UncertaintyAssessment
from .orchestrator import V59DecisionOrchestrator
from .production_adapter import adapt_research_decision


def fixture_decision(event='fixture-1', symbol='BTC/USDT'):
    decision = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = decision + timedelta(minutes=5)
    candidate = SignalCandidate(event_id=digest({'fixture_event': event}), strategy_id='C10_09_REGIME_ALIGNED_CONTINUATION',
        strategy_family='CONFLUENCE10', symbol=symbol, venue='fixture', direction=Direction.LONG,
        decision_at=decision, entry_time=entry, entry_price=100., stop_price=99., target_price=102.,
        horizon_bars=12, regime=Regime.TREND_UP, regime_confidence=.8,
        feature_snapshot_id=digest({'fixture_features': event}), data_version=digest({'fixture_data': 1}),
        strategy_version='V59_NATIVE_CAUSAL_1', source_hash=digest({'fixture_source': 1}))
    prediction = ModelPrediction(candidate.event_id, 'LOGISTIC_FIXTURE', 'fixture-1', decision,
        {'TP': .70, 'SL': .20, 'TIMEOUT': .10}, True, digest({'fixture_calibration': 1}), .60, 1.)
    uncertainty = UncertaintyAssessment(candidate.event_id, ('TP',), .9, .9, False, 'FIXTURE_NOT_VALIDATED_COVERAGE')
    portfolio = PortfolioState(entry, 10_000., 10_000., 10_000., 0., {}, (.001,)*30)
    engine = V59DecisionOrchestrator()
    final = engine.evaluate(candidate=candidate, prediction=prediction, uncertainty=uncertainty, portfolio=portfolio)
    return engine, final, candidate, prediction


def fixture_artifacts():
    return tuple((name, json.dumps(value, sort_keys=True, separators=(',', ':')).encode()) for name, value in
                 [('model.bin', {'fixture_model': 1}), ('scaler.json', {'fixture_scaler': 1}),
                  ('feature_schema.json', {'fixture_schema': 1})])


def fixture_manifest(*, promoted=True):
    return FrozenModelManifest(model_id='LOGISTIC_FIXTURE', model_version='fixture-1',
        model_sha256=digest({'fixture_model': 1}), scaler_sha256=digest({'fixture_scaler': 1}),
        feature_schema_sha256=digest({'fixture_schema': 1}), training_dataset_hash=digest({'fixture_data': 1}),
        validation_protocol_hash=digest({'fixture_protocol': 1}),
        git_commit='8d94096d8d9a1d066a30ecabb8134b55581a539f', strategy_version='V59_NATIVE_CAUSAL_1',
        approved_symbols=('BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','DOGE/USDT'),
        approved_timeframes=('1h',), approved_venues=('fixture',), risk_constitution_hash=R.sha256,
        promotion_evidence_hash=digest({'fixture_approval_evidence': 1}), created_at=datetime(2026,1,1,tzinfo=timezone.utc),
        promotion_decision='PROMOTED' if promoted else 'NOT_PROMOTED')


def fixture_promotion_gate(manifest):
    return PromotionGate(frozenset({manifest.sha256}), purpose='ENGINEERING_REPLAY', verified_artifacts=fixture_artifacts())


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    engine, final, candidate, prediction = fixture_decision()
    manifest, authority = fixture_manifest(), IntentAuthority(os.urandom(32))
    sealed = adapt_research_decision(engine=engine, final=final, candidate=candidate, prediction=prediction,
        manifest=manifest, authority=authority, account_id='engineering-account', timeframe='1h', now=candidate.entry_time,
        promotion_gate=fixture_promotion_gate(manifest))
    engine.ledger.write_immutable(output/'decision-ledger.json')
    (output/'risk-constitution.json').write_text(json.dumps(asdict(R), sort_keys=True, indent=2)+'\n')
    (output/'REPLAY_MODEL_MANIFEST.json').write_text(json.dumps(asdict(manifest), default=lambda x:x.isoformat(), sort_keys=True, indent=2)+'\n')
    (output/'intent.json').write_text(json.dumps(sealed.intent.payload(), sort_keys=True, indent=2)+'\n')
    drills = []
    for scenario in ('normal', 'crash-after-submit', 'ambiguous-before-submit'):
        gateway = EngineeringReplayExchange(venue='fixture', quotes={candidate.symbol:
            {'bid': 100., 'ask': 100., 'timestamp': candidate.entry_time}})
        gateway.fail_after_submit = scenario == 'crash-after-submit'
        gateway.fail_before_submit = scenario == 'ambiguous-before-submit'
        identity = OrderManagementSystem.identity('engineering-account', manifest)
        kwargs = dict(gateway=gateway, manifest=manifest, authority=authority, account_id='engineering-account',
                      clock=lambda: candidate.entry_time, recent_returns=(.001,)*30,
                      promotion_gate=fixture_promotion_gate(manifest))
        path = output/(scenario+'.sqlite')
        with LiveLedger(path, identity) as ledger:
            first = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
        gateway.fail_after_submit = gateway.fail_before_submit = False
        with LiveLedger(path, identity) as ledger:
            second = OrderManagementSystem(ledger=ledger, **kwargs).process(sealed)
            state, order = ledger.get_state(), ledger.get_order(sealed.intent.client_order_id)
        if gateway.submit_calls != 1:
            raise RuntimeError('RESTART_DUPLICATE_SUBMISSION')
        drills.append({'scenario': scenario, 'first': first, 'after_restart': second,
                      'submit_calls': gateway.submit_calls, 'order_state': order['state'],
                      'filled': order['result']['filled'], 'cash': state['cash'],
                      'protection_submit_calls': gateway.protection_submit_calls,
                      'protection_state': state['protection'][sealed.intent.client_order_id]['state'],
                      'halt_reason': state['halt_reason']})
    source_root = Path(__file__).parents[1]
    files = list((source_root/'execution').glob('*.py')) + [Path(__file__), Path(__file__).with_name('production_adapter.py')]
    report = {'status': 'ENGINEERING_REPLAY_CONFIRMED', 'evidence_class': 'ENGINEERING_FIXTURE',
              'economic_evidence_countable': False, 'risk_constitution_hash': R.sha256,
              'manifest_hash': manifest.sha256, 'drills': drills, 'private_execution_authorized': False,
              'paper_service_enabled': False, 'protective_exit_integration': 'FAKE_TRANSPORT_CONFIRMED_NATIVE_UNVERIFIED',
              'source_hashes': {str(p.relative_to(source_root.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (output/'report.json').write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+'\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('engineering-replay', 'testnet', 'live'), default='engineering-replay')
    args = parser.parse_args(argv)
    if args.mode != 'engineering-replay':
        print(json.dumps({'status': 'BLOCKED', 'reason': 'PRIVATE_PROMOTION_NOT_IMPLEMENTED', 'execution_authorized': False}))
        return 2
    report = run(args.output)
    print(json.dumps({'status': report['status'], 'drills': len(report['drills']), 'execution_authorized': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
