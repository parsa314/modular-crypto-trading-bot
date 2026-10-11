"""Causal HPT dataset repair; no training, inference, order or promotion here.

Indices refer to ordered CLOSED bars. Source/availability, asset identity and
spacing must be verified by the dataset owner before these statistical arrays
can support a scientific experiment. Statistical prices/returns use NumPy;
financial execution remains Decimal in the completion layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np


@dataclass(frozen=True)
class HPTWindows:
    features: np.ndarray
    targets: np.ndarray
    decision_indices: np.ndarray
    label_end_indices: np.ndarray


@dataclass(frozen=True)
class HPTFold:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray


def _positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(name + " must be a positive integer")


def _zscore(values: np.ndarray, window: int) -> np.ndarray:
    output = np.zeros(len(values), dtype=np.float64)
    for i in range(len(values)):
        past = values[max(0, i-window+1):i+1]
        sd = past.std()
        if sd > 1e-12:
            output[i] = (values[i]-past.mean())/sd
    return output


def make_hpt_windows(closes: Sequence[float], sequence_length: int = 32,
                     horizon: int = 1, z_window: int = 30, rsi_period: int = 14) -> HPTWindows:
    for name, value in (("sequence_length",sequence_length),("horizon",horizon),("z_window",z_window),("rsi_period",rsi_period)):
        _positive_int(value, name)
    prices = np.asarray(closes, dtype=np.float64)
    if prices.ndim != 1 or not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("finite positive one-dimensional closed prices required")
    if len(prices) <= sequence_length + horizon:
        raise ValueError("insufficient closed bars")
    logs = np.log(prices)
    returns = np.diff(logs)
    deltas = np.diff(prices)
    gains, losses = np.maximum(deltas,0), np.maximum(-deltas,0)
    rsi = np.zeros(len(returns),dtype=np.float64)
    for i in range(rsi_period-1,len(returns)):
        gain, loss = gains[i-rsi_period+1:i+1].mean(), losses[i-rsi_period+1:i+1].mean()
        rsi[i] = 0 if gain == 0 and loss == 0 else 1 if loss == 0 else (100-100/(1+gain/loss)-50)/50
    features = np.stack((_zscore(returns,z_window),_zscore(np.abs(returns),z_window),_zscore(rsi,z_window)),axis=-1)
    # Feature index i-1 is the return ending at close[i]. Target starts AFTER i.
    decisions = np.arange(sequence_length,len(prices)-horizon,dtype=np.int64)
    ends = decisions + horizon
    x = np.stack([features[i-sequence_length:i] for i in decisions]).astype(np.float32)
    y = (logs[ends]-logs[decisions]).astype(np.float32)[:,None]
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("feature/target overflow")
    for array in (x,y,decisions,ends):
        array.setflags(write=False)
    return HPTWindows(x,y,decisions,ends)


def purged_hpt_walk_forward(windows: HPTWindows, min_train: int, validation_size: int,
                           test_size: int, step: int | None = None) -> tuple[HPTFold,...]:
    step = test_size if step is None else step
    for name, value in (("min_train",min_train),("validation_size",validation_size),("test_size",test_size),("step",step)):
        _positive_int(value,name)
    if step < test_size:
        raise ValueError("out-of-sample test blocks must not overlap")
    n = len(windows.targets)
    if (windows.targets.shape != (n,1) or windows.features.ndim != 3 or len(windows.features)!=n
            or windows.decision_indices.shape != (n,) or windows.label_end_indices.shape != (n,)
            or not np.isfinite(windows.features).all() or not np.isfinite(windows.targets).all()
            or np.any(np.diff(windows.decision_indices)<=0) or np.any(windows.label_end_indices<=windows.decision_indices)):
        raise ValueError("invalid ordered supervised windows")
    horizon = int(np.max(windows.label_end_indices-windows.decision_indices)) if n else 0
    folds=[]
    for validation_start in range(min_train+horizon,n,step):
        test_start=validation_start+validation_size+horizon
        if test_start+test_size>n:
            break
        train=np.arange(validation_start)
        train=train[windows.label_end_indices[train]<windows.decision_indices[validation_start]]
        validation=np.arange(validation_start,validation_start+validation_size)
        validation=validation[windows.label_end_indices[validation]<windows.decision_indices[test_start]]
        test=np.arange(test_start,test_start+test_size)
        if len(train)<min_train or len(validation)!=validation_size:
            continue
        for indices in (train,validation,test):
            indices.setflags(write=False)
        folds.append(HPTFold(train,validation,test))
    if not folds:
        raise ValueError("insufficient support for purged train/validation/test folds")
    return tuple(folds)
