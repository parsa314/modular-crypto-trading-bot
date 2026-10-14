"""Confluence invariants: scoring, opposing-signal veto, and NO_TRADE."""
from datetime import datetime, timezone, timedelta
import pytest
from research_bot.strategies.common import StrategySignal
from research_bot.strategies.confluence import combine, STYLES


AT=datetime(2024,1,3,tzinfo=timezone.utc)


def votes(actions=("BUY","BUY","BUY","HOLD"),scores=(3,3,1,0)):
    return {s:StrategySignal(s,a,n,AT) for s,a,n in zip(STYLES,actions,scores)}


def test_confluence_seven_of_twelve_requires_three_agreeing():
    out=combine(votes(),as_of=AT)
    assert out.action=="BUY"
    assert out.score_0_to_12==7.
    assert out.reason=="RESEARCH_SIGNAL_ONLY_NOT_EXECUTION"


def test_confluence_conflicting_high_scores_rejected():
    x=votes(actions=("BUY","BUY","SELL","HOLD"),scores=(3,3,3,0))
    out=combine(x,as_of=AT)
    assert out.action=="HOLD"
    assert out.reason=="CONFLICT_VETO"


def test_confluence_score_7_not_sufficient_if_only_two_voters():
    x=votes(actions=("SELL","SELL","HOLD","HOLD"), scores=(3,3,0,0))
    assert combine(x,as_of=AT).reason=="INSUFFICIENT_INDEPENDENT_AGREEMENT"


def test_confluence_strict_snapshot_alignment():
    x=votes()
    x["ICT"]=StrategySignal("ICT","BUY",3,AT+timedelta(seconds=1))
    with pytest.raises(ValueError,match="MISALIGNED_SIGNALS"):
        combine(x,as_of=AT+timedelta(seconds=1))


def test_confluence_weight_sanity_and_abstention():
    x=votes()
    with pytest.raises(ValueError,match="WEIGHTS_MUST_SUM"):
        combine(x,as_of=AT,weights={k:.1 for k in x})
    assert combine(votes(actions=("HOLD",)*4,scores=(0,)*4),as_of=AT).action=="HOLD"
