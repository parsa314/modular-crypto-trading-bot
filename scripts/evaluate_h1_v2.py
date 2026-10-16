"""Frozen H1 development evaluation from docs/pre_registration_v2.md.

Reads only the verified 2022-24 BTC archive. This program never opens holdouts,
places orders, changes the primary signal, or tunes a model from validation.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew
from sklearn.impute import SimpleImputer
from xgboost import XGBClassifier

from research_bot.strategies import combine, evaluate_all
from scripts.backtest_btcusdt_2022_2024 import confluence_positions, read_verified


FEE = 0.001
TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
VAL_START = pd.Timestamp("2024-01-21", tz="UTC")
VAL_END = pd.Timestamp("2025-01-01", tz="UTC")
BAR = pd.Timedelta(minutes=15)
HORIZON = 128
FEATURES = ("score", "atr_ratio", "regime", "hour_sin", "hour_cos", "dow_sin", "dow_cos", "resistance_atr", "support_atr")


@dataclass(frozen=True)
class Trade:
    entry: int
    exit: int
    entry_px: float
    exit_px: float
    entry_cost: float
    exit_cost: float
    exit_at_open: bool

    @property
    def factor(self) -> float:
        return (1 - self.entry_cost) * self.exit_px / self.entry_px * (1 - self.exit_cost)


def atr_ratio(frame: pd.DataFrame) -> pd.Series:
    prev = frame.close.shift(1)
    tr = pd.concat((frame.high - frame.low, (frame.high - prev).abs(), (frame.low - prev).abs()), axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / frame.close


def daily_vol_regime(frame: pd.DataFrame, train_end: pd.Timestamp) -> tuple[pd.Series, tuple[float, float]]:
    daily_close = frame.set_index("timestamp").close.resample("1D").last()
    # A value on day D uses complete daily closes only through day D-1.
    vol = np.log(daily_close).diff().rolling(20, min_periods=20).std().shift(1)
    train = vol[vol.index < train_end].dropna()
    q1, q2 = (float(z) for z in train.quantile([1 / 3, 2 / 3]))
    day = frame.timestamp.dt.floor("D")
    mapped = day.map(vol)
    regime = pd.Series(np.where(mapped < q1, 0, np.where(mapped < q2, 1, 2)), index=frame.index, dtype=float)
    regime[mapped.isna()] = np.nan
    return regime, (q1, q2)


def entry_features(frame: pd.DataFrame, atr: pd.Series, regime: pd.Series, decision: int, score: float) -> dict[str, float]:
    ts = frame.timestamp.iloc[decision]
    h = ts.hour + ts.minute / 60
    val = float(atr.iloc[decision] * frame.close.iloc[decision])
    hi = float(frame.high.iloc[decision - 96:decision].max())
    lo = float(frame.low.iloc[decision - 96:decision].min())
    return {"score": float(score), "atr_ratio": float(atr.iloc[decision]), "regime": float(regime.iloc[decision]),
            "hour_sin": math.sin(2 * math.pi * h / 24), "hour_cos": math.cos(2 * math.pi * h / 24),
            "dow_sin": math.sin(2 * math.pi * ts.dayofweek / 7), "dow_cos": math.cos(2 * math.pi * ts.dayofweek / 7),
            "resistance_atr": (hi - float(frame.close.iloc[decision])) / val,
            "support_atr": (float(frame.close.iloc[decision]) - lo) / val}


def slip_cost(atr: pd.Series, decision: int, median_train: float) -> float:
    ratio = float(atr.iloc[decision])
    if not math.isfinite(ratio):
        raise ValueError("ATR_UNAVAILABLE")
    return FEE + float(np.clip(5 * ratio / median_train, 2, 25)) / 10000


def barrier_trade(frame: pd.DataFrame, atr: pd.Series, median_train: float, entry: int) -> Trade:
    """One long event, conservative same-bar and gap priority, with fixed horizon."""
    t = entry - 2
    start = float(frame.open.iloc[entry])
    buy_cost = slip_cost(atr, t, median_train)
    atr_abs = float(atr.iloc[t] * frame.close.iloc[t])
    distance = max(3 * atr_abs, 2 * (2 * buy_cost) * start)
    profit, stop = start + distance, start - distance
    exit_i, exit_px, at_open = entry + HORIZON, float(frame.open.iloc[entry + HORIZON]), True
    for j in range(entry, entry + HORIZON):
        opened = float(frame.open.iloc[j])
        if opened <= stop:
            exit_i, exit_px, at_open = j, opened, True
            break
        if opened >= profit:
            exit_i, exit_px, at_open = j, profit, True
            break
        hit_stop = float(frame.low.iloc[j]) <= stop
        hit_profit = float(frame.high.iloc[j]) >= profit
        if hit_stop:  # includes same-bar double touch: loss first
            exit_i, exit_px, at_open = j, stop, False
            break
        if hit_profit:
            exit_i, exit_px, at_open = j, profit, False
            break
    sell_cost = slip_cost(atr, max(0, exit_i - 1), median_train)
    return Trade(entry, exit_i, start, exit_px, buy_cost, sell_cost, at_open)


def standard_trades(frame: pd.DataFrame, atr: pd.Series, median_train: float, at: np.ndarray, target: np.ndarray) -> list[Trade]:
    """Delayed long/flat baseline; segment starts cash and ends with forced close."""
    pos = np.zeros(len(at), dtype=np.int8)
    pos[2:] = target[:-2]
    starts = np.flatnonzero(np.diff(np.r_[0, pos]) == 1)
    ends = np.flatnonzero(np.diff(np.r_[pos, 0]) == -1) + 1
    out = []
    for a, b in zip(starts, ends):
        entry = int(at[a])
        exit_i = int(at[b]) if b < len(at) else int(at[-1]) + 1
        exit_px = float(frame.open.iloc[exit_i]) if b < len(at) else float(frame.close.iloc[at[-1]])
        out.append(Trade(entry, exit_i, float(frame.open.iloc[entry]), exit_px,
                         slip_cost(atr, entry - 2, median_train),
                         slip_cost(atr, max(0, exit_i - 1), median_train), b < len(at)))
    return out


def donchian_target(frame: pd.DataFrame) -> np.ndarray:
    upper = frame.high.shift(1).rolling(20).max().to_numpy()
    lower = frame.low.shift(1).rolling(20).min().to_numpy()
    close = frame.close.to_numpy()
    target = np.zeros(len(frame), dtype=np.int8)
    state = 0
    for i in range(len(frame)):
        if math.isfinite(upper[i]) and close[i] > upper[i]:
            state = 1
        elif math.isfinite(lower[i]) and close[i] < lower[i]:
            state = 0
        target[i] = state
    return target


def reset_events_to_segment(target: np.ndarray, at: np.ndarray) -> np.ndarray:
    event = np.diff(np.r_[0, target.astype(int)])
    out = np.zeros(len(at), dtype=np.int8)
    state = 0
    for j, i in enumerate(at):
        if event[i] > 0:
            state = 1
        elif event[i] < 0:
            state = 0
        out[j] = state
    return out


def random_matched_trades(frame: pd.DataFrame, atr: pd.Series, median_train: float,
                          first: int, last: int, source: list[Trade], seed: int) -> list[Trade]:
    if not source:
        return []
    durations = np.array([max(1, r.exit - r.entry + int(not r.exit_at_open and r.exit < last)) for r in source], dtype=int)
    generator = np.random.default_rng(seed)
    generator.shuffle(durations)
    count = len(durations)
    n = last - first
    free = int(n - 3 - durations.sum() - (count - 1))
    if free < 0:
        raise ValueError("RANDOM_MATCH_NOT_FEASIBLE")
    cuts = np.sort(generator.integers(0, free + 1, size=count))
    idle = np.diff(np.r_[0, cuts, free])
    a = first + 2 + int(idle[0])
    trades = []
    for j, duration in enumerate(durations):
        b = a + int(duration)
        if b >= last:
            b = last
            exit_px = float(frame.close.iloc[last - 1])
            at_open = False
        else:
            exit_px = float(frame.open.iloc[b])
            at_open = True
        trades.append(Trade(a, b, float(frame.open.iloc[a]), exit_px,
                            slip_cost(atr, a - 2, median_train), slip_cost(atr, b - 1, median_train), at_open))
        a = b + 1 + int(idle[j + 1]) if j < count - 1 else b
    assert len(trades) == count
    return trades


def nav_metrics(frame: pd.DataFrame, trades: list[Trade], first: int, last: int) -> tuple[dict, np.ndarray]:
    """Daily mark to market; last is exclusive and exit sentinel is last."""
    trades = sorted(trades, key=lambda z: z.entry)
    assert all(a.exit + int(not a.exit_at_open) <= b.entry for a, b in zip(trades, trades[1:])), "OVERLAP"
    assert all(first <= r.entry < last and r.exit <= last for r in trades)
    all_days = np.flatnonzero(((frame.timestamp >= frame.timestamp.iloc[first]) &
                              (frame.timestamp < frame.timestamp.iloc[last - 1] + BAR) &
                              (frame.timestamp.dt.hour == 0) & (frame.timestamp.dt.minute == 0)).to_numpy())
    assert len(all_days) > 30 and all_days[0] == first
    marks = [1.0]
    cash, j = 1.0, 0
    for day_i in all_days[1:]:
        while j < len(trades) and (trades[j].exit < day_i or (trades[j].exit == day_i and trades[j].exit_at_open)):
            cash *= trades[j].factor
            j += 1
        if j < len(trades) and trades[j].entry <= day_i <= trades[j].exit:
            z = trades[j]
            marks.append(cash * (1 - z.entry_cost) * float(frame.open.iloc[day_i]) / z.entry_px)
        else:
            marks.append(cash)
    while j < len(trades):
        cash *= trades[j].factor
        j += 1
    marks.append(cash)
    r = np.diff(np.array(marks)) / np.array(marks[:-1])
    std = float(r.std(ddof=1))
    mean = float(r.mean())
    down = float(np.sqrt(np.mean(np.minimum(r, 0) ** 2)))
    path = np.array(marks)
    dd = 1 - path / np.maximum.accumulate(path)
    longest = 0
    run = 0
    for z in dd[1:]:
        run = run + 1 if z > 1e-12 else 0
        longest = max(longest, run)
    tr = np.array([z.factor - 1 for z in trades])
    wins = tr[tr > 0]
    losses = -tr[tr < 0]
    years = len(r) / 365
    net = cash - 1
    maxdd = float(dd.max())
    out = {"net_return": net, "sharpe": math.sqrt(365) * mean / std if std > 1e-14 else None,
           "sortino": math.sqrt(365) * mean / down if down > 1e-14 else None,
           "calmar": ((cash ** (1 / years) - 1) / maxdd) if maxdd > 0 else None,
           "maxdd": maxdd, "longest_dd_days": longest,
           "win_rate": float(len(wins) / len(tr)) if len(tr) else None,
           "profit_factor": float(wins.sum() / losses.sum()) if len(losses) else None,
           "expectancy_per_trade": float(tr.mean()) if len(tr) else None,
           "skew_daily": float(skew(r, bias=False)) if std > 0 else None,
           "excess_kurt_daily": float(kurtosis(r, bias=False)) if std > 0 else None,
           "trades": len(tr), "turnover_sides": 2 * len(tr),
           "exposure_fraction": sum(max(1, z.exit - z.entry + int(not z.exit_at_open and z.exit < last)) for z in trades) / (last - first),
           "days": len(r)}
    return out, np.log1p(r)


def moving_block_lower(excess: np.ndarray) -> float:
    generator = np.random.default_rng(126)
    n = len(excess)
    means = np.empty(2000)
    for i in range(2000):
        starts = generator.integers(0, n, size=math.ceil(n / 7))
        ix = (starts[:, None] + np.arange(7)) % n
        means[i] = excess[ix.ravel()[:n]].mean()
    return float(np.quantile(means, 0.025))


def ece_equal_count(prob: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(prob, kind="stable")
    return float(sum(len(ix) / len(prob) * abs(float(prob[ix].mean()) - float(y[ix].mean()))
                     for ix in np.array_split(order, 5) if len(ix)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("results/h1_v2_development.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frame, manifest = read_verified(root / "data/btc_usdt_m15_2022_2024.parquet", root / "data/btc_usdt_m15_2022_2024.manifest.json")
    assert frame.timestamp.iloc[-1] < VAL_END  # fail closed: no test price is present
    pos, original = confluence_positions(frame)
    entries = np.flatnonzero(np.diff(np.r_[0, pos]) == 1)
    assert len(entries) == 299, "ORIGINAL_ENTRY_COUNT_DRIFT"
    atr = atr_ratio(frame)
    train_mask = frame.timestamp < TRAIN_END
    median = float(atr[train_mask].median())
    assert math.isfinite(median) and median > 0
    regime, terciles = daily_vol_regime(frame, TRAIN_END)
    record: list[dict] = []
    excluded: dict[str, int] = {}
    for entry in entries:
        t = int(entry) - 2
        entry_time = frame.timestamp.iloc[entry]
        segment = "train" if entry_time < TRAIN_END else "validation" if entry_time >= VAL_START else "embargo"
        if segment == "embargo":
            excluded["embargo"] = excluded.get("embargo", 0) + 1
            continue
        end_time = TRAIN_END if segment == "train" else VAL_END
        if entry + HORIZON >= len(frame) or frame.timestamp.iloc[entry + HORIZON] >= end_time:
            excluded["outcome_crosses_boundary"] = excluded.get("outcome_crosses_boundary", 0) + 1
            continue
        if not (frame.timestamp.iloc[t + 1:entry + HORIZON + 1].diff().dropna() == BAR).all():
            excluded["gap_in_outcome_window"] = excluded.get("gap_in_outcome_window", 0) + 1
            continue
        part = frame.iloc[t - 89:t + 1]
        as_of = part.available_at.iloc[-1].to_pydatetime()
        vote = combine(evaluate_all(part, as_of=as_of), as_of=as_of)
        assert vote.action == "BUY" and vote.score_0_to_12 >= 7
        feat = entry_features(frame, atr, regime, t, vote.score_0_to_12)
        if not all(math.isfinite(z) for z in feat.values()):
            excluded["feature_warmup"] = excluded.get("feature_warmup", 0) + 1
            continue
        trade = barrier_trade(frame, atr, median, int(entry))
        record.append({"segment": segment, "decision": t, "entry": int(entry), "trade": trade,
                       "label": int(trade.factor > 1), "features": feat})
    train = [z for z in record if z["segment"] == "train"]
    validation = [z for z in record if z["segment"] == "validation"]
    X_train = pd.DataFrame([z["features"] for z in train], columns=FEATURES)
    y_train = np.array([z["label"] for z in train], dtype=int)
    out: dict = {"pre_registration_commit": "c134d74e35b55b156c71012fbe33dc0ecfa869fd",
                 "source_sha256": manifest["output_sha256"], "original_entry_count": len(entries),
                 "original_diagnostics": original, "train_atr_median": median,
                 "train_volatility_terciles": terciles, "excluded": excluded,
                 "train_candidates": len(train), "train_positive": int(y_train.sum()),
                 "train_negative": int(len(y_train) - y_train.sum()), "validation_candidates": len(validation),
                 "holdouts_opened": False}
    if y_train.sum() < 30 or len(y_train) - y_train.sum() < 30:
        out["verdict"] = "H1_REJECTED_DATA_INSUFFICIENT"
    else:
        imputer = SimpleImputer(strategy="median")
        xt = imputer.fit_transform(X_train)
        model = XGBClassifier(n_estimators=30, max_depth=2, learning_rate=0.05,
                              min_child_weight=10, reg_lambda=10, subsample=1,
                              colsample_bytree=1, random_state=126, n_jobs=1,
                              objective="binary:logistic", eval_metric="logloss")
        model.fit(xt, y_train)
        xv = imputer.transform(pd.DataFrame([z["features"] for z in validation], columns=FEATURES))
        prob = model.predict_proba(xv)[:, 1] if len(validation) else np.array([])
        y_val = np.array([z["label"] for z in validation], dtype=int)
        chosen = {int(z["entry"]) for z, p in zip(validation, prob) if p >= 0.75}
        first = int(np.flatnonzero((frame.timestamp >= VAL_START).to_numpy())[0])
        last = len(frame)
        # Nonoverlap is applied after selection and identically to the all-signal comparator.
        def nonoverlap(candidates: list[dict], selected: set[int] | None) -> list[Trade]:
            trades = []
            next_entry = -1
            for z in candidates:
                tr: Trade = z["trade"]
                if (selected is None or tr.entry in selected) and tr.entry >= next_entry:
                    trades.append(tr)
                    next_entry = tr.exit + int(not tr.exit_at_open)
            return trades
        baseline_trades = nonoverlap(validation, None)
        model_trades = nonoverlap(validation, chosen)
        base, base_log = nav_metrics(frame, baseline_trades, first, last)
        meta, meta_log = nav_metrics(frame, model_trades, first, last)
        bh_entry = first
        bh = [Trade(bh_entry, last, float(frame.open.iloc[bh_entry]), float(frame.close.iloc[last - 1]),
                    slip_cost(atr, max(0, bh_entry - 2), median), slip_cost(atr, last - 1, median), False)]
        bh_met, bh_log = nav_metrics(frame, bh, first, last)
        at = np.arange(first, last, dtype=int)
        donchian = standard_trades(frame, atr, median, at,
                                  reset_events_to_segment(donchian_target(frame), at))
        donchian_met, _ = nav_metrics(frame, donchian, first, last)
        random_runs = []
        for seed in range(20261500, 20262500):
            trial = random_matched_trades(frame, atr, median, first, last, model_trades, seed)
            metrics, _ = nav_metrics(frame, trial, first, last)
            random_runs.append(metrics)
        random_median = {key: (float(np.median([r[key] for r in random_runs if r[key] is not None]))
                               if any(r[key] is not None for r in random_runs) else None)
                         for key in meta}
        random_base_runs = []
        for seed in range(20261500, 20262500):
            trial = random_matched_trades(frame, atr, median, first, last, baseline_trades, seed)
            metrics, _ = nav_metrics(frame, trial, first, last)
            random_base_runs.append(metrics)
        random_base_median = {key: (float(np.median([r[key] for r in random_base_runs if r[key] is not None]))
                                   if any(r[key] is not None for r in random_base_runs) else None)
                              for key in base}
        assert len(meta_log) == len(bh_log)
        win_uplift = (meta["win_rate"] - base["win_rate"]) if meta["win_rate"] is not None and base["win_rate"] is not None else None
        lower = moving_block_lower(meta_log - bh_log)
        ece = ece_equal_count(prob, y_val) if len(prob) >= 5 else None
        passed = (meta["trades"] >= 30 and win_uplift is not None and win_uplift >= 0.15 and
                  ece is not None and ece <= 0.10 and meta["net_return"] > max(base["net_return"], bh_met["net_return"]) and
                  meta["sharpe"] is not None and meta["sharpe"] > 1 and lower > 0)
        out.update({"model": {"name": "XGBoost", "version": __import__("xgboost").__version__,
                              "train_probability_range": [float(model.predict_proba(xt)[:, 1].min()), float(model.predict_proba(xt)[:, 1].max())],
                              "validation_probability_range": [float(prob.min()), float(prob.max())] if len(prob) else None,
                              "validation_predicted_ge_075": len(chosen)},
                    "validation": {"meta": meta, "same_exit_baseline": base, "buy_hold": bh_met,
                                   "donchian20": donchian_met,
                                   "random1000_median_metrics": random_median,
                                   "random1000_note": "Matched to selected trade durations/count; zero selected trades gives 1000 flat paths.",
                                   "random1000_same_exit_baseline_median_metrics": random_base_median,
                                   "win_rate_uplift_absolute": win_uplift, "ece_5_equal_count": ece,
                                   "paired_bootstrap_lower_025_daily_log_excess_vs_bh": lower,
                                   "development_gate_pass": bool(passed)},
                    "verdict": "H1_DEVELOPMENT_PASS_PENDING_H2" if passed else "H1_REJECTED_DEVELOPMENT"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "train_candidates": len(train),
                      "validation_candidates": len(validation), "validation": out.get("validation")}, allow_nan=False))


if __name__ == "__main__":
    main()
