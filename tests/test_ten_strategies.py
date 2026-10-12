from __future__ import annotations
from dataclasses import replace
from contextlib import closing
from decimal import Decimal,localcontext
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from hashlib import sha256
from statistics import NormalDist
import json,os,tempfile,unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from research_bot.ten_strategies.protocol import TenConfig,IDS
from research_bot.ten_strategies.detectors import detect,higher_context,Zone
from research_bot.ten_strategies.rules import Anchor,entry_flags,entry_anchor,exit_reason,candidate
from research_bot.ten_strategies.replay import empirical_es,decimal,replay_column,build_matrix
from research_bot.ten_strategies.statistics import daily_returns,pbo_cscv,dsr_bailey,diagnostics,rejection_gates
from research_bot.ten_strategies.history import archive_month,collect_archives,download
from research_bot.completion.journal import TradeJournal
from scripts.run_four_systems_research import synthetic_bars

def scenario(n=100):
    f=synthetic_bars(400).iloc[:n].copy()
    for k in ("open","high","low","close"):f[k]=100.
    f["ready"]=True;f["regime"]=True;f["cross"]=False
    for k in ("bos_up","bos_down","break_up","break_down","sweep_low","fvg_new","brooks_buy"):f[k]=False
    f["cloud_bottom"]=0.;f["cloud_top"]=90.;f["kijun"]=0.;f["ema20"]=0.
    f["sweep_low_price"]=90.;f["ob_retest"]=pd.Series([None]*n,dtype=object)
    f["fvg_recent"]=pd.Series([None]*n,dtype=object)
    f["invalidated"]=[()]*n;f["mitigated"]=[()]*n
    f.loc[30,"cross"]=True
    return f

def archive_payload(month):
    p=pd.Period(month,freq="M");start=p.start_time.tz_localize("UTC");end=(p+1).start_time.tz_localize("UTC")
    unit="us" if month>="2025-01" else "ms";factor=1000 if unit=="us" else 1000000;step=3600000000 if unit=="us" else 3600000
    rows=[[str(t.value//factor),"100","102","99","101","1",str(t.value//factor+step-1),"101","1",".5","50",0]
        for t in pd.date_range(start,end,freq="h",inclusive="left")]
    stream=BytesIO()
    with ZipFile(stream,"w") as z:z.writestr("BTCUSDT-1h-"+month+".csv",pd.DataFrame(rows).to_csv(index=False,header=False))
    return stream.getvalue()

class TenProtocolTests(unittest.TestCase):
    def test_decimal_parameters_and_digest(self):
        c=TenConfig();self.assertEqual(len(IDS),10);self.assertEqual(len(c.digest),64)
        self.assertNotEqual(c.digest,replace(c,fee=Decimal(0)).digest)
        for kw in ({"allocation":Decimal(".11")},{"fee":.001},{"fee":Decimal("NaN")},{"max_holding":49},
            {"pbo_blocks":7},{"min_trades":True},{"slippage":Decimal(".01")},{"take":Decimal(".01")}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):TenConfig(**kw)

class TenDetectorTests(unittest.TestCase):
    def test_prefix_future_perturbation_and_zones(self):
        x=synthetic_bars(500);a=detect(x);b=detect(x.iloc[:350])
        pd.testing.assert_frame_equal(a.iloc[:350].reset_index(drop=True),b,check_flags=False)
        y=x.copy();y.loc[350:,["open","high","low","close"]]*=3
        pd.testing.assert_frame_equal(a.iloc[:350].reset_index(drop=True),detect(y).iloc[:350].reset_index(drop=True))
        self.assertTrue(a.attrs["events"])
        for event in a.attrs["events"]:
            self.assertGreaterEqual(pd.Timestamp(event["available_at"]),x.available_at.iloc[event["confirmed"]])
        h=higher_context(x)
        self.assertTrue((h.timestamp.dt.hour%4==0).all())
        self.assertTrue((h.available_at>=h.timestamp).all())
    def test_missing_duplicate_wrong_symbol_and_zero_width(self):
        x=synthetic_bars(400)
        for y in (x.drop(index=10),pd.concat([x,x.iloc[-1:]]),x.assign(symbol="ETH_USDT")):
            with self.assertRaises(ValueError):detect(y)
        with self.assertRaises(ValueError):higher_context(x.iloc[:2])
        flat=x.copy()
        for k in ("open","high","low","close"):flat[k]=100.
        a=detect(flat);self.assertFalse(a.ready.any());self.assertTrue(a.body.isna().all())
    def test_pivot_confirmation_and_fvg_origin(self):
        x=synthetic_bars(400);a=detect(x)
        for event in a.attrs["events"]:
            self.assertLessEqual(event["origin"],event["confirmed"])
            if event["state"]=="CREATED" and event["kind"]=="FVG":
                self.assertEqual(event["confirmed"]-event["origin"],2)
        self.assertFalse(a.ready.iloc[:77].any())
        pd.testing.assert_frame_equal(higher_context(x).iloc[:-1],higher_context(x.iloc[:-4]).iloc[:len(higher_context(x))-1])

class TenRuleTests(unittest.TestCase):
    def test_ten_entry_truth_table_and_non_ichi_s9(self):
        f=scenario();row=f.iloc[30].copy()
        z=Zone("z","OB",1,90.,95.,20,25);gap=Zone("g","FVG",1,96.,98.,28,30)
        for key in ("bos_up","sweep_low","fvg_new","brooks_buy"):row[key]=True
        row["ob_retest"]=z;row["fvg_recent"]=gap
        self.assertEqual(entry_flags(row),(True,)*10)
        row["regime"]=False;self.assertEqual(entry_flags(row),(False,)*8+(True,False))
        row["ready"]=False;self.assertEqual(entry_flags(row),(False,)*10)
        row["ready"]=True;row["regime"]=True
        for n in range(1,11):self.assertIsInstance(entry_anchor(n,row,30),Anchor)
        self.assertEqual(entry_anchor(10,row,30).kind,"BROOKS")
    def test_exit_references_and_candidate_contract(self):
        row=scenario().iloc[30].copy();a=Anchor(101.,"zone","OB",20)
        for n in (3,5,8,9,10):self.assertEqual(exit_reason(n,row,a),"RULE_EXIT")
        row["kijun"]=101.;row["cloud_bottom"]=101.;row["ema20"]=101.;row["break_down"]=True
        for n in range(1,11):self.assertEqual(exit_reason(n,row,a),"RULE_EXIT")
        h=candidate("S1",row,TenConfig(),"ENTER",a)
        self.assertGreater(h.earliest_executable,h.timestamp);self.assertIsNone(h.metadata["expected_return"])
        self.assertFalse(h.metadata["execution_authorized"]);self.assertFalse(hasattr(h,"net_edge"))
        with self.assertRaises(ValueError):candidate("unknown",row,TenConfig(),"ENTER",a)
        with self.assertRaises(ValueError):candidate("S1",row,TenConfig(),"ORDER",a)

class TenReplayTests(unittest.TestCase):
    def test_first_available_open_and_costs_charged_once(self):
        f=scenario(70);r=replay_column(f,"S1",TenConfig())
        self.assertEqual(len(r.returns),70);self.assertEqual(r.hypotheses,1)
        self.assertEqual(r.returns[31],0);self.assertLess(r.returns[32],0)
        self.assertTrue(all(v==0 for v in r.returns[33:]))
        self.assertEqual(r.remaining_base>0,True)
    def test_exact_fee_slippage_take_profit_cashflow(self):
        f=scenario(70);f.loc[33,["open","high","low","close"]]=[100.,111.,99.,110.]
        r=replay_column(f,"S1",TenConfig());t=r.trades[0]
        with localcontext() as ctx:
            ctx.prec=36
            qty=Decimal(1000)/(Decimal("100.02")*Decimal("1.001"))
            proceeds=qty*Decimal("104")*Decimal(".9998")*Decimal(".999")
            self.assertLess(abs(Decimal(t["pnl"])-(proceeds-1000)),Decimal("1e-25"))
        self.assertEqual(t["entry_index"],32);self.assertEqual(t["exit_index"],33);self.assertEqual(t["reason"],"TAKE_PROFIT")
    def test_stop_first_and_gap_stop(self):
        f=scenario(70);f.loc[33,["high","low"]]=[105.,97.]
        t=replay_column(f,"S1",TenConfig()).trades[0]
        self.assertEqual(t["raw_exit"],"98.000");self.assertTrue(t["ambiguous_ohlc_path"])
        f.loc[33,["open","high","low","close"]]=[90.,93.,89.,91.]
        t=replay_column(f,"S1",TenConfig()).trades[0]
        self.assertEqual(t["reason"],"GAP_STOP");self.assertEqual(Decimal(t["raw_exit"]),90)
    def test_exit_latency_max_hold_and_anchor_gap(self):
        f=scenario();f.loc[33,"kijun"]=101.
        t=replay_column(f,"S1",TenConfig()).trades[0]
        self.assertEqual(t["exit_index"],35)
        t=replay_column(scenario(),"S1",TenConfig()).trades[0]
        self.assertEqual(t["exit_index"]-t["entry_index"],48);self.assertEqual(t["reason"],"MAX_HOLDING")
        f=scenario();f["cross"]=False;f.loc[30,"ob_retest"]=Zone("z","OB",1,110.,111.,20,25)
        r=replay_column(f,"S3",TenConfig());self.assertEqual(r.risk_rejections,1);self.assertEqual(r.remaining_base,0)
        self.assertEqual(len(r.returns),len(f))
    def test_replay_journal_is_idempotent_and_no_order_fill(self):
        with closing(TradeJournal(":memory:","ten_fixture")) as j, patch("research_bot.orchestrator.PaperExecutionEngine.execute",side_effect=AssertionError("no order execution")):
            x=synthetic_bars(400);a,report=build_matrix(x,journal=j)
            n=len(j.events());b,again=build_matrix(x,journal=j)
            self.assertEqual(n,len(j.events()));pd.testing.assert_frame_equal(a,b)
            self.assertEqual(report["orders_placed"],0);self.assertEqual(tuple(a.columns),IDS)
            for e in j.events("TEN_HYPOTHESIS"):
                self.assertEqual(e["payload"]["decision"],"NO_TRADE")
            self.assertFalse(any(e["kind"] in ("ORDER","FILL") for e in j.events()))
    def test_prefix_equity_and_nonprofit_negative_control(self):
        f=scenario(100);full=replay_column(f,"S1",TenConfig());prefix=replay_column(f.iloc[:50],"S1",TenConfig())
        self.assertEqual(full.returns[:50],prefix.returns)
        x=synthetic_bars(400)
        for k,value in (("open",100.),("close",100.),("high",101.),("low",99.)):x[k]=value
        matrix,report=build_matrix(x)
        self.assertTrue((matrix==0).all().all())
        self.assertTrue(all(r["closed_trades"]==0 and Decimal(r["net_total_return"])==0 for r in report["results"].values()))
    def test_decimal_es_and_invalid_money(self):
        with localcontext() as ctx:
            ctx.prec=36
            self.assertEqual(empirical_es((Decimal("-.1"),Decimal(0),Decimal(".05")),Decimal(".5")),Decimal(".1")/Decimal("1.5"))
        for value in (True,"NaN","Infinity"):
            with self.assertRaises(ValueError):decimal(value)
        with self.assertRaises(ValueError):empirical_es(())
        with self.assertRaises(ValueError):replay_column(scenario(),"XX",TenConfig())

class TenStatisticsTests(unittest.TestCase):
    def test_cscv_known_order_and_exact_paper_rank(self):
        noise=np.tile([-.001,.001],100)
        x=np.column_stack([noise+.01,noise+.005,noise-.005])
        p=pbo_cscv(x,8,2,10)
        self.assertEqual(p["splits"],70);self.assertEqual(p["pbo"],0.)
        self.assertAlmostEqual(p["logits"][0],np.log(3),12)
        self.assertEqual(pbo_cscv(x[:10])["reason"],"INSUFFICIENT_BLOCK_SUPPORT")
        self.assertEqual(pbo_cscv(x[:,:1])["status"],"NOT_APPLICABLE")
        self.assertEqual(pbo_cscv(np.zeros((500,3)))["reason"],"DEGENERATE_VARIANT_OR_SPLIT")
        with self.assertRaises(ValueError):pbo_cscv(x,7)
    def test_dsr_zero_sharpe_matches_eq2_and_missing_support(self):
        x=np.tile([-.01,.01],550);n=10;sd=.01;normal=NormalDist();gamma=.5772156649015329
        sr0=sd*((1-gamma)*normal.inv_cdf(.9)+gamma*normal.inv_cdf(1-1/(n*np.e)))
        d=dsr_bailey(x,n,sd)
        self.assertAlmostEqual(d["probability"],normal.cdf(-sr0*np.sqrt(len(x)-1)),12)
        self.assertEqual(dsr_bailey(x[:30],10,sd)["status"],"NOT_RUN")
        self.assertEqual(dsr_bailey(x,1,sd)["status"],"NOT_RUN")
        with self.assertRaises(ValueError):dsr_bailey(np.array([np.nan]),10,sd)
    def test_daily_clock_and_no_false_graduation(self):
        ix=pd.date_range("2025-01-01",periods=120,freq="h",tz="UTC")
        m=pd.DataFrame(.001,index=ix,columns=IDS);d=daily_returns(m)
        self.assertEqual(len(d),4);self.assertAlmostEqual(d.iloc[0,0],1.001**24-1)
        stats=diagnostics(m);self.assertTrue(stats["dsr_status"].startswith("NOT_RUN"))
        report={"results":{s:{"closed_trades":0,"net_total_return":"-.001"} for s in IDS}}
        gates=rejection_gates(report,stats,synthetic=True)
        self.assertTrue(all(g["status"]=="FAIL" and not g["paper_candidate"] for g in gates.values()))
        with self.assertRaises(ValueError):daily_returns(m.rename(columns={"S1":"OTHER"}))
        with self.assertRaises(ValueError):daily_returns(m.drop(index=ix[50]))
        with self.assertRaises(ValueError):pbo_cscv(np.full((100,2),np.nan))

    def test_positive_replay_cannot_pass_unbound_gates(self):
        stats={"pbo":{"pbo":0.}}
        report={"results":{s:{"closed_trades":200,"net_total_return":".05"} for s in IDS}}
        gates=rejection_gates(report,stats)
        self.assertTrue(all(g["status"]=="INCONCLUSIVE" and not g["paper_candidate"] for g in gates.values()))
        self.assertTrue(all("R6_PRIOR_SEARCH_AND_DSR_ASSUMPTIONS_UNBOUND" in g["reason_codes"] for g in gates.values()))

class TenArchiveTests(unittest.TestCase):
    def test_checksum_ms_us_closed_boundary_and_complete_range(self):
        payloads={m:archive_payload(m) for m in ("2024-02","2025-01","2025-02")}
        def get(url):
            month=url.split("BTCUSDT-1h-")[1][:7];p=payloads[month]
            return (sha256(p).hexdigest()+"  source.zip").encode() if url.endswith("CHECKSUM") else p
        for m,count,unit in (("2024-02",696,"ms"),("2025-01",744,"us")):
            x,meta=archive_month(m,get);self.assertEqual(len(x),count);self.assertEqual(meta["timestamp_unit"],unit)
            self.assertEqual(x.available_at.iloc[0]-x.timestamp.iloc[0],pd.Timedelta(seconds=2))
        x,meta=collect_archives("2025-01","2025-02",get);self.assertEqual(len(x),1416)
        self.assertFalse(meta["prospective_evidence"])
        with self.assertRaises(ValueError):collect_archives("2025-02","2025-01",get)
        with self.assertRaises(ValueError):collect_archives("2099-01","2099-02",get)
        with self.assertRaises(ValueError):archive_month("2025-01",lambda url:b"BAD" if url.endswith("CHECKSUM") else payloads["2025-01"])
    def test_download_bounded(self):
        with patch("research_bot.ten_strategies.history.urlopen",return_value=BytesIO(b"data")):
            self.assertEqual(download("https://example.test"),b"data")
        with patch("research_bot.ten_strategies.history.urlopen",return_value=BytesIO(b"x"*(16*1024*1024+1))):
            with self.assertRaises(ValueError):download("https://example.test")

@unittest.skipIf(os.environ.get("TEN_READONLY_CHECK")=="1","CI verifies real artifact persistence")
class TenCliTests(unittest.TestCase):
    def test_real_files_and_second_replay(self):
        from scripts.run_ten_strategies_research import run
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);output=p/"report.json";journal=p/"research.sqlite"
            a=run(None,output,journal,True);b=run(None,output,journal,True)
            self.assertEqual(a["journal_events"],b["journal_events"])
            self.assertEqual(a["matrix_sha256"],b["matrix_sha256"])
            self.assertEqual(pd.read_csv(output.with_suffix(".net_returns.csv")).shape[1],11)
            with self.assertRaises(ValueError):run(p/"missing.csv",p/"missing.json",journal)
