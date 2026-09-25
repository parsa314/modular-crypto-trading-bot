from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research_bot.v58.barriers import BarrierPolicy, DecisionEvent, materialize_entry, resolve_barriers
from research_bot.v58.contracts import Direction, StrategyArm, TargetClass
from research_bot.v58.generators import CandidateEvent, candidate_event_id, generate_candidates
from research_bot.v58.targets import OHLCBar


UTC = timezone.utc


def _frame(n=260):
    ts=pd.date_range("2025-01-01",periods=n,freq="4h",tz="UTC")
    c=np.linspace(100,180,n); c[-1]+=10; o=np.r_[c[0],c[:-1]]
    return pd.DataFrame({"timestamp":ts,"open":o,"high":np.maximum(o,c)+.5,
                         "low":np.minimum(o,c)-.5,"close":c,"volume":1000.0})


def test_arm_a_no_current_bar_leakage():
    frame=_frame(); a=generate_candidates(frame,venue="v",symbol="BTC/USDT")
    changed=frame.copy(); changed.loc[len(frame)-1,"high"]=10000
    b=generate_candidates(changed,venue="v",symbol="BTC/USDT")
    a_ids={e.event_id for e in a if e.strategy_arm is StrategyArm.ARM_A}
    b_ids={e.event_id for e in b if e.strategy_arm is StrategyArm.ARM_A}
    assert a_ids == b_ids


def test_arm_b_cloud_causality():
    frame=_frame(); prefix=frame.iloc[:240]
    a=generate_candidates(prefix,venue="v",symbol="BTC/USDT")
    changed=frame.copy(); changed.loc[240:, ["open","high","low","close"]] *= 9
    b=generate_candidates(changed.iloc[:240],venue="v",symbol="BTC/USDT")
    assert [e.event_id for e in a if e.strategy_arm is StrategyArm.ARM_B] == [e.event_id for e in b if e.strategy_arm is StrategyArm.ARM_B]


def test_arm_c_sequence_order():
    events=generate_candidates(_frame(),venue="v",symbol="BTC/USDT")
    for event in (e for e in events if e.strategy_arm is StrategyArm.ARM_C):
        assert event.states[:3] == ("SWEEP_RECLAIM","DISPLACEMENT","MSS")


def test_arm_d_proxy_determinism():
    a=generate_candidates(_frame(),venue="v",symbol="BTC/USDT")
    b=generate_candidates(_frame(),venue="v",symbol="BTC/USDT")
    assert [(e.event_id,e.setup_subtype) for e in a if e.strategy_arm is StrategyArm.ARM_D] == [(e.event_id,e.setup_subtype) for e in b if e.strategy_arm is StrategyArm.ARM_D]


def test_arm_e_component_trace():
    event=CandidateEvent("x"*64,"v","BTC/USDT","4h",datetime(2025,1,1,tzinfo=UTC),
                         StrategyArm.ARM_E,"E1_FOUR_FRAMEWORK_CONFLUENCE",Direction.LONG,"58.1",
                         ("S6","ICHIMOKU","ICT_SMC","BROOKS_PROXY"),("ALL_FOUR_PRESENT","CORRELATED_COMPONENTS"),200)
    assert set(event.contributing_families) == {"S6","ICHIMOKU","ICT_SMC","BROOKS_PROXY"}


def _decision(atr=2.0):
    return DecisionEvent("e"*64,"BTC/USDT","v",StrategyArm.ARM_A,Direction.LONG,
                         datetime(2025,1,1,tzinfo=UTC),atr,"f"*64,"d","c","s")


def test_barrier_uses_event_time_atr():
    bar=OHLCBar(datetime(2025,1,1,4,tzinfo=UTC),100,101,99,100)
    entered=materialize_entry(_decision(2),entry_bar=bar,policy=BarrierPolicy())
    assert entered.stop_price == 98 and entered.target_price == 103


def test_timeout_exact_horizon():
    policy=BarrierPolicy(); entry=OHLCBar(datetime(2025,1,1,4,tzinfo=UTC),100,101,99,100)
    entered=materialize_entry(_decision(),entry_bar=entry,policy=policy)
    bars=[OHLCBar(datetime(2025,1,1,4,tzinfo=UTC)+pd.Timedelta(hours=4*i),100,101,99,100) for i in range(12)]
    out=resolve_barriers(entered,direction=Direction.LONG,bars=bars,policy=policy)
    assert out.target_class is TargetClass.TIMEOUT and out.observed_bars == 12


def test_same_bar_stop_first():
    policy=BarrierPolicy(); bar=OHLCBar(datetime(2025,1,1,4,tzinfo=UTC),100,104,97,100)
    entered=materialize_entry(_decision(),entry_bar=bar,policy=policy)
    assert resolve_barriers(entered,direction=Direction.LONG,bars=[bar],policy=policy).target_class is TargetClass.SL


def test_event_id_reproducibility():
    kw=dict(venue="v",symbol="BTC/USDT",timeframe="4h",event_timestamp=datetime(2025,1,1,tzinfo=UTC),strategy_arm=StrategyArm.ARM_A,setup_subtype="S6_BREAKOUT",direction=Direction.LONG)
    assert candidate_event_id(**kw) == candidate_event_id(**kw)


def test_event_id_uniqueness():
    kw=dict(venue="v",symbol="BTC/USDT",timeframe="4h",event_timestamp=datetime(2025,1,1,tzinfo=UTC),strategy_arm=StrategyArm.ARM_A,direction=Direction.LONG)
    assert candidate_event_id(setup_subtype="S6_BREAKOUT",**kw) != candidate_event_id(setup_subtype="OTHER",**kw)


def test_event_replay_equivalence():
    assert generate_candidates(_frame(),venue="v",symbol="BTC/USDT") == generate_candidates(_frame(),venue="v",symbol="BTC/USDT")
