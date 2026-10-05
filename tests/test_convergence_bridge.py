from dataclasses import asdict, replace
from datetime import timedelta
import json
import subprocess
import sys

import pytest

from research_bot.execution.constitution import RISK_CONSTITUTION_V1 as R
from research_bot.execution.intent import FrozenModelManifest, IntentAuthority
from research_bot.execution.promotion import PromotionGate, verify_model_artifacts
from research_bot.v59.convergence_replay import fixture_decision, fixture_manifest, fixture_artifacts, fixture_promotion_gate
from research_bot.v59.production_adapter import adapt_research_decision
from research_bot.v59.contracts import PortfolioState, UncertaintyAssessment
from research_bot.v59.orchestrator import V59DecisionOrchestrator


def bridge(engine, final, candidate, prediction, manifest, **kwargs):
    return adapt_research_decision(engine=engine, final=final, candidate=candidate, prediction=prediction,
        manifest=manifest, authority=IntentAuthority(b'fixture-authority-key-32-bytes-only'),
        account_id='fixture-account', timeframe='1h', now=candidate.entry_time, **kwargs)


@pytest.mark.parametrize('promoted', [False, True])
def test_default_registry_cannot_be_bypassed_by_manifest_claim(promoted):
    e, f, c, p = fixture_decision()
    with pytest.raises(ValueError, match='PROMOTION_NOT_AUTHORIZED'):
        bridge(e, f, c, p, fixture_manifest(promoted=promoted))


def test_unpromoted_model_cannot_get_intent_even_in_explicit_fake_registry():
    e, f, c, p = fixture_decision(); m = fixture_manifest(promoted=False)
    with pytest.raises(ValueError, match='PROMOTION_NOT_AUTHORIZED'):
        bridge(e, f, c, p, m, promotion_gate=fixture_promotion_gate(m))


@pytest.mark.parametrize('failure', ['uncertainty', 'drawdown', 'economics'])
def test_any_scientific_gate_veto_prevents_intent(failure):
    _, _, c, p = fixture_decision()
    if failure == 'economics':
        p = replace(p, probabilities={'TP': .1, 'SL': .8, 'TIMEOUT': .1})
    u = UncertaintyAssessment(c.event_id, ('TP',), .9, .9, failure == 'uncertainty', 'FIXTURE')
    portfolio = PortfolioState(c.entry_time, 9400. if failure == 'drawdown' else 10000.,
        9400. if failure == 'drawdown' else 10000., 10000., 0., {}, (.001,)*30)
    e = V59DecisionOrchestrator(); f = e.evaluate(candidate=c, prediction=p, uncertainty=u, portfolio=portfolio)
    m = fixture_manifest()
    with pytest.raises(ValueError, match='NOT_ADMITTED'):
        bridge(e, f, c, p, m, promotion_gate=fixture_promotion_gate(m))


def test_explicit_fake_promotion_gives_deterministic_intent_and_risk_binding():
    e, f, c, p = fixture_decision(); m = fixture_manifest()
    a = bridge(e, f, c, p, m, promotion_gate=fixture_promotion_gate(m))
    b = bridge(e, f, c, p, m, promotion_gate=fixture_promotion_gate(m))
    assert a == b
    assert a.intent.risk_hash == f.risk_constitution_hash == m.risk_constitution_hash == R.sha256
    assert a.intent.intent_id == b.intent.intent_id and a.intent.client_order_id == b.intent.client_order_id
    with pytest.raises(RuntimeError, match='duplicate event'):
        e.evaluate(candidate=c, prediction=p, uncertainty=UncertaintyAssessment(c.event_id, ('TP',), .9, .9, False, 'FIXTURE'),
                   portfolio=PortfolioState(c.entry_time, 10000., 10000., 10000., 0., {}, (.001,)*30))


@pytest.mark.parametrize('field,value', [('approved_symbols', ('ETH/USDT',)),
    ('approved_venues', ('binance',)), ('approved_timeframes', ('4h',)), ('model_version', 'wrong')])
def test_manifest_scope_mismatch_is_fail_closed(field, value):
    e, f, c, p = fixture_decision(); m = replace(fixture_manifest(), **{field: value})
    with pytest.raises(ValueError, match='SCOPE_MISMATCH'):
        bridge(e, f, c, p, m, promotion_gate=fixture_promotion_gate(m))


def test_artifacts_verified_as_bytes_without_loading_arbitrary_model(tmp_path):
    m = fixture_manifest()
    for name, payload in fixture_artifacts():
        (tmp_path/name).write_bytes(payload)
    verified = verify_model_artifacts(tmp_path, m, expected_feature_schema_hash=m.feature_schema_sha256)
    assert verified == fixture_artifacts()
    gate = PromotionGate(frozenset({m.sha256}), purpose='ENGINEERING_REPLAY', verified_artifacts=verified)
    assert gate.evaluate(m, risk_hash=R.sha256).approved
    assert not gate.evaluate(m, risk_hash=replace(R, drawdown_kill=.04).sha256).approved
    (tmp_path/'model.bin').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        verify_model_artifacts(tmp_path, m, expected_feature_schema_hash=m.feature_schema_sha256)
    with pytest.raises(ValueError, match='SCHEMA_MISMATCH'):
        verify_model_artifacts(tmp_path, m, expected_feature_schema_hash=m.training_dataset_hash)


def test_manifest_json_is_strict_and_round_trips():
    m = fixture_manifest(); obj = asdict(m); obj['created_at'] = m.created_at.isoformat()
    assert FrozenModelManifest.from_json(json.dumps(obj)).sha256 == m.sha256
    for payload in ['{"model_id":"a","model_id":"b"}', '{"relax_risk":true}', '{"created_at":NaN}']:
        with pytest.raises(ValueError):
            FrozenModelManifest.from_json(payload)


def test_executor_import_does_not_import_research_or_ppo():
    subprocess.run([sys.executable, '-c', "import sys; import research_bot.execution.oms; "
        "assert not any(k.startswith(('research_bot.v59','research_bot.v58','stable_baselines3','torch')) for k in sys.modules)"],
        check=True)
