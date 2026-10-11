from dataclasses import FrozenInstanceError
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from research_bot.course.contracts import UnsupportedCapability
from research_bot.course.labels import replay_course_events
from research_bot.course.strategies import CourseStrategy


def bars(prices):
    prices = np.asarray(prices,dtype=float)
    times = pd.date_range('2026-01-01', periods=len(prices),freq='4h',tz='UTC')
    closed = times+pd.Timedelta(hours=4)-pd.Timedelta(milliseconds=1)
    return pd.DataFrame({'open_time':times,'close_time':closed,'available_at':closed,
                         'open':prices,'high':prices*1.001,'low':prices*.999,
                         'close':prices,'volume':np.ones(len(prices))})


def replay(frame,sid='bollinger_reversion',**kwargs):
    return replay_course_events(frame,CourseStrategy(sid,enabled=True),
                                symbol='BTCUSDT',venue='CoinEx',**kwargs)


@pytest.mark.parametrize('sid,signal_prefix',[
    ('sma_crossover',[100]*100+[90]*50+[1000]),
    ('bollinger_reversion',[100]*19+[50]),
    ('rsi_reversal',list(range(100,85,-1))+[100]),
    ('donchian_breakout',[100]*30+[110]),
    ('macd_crossover',[100]*33+[90,110]),
    ('technical_confluence',[100]*49+[90,110]),
])
def test_all_six_source_baselines_next_open_not_signal_close(sid,signal_prefix):
    frame=bars(signal_prefix+[60])
    target=Decimal(60)*(1+Decimal(str(CourseStrategy(sid).parameters['take_profit'])))
    frame.loc[len(frame)-1,'high']=float(target)
    events=replay(frame,sid)
    assert events
    event=events[-1]
    assert event.entry_price == 60
    assert event.entry_time == frame.open_time.iloc[-1]
    assert event.entry_time > event.decision_time
    assert event.outcome == 'TP' and event.exit_price == target
    assert event.round_trip_cost == 0
    assert event.gross_return == event.net_return
    assert event.label_available_at == frame.available_at.iloc[-1]


def test_signal_bar_low_cannot_retroactively_stop_next_open_entry():
    frame=bars([100]*19+[50,60])
    frame.loc[19,'low']=1
    frame.loc[20,'high']=69
    event=replay(frame)[0]
    assert event.entry_price==60 and event.outcome=='TP'


def test_tied_tp_sl_same_bar_is_adverse_stop_first():
    frame=bars([100]*19+[50,60])
    frame.loc[20,'low']=50
    frame.loc[20,'high']=90
    event=replay(frame)[0]
    assert event.outcome=='SL'
    assert event.exit_price == Decimal(60)*Decimal('.95')
    assert event.exit_time == frame.close_time.iloc[-1]


def test_gap_stop_realizes_worse_loss_at_open():
    frame=bars([100]*19+[50,60,40])
    event=replay(frame)[0]
    assert event.outcome=='SL' and event.exit_price==40
    assert event.exit_time == frame.open_time.iloc[-1]
    assert event.gross_return < Decimal('-.05')


def test_bollinger_mean_exit_waits_for_next_open():
    frame=bars([100]*19+[50,60,100,65])
    # Suppress modeled TP so only mean-close condition selects a close.
    # Explicitly configure a large target; still fixed and versioned.
    strategy=CourseStrategy('bollinger_reversion',enabled=True)
    # use a configuration fixture, not mutation of the strategy object's state
    import json
    import tempfile
    from pathlib import Path

    from research_bot.course.strategies import load_strategy_config
    config=load_strategy_config()
    config['strategies']['bollinger_reversion']['parameters']['take_profit']['value']=.9
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'config.json';path.write_text(json.dumps(config))
        strategy=CourseStrategy('bollinger_reversion',path,enabled=True)
        events=replay_course_events(frame,strategy,symbol='BTCUSDT',venue='CoinEx')
    assert events[0].outcome=='INDICATOR_EXIT'
    assert events[0].exit_time == frame.open_time.iloc[-1]
    assert events[0].exit_price==65
    assert events[0].exit_time > frame.available_at.iloc[-2]


def test_delayed_signal_is_not_filled_retrospectively():
    frame=bars([100]*19+[50,60])
    frame.loc[19,'available_at']=frame.open_time.iloc[20]+pd.Timedelta(seconds=1)
    frame.loc[20,'high']=90
    assert replay(frame)==()


def test_unclosed_open_position_is_not_force_closed_at_dataset_boundary():
    assert replay(bars([100]*19+[50,60])) == ()


def test_completed_events_are_identical_under_future_extension():
    original=bars([100]*19+[50,60])
    original.loc[20,'high']=90
    extended=bars([100]*19+[50,60]+[100]*10)
    extended.loc[20,'high']=90
    first=replay(original)[0]
    assert replay(extended)[0]==first
    with pytest.raises(FrozenInstanceError):
        first.exit_price=Decimal(1)


def test_cost_applied_once_and_default_is_gross_for_existing_evaluator():
    frame=bars([100]*19+[50,60]);frame.loc[20,'high']=90
    gross=replay(frame)[0]
    charged=replay(frame,cost_bps=24)[0]
    assert charged.gross_return==gross.gross_return
    assert charged.round_trip_cost==Decimal('.0024')
    assert charged.net_return==gross.gross_return-Decimal('.0024')
    assert gross.round_trip_cost==0


@pytest.mark.parametrize('sid',['simple_scalper','order_book_imbalance','rsi_moving_average'])
def test_ticker_or_book_lifecycle_not_claimed_as_candle_baseline(sid):
    with pytest.raises(UnsupportedCapability):
        replay(bars([100]*20),sid)


def test_requires_real_open_time_and_rejects_overlap_or_future_availability():
    frame=bars([100]*19+[50,60])
    with pytest.raises(ValueError,match='OPEN_TIMESTAMPS'):
        replay(frame.drop(columns='open_time'))
    frame.loc[20,'open_time']=frame.open_time.iloc[19]+pd.Timedelta(seconds=1)
    with pytest.raises(ValueError,match='OVERLAPPING'):
        replay(frame)


def test_short_target_and_gap_returns_have_correct_direction():
    frame=bars([100]*33+[110,90,60])
    frame.loc[35,'low']=57
    event=replay(frame,'macd_crossover')[0]
    assert event.direction==-1 and event.outcome=='TP'
    assert event.entry_price==60 and event.exit_price==57
    assert event.gross_return==Decimal('.05')
    frame=bars([100]*33+[110,90,60,100])
    event=replay(frame,'macd_crossover')[0]
    assert event.outcome=='SL' and event.exit_price==100
    assert event.gross_return < Decimal('-.02')


def test_configured_timeout_is_close_known_then_next_open(tmp_path):
    import json

    from research_bot.course.strategies import load_strategy_config
    config=load_strategy_config()
    config['strategies']['bollinger_reversion']['parameters']['timeout_bars']['value']=2
    path=tmp_path/'timeout.json';path.write_text(json.dumps(config))
    strategy=CourseStrategy('bollinger_reversion',path,enabled=True)
    frame=bars([100]*19+[50,60,60,65])
    event=replay_course_events(frame,strategy,symbol='BTCUSDT',venue='CoinEx')[0]
    assert event.outcome=='TIMEOUT' and event.exit_price==65
    assert event.exit_time==frame.open_time.iloc[-1]
    assert event.bars_held==3


@pytest.mark.parametrize('sid,prices,direction',[
    ('sma_crossover',[100]*100+[90]*50+[1000]+[1]*5,1),
    ('bollinger_reversion',[100]*19+[50,100],1),
    ('rsi_reversal',list(range(100,120)),1),
    ('rsi_reversal',list(range(120,100,-1)),-1),
    ('donchian_breakout',[100]*30+[90],1),
    ('donchian_breakout',[100]*30+[110],-1),
    ('macd_crossover',[100]*49+[110,90],1),
    ('technical_confluence',[100]*49+[110,90],1),
])
def test_all_verified_source_indicator_exit_rules(sid,prices,direction):
    from research_bot.course.labels import _indicator_exit
    assert _indicator_exit(CourseStrategy(sid,enabled=True),bars(prices),direction)


def test_contiguous_real_candle_boundaries_use_first_strictly_available_prefix():
    frame=bars([100]*19+[50,60,60])
    # Real OHLCV: the prior bar closes exactly when the next bar opens.
    frame['close_time']=frame.open_time+pd.Timedelta(hours=4)
    frame['available_at']=frame.close_time+pd.Timedelta(seconds=5)
    frame.loc[21,'high']=90
    events=replay(frame)
    assert len(events)==1
    assert events[0].entry_time==frame.open_time.iloc[21]
    assert events[0].entry_time>events[0].decision_time
    assert events[0].decision_time==frame.available_at.iloc[19]


def test_delayed_timeout_keeps_first_known_time_instead_of_postponing_forever(tmp_path):
    import json

    from research_bot.course.strategies import load_strategy_config
    config = load_strategy_config()
    p = config['strategies']['bollinger_reversion']['parameters']
    p['timeout_bars']['value'] = 1
    p['take_profit']['value'] = .9
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    frame = bars([100]*19+[50]+[60]*5)
    frame['close_time'] = frame.open_time+pd.Timedelta(hours=4)
    frame['available_at'] = frame.close_time+pd.Timedelta(seconds=5)
    events = replay_course_events(frame, CourseStrategy('bollinger_reversion', path, enabled=True),
                                  symbol='BTCUSDT', venue='CoinEx')
    assert events[0].outcome == 'TIMEOUT'
    assert events[0].entry_time == frame.open_time.iloc[21]
    assert events[0].exit_time == frame.open_time.iloc[23]
