"""Replay the user requested 2022-24 v3 development windows from frozen daily bars.

This is a rewindowed diagnostic. It is not the original v3 preregistered test.
No 2025-26 prices are loaded or requested here.
"""

import hashlib
import inspect
import json
import sys
from pathlib import Path

import numpy as np,pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import research_bot.cross_sectional_v3 as v3
INPUT = ROOT / 'data/binance_spot_usdt_1d_20211001_20241231.csv.gz'
OUTPUT = ROOT / 'results/v3_train_val.json'
panel = pd.read_csv(INPUT)
panel['date']=pd.to_datetime(panel.pop('open_time_ms'),unit='ms',utc=True)
panel=panel.drop_duplicates(['symbol','date']).sort_values(['symbol','date']).reset_index(drop=True)
# Treat gaps in a venue symbol's traded history as distinct listing epochs.
# Old holdings are written off at the first untradeable day; no gap is filled.
for sym, group in panel.groupby('symbol', sort=False):
    times=group.date.sort_values()
    gaps=times.diff().dt.days
    if gaps.gt(1).any():
        boundary=times.loc[gaps.gt(1)].iloc[0]
        panel.loc[(panel.symbol==sym)&(panel.date<boundary),'symbol']=sym[:-4]+'OLDUSDT'

range_by_symbol=panel.groupby('symbol').date.agg(['min','max'])
last=pd.Timestamp('2024-12-31',tz='UTC')
cat=pd.DataFrame({'symbol':range_by_symbol.index,'listed_at':range_by_symbol['min'].values,'delisted_at':[pd.NaT if d>=last else d+pd.Timedelta(days=1) for d in range_by_symbol['max']],'source':'Binance spot daily API observations, selected from historical S3 symbol inventory'})
cat['listed_at']=pd.to_datetime(cat.listed_at,utc=True)
cat['delisted_at']=pd.to_datetime(cat.delisted_at,utc=True)
# The owner explicitly redefined 2024 as development Validation. Keep Test-3
# sealed by ending this process at 2025-01-01.
v3.DEVELOPMENT_END=pd.Timestamp('2025-01-01',tz='UTC')
old_panel=v3.prepare_panel;old_cat=v3.prepare_catalog
pc={};cc={}
def prep_panel(arg):
 key=id(arg)
 if key not in pc:pc[key]=old_panel(arg)
 return pc[key]
def prep_cat(arg,*,complete):
 key=(id(arg),complete)
 if key not in cc:cc[key]=old_cat(arg,complete=complete)
 return cc[key]
v3.prepare_panel=prep_panel;v3.prepare_catalog=prep_cat
# An order for a symbol with no execution-day bar cannot fill. Leave its target weight in cash.
src=inspect.getsource(v3.simulate_development)
old_line = '        symbols = set(holdings[name]) | set(target)'
assert src.count(old_line) == 1
src=src.replace(old_line, '        target = {s: w for s, w in target.items() if (s, day) in bars.index}\n' + old_line)
exec(src,v3.__dict__)


def metrics(result):
 e=result.equity;r=e.strategy.pct_change().fillna(e.strategy.iloc[0]/50_000-1)
 std=r.std(ddof=1)
 w=e.strategy.resample('W-SUN').last().pct_change().dropna()
 gains=w[w>0].sum();loss=-w[w<0].sum()
 o=result.orders[result.orders.book=='strategy']
 return {'Sharpe':float(np.sqrt(365)*r.mean()/std) if std>0 else None,'Sortino':float(np.sqrt(365)*r.mean()/np.sqrt(np.mean(np.minimum(r,0)**2))) if np.any(r<0) else None,'MaxDD':float((e.strategy/e.strategy.cummax()-1).min()),'Profit_Factor':float(gains/loss) if loss>0 else None,'Win_Rate':float((w>0).mean()) if len(w) else None,'Trade_Count':int((o.side!='writeoff').sum()),'Net_Return':float(e.strategy.iloc[-1]/50_000-1),'BTC_Return':float(e.btc.iloc[-1]/50_000-1),'Equal_Weight_Return':float(e.equal.iloc[-1]/50_000-1),'Week_Count':len(w),'Universe_N_Min':min(len(d.universe) for d in result.decisions),'Universe_N_Max':max(len(d.universe) for d in result.decisions)}
measured = {}
for name,start,end in [('train','2022-01-01','2024-01-01'),('val','2024-01-01','2025-01-01')]:
 result=v3.simulate_development(panel,cat,start=pd.Timestamp(start,tz='UTC'),end=pd.Timestamp(end,tz='UTC'),catalog_complete=True)
 measured[name]=metrics(result)
 print(name, 'Sharpe', measured[name]['Sharpe'], flush=True)

val=measured['val']
out={
 'strategy':'v3_cross_sectional_momentum',
 'scope':'owner_requested_rewindowed_development_diagnostic',
 'train_window':'2022-01-01/2023-12-31',
 'val_window':'2024-01-01/2024-12-31',
 'data_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
 'data_rows':len(panel),
 'historical_symbols_after_epoch_split':int(panel.symbol.nunique()),
 'execution_adjustments':['listing_gap_as_separate_epoch_with_old_position_written_off','missing_execution_open_cancels_order_to_cash'],
 'sharpe_train':measured['train']['Sharpe'],
 'sharpe_val':val['Sharpe'],
 'sortino_val':val['Sortino'],
 'maxdd_val':val['MaxDD'],
 'profit_factor_val':val['Profit_Factor'],
 'win_rate_val':val['Win_Rate'],
 'num_trades_val':val['Trade_Count'],
 'return_val':val['Net_Return'],
 'return_btc_val':val['BTC_Return'],
 'return_ew_val':val['Equal_Weight_Return'],
 'verdict':'PASS' if val['Sharpe']>0.6 and val['Net_Return']>val['BTC_Return'] else 'FAIL',
 'test3_opened':False,
}
OUTPUT.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
