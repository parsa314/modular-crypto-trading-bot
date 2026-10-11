import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from research_bot.course.contracts import BookSnapshot, MarketMetadata
from research_bot.course.strategies import (
    CourseStrategy,
    load_strategy_config,
    macd,
    rsi_adjusted,
    rsi_simple,
    strategy_ids,
    validated_candles,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candles(prices):
    prices = np.asarray(prices, dtype=float)
    times = pd.date_range(end=NOW, periods=len(prices), freq='4h')
    return pd.DataFrame({'close_time': times, 'available_at': times,
                         'open': prices, 'high': prices + 1,
                         'low': prices - .5, 'close': prices, 'volume': np.ones(len(prices))})


def book(bid_size='3', ask_size='1'):
    market = MarketMetadata('Bitpin', 'BTC_USDT', 'BTC', 'USDT', Decimal('.01'),
                            Decimal('.001'), Decimal(1), 'USDT')
    return BookSnapshot(market, NOW, NOW, NOW, NOW, 'sequence-1',
                        tuple((Decimal(100-i), Decimal(bid_size)) for i in range(5)),
                        tuple((Decimal(101+i), Decimal(ask_size)) for i in range(5)), 'source-sha')


def generated(sid, frame):
    venue, symbol = ('Bitpin', 'BTC_USDT') if isinstance(frame, BookSnapshot) else ('CoinEx', 'BTCUSDT')
    return CourseStrategy(sid, enabled=True).generate(frame, symbol=symbol, venue=venue, decision_time=NOW)


@pytest.mark.parametrize('sid', strategy_ids())
def test_disabled_default_never_generates(sid):
    assert not CourseStrategy(sid).enabled
    assert CourseStrategy(sid).generate(None, symbol='BTC', venue='CoinEx', decision_time=NOW) == []


@pytest.mark.parametrize('sid,prices,direction', [
    ('sma_crossover', [100]*100+[90]*50+[1000], 1),
    ('bollinger_reversion', [100]*19+[50], 1),
    ('rsi_reversal', list(range(100, 85, -1))+[100], 1),
    ('donchian_breakout', [100]*30+[110], 1),
    ('macd_crossover', [100]*34+[90,110], 1),
    ('technical_confluence', [100]*49+[90,110], 1),
    ('simple_scalper', [100], 1),
    ('rsi_moving_average', [10]*6+[140]+list(np.linspace(130,100,14)), 1),
])
def test_each_candle_rule_positive_fixture(sid, prices, direction):
    result = generated(sid, candles(prices))
    assert len(result) == 1
    candidate = result[0]
    assert candidate.direction == direction
    assert candidate.status == 'RESEARCH_CANDIDATE'
    assert candidate.expected_edge is None and candidate.confidence is None
    assert candidate.earliest_execution_time > NOW
    signal = candidate.to_signal_evidence()
    assert signal.confidence == 0 and signal.expected_return == 0
    assert 'UNESTIMATED_EDGE_OR_CONFIDENCE' in signal.reasons


def test_book_imbalance_positive_negative_standaside_and_stale():
    assert generated('order_book_imbalance', book())[0].direction == 1
    assert generated('order_book_imbalance', book('1','3'))[0].direction == 0
    with pytest.raises(ValueError, match='STALE_BOOK'):
        CourseStrategy('order_book_imbalance', enabled=True).generate(
            book(), symbol='BTC_USDT', venue='Bitpin', decision_time=NOW+timedelta(seconds=11))


def test_donchian_uses_previous_channel_not_current_high():
    frame = candles([100]*30+[110])
    frame.loc[30, 'high'] = 200
    assert generated('donchian_breakout', frame)[0].direction == 1
    frame.loc[30, ['open','close']] = 100
    frame.loc[30, 'low'] = 99
    assert generated('donchian_breakout', frame)[0].direction == 0


def test_rsi_adjusted_matches_independent_finite_weight_formula():
    prices = pd.Series([100, 98, 95, 99, 97, 102, 105, 103, 107, 100, 99, 104, 106, 108, 101.])
    changes = np.r_[0, np.diff(prices)]
    decay = 1-1/14
    weights = decay**np.arange(len(prices)-1,-1,-1)
    gain = np.sum(np.maximum(changes,0)*weights)/np.sum(weights)
    loss = np.sum(np.maximum(-changes,0)*weights)/np.sum(weights)
    expected = 100-100/(1+gain/loss)
    assert rsi_adjusted(prices).iloc[-1] == pytest.approx(expected, rel=1e-12)
    assert rsi_simple(prices).iloc[-1] != pytest.approx(expected)


def test_simple_rsi_windows_and_zero_loss_not_fabricated_neutral():
    assert np.isnan(rsi_simple(pd.Series([100.]*15)).iloc[-1])
    assert rsi_simple(pd.Series(range(100,115))).iloc[-1] == 100
    assert rsi_simple(pd.Series(range(100,85,-1))).iloc[-1] == 0


def test_macd_variants_explicitly_differ_and_prefix_invariance():
    prices = pd.Series([100,99,105,102,98,108.])
    adjusted, _ = macd(prices,12,26,9,price_adjust=True)
    recursive, _ = macd(prices,12,26,9,price_adjust=False)
    assert adjusted.iloc[-1] != pytest.approx(recursive.iloc[-1])
    future = pd.concat([prices, pd.Series([999.,1.])],ignore_index=True)
    full_line, full_signal = macd(future,12,26,9,price_adjust=True)
    line, signal = macd(prices,12,26,9,price_adjust=True)
    pd.testing.assert_series_equal(full_line.iloc[:len(prices)],line)
    pd.testing.assert_series_equal(full_signal.iloc[:len(prices)],signal)
    pd.testing.assert_series_equal(rsi_adjusted(future).iloc[:len(prices)],rsi_adjusted(prices))


@pytest.mark.parametrize('mutation,reason', [
    (lambda f: f.assign(available_at=[NOW+timedelta(seconds=1)]*len(f)), 'POINT_IN_TIME'),
    (lambda f: f.assign(close_time=[NOW]*len(f)), 'DUPLICATE'),
    (lambda f: f.assign(close_time=[NOW.replace(tzinfo=None)]*len(f)), 'NAIVE'),
    (lambda f: f.assign(close=np.inf), 'NUMERIC'),
    (lambda f: f.assign(volume=-1), 'NUMERIC'),
    (lambda f: f.assign(high=1), 'GEOMETRY'),
    (lambda f: f.assign(revision=1), 'REVISED'),
    (lambda f: f.assign(is_closed=False), 'OPEN_CANDLE'),
    (lambda f: f.assign(quality_status='UNKNOWN'), 'QUALITY'),
])
def test_bad_rows_fail_whole_input_without_silent_filtering(mutation, reason):
    with pytest.raises(ValueError, match=reason):
        generated('bollinger_reversion', mutation(candles([100]*20)))


def test_late_received_old_bar_and_future_bar_are_rejected():
    frame = candles([100]*20)
    frame.loc[0,'available_at'] = NOW+timedelta(seconds=1)
    with pytest.raises(ValueError, match='POINT_IN_TIME'):
        generated('bollinger_reversion',frame)
    frame = candles([100]*20)
    frame.loc[19,'close_time'] = NOW+timedelta(hours=4)
    frame.loc[19,'available_at'] = NOW+timedelta(hours=4)
    with pytest.raises(ValueError, match='POINT_IN_TIME'):
        generated('bollinger_reversion',frame)


def test_deterministic_identity_configuration_and_input_immutability():
    frame = candles([100]*19+[50])
    original = frame.copy(deep=True)
    first = generated('bollinger_reversion',frame)[0]
    second = generated('bollinger_reversion',frame)[0]
    assert first == second
    pd.testing.assert_frame_equal(original,frame)
    changed = frame.copy(deep=True)
    changed.loc[0,'volume']=2
    assert generated('bollinger_reversion',changed)[0].event_id != first.event_id
    strategy = CourseStrategy('bollinger_reversion',enabled=True)
    copied = strategy.parameters
    copied['window'] = 1
    assert strategy.parameters['window'] == 20
    assert CourseStrategy('bollinger_reversion').config_hash != strategy.config_hash


def test_warmup_and_staleness_are_not_neutral_forecasts():
    assert generated('sma_crossover',candles([100]*150)) == []
    with pytest.raises(ValueError,match='STALE'):
        validated_candles(candles([100]),NOW+timedelta(hours=5),max_age_seconds=14400)


def test_config_parameter_governance_rejects_bool_as_integer_and_bad_thresholds(tmp_path):
    config = load_strategy_config()
    config['strategies']['sma_crossover']['parameters']['fast']['value'] = True
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='PARAMETER'):
        load_strategy_config(path)
    config = load_strategy_config()
    config['strategies']['rsi_reversal']['parameters']['oversold']['value'] = 90
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='THRESHOLD'):
        load_strategy_config(path)


def test_availability_is_actual_not_decision_timestamp():
    frame = candles([100]*19+[50])
    decision_time = NOW+timedelta(seconds=5)
    candidate = CourseStrategy('bollinger_reversion',enabled=True).generate(
        frame,symbol='BTCUSDT',venue='CoinEx',decision_time=decision_time)[0]
    assert candidate.evidence_available_at == NOW
    assert candidate.source_observed_at == NOW
    stamp = candidate.to_signal_evidence().evidence[0]
    assert stamp.available_at == NOW


@pytest.mark.parametrize('sid,prices',[
    ('rsi_reversal',list(range(100,115))+[99]),
    ('donchian_breakout',[100]*30+[90]),
    ('macd_crossover',[100]*34+[110,90]),
    ('technical_confluence',[100]*49+[110,90]),
])
def test_source_supported_bearish_rules(sid,prices):
    assert generated(sid,candles(prices))[0].direction == -1


def test_long_only_sma_reverse_cross_is_not_invented_short_entry():
    frame = candles([90]*100+[100]*50+[10])
    assert generated('sma_crossover',frame)[0].direction == 0


def test_warmup_parameter_cannot_silently_change_computation(tmp_path):
    config = load_strategy_config()
    config['strategies']['sma_crossover']['parameters']['warmup']['value'] = 1
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='WARMUP'):
        load_strategy_config(path)
