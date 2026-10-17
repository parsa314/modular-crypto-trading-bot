import numpy as np
import pandas as pd
from scripts.rl_final_experiment import CryptoEnv,N_STEPS,TRAIN_END,TEST_SEAL

def fixture():
    t=pd.date_range("2022-01-01",periods=320,freq="4h",tz="UTC")
    c=100+np.linspace(0,20,320)
    return pd.DataFrame({"open":c,"close":c+0.1,"high":c+1,"low":c-1,
       "atr":.01,"rsi":.5,"ma50":.02,"ma200":.03,"funding":0.,
       "ret4":.001,"ret24":.006,"regime0":1.,"regime1":0.},index=t)
def test_state_and_accounting():
    env=CryptoEnv(training=False,eval_sequence=fixture())
    obs,_=env.reset()
    assert env.observation_space.shape==(12,)
    obs,r,done,trunc,inf=env.step(np.array([.25]))
    assert obs.shape==(12,) and np.isfinite(r)
    assert inf["nav"]>0 and env.position==.25
def test_cost():
    a=CryptoEnv(training=False,eval_sequence=fixture())
    b=CryptoEnv(training=False,eval_sequence=fixture())
    a.reset();b.reset()
    _,_,_,_,z=a.step([0]);_,_,_,_,w=b.step([1])
    assert z["nav"]==1 and w["nav"]!=z["nav"]
def test_cutoff_and_steps():
    assert TRAIN_END.year==2023 and TEST_SEAL.year==2024 and N_STEPS==500000
