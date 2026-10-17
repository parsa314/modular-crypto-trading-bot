"""MT5 demo-only boundary tests, fake local terminal; no credentials or network."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace as O
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from adapters.mt5_broker import MT5Broker,MT5DemoError


class Terminal:
    ACCOUNT_TRADE_MODE_DEMO=0
    ACCOUNT_TRADE_MODE_REAL=2
    TIMEFRAME_H1=16385
    TRADE_ACTION_DEAL=1
    ORDER_TYPE_BUY=0
    ORDER_TYPE_SELL=1
    ORDER_FILLING_IOC=1
    ORDER_FILLING_FOK=0
    SYMBOL_FILLING_IOC=2
    SYMBOL_FILLING_FOK=1
    TRADE_RETCODE_DONE=10009

    def __init__(self,mode=0):
        self.mode=mode
        self.shutdown_count=0
        self.order_send_count=0
        self.last_pos_args=None
        self.bad_data=False
        self.missing_data=False
        self.mock_account_login=123456

    def initialize(self,**kw):return True
    def last_error(self):return (0,"test stub")
    def shutdown(self):self.shutdown_count+=1
    def terminal_info(self):return O(connected=True,trade_allowed=True)
    def account_info(self):
        return O(trade_mode=self.mode,login=self.mock_account_login,equity=10000,
                 trade_allowed=True,trade_expert=True)
    def symbol_info(self,s):return O(visible=True,volume_step=.01,volume_min=.01,volume_max=100.,filling_mode=2)
    def symbol_select(self,s,visible):return True
    def positions_get(self,**kw):return ()
    def symbol_info_tick(self,s):
        return O(time=datetime.now(timezone.utc).timestamp(),ask=1.1,bid=1.0998)
    def order_calc_profit(self,typ,symbol,volume,entry,stop):
        return -abs(entry-stop)*volume*100000
    def order_check(self,request):return O(retcode=0)
    def order_send(self,request):
        self.order_send_count+=1
        return O(retcode=10009,deal=7654,order=1234)
    def copy_rates_from_pos(self,symbol,tf,index,count):
        self.last_pos_args=(symbol,tf,index,count)
        if self.bad_data:return None
        if self.missing_data:count-=1
        base=1770000000-(1770000000%3600)
        return np.array(
            [(base-(count-1-i)*3600,1.1,1.101,1.099,1.1005,1000,2,0)
             for i in range(count)],
            dtype=[("time","i8"),("open","f8"),("high","f8"),
                   ("low","f8"),("close","f8"),("tick_volume","i8"),
                   ("spread","i8"),("real_volume","i8")])


def test_live_account_refused_and_terminal_shutdown():
    fake=Terminal(mode=2)
    with pytest.raises(MT5DemoError,match="REAL_OR_CONTEST"):
        MT5Broker(client=fake).connect()
    assert fake.shutdown_count==1 and fake.order_send_count==0


def test_contest_refused():
    with pytest.raises(MT5DemoError,match="REAL_OR_CONTEST"):
        MT5Broker(client=Terminal(mode=1)).connect()


def test_h1_100_completed_bars_skip_live_candle():
    fake=Terminal()
    with MT5Broker(client=fake) as bot:
        df=bot.get_historical_data("EURUSD","H1",100)
        assert len(df)==100
        assert fake.last_pos_args==("EURUSD",fake.TIMEFRAME_H1,1,100)
        assert df.timestamp.dt.tz is not None
        assert df.timestamp.is_monotonic_increasing
        assert list(df.columns)==["timestamp","open","high","low","close","tick_volume"]
    assert fake.shutdown_count==1


def test_history_missing_fails():
    fake=Terminal();fake.missing_data=True
    with MT5Broker(client=fake) as bot:
        with pytest.raises(MT5DemoError,match="INSUFFICIENT_OR_INVALID"):
            bot.get_historical_data()


def test_history_broken_fails():
    fake=Terminal();fake.bad_data=True
    with MT5Broker(client=fake) as bot:
        with pytest.raises(MT5DemoError,match="MT5_RATES_FAILED"):
            bot.get_historical_data()


def test_default_never_calls_order_send():
    fake=Terminal()
    with MT5Broker(client=fake) as bot:
        with pytest.raises(MT5DemoError,match="EXECUTION_DISABLED"):
            bot.place_order(symbol="EURUSD",side="BUY",volume=.01,stop_loss=1.095)
    assert fake.order_send_count==0


def test_demo_optin_requires_pinned_login():
    with pytest.raises(ValueError,match="REQUIRES_PINNED_LOGIN"):
        MT5Broker(demo_order_enabled=True)


def test_wrong_login_refuses_connection():
    fake=Terminal()
    with pytest.raises(MT5DemoError,match="WRONG_LOGIN"):
        MT5Broker(client=fake,demo_order_enabled=True,expected_demo_login=765432).connect()
    assert fake.order_send_count==0


def test_risk_limit_refuses_demo_order():
    fake=Terminal()
    with MT5Broker(client=fake,demo_order_enabled=True,expected_demo_login=123456) as bot:
        with pytest.raises(MT5DemoError,match="STOP_RISK_EXCEEDS"):
            bot.place_order(symbol="EURUSD",side="BUY",volume=.1,stop_loss=1.05)
    assert fake.order_send_count==0


def test_demo_optin_is_order_capable_only_after_checks():
    fake=Terminal()
    with MT5Broker(client=fake,demo_order_enabled=True,expected_demo_login=123456) as bot:
        result=bot.place_order(symbol="EURUSD",side="BUY",volume=.01,stop_loss=1.095)
        assert result.status=="FILLED_DEMO" and result.demo_only
    assert fake.order_send_count==1


def test_switch_demo_to_real_refuses_order_send():
    fake=Terminal()
    with MT5Broker(client=fake,demo_order_enabled=True,expected_demo_login=123456) as bot:
        fake.mode=2
        with pytest.raises(MT5DemoError,match="REAL_OR_CONTEST"):
            bot.place_order(symbol="EURUSD",side="BUY",volume=.01,stop_loss=1.095)
    assert fake.order_send_count==0


def test_positions_read_only():
    fake=Terminal()
    with MT5Broker(client=fake) as bot:
        assert bot.get_open_positions()==[]
    assert fake.order_send_count==0
