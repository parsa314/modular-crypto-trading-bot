"""Actual causal feature pipeline, fixture model, all gates, durable Fake OMS.

No synthetic fixture is eligible for economic evidence or production promotion.
"""
from dataclasses import replace
from datetime import timedelta
import numpy as np
import pandas as pd

from research_bot.execution.intent import IntentAuthority, digest
from research_bot.execution.ledger import LiveLedger
from research_bot.execution.oms import OrderManagementSystem
from research_bot.execution.replay_exchange import EngineeringReplayExchange
from research_bot.v59.native_features import add_native_features
from research_bot.v59.native_strategy import MODEL_FEATURES
from research_bot.v59.contracts import PortfolioState, UncertaintyAssessment
from research_bot.v59.orchestrator import V59DecisionOrchestrator
from research_bot.v59.convergence_replay import fixture_decision, fixture_manifest, fixture_promotion_gate
from research_bot.v59.production_adapter import adapt_research_decision


class FakeMarketData:
    def closed_bars(self):
        p = 70.+np.arange(120)*.25
        return pd.DataFrame({'timestamp': pd.date_range('2025-12-27', periods=120, freq='h', tz='UTC'),
            'open': p, 'high': p+1, 'low': p-.5, 'close': p+.25, 'volume': 100.+np.arange(120)})


def test_market_to_protected_position_and_evidence_without_network(tmp_path, monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_PHASE1')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    bars = FakeMarketData().closed_bars()
    features = add_native_features(bars)
    snapshot = {name: float(features.iloc[-1][name]) for name in MODEL_FEATURES}
    available_at = bars.timestamp.iloc[-1]+pd.Timedelta(hours=1)
    _, _, candidate, prediction = fixture_decision()
    candidate = replace(candidate, decision_at=available_at.to_pydatetime(),
        entry_time=(available_at+pd.Timedelta(hours=1)).to_pydatetime(),
        event_id=digest({'fixture_market_event': available_at.isoformat(), 'feature_snapshot': snapshot}),
        feature_snapshot_id=digest(snapshot), source_hash=digest(bars.to_json(orient='records', date_format='iso')))
    prediction = replace(prediction, event_id=candidate.event_id, available_at=candidate.decision_at)
    engine = V59DecisionOrchestrator()
    final = engine.evaluate(candidate=candidate, prediction=prediction,
        uncertainty=UncertaintyAssessment(candidate.event_id, ('TP',), .9, .9, False, 'FIXTURE_NOT_SCIENTIFICALLY_VALIDATED'),
        portfolio=PortfolioState(candidate.entry_time, 10000., 10000., 10000., 0., {}, (.001,)*30))
    manifest, authority = fixture_manifest(), IntentAuthority(b'fixture-unique-trusted-key-not-live-32')
    gate = fixture_promotion_gate(manifest)
    sealed = adapt_research_decision(engine=engine, final=final, candidate=candidate, prediction=prediction,
        manifest=manifest, authority=authority, account_id='e2e', timeframe='1h', now=candidate.entry_time, promotion_gate=gate)
    gateway = EngineeringReplayExchange(venue='fixture', quotes={candidate.symbol:
        {'bid': 100., 'ask': 100., 'timestamp': candidate.entry_time}})
    with LiveLedger(tmp_path/'e2e.sqlite', OrderManagementSystem.identity('e2e', manifest)) as ledger:
        result = OrderManagementSystem(gateway=gateway, ledger=ledger, manifest=manifest, authority=authority,
            account_id='e2e', clock=lambda: candidate.entry_time, recent_returns=(.001,)*30, promotion_gate=gate).process(sealed)
        assert result['status'] == 'FILLED' and result['execution_authorized'] is False
        record = ledger.get_state()['protection'][sealed.intent.client_order_id]
        assert record['state'] == 'POSITION_PROTECTED' and record['ack_hash']
        assert ledger.identity['risk_hash'] == sealed.intent.risk_hash == final.risk_constitution_hash
    engine.ledger.write_immutable(tmp_path/'decision-evidence.json')
    assert engine.ledger.verify()
