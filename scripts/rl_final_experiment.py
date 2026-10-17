"""PPO/SAC independent financial RL on 50 public Binance spot H4 histories.

Train-only 2019-22; validation 2023; no 2024+ API calls.
Each run trains EXACTLY 500_000 SB3 timesteps. Read-only spot only.
Fifty hindsight-selected names, not PIT-universe. No real or demo orders.
"""
from __future__ import annotations
import argparse,json,math,random
from pathlib import Path
import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO,SAC
from ml.data_loader import SYMBOLS
from scripts.ml_v2_final import fetch_spot,fit_hmm,STEP
from datetime import timezone

TRAIN_END=pd.Timestamp("2023-01-01",tz="UTC")
TEST_SEAL=pd.Timestamp("2024-01-01",tz="UTC")
ROOT=Path("results")
ROOT.mkdir(exist_ok=True)
MODELS=Path("models")
MODELS.mkdir(exist_ok=True)
N_STEPS=500_000
SIDE_COST=.0015

def calc_features(p,regime):
    close=p.close;shift=close.shift(1)
    tr=pd.concat([(p.high-p.low),(p.high-shift).abs(),(p.low-shift).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/close
    delta=close.diff();pos=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    neg=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rsi=(100-100/(1+pos/neg.replace(0,np.nan))).fillna(50)/100
    ma50=close.rolling(50).mean()
    ma200=close.rolling(200).mean()
    ret4=close.pct_change(fill_method=None)
    ret24=close.pct_change(6,fill_method=None)
    rg=regime.reindex(p.index,method="ffill").fillna("HMM2")
    feat=pd.DataFrame({"atr":atr,"rsi":rsi,"ma50":close/ma50-1,"ma200":close/ma200-1,
              "ret4":ret4,"ret24":ret24,"regime0":(rg=="HMM0").astype(float),
              "regime1":(rg=="HMM1").astype(float),"funding":0.},index=p.index)
    # All funding observations unavailable in this H4-only standalone benchmark:
    # always 0 with explicit metadata, never presented as measured funding.
    return feat

def prepared_panel():
    file=ROOT/"rl_h4_50_2019_2023.parquet"
    if not file.exists():raise RuntimeError("REAL_H4_DATASET_MISSING_RUN_PREPARE")
    z=pd.read_parquet(file)
    if (pd.to_datetime(z.timestamp,utc=True)>=TEST_SEAL).any():
        raise RuntimeError("HOLDOUT_2024_ACCESS_PROHIBITED")
    panel={}
    for symbol,g in z.groupby("symbol"):
        q=g.set_index("timestamp").sort_index()
        panel[symbol]=q
    return panel

def prepare():
    panel={};manifests=[]
    for i,s in enumerate(SYMBOLS):
        try:
            bars,info=fetch_spot(s,"4h")
        except Exception as ex:
            bars=None;info={"symbol":s,"status":"ERROR","reason":str(ex)[:120]}
        manifests.append(info)
        if bars is not None and len(bars)>300:panel[s]=bars
        print("H4",i+1,"/",len(SYMBOLS),s,len(bars) if bars is not None else 0,flush=True)
    if "BTCUSDT" not in panel:raise RuntimeError("NO_BTC_H4")
    hmm,meta=fit_hmm(panel["BTCUSDT"])
    frames=[]
    for sym,p in panel.items():
        f=calc_features(p,hmm)
        joined=p.join(f)
        joined["symbol"]=sym
        joined=joined.reset_index()
        joined=joined.replace([np.inf,-np.inf],np.nan).dropna()
        frames.append(joined)
    allbars=pd.concat(frames,ignore_index=True)
    allbars.to_parquet(ROOT/"rl_h4_50_2019_2023.parquet",index=False)
    report={"symbols_requested":50,"symbols_usable":len(panel),
            "rows":len(allbars),"max_time":str(pd.to_datetime(allbars.timestamp,utc=True).max()),
            "hmm":meta,"funding_rate":"not measured in this prototype; constant neutral 0, no funding claims",
            "return_1h":"unavailable at H4; uses return_4h in state slot",
            "manifest":manifests,"test_2024_opened":False,
            "universe_note":"retrospectively known 50 spot pairs"}
    (ROOT/"rl_data_manifest.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
    print("H4_REAL_ROWS",len(allbars),"H4_SYMBOLS",len(panel),flush=True)

def to_sequences(panel,start,end):
    sequences={}
    for symbol,p in panel.items():
        q=p[(p.index>=start)&(p.index<end)].copy()
        if len(q)<300:continue
        # Check exact H4 contiguity for each experience interval.
        seqs=[];left=0
        for i in range(1,len(q)):
            if q.index[i]-q.index[i-1]!=STEP["4h"]:
                if i-left>=300:seqs.append(q.iloc[left:i])
                left=i
        if len(q)-left>=300:seqs.append(q.iloc[left:])
        if seqs:sequences[symbol]=seqs
    return sequences

class CryptoEnv(gym.Env):
    metadata={"render_modes":[]}
    def __init__(self,panel=None,training=True,seed=42,eval_symbol=None,eval_sequence=None):
        super().__init__()
        self.panel=panel or {}
        self.training=training
        self.eval_symbol=eval_symbol;self.eval_sequence=eval_sequence
        self.rng=np.random.default_rng(seed)
        self.action_space=spaces.Box(low=0,high=1,shape=(1,),dtype=np.float32)
        self.observation_space=spaces.Box(-np.inf,np.inf,shape=(12,),dtype=np.float32)
        self.horizon=720
        self.position=0;self.nav=1;self.peak=1;self.i=0;self.last_buy_price=0
        self.returns=[];self.data=None;self.side_cost=SIDE_COST

    def _obs(self):
        row=self.data.iloc[self.i]
        value=np.array([self.position,1-self.position,
            self.position*(row.close/self.last_buy_price-1) if self.position and self.last_buy_price else 0,
            row.atr,row.rsi,row.ma50,row.ma200,row.funding,row.ret4,row.ret24,
            row.regime0,row.regime1],dtype=np.float32)
        if not np.isfinite(value).all():raise RuntimeError("INVALID_RL_OBSERVATION")
        return np.clip(value,-100,100)

    def reset(self,*,seed=None,options=None):
        super().reset(seed=seed)
        if self.eval_sequence is not None:
            self.data=self.eval_sequence
        else:
            names=[s for s,q in self.panel.items() if q]
            if not names:raise RuntimeError("NO_TRAINING_SERIES")
            name=names[int(self.rng.integers(len(names)))]
            series=self.panel[name][int(self.rng.integers(len(self.panel[name])))]
            if self.training and len(series)>self.horizon+1:
                at=int(self.rng.integers(len(series)-self.horizon))
                series=series.iloc[at:at+self.horizon+1]
            self.data=series
        self.i=0;self.position=0.;self.nav=1.;self.peak=1.;
        self.returns=[];self.last_buy_price=0
        return self._obs(),{}

    def step(self,action):
        new=float(np.clip(float(np.asarray(action).reshape(-1)[0]),0,1))
        j=self.i+1
        if j>=len(self.data):raise RuntimeError("STEP_PAST_END")
        curr=self.data.iloc[self.i];future=self.data.iloc[j]
        before=self.nav
        # Held fraction experiences next-open gap before rebalance.
        self.nav*=max(1e-9,1+self.position*(future.open/curr.close-1))
        cost=self.side_cost*abs(new-self.position)
        self.nav*=max(1e-9,1-cost)
        self.nav*=max(1e-9,1+new*(future.close/future.open-1))
        if new>0 and self.position==0:self.last_buy_price=float(future.open)
        self.position=new;self.i=j
        self.peak=max(self.peak,self.nav)
        dd=1-self.nav/self.peak
        step_return=self.nav/before-1
        self.returns.append(step_return)
        lookback=self.returns[-180:] # 30d H4, not falsely "30 bars = 30d".
        if len(lookback)>=25 and np.std(lookback)>1e-12:
            rolling=float(np.sqrt(365*6)*np.mean(lookback)/np.std(lookback,ddof=1))
        else:rolling=0.
        reward=float(np.clip(rolling,-5,5)-2*max(0,dd-.10)-cost)
        terminated=(j==len(self.data)-1)
        truncated=False
        if self.nav<=.01:terminated=True
        return self._obs(),reward,terminated,truncated,{"nav":self.nav,"drawdown":dd}

def evaluate_model(model,eval_panel):
    total_by_day={}
    accounts=[]
    for sym,sequences in eval_panel.items():
        for seq in sequences:
            env=CryptoEnv(training=False,eval_sequence=seq)
            obs,_=env.reset()
            navs=[1.]
            done=False
            while not done:
                act,_=model.predict(obs,deterministic=True)
                obs,rew,done,trunc,info=env.step(act)
                navs.append(float(info["nav"]))
            for date,value in zip(seq.index,navs):
                total_by_day.setdefault(date,[]).append(value)
            accounts.append((sym,float(navs[-1]-1)))
    if not total_by_day:raise RuntimeError("NO_VAL_EPISODES")
    values=[float(np.mean(total_by_day[k])) for k in sorted(total_by_day)]
    return calc_metric(values,len(accounts))

def calc_metric(nav,n):
    rets=np.diff(nav)/np.asarray(nav[:-1],float)
    mu=float(np.mean(rets));sd=float(np.std(rets,ddof=1))
    top=np.maximum.accumulate(nav)
    return {"sharpe":float(np.sqrt(365*6)*mu/sd) if sd>1e-12 else None,
            "net_return":float(nav[-1]/nav[0]-1),
            "maxdd":float(np.max(1-np.asarray(nav)/top)),
            "evaluated_accounts":n,
            "note":"Equal-weight across independently simulated spot-symbol accounts (not executable shared cross-asset portfolio)"}

def fixed_eval(panel,target=0.02):
    allreturns={}
    for s,sequences in panel.items():
        for seq in sequences:
            env=CryptoEnv(training=False,eval_sequence=seq)
            env.reset()
            navs=[1.]
            for _ in range(len(seq)-1):
                _,_,_,_,inf=env.step(np.array([target],dtype=np.float32))
                navs.append(float(inf["nav"]))
            for t,v in zip(seq.index,navs):allreturns.setdefault(t,[]).append(v)
    if not allreturns:raise RuntimeError("NO_VALIDATION_SERIES")
    vals=[float(np.mean(allreturns[k])) for k in sorted(allreturns)]
    return calc_metric(vals,len(panel))

def train(alg,seed):
    panel=prepared_panel()
    trainsets=to_sequences(panel,pd.Timestamp("2019-01-01",tz="UTC"),TRAIN_END)
    validation=to_sequences(panel,TRAIN_END,TEST_SEAL)
    if len(trainsets)<5 or len(validation)<5:
        raise RuntimeError("TOO_FEW_TRAINING_OR_VALIDATION_SYMBOLS")
    env=CryptoEnv(trainsets,training=True,seed=seed)
    env.reset()
    cls=PPO if alg=="PPO" else SAC
    kwargs={"verbose":0,"seed":seed,"device":"cpu"}
    if alg=="PPO":kwargs.update({"n_steps":512,"batch_size":128,"learning_rate":3e-4})
    else:kwargs.update({"learning_starts":1000,"batch_size":128,"learning_rate":3e-4,"buffer_size":100000})
    model=cls("MlpPolicy",env,**kwargs)
    model.learn(total_timesteps=N_STEPS,progress_bar=False)
    model.save(str(MODELS/f"{alg.lower()}_agent_seed{seed}.zip"))
    val=evaluate_model(model,validation)
    outcome={"algorithm":alg,"seed":seed,"timesteps":N_STEPS,
            "validation_2023":val,"training_assets":len(trainsets),
            "validation_assets":len(validation),
            "test_2024_opened":False}
    (ROOT/f"rl_{alg.lower()}_seed{seed}.json").write_text(json.dumps(outcome,indent=2)+"\n")
    print("FINAL_RL_VAL",alg,seed,json.dumps(val),flush=True)

def aggregate():
    panel=prepared_panel()
    validation=to_sequences(panel,TRAIN_END,TEST_SEAL)
    baseline_fixed=fixed_eval(validation,.02)
    # Benchmark BTC buy-and-hold: independent BTC H4 account.
    btc={"BTCUSDT":validation["BTCUSDT"]}
    baseline_btc=fixed_eval(btc,1.)
    runs=[]
    for alg in ("PPO","SAC"):
        for seed in (42,7):
            p=ROOT/f"rl_{alg.lower()}_seed{seed}.json"
            if not p.exists():raise RuntimeError("MISSING_RL_RUN_"+p.name)
            runs.append(json.loads(p.read_text()))
    successes=[]
    for run in runs:
        val=run["validation_2023"]
        val_sharpe=val["sharpe"]
        passed=val_sharpe is not None and val_sharpe>.8 and (
            baseline_fixed["sharpe"] is None or val_sharpe>baseline_fixed["sharpe"])
        run["pass"]=bool(passed)
        successes.append(bool(passed))
    out={"runs":runs,"baseline_fixed_2pct":baseline_fixed,
         "baseline_btc_buy_hold":baseline_btc,
         "validation_2023_only":True,"test_2024_opened":False,
         "total_timesteps_train":N_STEPS*len(runs),
         "verdict":"PASS" if any(successes) else "FAIL",
         "funding_limitation":"0 neutral proxy, funding not downloaded in this RL branch",
         "return_1h_limitation":"uses observed H4 return in state slot; 1h is not inferable from H4 data"}
    (ROOT/"rl_final.json").write_text(json.dumps(out,indent=2)+"\n")
    print("FINAL_RL_AGGREGATE",json.dumps({
        "fixed_sharpe":baseline_fixed["sharpe"],
        "btc_sharpe":baseline_btc["sharpe"],
        "rl_sharpes":[x["validation_2023"]["sharpe"] for x in runs],
        "verdict":out["verdict"]}),flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("step",choices=["prepare","train","aggregate"])
    ap.add_argument("--algorithm",choices=["PPO","SAC"],default="PPO")
    ap.add_argument("--seed",type=int,choices=[42,7],default=42)
    x=ap.parse_args()
    if x.step=="prepare":prepare()
    elif x.step=="train":train(x.algorithm,x.seed)
    else:aggregate()

if __name__=="__main__":main()
