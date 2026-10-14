from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import NormalDist
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


PRICE_FEATURES = (
    "ret_1",
    "ret_2",
    "ret_6",
    "ret_18",
    "rv_6",
    "rv_18",
    "ma_gap_6",
    "ma_gap_18",
)

FLOW_PROXY_FEATURES = (
    "flow_mean",
    "flow_std",
    "flow_last",
    "flow_slope",
    "log_quote_volume",
    "quote_volume_cv",
    "log_trade_count",
    "trade_count_cv",
    "range_5m_median_bps",
    "range_5m_p95_bps",
)


@dataclass(frozen=True)
class V21HistoricalProxyConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    source_start: str = "2025-09-01T00:00:00Z"
    source_end: str = "2026-09-01T00:00:00Z"
    initial_train_end: str = "2026-05-01T00:00:00Z"
    evaluation_months: tuple[str, ...] = ("2026-05", "2026-06", "2026-07", "2026-08")
    decision_horizon_hours: int = 4
    subbar_minutes: int = 5
    required_subbars: int = 48
    probability_threshold: float = 0.50
    max_weight_per_asset: float = 0.50
    cost_bps_grid: tuple[float, ...] = (4.0, 8.0, 12.0, 20.0)
    primary_cost_bps: float = 12.0
    bootstrap_reps: int = 1000
    bootstrap_block_length: int = 18
    bootstrap_seed: int = 20260910
    annualization_periods: int = 6 * 365


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def parse_binance_archive_timestamp(values: pd.Series) -> pd.Series:
    """Parse Binance Vision archive timestamps across the 2025 ms->us transition."""
    numeric = pd.to_numeric(values, errors="coerce")
    out = pd.Series(pd.NaT, index=numeric.index, dtype="datetime64[ns, UTC]")
    finite = numeric.notna()
    if not finite.any():
        return out
    ms = finite & numeric.abs().lt(1e14)
    us = finite & numeric.abs().ge(1e14) & numeric.abs().lt(1e17)
    ns = finite & numeric.abs().ge(1e17)
    if ms.any():
        out.loc[ms] = pd.to_datetime(numeric.loc[ms], unit="ms", utc=True, errors="coerce")
    if us.any():
        out.loc[us] = pd.to_datetime(numeric.loc[us], unit="us", utc=True, errors="coerce")
    if ns.any():
        out.loc[ns] = pd.to_datetime(numeric.loc[ns], unit="ns", utc=True, errors="coerce")
    return out


def normalize_binance_5m_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    if list(frame.columns) != required:
        if frame.shape[1] < 12:
            raise ValueError("Binance 5m frame requires 12 columns")
        frame = frame.iloc[:, :12].copy()
        frame.columns = required
    x = frame.copy()
    x["timestamp"] = parse_binance_archive_timestamp(x["open_time"])
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "trade_count", "taker_buy_quote"]:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x["symbol"] = symbol
    x = x.dropna(subset=["timestamp", "open", "high", "low", "close", "quote_volume", "trade_count", "taker_buy_quote"])
    x = x[(x["open"] > 0) & (x["high"] > 0) & (x["low"] > 0) & (x["close"] > 0)]
    x = x.drop_duplicates(["symbol", "timestamp"]).sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return x


def _safe_cv(values: np.ndarray) -> float:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan")
    m = float(np.mean(x))
    return float(np.std(x, ddof=0) / m) if m > 0 else 0.0


def _fixed_slope(values: np.ndarray) -> float:
    y = np.asarray(values, dtype=float)
    if len(y) < 2 or not np.isfinite(y).all():
        return float("nan")
    x = np.linspace(-1.0, 1.0, len(y))
    denom = float(np.dot(x - x.mean(), x - x.mean()))
    return float(np.dot(x - x.mean(), y - y.mean()) / denom) if denom > 0 else 0.0


def aggregate_5m_to_4h(frame: pd.DataFrame, config: V21HistoricalProxyConfig | None = None) -> pd.DataFrame:
    cfg = config or V21HistoricalProxyConfig()
    rows: list[dict] = []
    for symbol, raw in frame.groupby("symbol", sort=True):
        x = raw.sort_values("timestamp").copy()
        x["subbar_end"] = pd.to_datetime(x["timestamp"], utc=True) + pd.Timedelta(minutes=cfg.subbar_minutes)
        x["decision_at"] = x["subbar_end"].dt.ceil(f"{cfg.decision_horizon_hours}h")
        denom = x["quote_volume"].where(x["quote_volume"] > 0)
        x["flow_imbalance"] = ((2.0 * x["taker_buy_quote"] - x["quote_volume"]) / denom).clip(-1.0, 1.0)
        mid5 = (x["high"] + x["low"]) / 2.0
        x["range_5m_bps"] = ((x["high"] - x["low"]) / mid5.where(mid5 > 0) * 10_000.0).clip(lower=0)

        for decision_at, g in x.groupby("decision_at", sort=True):
            g = g.sort_values("timestamp")
            if len(g) != cfg.required_subbars:
                continue
            # Strictly require contiguous 5-minute timestamps; duplicated or gapped
            # bars may not masquerade as a complete 4h interval.
            spacing = g["timestamp"].diff().dropna().dt.total_seconds().to_numpy()
            if len(spacing) and not np.allclose(spacing, cfg.subbar_minutes * 60.0, atol=1e-6):
                continue
            flow = g["flow_imbalance"].to_numpy(dtype=float)
            qv = g["quote_volume"].to_numpy(dtype=float)
            tc = g["trade_count"].to_numpy(dtype=float)
            ranges = g["range_5m_bps"].to_numpy(dtype=float)
            if not np.isfinite(flow).all():
                continue
            rows.append({
                "symbol": symbol,
                "decision_at": _utc(decision_at),
                "open": float(g.iloc[0]["open"]),
                "high": float(g["high"].max()),
                "low": float(g["low"].min()),
                "close": float(g.iloc[-1]["close"]),
                "subbar_count": int(len(g)),
                "flow_mean": float(np.mean(flow)),
                "flow_std": float(np.std(flow, ddof=0)),
                "flow_last": float(flow[-1]),
                "flow_slope": _fixed_slope(flow),
                "log_quote_volume": float(np.log1p(np.sum(qv))),
                "quote_volume_cv": _safe_cv(qv),
                "log_trade_count": float(np.log1p(np.sum(tc))),
                "trade_count_cv": _safe_cv(tc),
                "range_5m_median_bps": float(np.nanmedian(ranges)),
                "range_5m_p95_bps": float(np.nanquantile(ranges, 0.95)),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["symbol", "decision_at"]).reset_index(drop=True)


def add_price_and_target_features(bars: pd.DataFrame, config: V21HistoricalProxyConfig | None = None) -> pd.DataFrame:
    cfg = config or V21HistoricalProxyConfig()
    pieces: list[pd.DataFrame] = []
    for symbol, raw in bars.groupby("symbol", sort=True):
        g = raw.sort_values("decision_at").copy()
        close = g["close"].astype(float)
        g["ret_1"] = close.pct_change(1)
        g["ret_2"] = close.pct_change(2)
        g["ret_6"] = close.pct_change(6)
        g["ret_18"] = close.pct_change(18)
        g["rv_6"] = g["ret_1"].rolling(6, min_periods=6).std(ddof=0)
        g["rv_18"] = g["ret_1"].rolling(18, min_periods=18).std(ddof=0)
        g["ma_gap_6"] = close / close.rolling(6, min_periods=6).mean() - 1.0
        g["ma_gap_18"] = close / close.rolling(18, min_periods=18).mean() - 1.0
        g["target_return"] = close.shift(-1) / close - 1.0
        g["target_end_at"] = g["decision_at"].shift(-1)
        expected_next = pd.to_datetime(g["decision_at"], utc=True) + pd.Timedelta(hours=cfg.decision_horizon_hours)
        contiguous_target = pd.to_datetime(g["target_end_at"], utc=True) == expected_next
        g.loc[~contiguous_target, "target_return"] = np.nan
        g["target_positive"] = np.where(g["target_return"].notna(), (g["target_return"] > 0).astype(int), np.nan)
        pieces.append(g)
    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values(["symbol", "decision_at"]).reset_index(drop=True)


def build_historical_proxy_panel(frame: pd.DataFrame, config: V21HistoricalProxyConfig | None = None) -> pd.DataFrame:
    cfg = config or V21HistoricalProxyConfig()
    bars = aggregate_5m_to_4h(frame, cfg)
    panel = add_price_and_target_features(bars, cfg)
    required = list(PRICE_FEATURES + FLOW_PROXY_FEATURES) + ["target_return", "target_positive", "target_end_at"]
    panel = panel.dropna(subset=required).copy()
    start = _utc(cfg.source_start)
    end = _utc(cfg.source_end)
    panel = panel[(panel["decision_at"] >= start) & (panel["decision_at"] < end)].copy()
    return panel.reset_index(drop=True)


def _make_model(model_name: str):
    if model_name == "logistic":
        return Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=2000, solver="lbfgs", random_state=0)),
        ])
    if model_name == "hgb":
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=150,
            max_leaf_nodes=15,
            l2_regularization=0.10,
            min_samples_leaf=20,
            random_state=0,
        )
    raise ValueError(f"unknown model {model_name}")


def walk_forward_probabilities(
    panel: pd.DataFrame,
    *,
    model_name: str,
    feature_names: tuple[str, ...],
    config: V21HistoricalProxyConfig | None = None,
) -> pd.DataFrame:
    cfg = config or V21HistoricalProxyConfig()
    outputs: list[pd.DataFrame] = []
    for symbol in cfg.symbols:
        asset = panel[panel["symbol"] == symbol].sort_values("decision_at").copy()
        for month in cfg.evaluation_months:
            eval_start = _utc(f"{month}-01T00:00:00Z")
            eval_end = eval_start + pd.offsets.MonthBegin(1)
            train = asset[pd.to_datetime(asset["target_end_at"], utc=True) < eval_start].copy()
            test = asset[(asset["decision_at"] >= eval_start) & (asset["decision_at"] < eval_end)].copy()
            if train.empty or test.empty:
                continue
            y_train = train["target_positive"].astype(int)
            if y_train.nunique() < 2:
                continue
            model = _make_model(model_name)
            model.fit(train[list(feature_names)].astype(float), y_train)
            probability = model.predict_proba(test[list(feature_names)].astype(float))[:, 1]
            out = test[["symbol", "decision_at", "target_end_at", "target_return"]].copy()
            out["probability"] = probability
            out["model"] = model_name
            out["feature_family"] = "price_only" if tuple(feature_names) == PRICE_FEATURES else "price_plus_flow_proxy"
            out["eval_month"] = month
            out["train_rows"] = int(len(train))
            out["train_end_target_at"] = pd.to_datetime(train["target_end_at"], utc=True).max().isoformat()
            outputs.append(out)
    if not outputs:
        return pd.DataFrame()
    return pd.concat(outputs, ignore_index=True).sort_values(["decision_at", "symbol"]).reset_index(drop=True)


def portfolio_net_returns(
    predictions: pd.DataFrame,
    *,
    cost_bps: float,
    config: V21HistoricalProxyConfig | None = None,
) -> pd.DataFrame:
    cfg = config or V21HistoricalProxyConfig()
    if predictions.empty:
        return pd.DataFrame()
    prob = predictions.pivot(index="decision_at", columns="symbol", values="probability").reindex(columns=cfg.symbols)
    fwd = predictions.pivot(index="decision_at", columns="symbol", values="target_return").reindex(columns=cfg.symbols)
    valid = prob.notna().all(axis=1) & fwd.notna().all(axis=1)
    prob = prob.loc[valid]
    fwd = fwd.loc[valid]
    weights = (prob >= cfg.probability_threshold).astype(float) * cfg.max_weight_per_asset
    turnover = weights.diff().abs().fillna(weights.abs()).sum(axis=1)
    gross = (weights * fwd).sum(axis=1)
    cost_rate = float(cost_bps) / 10_000.0
    costs = turnover * cost_rate
    net = gross - costs
    if len(net):
        terminal_turnover = float(weights.iloc[-1].abs().sum())
        turnover.iloc[-1] += terminal_turnover
        terminal_cost = terminal_turnover * cost_rate
        costs.iloc[-1] += terminal_cost
        net.iloc[-1] -= terminal_cost
    out = pd.DataFrame({
        "decision_at": net.index,
        "gross_return": gross.to_numpy(dtype=float),
        "net_return": net.to_numpy(dtype=float),
        "turnover": turnover.to_numpy(dtype=float),
        "explicit_cost": costs.to_numpy(dtype=float),
        "gross_exposure": weights.sum(axis=1).to_numpy(dtype=float),
    })
    return out.reset_index(drop=True)


def performance_metrics(returns: pd.DataFrame, annualization_periods: int = 6 * 365) -> dict:
    if returns.empty:
        return {"n": 0}
    r = returns["net_return"].to_numpy(dtype=float)
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    mean_r = float(np.mean(r))
    std_r = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    downside = r[r < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    sharpe = mean_r / std_r * sqrt(annualization_periods) if std_r > 0 else 0.0
    sortino = mean_r / downside_std * sqrt(annualization_periods) if downside_std > 0 else 0.0
    positive_sum = float(r[r > 0].sum())
    negative_sum = float(-r[r < 0].sum())
    return {
        "n": int(len(r)),
        "total_return": float(equity[-1] - 1.0),
        "mean_net_return": mean_r,
        "annualized_sharpe": float(sharpe),
        "annualized_sortino": float(sortino),
        "max_drawdown": float(drawdown.min()),
        "profit_factor": float(positive_sum / negative_sum) if negative_sum > 0 else None,
        "turnover_sum": float(returns["turnover"].sum()),
        "explicit_cost_sum": float(returns["explicit_cost"].sum()),
        "mean_gross_exposure": float(returns["gross_exposure"].mean()),
    }


def _moving_block_indices(n: int, block_length: int, rng: np.random.Generator) -> np.ndarray:
    if n <= 0:
        return np.asarray([], dtype=int)
    block = max(1, min(int(block_length), n))
    pieces: list[np.ndarray] = []
    while sum(len(x) for x in pieces) < n:
        start = int(rng.integers(0, n))
        pieces.append((start + np.arange(block)) % n)
    return np.concatenate(pieces)[:n]


def paired_block_inference(
    difference: pd.Series,
    *,
    reps: int,
    block_length: int,
    seed: int,
) -> dict:
    x = pd.to_numeric(difference, errors="coerce").dropna().to_numpy(dtype=float)
    if len(x) < 8:
        return {"n": int(len(x)), "mean_difference": float(np.mean(x)) if len(x) else None, "ci95": [None, None], "one_sided_p": None}
    rng = np.random.default_rng(seed)
    raw_means = np.empty(reps, dtype=float)
    centered_means = np.empty(reps, dtype=float)
    observed = float(np.mean(x))
    centered = x - observed
    for i in range(reps):
        idx = _moving_block_indices(len(x), block_length, rng)
        raw_means[i] = float(np.mean(x[idx]))
        centered_means[i] = float(np.mean(centered[idx]))
    p = float((1.0 + np.sum(centered_means >= observed)) / (reps + 1.0))
    return {
        "n": int(len(x)),
        "mean_difference": observed,
        "ci95": [float(np.quantile(raw_means, 0.025)), float(np.quantile(raw_means, 0.975))],
        "one_sided_p": p,
    }


def benjamini_hochberg(p_values: dict[str, float | None]) -> dict[str, float | None]:
    valid = [(k, float(v)) for k, v in p_values.items() if v is not None and np.isfinite(float(v))]
    if not valid:
        return {k: None for k in p_values}
    ordered = sorted(valid, key=lambda kv: kv[1])
    m = len(ordered)
    q_raw = [min(1.0, p * m / (i + 1)) for i, (_, p) in enumerate(ordered)]
    q_adj = q_raw[:]
    for i in range(m - 2, -1, -1):
        q_adj[i] = min(q_adj[i], q_adj[i + 1])
    out = {k: None for k in p_values}
    for (key, _), q in zip(ordered, q_adj):
        out[key] = float(q)
    return out


def joint_max_t_fwer(
    differences: pd.DataFrame,
    *,
    reps: int,
    block_length: int,
    seed: int,
) -> dict[str, float]:
    x = differences.dropna().astype(float)
    if x.empty:
        return {}
    observed = x.mean(axis=0)
    centered = x - observed
    rng = np.random.default_rng(seed)
    max_null = np.empty(reps, dtype=float)
    for i in range(reps):
        idx = _moving_block_indices(len(x), block_length, rng)
        max_null[i] = float(centered.iloc[idx].mean(axis=0).max())
    return {
        col: float((1.0 + np.sum(max_null >= float(observed[col]))) / (reps + 1.0))
        for col in x.columns
    }


def _monthly_total(returns: pd.DataFrame) -> dict[str, float]:
    if returns.empty:
        return {}
    x = returns.copy()
    x["month"] = pd.to_datetime(x["decision_at"], utc=True).dt.strftime("%Y-%m")
    return {
        month: float(np.prod(1.0 + g["net_return"].to_numpy(dtype=float)) - 1.0)
        for month, g in x.groupby("month", sort=True)
    }


def run_historical_proxy_stress_test(panel: pd.DataFrame, config: V21HistoricalProxyConfig | None = None) -> dict:
    cfg = config or V21HistoricalProxyConfig()
    families = {
        "price_only": PRICE_FEATURES,
        "price_plus_flow_proxy": PRICE_FEATURES + FLOW_PROXY_FEATURES,
    }
    prediction_map: dict[tuple[str, str], pd.DataFrame] = {}
    for model in ("logistic", "hgb"):
        for family, features in families.items():
            prediction_map[(model, family)] = walk_forward_probabilities(panel, model_name=model, feature_names=features, config=cfg)

    strategies: dict[str, dict] = {}
    return_map: dict[str, pd.DataFrame] = {}
    comparisons: dict[str, dict] = {}
    difference_series: dict[str, pd.Series] = {}
    p_values: dict[str, float | None] = {}

    for model in ("logistic", "hgb"):
        for cost_bps in cfg.cost_bps_grid:
            cells: dict[str, pd.DataFrame] = {}
            for family in families:
                key = f"{model}|{family}|{cost_bps:g}bps"
                ret = portfolio_net_returns(prediction_map[(model, family)], cost_bps=cost_bps, config=cfg)
                cells[family] = ret
                return_map[key] = ret
                strategies[key] = {
                    "model": model,
                    "feature_family": family,
                    "cost_bps": float(cost_bps),
                    "metrics": performance_metrics(ret, cfg.annualization_periods),
                    "monthly_total_return": _monthly_total(ret),
                }
            left = cells["price_only"].set_index("decision_at")["net_return"]
            right = cells["price_plus_flow_proxy"].set_index("decision_at")["net_return"]
            aligned = pd.concat([left.rename("price_only"), right.rename("plus")], axis=1).dropna()
            diff = aligned["plus"] - aligned["price_only"]
            comp_id = f"{model}|{cost_bps:g}bps|plus_minus_price"
            inference = paired_block_inference(
                diff,
                reps=cfg.bootstrap_reps,
                block_length=cfg.bootstrap_block_length,
                seed=cfg.bootstrap_seed + len(comparisons),
            )
            comparisons[comp_id] = {
                "model": model,
                "cost_bps": float(cost_bps),
                "inference": inference,
                "incremental_total_return_pp": float(
                    (strategies[f"{model}|price_plus_flow_proxy|{cost_bps:g}bps"]["metrics"].get("total_return", 0.0)
                     - strategies[f"{model}|price_only|{cost_bps:g}bps"]["metrics"].get("total_return", 0.0)) * 100.0
                ),
                "monthly_incremental_total_return": {
                    month: float(
                        strategies[f"{model}|price_plus_flow_proxy|{cost_bps:g}bps"]["monthly_total_return"].get(month, 0.0)
                        - strategies[f"{model}|price_only|{cost_bps:g}bps"]["monthly_total_return"].get(month, 0.0)
                    )
                    for month in cfg.evaluation_months
                },
            }
            difference_series[comp_id] = diff.rename(comp_id)
            p_values[comp_id] = inference.get("one_sided_p")

    q_values = benjamini_hochberg(p_values)
    common_diff = pd.concat(difference_series.values(), axis=1, join="inner") if difference_series else pd.DataFrame()
    fwer = joint_max_t_fwer(
        common_diff,
        reps=cfg.bootstrap_reps,
        block_length=cfg.bootstrap_block_length,
        seed=cfg.bootstrap_seed + 999,
    )
    for key in comparisons:
        comparisons[key]["fdr_q"] = q_values.get(key)
        comparisons[key]["max_t_fwer_p"] = fwer.get(key)

    primary_ids = [f"{model}|{cfg.primary_cost_bps:g}bps|plus_minus_price" for model in ("logistic", "hgb")]
    primary_support = []
    for key in primary_ids:
        item = comparisons[key]
        inf = item["inference"]
        ci = inf.get("ci95") or [None, None]
        primary_support.append(bool(
            inf.get("mean_difference") is not None
            and inf["mean_difference"] > 0
            and ci[0] is not None and ci[0] > 0
            and item.get("fdr_q") is not None and item["fdr_q"] <= 0.10
            and item.get("max_t_fwer_p") is not None and item["max_t_fwer_p"] <= 0.10
        ))
    supportive = any(primary_support)

    return {
        "version": "v0.21-H",
        "research_status": "HISTORICAL_SINGLE_VENUE_FLOW_PROXY_STRESS_TEST_NOT_PROSPECTIVE",
        "config": asdict(cfg),
        "price_features": list(PRICE_FEATURES),
        "flow_proxy_features": list(FLOW_PROXY_FEATURES),
        "panel_rows": int(len(panel)),
        "panel_symbols": sorted(panel["symbol"].unique().tolist()) if len(panel) else [],
        "panel_start": pd.to_datetime(panel["decision_at"], utc=True).min().isoformat() if len(panel) else None,
        "panel_end": pd.to_datetime(panel["decision_at"], utc=True).max().isoformat() if len(panel) else None,
        "strategies": strategies,
        "comparisons": comparisons,
        "planned_comparison_count": 8,
        "realized_comparison_count": int(len(comparisons)),
        "primary_cost_bps": float(cfg.primary_cost_bps),
        "interpretation": "HISTORICAL_PROXY_SUPPORTIVE_FOR_FURTHER_PROSPECTIVE_TESTING" if supportive else "NO_COST_ROBUST_HISTORICAL_PROXY_CONFIRMATION",
        "signal_authorized": False,
        "paper_strategy_replacement_authorized": False,
        "testnet_promotion_authorized": False,
        "live_execution_authorized": False,
    }


def probabilistic_sharpe_ratio(returns: Iterable[float], benchmark_sharpe_per_period: float = 0.0) -> float | None:
    """Optional descriptive PSR diagnostic; not used as the promotion gate."""
    x = np.asarray(list(returns), dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 8:
        return None
    mean_x = float(np.mean(x))
    std_x = float(np.std(x, ddof=1))
    if std_x <= 0:
        return None
    sr = mean_x / std_x
    centered = (x - mean_x) / std_x
    skew = float(np.mean(centered ** 3))
    kurt = float(np.mean(centered ** 4))
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return None
    z = (sr - benchmark_sharpe_per_period) * sqrt(len(x) - 1.0) / sqrt(denom)
    return float(NormalDist().cdf(z))
