"""Replay the user requested v4/v5 Train and Validation trials only.

The 2025-26 Test-3 prices are never loaded by this script.
"""

import hashlib
import json
from pathlib import Path

import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'data/binance_spot_usdt_1d_20211001_20241231.csv.gz'
p=pd.read_csv(INPUT)
p['date']=pd.to_datetime(p.pop('open_time_ms'),unit='ms',utc=True)
p=p.drop_duplicates(['symbol','date']).sort_values(['symbol','date']).reset_index(drop=True)
for sym,g in p.groupby('symbol',sort=False):
 t=g.date.sort_values();gap=t.diff().dt.days
 if gap.gt(1).any():
  cut=t.loc[gap.gt(1)].iloc[0]
  p.loc[(p.symbol==sym)&(p.date<cut),'symbol']=sym[:-4]+'OLDUSDT'
idx=pd.date_range('2021-10-01','2024-12-31',tz='UTC',freq='D')
fields={c:p.pivot(index='date',columns='symbol',values=c).reindex(idx) for c in ['open','high','low','close','quote_volume']}
O,H,L,C,V=(fields[x] for x in ['open','high','low','close','quote_volume'])
mean30=V.rolling(30,min_periods=30).mean()
prev=C.shift(1)
tr=pd.DataFrame(np.maximum.reduce([H.sub(L).to_numpy(),H.sub(prev).abs().to_numpy(),L.sub(prev).abs().to_numpy()]),index=idx,columns=C.columns)
atr=tr.rolling(14,min_periods=14).mean()
hi55=H.shift(1).rolling(55,min_periods=55).max()
lo20=L.shift(1).rolling(20,min_periods=20).min()
delta=C.diff();gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
rsi=100-100/(1+gain/loss.replace(0,np.nan))

def nav(cash,shares,day,col):
 z=cash
 for s,q in shares.items():
  px=fields[col].at[day,s]
  if not np.isfinite(px):raise ValueError(f'MISSING_HELD_{s}_{day}_{col}')
  z+=q*px
 return z

def rebalance(cash,shares,targets,day,col,costs):
 syms=set(shares)|set(targets);prices={s:fields[col].at[day,s] for s in syms}
 if any(not np.isfinite(v) or v<=0 for v in prices.values()):raise ValueError(f'MISSING_EXEC_{day}_{[s for s,v in prices.items() if not np.isfinite(v)]}')
 gross=cash+sum(shares[s]*prices[s] for s in shares)
 def cost(post):return sum(abs(targets.get(s,0)*post-shares.get(s,0)*prices[s])*costs.get(s,50)/10000 for s in syms)
 lo,hi=0,gross
 for _ in range(55):
  mid=(lo+hi)/2
  if mid+cost(mid)>gross:hi=mid
  else:lo=mid
 post=(lo+hi)/2
 orders=sum(abs(targets.get(s,0)*post-shares.get(s,0)*prices[s])>1e-8 for s in syms)
 return post*(1-sum(targets.values())),{s:w*post/prices[s] for s,w in targets.items() if w>0},orders

def top_cost(day):
 ranked=mean30.loc[day].dropna().sort_values(ascending=False)
 top=list(ranked.head(20).index)
 costs={s:(15 if s in ('BTCUSDT','ETHUSDT') else 25 if i<20 else 50) for i,s in enumerate(ranked.index)}
 return top,costs

def run(which,start,end):
 dates=pd.date_range(start,end-pd.Timedelta(days=1),tz='UTC',freq='D')
 if which=='v4': dates=dates[dates>=dates[dates.weekday==1][0]]
 cash=50000.;shares={};entries={};eq=[];norders=0
 bcash=50000.;bshares={};equal_cash=50000.;equal_shares={};equal_eq=[];btc_eq=[];first=dates[0]
 for day in dates:
  for book in (shares,bshares,equal_shares):
   for s in list(book):
    if not np.isfinite(C.at[day,s]):del book[s]
  if day==first:
   bcash,bshares,k=rebalance(bcash,bshares,{'BTCUSDT':1.0},day,'open',{'BTCUSDT':15})
  signal=day-pd.Timedelta(days=2 if which=='v4' else 1)
  if which=='v5' or day.weekday()==1:
   top,costs=top_cost(signal)
   if which=='v4': top=[s for s in top if mean30.at[signal,s]>10_000_000]
   topset=set(top)
   active=[]
   for s in shares:
    if s not in topset:continue
    if which=='v4':
     peak=H.loc[entries[s]:signal,s].max()
     close=C.at[signal,s]
     leave=close<lo20.at[signal,s] or close<peak-2*atr.at[signal,s]
    else:leave=rsi.at[signal,s]>55 or (day-entries[s]).days>=5
    if not leave:active.append(s)
   for s in top:
    if s in active or s in shares:continue
    if which=='v4':take=C.at[signal,s]>hi55.at[signal,s]
    else:take=rsi.at[signal,s]<30
    if take and np.isfinite(O.at[day,s]):active.append(s)
   targets={s:1/len(active) for s in active} if active else {}
   cash,shares,k=rebalance(cash,shares,targets,day,'open',costs);norders+=k
   entries={s:entries.get(s,day) for s in shares}
   eq_targets={s:1/len(top) for s in top if np.isfinite(O.at[day,s])}
   equal_cash,equal_shares,_=rebalance(equal_cash,equal_shares,eq_targets,day,'open',costs)
  eq.append(nav(cash,shares,day,'close'));btc_eq.append(nav(bcash,bshares,day,'close'));equal_eq.append(nav(equal_cash,equal_shares,day,'close'))
 last=dates[-1];_,costs=top_cost(last-pd.Timedelta(days=1))
 cash,shares,k=rebalance(cash,shares,{},last,'close',costs);norders+=k
 bcash,bshares,_=rebalance(bcash,bshares,{},last,'close',{'BTCUSDT':15})
 equal_cash,equal_shares,_=rebalance(equal_cash,equal_shares,{},last,'close',costs)
 eq[-1]=cash;btc_eq[-1]=bcash;equal_eq[-1]=equal_cash
 def m(x):
  a=np.asarray(x,dtype=float);r=np.diff(np.r_[50000.,a])/np.r_[50000.,a[:-1]]
  downside=np.sqrt(np.mean(np.minimum(r,0)**2))
  weeks=pd.Series(a,index=dates).resample('W-SUN').last().pct_change().dropna()
  gain=weeks[weeks>0].sum();loss=-weeks[weeks<0].sum()
  return {'Sharpe':float(np.sqrt(365)*r.mean()/r.std(ddof=1)) if r.std(ddof=1)>0 else None,
          'Sortino':float(np.sqrt(365)*r.mean()/downside) if downside>0 else None,
          'Net_Return':float(a[-1]/50000-1),
          'MaxDD':float((a/np.maximum.accumulate(np.r_[50000.,a])[1:]-1).min()),
          'Profit_Factor':float(gain/loss) if loss>0 else None,
          'Win_Rate':float((weeks>0).mean()) if len(weeks) else None}
 return {'strategy':m(eq),'btc':m(btc_eq),'equal':m(equal_eq),'orders':int(norders)}
for which in ('v4','v5'):
 measured={}
 for name,start,end in [('train','2022-01-01','2024-01-01'),('val','2024-01-01','2025-01-01')]:
  measured[name]=run(which,pd.Timestamp(start,tz='UTC'),pd.Timestamp(end,tz='UTC'))
  print(which,name,'Sharpe',measured[name]['strategy']['Sharpe'],flush=True)
 val=measured['val']
 out={
  'strategy':which+'_daily_spot',
  'scope':'owner_requested_exploratory_development_trial',
  'train_window':'2022-01-01/2023-12-31',
  'val_window':'2024-01-01/2024-12-31',
  'data_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
  'sharpe_train':measured['train']['strategy']['Sharpe'],
  'sharpe_val':val['strategy']['Sharpe'],
  'sortino_val':val['strategy']['Sortino'],
  'maxdd_val':val['strategy']['MaxDD'],
  'profit_factor_val':val['strategy']['Profit_Factor'],
  'win_rate_val':val['strategy']['Win_Rate'],
  'num_trades_val':val['orders'],
  'return_val':val['strategy']['Net_Return'],
  'return_btc_val':val['btc']['Net_Return'],
  'return_ew_val':val['equal']['Net_Return'],
  'verdict':'PASS' if val['strategy']['Sharpe']>0.6 and val['strategy']['Net_Return']>val['btc']['Net_Return'] else 'FAIL',
  'test3_opened':False,
 }
 (ROOT/'results'/f'{which}_train_val.json').write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
