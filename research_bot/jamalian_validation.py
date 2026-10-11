"""Diagnostics for synchronized, already costed returns from frozen trials.

CPCV here validates selection among fixed replay paths. It does not refit a
predictor, reconstruct missing depth, or approve a strategy. Stateful strategies
must first produce chronological inventory-aware paths in a separate replay.
"""
from __future__ import annotations

from itertools import combinations
from math import comb
import re

import numpy as np
import pandas as pd

from .regime_robustness import deflated_sharpe_probability, probability_backtest_overfitting


def purged_combinatorial_splits(starts: pd.DatetimeIndex, ends: pd.DatetimeIndex, *,
                                n_groups: int = 6, test_groups: int = 2,
                                embargo: pd.Timedelta = pd.Timedelta(0)):
    if type(n_groups) is not int or n_groups < 4 or n_groups % 2:
        raise ValueError("n_groups must be an even integer >= 4")
    if type(test_groups) is not int or not 1 <= test_groups < n_groups:
        raise ValueError("invalid test group count")
    if starts.tz is None or ends.tz is None or starts.hasnans or ends.hasnans:
        raise ValueError("finite timezone-aware label intervals required")
    if len(starts) != len(ends) or len(starts) < n_groups or (ends < starts).any():
        raise ValueError("invalid label interval endpoints")
    if not starts.is_monotonic_increasing or not starts.is_unique or embargo < pd.Timedelta(0):
        raise ValueError("unique chronological starts and nonnegative embargo required")
    groups = np.array_split(np.arange(len(starts)), n_groups)
    for selected in combinations(range(n_groups), test_groups):
        test = np.concatenate([groups[g] for g in selected])
        train_mask = np.ones(len(starts), dtype=bool)
        train_mask[test] = False
        for group in selected:
            lo, hi = starts[groups[group]].min(), ends[groups[group]].max() + embargo
            train_mask &= ~((starts <= hi) & (ends >= lo))
        yield np.flatnonzero(train_mask), test


class ValidationHarness:
    def diagnose(self, net_returns: pd.DataFrame, label_ends: pd.DatetimeIndex, *,
                 dataset_sha256: str, trial_count: int, data_class: str,
                 n_groups: int = 6, test_groups: int = 2,
                 embargo: pd.Timedelta = pd.Timedelta(0)) -> dict:
        """All tried configurations must be columns, with common timestamps.

        Costs, latent queue behavior and data truth cannot be inferred from a
        return matrix. Their independent provenance must accompany the matrix.
        The output is diagnostic-only, including on apparently favorable data.
        """
        if not re.fullmatch(r"[a-f0-9]{64}", dataset_sha256):
            raise ValueError("dataset SHA-256 required")
        if data_class not in {"SYNTHETIC_SMOKE", "DEVELOPMENT_REPLAY"}:
            raise ValueError("sealed holdout/prospective data requires its own protocol")
        if not isinstance(net_returns.index, pd.DatetimeIndex):
            raise ValueError("returns require datetime index")
        if type(trial_count) is not int or trial_count != net_returns.shape[1] or trial_count < 2:
            raise ValueError("all searched configurations must be included")
        if not net_returns.columns.is_unique or not all(isinstance(c, str) for c in net_returns.columns):
            raise ValueError("unique string trial identities required")
        if len(net_returns) < n_groups * 20:
            raise ValueError("insufficient diagnostic sample; no statistic emitted")
        values = net_returns.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= -1).any():
            raise ValueError("invalid net-return matrix; rows cannot be silently dropped")
        # This also validates timestamps, intervals and splitter configuration.
        folds = list(purged_combinatorial_splits(net_returns.index, label_ends,
            n_groups=n_groups, test_groups=test_groups, embargo=embargo))
        if (net_returns.std(ddof=1) <= 0).any():
            raise ValueError("constant trial returns cannot support Sharpe diagnostics")
        cpcv = []
        for train, test in folds:
            if len(train) < 20 or len(test) < 20:
                raise ValueError("insufficient purged fold support")
            training = net_returns.iloc[train]
            spread = training.std(ddof=1)
            if (spread <= 0).any():
                raise ValueError("constant training fold cannot support selection")
            winner = (training.mean() / spread).idxmax()
            cpcv.append({"n_train": len(train), "n_test": len(test),
                "training_selected_trial": winner,
                "test_mean_net_return": float(net_returns.iloc[test][winner].mean())})
        pbo = probability_backtest_overfitting(net_returns, n_groups=n_groups)
        groups = np.array_split(np.arange(len(net_returns)), n_groups)
        for selected in combinations(range(n_groups), n_groups // 2):
            for indices in (np.concatenate([groups[g] for g in selected]),
                            np.concatenate([groups[g] for g in range(n_groups) if g not in selected])):
                sample = net_returns.iloc[indices]
                dispersion = sample.std(ddof=1)
                scores = sample.mean() / dispersion
                if (dispersion <= 0).any() or not np.isfinite(scores).all() or scores.duplicated().any():
                    raise ValueError("CSCV half lacks finite, unambiguous trial ranks")
        if pbo["splits"] != comb(n_groups, n_groups // 2) or not np.isfinite(pbo["pbo"]):
            raise ValueError("incomplete CSCV/PBO support")
        sharpes = net_returns.mean() / net_returns.std(ddof=1)
        sharpe_std = float(sharpes.std(ddof=1))
        if not np.isfinite(sharpe_std) or sharpe_std <= 0:
            raise ValueError("trial Sharpe dispersion unavailable; cannot deflate")
        dsr = {name: deflated_sharpe_probability(net_returns[name], trial_count, sharpe_std)
            for name in net_returns.columns}
        if not all(np.isfinite(x) for x in dsr.values()):
            raise ValueError("DSR unavailable")
        return {"status": "DIAGNOSTIC_ONLY_NOT_PROMOTION", "data_class": data_class,
            "dataset_sha256": dataset_sha256, "n_observations": len(net_returns),
            "trial_count": trial_count, "cpcv_fixed_path_selection": cpcv,
            "cscv_pbo": pbo, "dsr_per_period": dsr,
            "embargo_seconds": embargo.total_seconds(), "paper_execution": False,
            "live_execution": False, "profitable_alpha_claim": False,
            "limitations": ["Fixed-path diagnostics do not refit models or prove replay causality.",
                "Correlated folds are not independent observations.",
                "DSR uses legacy IID-style approximation; serial dependence needs block-based inference.",
                "Fee, fill, queue and non-atomic risk assumptions require independent validation."]}
