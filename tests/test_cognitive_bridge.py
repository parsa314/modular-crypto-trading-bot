from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import pytest

from research_bot.cognitive_core.bridge import DetectorEvent, Forecast, bridge_signal, load_protocol
from research_bot.contracts import Decision, EvidenceStamp, SignalEvidence
from research_bot.orchestrator import ResearchTradingOrchestrator

T = datetime(2024, 1, 1, 4, tzinfo=timezone.utc)

def event():
    return DetectorEvent(asset='BTC', venue='CoinEx', strategy_id='CC01_S2',
        confirmed_at=T, decision_at=T+timedelta(seconds=1),
        entry_not_before=T+timedelta(hours=4), detector_version='1.0.0',
        snapshot_sha256='a'*64, evidence=(EvidenceStamp('fixture',T,T),),
        reasons=('BOS_CONFIRMED',), fixture=True)

def forecast():
    return Forecast(.02,.001,.001,.8,'b'*64,T)

def test_unscored_uses_canonical_contract_and_abstains():
    x=bridge_signal(event())
    assert isinstance(x,SignalEvidence)
    assert x.decision is Decision.NO_TRADE
    assert x.metadata['forecast_state']=='UNESTIMATED'
    assert x.expected_cost==.0024

def test_scored_uses_canonical_decision_engine():
    x=bridge_signal(event(),forecast())
    assert x.decision is Decision.BUY_CANDIDATE
    assert x.net_alpha==pytest.approx(.0156)

def test_orchestrator_never_executes_bridge():
    class Bomb:
        def execute(self,*a,**k): raise AssertionError('execution forbidden')
    o=ResearchTradingOrchestrator(execution_engine=Bomb())
    result=o.process_detector_event(event(),forecast())
    assert result.fill is None and result.risk is None
    assert result.status=='RESEARCH_EVIDENCE_ONLY'

@pytest.mark.parametrize('field,value',[
    ('asset','FAKE'),('venue','Kraken'),('strategy_id','S2'),
    ('detector_version','2.0.0'),('snapshot_sha256','bad'),
    ('entry_not_before',T),('confirmed_at',T+timedelta(days=1)),
    ('side','SHORT'),('decision_at',T.replace(tzinfo=None)),('evidence',()),
    ('evidence',(EvidenceStamp('future',T,T+timedelta(days=1)),)),
])
def test_invalid_events_fail_closed(field,value):
    with pytest.raises(ValueError):bridge_signal(replace(event(),**{field:value}),forecast())

@pytest.mark.parametrize('field', ['expected_return','risk_penalty','uncertainty_penalty','confidence'])
@pytest.mark.parametrize('value',[math.nan,math.inf,-math.inf])
def test_nonfinite_forecast_rejected(field,value):
    with pytest.raises(ValueError):bridge_signal(event(),replace(forecast(),**{field:value}))

@pytest.mark.parametrize('field,value',[('confidence',1.1),('risk_penalty',-.1),('uncertainty_penalty',-.1),('scorer_sha256','bad'),('available_at',T+timedelta(days=1))])
def test_bad_forecast_rejected(field,value):
    with pytest.raises(ValueError):bridge_signal(event(),replace(forecast(),**{field:value}))

def test_identity_is_stable_and_forecast_independent():
    a=bridge_signal(event(),forecast())
    b=bridge_signal(event(),replace(forecast(),expected_return=-.2))
    assert a.metadata['event_id']==b.metadata['event_id']
    assert bridge_signal(replace(event(),snapshot_sha256='c'*64)).metadata['event_id']!=a.metadata['event_id']

def test_protocol_tampering_rejected(tmp_path):
    import json
    p=tmp_path/'manifest.json'
    x=load_protocol();x['cost_model']['primary_bps']=0
    p.write_text(json.dumps(x))
    with pytest.raises(ValueError):load_protocol(p)

def test_nonfixture_requires_verified_raw_data():
    with pytest.raises(ValueError,match='RAW_DATA'):bridge_signal(replace(event(),fixture=False))

def test_sealed_window_even_fixture_is_denied():
    t=datetime(2026,10,1,tzinfo=timezone.utc)
    e=replace(event(),confirmed_at=t,decision_at=t+timedelta(seconds=1),entry_not_before=t+timedelta(hours=4),evidence=(EvidenceStamp('fixture',t,t),))
    with pytest.raises(ValueError,match='SEALED'):bridge_signal(e)

def test_freeze_references_and_adr_integrity():
    from pathlib import Path
    from hashlib import sha256
    from research_bot.cognitive_core.bridge import FREEZE_SHA
    p=load_protocol()
    assert len(FREEZE_SHA)==40
    assert p['base_sha']=='7db5643f24bb713ba1df2abb9a2989f73d56a8c6'
    assert p['search_history']['complete_global_history'] is False
    assert p['economic_execution_allowed'] is False
    for name,digest in p['canonical_file_sha256'].items():
        assert sha256(Path(name).read_bytes()).hexdigest()==digest
    window=p['future_window']
    start=datetime.fromisoformat(window['start_open'])
    end=datetime.fromisoformat(window['end_exclusive'])
    assert (end-start).total_seconds()/14400==window['bars_per_asset']==900

def test_adr_tampering_rejected(tmp_path):
    from pathlib import Path
    import research_bot.cognitive_core.bridge as module
    original=Path(module.__file__).with_name('manifest.json')
    (tmp_path/'manifest.json').write_bytes(original.read_bytes())
    (tmp_path/'ADR.md').write_text('retuned after outcomes')
    with pytest.raises(ValueError,match='ADR_DIGEST'):load_protocol(tmp_path/'manifest.json')

def test_equivalent_timezones_have_same_identity():
    e=event()
    shifted=replace(e,confirmed_at=e.confirmed_at.astimezone(timezone(timedelta(hours=3,minutes=30))))
    assert bridge_signal(e).metadata['event_id']==bridge_signal(shifted).metadata['event_id']

def test_input_event_rejects_realized_outcome_field():
    with pytest.raises(TypeError):DetectorEvent(**{**event().__dict__,'realized_return':10.0})
