from __future__ import annotations
from datetime import datetime,timezone
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError,URLError
import json
import pandas as pd
from research_bot.four_systems.binance_history import collect,convert,validate,public_page,save,read_research_csv,integer

def rows(count=10):
    first=pd.Timestamp("2025-01-01",tz="UTC").value//1000000
    return [[first+3600000*i,"100","102","99","101","10",first+3600000*(i+1)-1,"1010",10,"5","500",0] for i in range(count)]

class BinanceInputTests(unittest.TestCase):
    now=datetime(2026,10,11,tzinfo=timezone.utc)
    def test_inclusive_close_becomes_boundary_without_precision_loss(self):
        x=convert(rows(),as_of=self.now,delay_s=.125)
        self.assertEqual(x.timestamp_close.iloc[0],pd.Timestamp("2025-01-01T01:00:00Z"))
        self.assertEqual(x.available_at.iloc[0]-x.timestamp_close.iloc[0],pd.Timedelta(milliseconds=125))
        self.assertEqual(x.availability_basis.iloc[0],"DECLARED_CLOSE_PLUS_LAG_NOT_OBSERVED")
    def test_invalid_rows_are_not_sorted_or_deduplicated(self):
        for change in (lambda r:r.__setitem__(3,r[2]),lambda r:r.pop(3),lambda r:r.reverse(),
            lambda r:r[0].__setitem__(2,"inf"),lambda r:r[0].__setitem__(5,"nan"),
            lambda r:r[0].__setitem__(5,"-1"),lambda r:r[0].__setitem__(6,0),lambda r:r[0].__setitem__(1,True)):
            r=rows();change(r)
            with self.subTest(change=change),self.assertRaises(ValueError):convert(r,as_of=self.now)
        with self.assertRaises(ValueError):convert(rows(),as_of=datetime(2024,1,1,tzinfo=timezone.utc))
        with self.assertRaises(ValueError):convert([],as_of=self.now)
        for delay in (-1,3600,float('nan'),True):
            with self.assertRaises(ValueError):convert(rows(),as_of=self.now,delay_s=delay)
        for v in (True,1.5):
            with self.assertRaises(ValueError):integer(v)
    def test_pagination_complete_range_current_bar_excluded(self):
        data=rows(1800);pages=[]
        def get(p):
            pages.append(p)
            return [r for r in data if p['startTime']<=r[0]<=p['endTime']][:1000]
        start=pd.Timestamp("2025-01-01",tz="UTC");end=start+pd.Timedelta(hours=1800)
        x,m=collect(start,end,as_of=self.now,getter=get,sleep_s=0)
        self.assertEqual(len(x),1800);self.assertEqual(len(pages),2)
        self.assertFalse(m['prospective_evidence']);self.assertFalse(m['observed_historical_availability_verified'])
        at=(start+pd.Timedelta(hours=3,minutes=15)).to_pydatetime()
        x,_=collect(start,end,as_of=at,getter=get,sleep_s=0)
        self.assertEqual(len(x),3)
    def test_malformed_nonprogress_and_empty_pages_fail(self):
        start=pd.Timestamp("2025-01-01",tz="UTC");end=start+pd.Timedelta(hours=10)
        for response in ([],{},rows(1)+rows(1),[[1]],rows(11)):
            with self.assertRaises(ValueError):collect(start,end,as_of=self.now,getter=lambda p:response,sleep_s=0)
        with self.assertRaises(ValueError):collect(end,start,as_of=self.now)
        with self.assertRaises(ValueError):collect(start,end,as_of=self.now,sleep_s=-1)
        with self.assertRaises(ValueError):collect(start,end,as_of=self.now,delay_s=float('nan'))
    def test_retry_transient_only_and_bounded_responses(self):
        module='research_bot.four_systems.binance_history'
        params={'symbol':'BTCUSDT'}
        with patch(module+'.urlopen',side_effect=[HTTPError('url',429,'limit',{},None),BytesIO(b'[]')]) as get,patch(module+'.time.sleep'):
            self.assertEqual(public_page(params),[]);self.assertEqual(get.call_count,2)
        with patch(module+'.urlopen',side_effect=HTTPError('url',451,'restricted',{},None)) as get,patch(module+'.time.sleep'):
            with self.assertRaises(HTTPError):public_page(params)
            self.assertEqual(get.call_count,1)
        with patch(module+'.urlopen',side_effect=URLError('offline')) as get,patch(module+'.time.sleep'):
            with self.assertRaises(URLError):public_page(params)
            self.assertEqual(get.call_count,3)
        for body in (b'{}',b'x'*(1024*1024+1)):
            with patch(module+'.urlopen',return_value=BytesIO(body)):
                with self.assertRaises(ValueError):public_page(params)
    def test_csv_manifest_replay_and_tampering(self):
        x=convert(rows(),as_of=self.now);meta={'availability_basis':'DECLARED_CLOSE_PLUS_LAG_NOT_OBSERVED','prospective_evidence':False}
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'BTC_USDT_1h_closed.csv'
            first=save(x,meta,p);self.assertEqual(first,save(x,meta,p))
            y=read_research_csv(p)
            self.assertIn('timestamp',y);self.assertNotIn('timestamp_close',y)
            self.assertEqual(y.attrs['availability_basis'],meta['availability_basis'])
            p.write_text(p.read_text().replace(',101,',',100.5,',1))
            with self.assertRaises(ValueError):read_research_csv(p)
            with self.assertRaises(FileExistsError):save(x,meta,p)
    def test_wrong_market_naive_times_conflicts_and_open_schema(self):
        x=convert(rows(),as_of=self.now)
        for field,value in (('symbol','ETH_USDT'),('timestamp_close',pd.Timestamp('2025-01-01'))):
            bad=x.copy();bad[field]=value
            with self.assertRaises(ValueError):validate(bad)
        with self.assertRaises(ValueError):validate(x.drop(columns='volume'))
        bad=x.copy();bad['timestamp']=bad.timestamp_close-pd.Timedelta(hours=1)
        with self.assertRaises(ValueError):validate(bad)
        with self.assertRaises(ValueError):validate(x.rename(columns={'timestamp_close':'open_time'}))

if __name__=='__main__':unittest.main()
