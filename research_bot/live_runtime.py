"""Operator-run spot execution for a frozen PPO artifact; no service endpoint.

The default mode previews orders using public data. MASTER v3 Stage 0 blocks
private modes until a later promotion gate is implemented and verified. No
credentials or private requests are needed to display CLI help.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd

from .data import _timeframe_to_milliseconds
from .ensemble_features import FEATURE_COLUMNS, extract_all_features
from .execution.config import LiveConfig


class SafetyStop(RuntimeError):
    pass


class FrozenPpoPolicy:
    """Verify same-fold weights/scaler and preserve the training observation seam."""
    def __init__(self, run_dir, *, fold: int, seed: int):
        from stable_baselines3 import PPO
        from .ensemble_env import TradingEnvConfig
        root = Path(run_dir).resolve()
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest.get("status") != "COMPLETED" or not isinstance(manifest.get("synthetic"), bool):
            raise ValueError("completed manifest with explicit synthetic status is required")
        if manifest.get("feature_columns") != FEATURE_COLUMNS:
            raise ValueError("model feature schema mismatch")
        source_root = Path(__file__).resolve().parents[1]
        for name in ("ensemble_features.py", "ensemble_env.py", "ensemble_agent.py", "ensemble_rl.py"):
            relative = f"research_bot/{name}"
            expected = manifest.get("source", {}).get("file_sha256", {}).get(relative)
            if expected != hashlib.sha256((source_root / relative).read_bytes()).hexdigest():
                raise ValueError("training source semantics differ from runtime")
        if isinstance(fold, bool) or isinstance(seed, bool) or not isinstance(fold, int) or not isinstance(seed, int) or fold < 0 or seed < 0:
            raise ValueError("fold and seed must be nonnegative integers")
        model_path = root / f"fold_{fold}/ppo_seed_{seed}.zip"
        scaler_path = root / f"fold_{fold}/scaler.json"
        snapshots = {}
        for path in (model_path, scaler_path):
            if path.resolve().parent != (root / f"fold_{fold}").resolve() or not path.resolve().is_relative_to(root):
                raise ValueError("artifact path escaped the run directory")
            snapshots[path] = path.read_bytes()
            digest = hashlib.sha256(snapshots[path]).hexdigest()
            if manifest.get("artifacts_sha256", {}).get(str(path.relative_to(root))) != digest:
                raise ValueError("model/scaler artifact checksum mismatch")
        scaler = json.loads(snapshots[scaler_path])
        if scaler.get("columns") != FEATURE_COLUMNS:
            raise ValueError("scaler feature order mismatch")
        self.mean = np.asarray(scaler["mean"], dtype=float)
        self.scale = np.asarray(scaler["scale"], dtype=float)
        if self.mean.shape != (len(FEATURE_COLUMNS),) or self.scale.shape != self.mean.shape or not np.isfinite(self.mean).all() or not np.isfinite(self.scale).all() or np.any(self.scale <= 0):
            raise ValueError("invalid scaler values")
        self.config = TradingEnvConfig(**manifest["environment"])
        self.synthetic = manifest["synthetic"]
        self.market_identity = manifest.get("market_identity")
        self.bar_seconds = float(manifest["bar_seconds"])
        self.model = PPO.load(io.BytesIO(snapshots[model_path]), device="cpu")
        if self.model.observation_space.shape != (self.config.lookback, len(FEATURE_COLUMNS) + 4) or getattr(self.model.action_space, "n", None) != 3:
            raise ValueError("PPO spaces do not match the live observation/action contract")
        self.fingerprint = hashlib.sha256(manifest_bytes + snapshots[model_path] + snapshots[scaler_path]).hexdigest()

    def observation(self, history, *, cash, quantity, initial_equity, model_peak):
        frame = pd.DataFrame(history)
        frame["timestamp"] = pd.to_datetime(frame.pop("timestamp_ms"), unit="ms", utc=True)
        featured, columns = extract_all_features(frame)
        values = featured[columns].iloc[-self.config.lookback:].to_numpy(dtype=float)
        if len(values) != self.config.lookback or not np.isfinite(values).all():
            raise SafetyStop("FEATURE_HISTORY_NOT_READY")
        close = float(frame.close.iloc[-1])
        equity = cash + quantity * close
        state = np.array([quantity * close / equity, cash / initial_equity,
                          equity / initial_equity, max(0., 1 - equity / model_peak)])
        observation = np.concatenate(((values-self.mean)/self.scale,
                                      np.broadcast_to(state, (len(values), 4))), axis=1)
        if not np.isfinite(observation).all() or np.any(np.abs(observation) > np.finfo(np.float32).max):
            raise SafetyStop("NONFINITE_POLICY_OBSERVATION")
        return observation.astype(np.float32)

    def predict(self, observation):
        action, _ = self.model.predict(observation, deterministic=True)
        return int(np.asarray(action).item())


def risk_marks(state, equity, now_ms, config):
    """Persist anchors; an overnight loss is measured before any day reset."""
    if not math.isfinite(equity) or equity <= 0:
        raise SafetyStop("INVALID_ACCOUNT_EQUITY")
    state = dict(state)
    day = pd.Timestamp(now_ms, unit="ms", tz="UTC").date().isoformat()
    if state.get("day") != day:
        state["day"] = day
        state["day_start_equity"] = float(state.get("last_equity", equity))
    state["peak_equity"] = max(float(state.get("peak_equity", equity)), equity)
    reasons = []
    if equity <= state["day_start_equity"] * (1 - config.max_daily_loss):
        reasons.append("MAX_DAILY_LOSS")
    if equity <= state["peak_equity"] * (1 - config.max_drawdown):
        reasons.append("MAX_DRAWDOWN")
    state["last_equity"] = equity
    if reasons:
        state["halted"] = True
        state["halt_reason"] = "+".join(reasons)
        state["target_weight"] = 0.
    return state


def balances_match(actual, expected):
    return all(abs(float(actual[k])-float(expected[k])) <= 1e-8 * max(1., abs(float(expected[k])))
               for k in ("base_total", "quote_total"))


def settlement_matches(actual, previous, intent, result, config):
    """Use actual filled quote cost; unknown fee denomination is bounded, not guessed."""
    filled = float(result.get("filled", 0))
    if not math.isfinite(filled) or filled < 0:
        return False
    if filled == 0:
        return balances_match(actual, previous)
    cost = result.get("cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost <= 0:
        return False
    buy = intent["side"] == "buy"
    base = previous["base_total"] + (filled if buy else -filled)
    quote = previous["quote_total"] + (-cost if buy else cost)
    base_currency, quote_currency = config.symbol.split("/")
    fees = result.get("fees")
    if fees is not None:
        if not isinstance(fees, list):
            return False
        fee_quote = 0.
        for fee in fees:
            if not isinstance(fee, dict):
                return False
            currency, value = fee.get("currency"), fee.get("cost")
            if currency not in {base_currency, quote_currency} or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                return False
            if currency == base_currency:
                base -= value
                fee_quote += value * cost / filled
            else:
                quote -= value
                fee_quote += value
        if fee_quote > cost * config.max_fee_bps / 10000 + 1e-8:
            return False
        return balances_match(actual, {"base_total": base, "quote_total": quote})
    fraction = config.max_fee_bps / 10000
    base_fee = base - actual["base_total"]
    quote_fee = quote - actual["quote_total"]
    return (base_fee >= -1e-8 and quote_fee >= -1e-8
            and max(0., base_fee) * cost/filled + max(0., quote_fee) <= cost*fraction+1e-8)


def order_plan(config, quote, balances, target_weight):
    """Post-cost target equation, then cap size at operator's per-order ceiling."""
    mid, quantity, cash = quote.mid, balances["base_total"], balances["quote_total"]
    equity = cash + quantity*mid
    difference = target_weight*equity - quantity*mid
    if abs(difference) <= 1e-10 * max(1., equity):
        return None
    side = "buy" if difference > 0 else "sell"
    slip, fee = config.max_slippage_bps/10000, config.max_fee_bps/10000
    price = quote.ask*(1+slip) if side == "buy" else quote.bid*(1-slip)
    if side == "buy":
        units = difference / (mid + target_weight*(price*(1+fee)-mid))
        units = min(units, balances["quote_free"]/(price*(1+fee)))
    else:
        units = -difference / (mid - target_weight*(mid-price*(1-fee)))
        units = min(units, balances["base_free"])
    units = min(units, config.max_order_quote/price)
    return None if units <= 0 else {"side": side, "quantity": units, "limit_price": price}


class LiveController:
    def __init__(self, exchange, ledger, policy, config, *, mode="dry-run", clock_ms=None):
        if mode not in {"dry-run", "testnet", "live"}:
            raise ValueError("invalid execution mode")
        self.exchange, self.ledger, self.policy, self.config, self.mode = exchange, ledger, policy, config, mode
        if exchange.mode != mode or exchange.symbol != config.symbol or exchange.exchange_id != config.exchange_id:
            raise SafetyStop("EXCHANGE_EXECUTION_IDENTITY_MISMATCH")
        self.clock_ms = clock_ms or (lambda: int(time.time()*1000))
        if policy.bar_seconds*1000 != _timeframe_to_milliseconds(config.timeframe):
            raise SafetyStop("MODEL_TIMEFRAME_MISMATCH")
        if policy.config.lookback > 222 or policy.config.cvar_window > 298:
            raise SafetyStop("MODEL_HISTORY_EXCEEDS_299_BAR_BOOTSTRAP")
        if config.max_position_weight > policy.config.max_asset_weight or config.max_drawdown > policy.config.max_drawdown:
            raise SafetyStop("RUNTIME_RISK_LIMIT_EXCEEDS_TRAINING_LIMIT")
        if mode == "live":
            if policy.synthetic:
                raise SafetyStop("SYNTHETIC_MODEL_CANNOT_TRADE_LIVE")
            if policy.market_identity != {"exchange_id": config.exchange_id, "symbol": config.symbol, "timeframe": config.timeframe}:
                raise SafetyStop("MODEL_MARKET_IDENTITY_MISMATCH")

    def _latch(self, state, reason):
        state.update(halted=True, halt_reason=reason, target_weight=0.)
        self.ledger.set_state(state)

    def _validate_receipt(self, row, result):
        intent = row["intent"]
        if (result.get("clientOrderId") != row["id"] or result.get("symbol") != intent["symbol"]
                or result.get("side") != intent["side"]):
            raise SafetyStop("RECOVERED_ORDER_IDENTITY_MISMATCH")
        amount, filled = result.get("amount"), result.get("filled")
        if (isinstance(amount, bool) or isinstance(filled, bool)
                or not isinstance(amount, (int, float)) or not isinstance(filled, (int, float))
                or not math.isfinite(amount) or not math.isfinite(filled) or filled < 0
                or not math.isclose(amount, intent["amount"], rel_tol=1e-8, abs_tol=1e-12)
                or filled > intent["amount"] * (1 + 1e-8)):
            raise SafetyStop("RECOVERED_ORDER_SIZE_MISMATCH")
        cost = result.get("cost")
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                raise SafetyStop("RECOVERED_ORDER_COST_INVALID")
            bound = filled * intent["price"]
            if ((filled == 0 and cost != 0) or (filled > 0 and cost <= 0)
                    or (intent["side"] == "buy" and cost > bound * (1 + 1e-8))
                    or (intent["side"] == "sell" and cost < bound * (1 - 1e-8))):
                raise SafetyStop("RECOVERED_FILL_LIMIT_MISMATCH")

    @staticmethod
    def _preserve_execution_evidence(row, result):
        result = dict(result)
        if result.get("filled") == row["result"].get("filled"):
            for name in ("cost", "fees"):
                if result.get(name) is None and row["result"].get(name) is not None:
                    result[name] = row["result"][name]
        return result

    def _recover(self, state):
        refreshed = set()
        for row in self.ledger.pending_orders():
            state["settlement_pending"] = row["id"]
            self.ledger.set_state(state)
            try:
                result = self.exchange.find_order(row["id"], row["result"].get("id"))
                if result is None:
                    return "SUBMIT_UNRESOLVED"
                result = self._preserve_execution_evidence(row, result)
                self._validate_receipt(row, result)
                self.ledger.update_order(row["id"], result["status"], result)
                refreshed.add(row["id"])
            except Exception as exc:
                self.ledger.update_order(row["id"], "UNKNOWN", {"error_type": type(exc).__name__})
                return "RECONCILIATION_UNAVAILABLE"
        if self.ledger.pending_orders():
            return "ORDER_PENDING"
        # A terminal receipt can arrive before its cost/fees or balances. Keep
        # refreshing that exact order, without reserving a replacement.
        pending_id = state.get("settlement_pending")
        row = self.ledger.get_order(pending_id) if pending_id else None
        if row and pending_id not in refreshed and row["result"].get("id"):
            try:
                result = self.exchange.find_order(pending_id, row["result"]["id"])
                if result is None:
                    return "SETTLEMENT_RECEIPT_UNAVAILABLE"
                result = self._preserve_execution_evidence(row, result)
                self._validate_receipt(row, result)
                self.ledger.update_order(pending_id, result["status"], result)
            except Exception:
                # Preserve the terminal receipt; transient failure is not a
                # state regression or permission to submit again.
                return "SETTLEMENT_RECEIPT_UNAVAILABLE"
        return None

    def cycle(self):
        with self.ledger.session_lock():
            return self._cycle()

    def _cycle(self):
        from .live_exchange import BelowMinimumOrder
        cfg, now = self.config, self.clock_ms()
        cvar = None
        risk_reduction = False
        state = self.ledger.get_state()
        if Path(cfg.kill_file).exists():
            self._latch(state, "OPERATOR_STOP")
        if self.mode != "dry-run":
            pending = self._recover(state)
            if pending:
                return {"status": "BLOCKED", "reason": pending, "halted": bool(state.get("halted"))}
        quote = self.exchange.fetch_quote(now, int(cfg.quote_max_age_seconds*1000))
        if quote.spread_bps > cfg.max_spread_bps:
            return {"status": "BLOCKED", "reason": "SPREAD_LIMIT"}
        if self.mode == "dry-run":
            balances = {"base_total": 0., "base_free": 0., "quote_total": cfg.capital_limit_quote,
                        "quote_free": cfg.capital_limit_quote, "other_total": {}}
        else:
            balances = self.exchange.fetch_balances()
            if balances.get("other_total"):
                self._latch(state, "THIRD_ASSET_ACCOUNTING_UNSUPPORTED")
                return {"status": "BLOCKED", "reason": state["halt_reason"]}
            if self.exchange.fetch_account_open_orders():
                self._latch(state, "UNRECONCILED_OPEN_ORDER")
                return {"status": "BLOCKED", "reason": state["halt_reason"]}
        if not state.get("initialized"):
            if balances["base_total"] > 1e-12 or balances["quote_total"] > cfg.capital_limit_quote or balances["quote_total"] <= 0:
                raise SafetyStop("USE_DEDICATED_FLAT_ACCOUNT_WITHIN_CAPITAL_CEILING")
            state.update(initialized=True, initial_equity=balances["quote_total"],
                         model_peak_equity=balances["quote_total"], target_weight=0., last_bar_ms=-1,
                         account={k: balances[k] for k in ("base_total", "quote_total")})
        if self.mode != "dry-run":
            pending_id = state.get("settlement_pending")
            if pending_id:
                row = self.ledger.get_order(pending_id)
                if row is None:
                    # Crash before claiming the intent: no outbound request could occur.
                    state.pop("settlement_pending", None)
                else:
                    if not settlement_matches(balances, row["intent"]["previous_account"], row["intent"], row["result"], cfg):
                        return {"status": "BLOCKED", "reason": "BALANCE_SETTLEMENT_PENDING"}
                    state["account"] = {k: balances[k] for k in ("base_total", "quote_total")}
                    state.pop("settlement_pending", None)
            if not balances_match(balances, state["account"]):
                self._latch(state, "UNEXPLAINED_ACCOUNT_CHANGE")
                return {"status": "BLOCKED", "reason": state["halt_reason"]}
        state = risk_marks(state, balances["quote_total"]+balances["base_total"]*quote.mid, now, cfg)
        self.ledger.set_state(state)
        if not state.get("halted"):
            # Historical risk must refresh even when strategy entry is late or
            # the same bar was consumed. A risk reduction needs no new BUY vote.
            candles = self.exchange.fetch_closed_candles(cfg.timeframe, 299, now)
            records = candles.rename(columns={"timestamp": "timestamp_ms"}).copy()
            records["timestamp_ms"] = (pd.to_datetime(records.timestamp_ms, utc=True).dt.as_unit("ns").astype("int64") // 1_000_000).astype(int)
            self.ledger.append_candles(records.to_dict("records"), _timeframe_to_milliseconds(cfg.timeframe))
            history = self.ledger.candles()
            from .ensemble_env import empirical_cvar
            closes = np.array([row["close"] for row in history], dtype=float)
            sample = (closes[1:] / closes[:-1] - 1)[-self.policy.config.cvar_window:]
            if len(sample) < self.policy.config.cvar_min_samples:
                raise SafetyStop("CVAR_HISTORY_NOT_READY")
            cvar = max(0., empirical_cvar(-sample, self.policy.config.cvar_alpha))
            state["cvar_cap"] = min(cfg.max_position_weight, self.policy.config.max_cvar/cvar) if cvar > 0 else cfg.max_position_weight
            unseen = [row["close"] for row in history
                      if row["timestamp_ms"] > state.get("last_model_mark_bar_ms", -1)]
            if unseen:
                closed_peak = max(balances["quote_total"] + balances["base_total"]*close for close in unseen)
                state["model_peak_equity"] = max(state["model_peak_equity"], closed_peak)
                state["peak_equity"] = max(state["peak_equity"], closed_peak)
                state["last_model_mark_bar_ms"] = history[-1]["timestamp_ms"]
            state = risk_marks(state, balances["quote_total"]+balances["base_total"]*quote.mid, now, cfg)
            self.ledger.set_state(state)
        equity_mid = balances["quote_total"]+balances["base_total"]*quote.mid
        risk_cap = min(cfg.max_position_weight, cfg.capital_limit_quote*cfg.max_position_weight/equity_mid,
                       state.get("cvar_cap", cfg.max_position_weight))
        if state.get("halted"):
            target, bar_ms = 0., now
            if balances["base_total"] <= 1e-12:
                return {"status": "HALTED", "reason": state.get("halt_reason"), "residual_base": 0.}
        elif balances["base_total"]*quote.mid/equity_mid > risk_cap + 1e-9:
            # Safety reductions may run between strategy bars, but never add
            # exposure. Pending/partial intents still pass through recovery.
            target, bar_ms, risk_reduction = min(state["target_weight"], risk_cap), now, True
        else:
            bar_ms = history[-1]["timestamp_ms"]
            if bar_ms <= state["last_bar_ms"]:
                return {"status": "WAITING", "reason": "BAR_ALREADY_CONSUMED"}
            if now - bar_ms - _timeframe_to_milliseconds(cfg.timeframe) > cfg.entry_grace_seconds*1000:
                return {"status": "WAITING", "reason": "ENTRY_WINDOW_EXPIRED"}
            observation = self.policy.observation(history, cash=balances["quote_total"],
                quantity=balances["base_total"], initial_equity=state["initial_equity"], model_peak=state["model_peak_equity"])
            action = self.policy.predict(observation)
            if isinstance(action, bool) or action not in {0, 1, 2}:
                raise SafetyStop("INVALID_POLICY_ACTION")
            cap = min(cfg.max_position_weight, cfg.capital_limit_quote*cfg.max_position_weight/(balances["quote_total"]+balances["base_total"]*quote.mid))
            if cvar > 0:
                cap = min(cap, self.policy.config.max_cvar/cvar)
            target = cap if action == 1 else 0. if action == 2 else min(state["target_weight"], cap)
            decision_id = hashlib.sha256(f"{self.policy.fingerprint}|{bar_ms}".encode()).hexdigest()
            if not self.ledger.claim_decision(decision_id, {"timestamp_ms": bar_ms, "action": action, "target_weight": target}):
                return {"status": "WAITING", "reason": "DECISION_ALREADY_CLAIMED"}
            state.update(last_bar_ms=bar_ms, target_weight=target)
            self.ledger.set_state(state)
        # Inference and persistence can take time. Refresh executable prices and
        # check the kill file again directly at the private request boundary.
        quote = self.exchange.fetch_quote(self.clock_ms(), int(cfg.quote_max_age_seconds*1000))
        if quote.spread_bps > cfg.max_spread_bps:
            return {"status": "BLOCKED", "reason": "SPREAD_LIMIT"}
        if Path(cfg.kill_file).exists():
            self._latch(state, "OPERATOR_STOP")
            target = 0.
        if self.mode != "dry-run":
            fresh_balances = self.exchange.fetch_balances()
            if self.exchange.fetch_account_open_orders():
                self._latch(state, "UNRECONCILED_OPEN_ORDER")
                return {"status": "BLOCKED", "reason": state["halt_reason"]}
            if fresh_balances.get("other_total") or not balances_match(fresh_balances, balances):
                self._latch(state, "ACCOUNT_CHANGED_BEFORE_SUBMIT")
                return {"status": "BLOCKED", "reason": state["halt_reason"]}
            balances = fresh_balances
        fresh_now = self.clock_ms()
        if fresh_now - quote.timestamp_ms > cfg.quote_max_age_seconds * 1000:
            return {"status": "BLOCKED", "reason": "QUOTE_EXPIRED_BEFORE_SUBMIT"}
        state = risk_marks(state, balances["quote_total"]+balances["base_total"]*quote.mid, fresh_now, cfg)
        if state.get("halted"):
            target = 0.
        else:
            if not risk_reduction and fresh_now - bar_ms - _timeframe_to_milliseconds(cfg.timeframe) > cfg.entry_grace_seconds*1000:
                self.ledger.set_state(state)
                return {"status": "WAITING", "reason": "ENTRY_WINDOW_EXPIRED"}
            equity = balances["quote_total"]+balances["base_total"]*quote.mid
            cap = min(cfg.max_position_weight, cfg.capital_limit_quote*cfg.max_position_weight/equity,
                      state.get("cvar_cap", cfg.max_position_weight))
            if cvar and cvar > 0:
                cap = min(cap, self.policy.config.max_cvar/cvar)
            if risk_reduction:
                if balances["base_total"]*quote.mid/equity <= cap + 1e-9:
                    self.ledger.set_state(state)
                    return {"status": "NO_ORDER", "reason": "RISK_REDUCTION_NO_LONGER_NEEDED"}
                target = min(state["target_weight"], cap)
            else:
                target = min(target, cap)
        state["target_weight"] = target
        self.ledger.set_state(state)
        plan = order_plan(cfg, quote, balances, target)
        if plan is None:
            return {"status": "NO_ORDER", "target_weight": target}
        if risk_reduction and plan["side"] != "sell":
            return {"status": "NO_ORDER", "reason": "RISK_REDUCTION_NO_LONGER_NEEDED"}
        try:
            intent = self.exchange.prepare_order(plan["side"], plan["quantity"], plan["limit_price"], cfg.max_order_quote)
        except BelowMinimumOrder:
            return {"status": "NO_ORDER", "reason": "BELOW_VENUE_MINIMUM"}
        if self.mode == "dry-run":
            return {"status": "PREVIEW", "mode": self.mode, "intent": intent, "target_weight": target}
        client_id = "mb" + hashlib.sha256(f"{self.policy.fingerprint}|{bar_ms}|{intent['side']}".encode()).hexdigest()[:26]
        intent["previous_account"] = {k: balances[k] for k in ("base_total", "quote_total")}
        state["settlement_pending"] = client_id
        self.ledger.set_state(state)
        if not self.ledger.claim_order(client_id, intent):
            return {"status": "BLOCKED", "reason": "ORDER_ALREADY_CLAIMED"}
        # A stop arriving after a BUY was planned may not let that BUY escape.
        if intent["side"] == "buy" and Path(cfg.kill_file).exists():
            self._latch(state, "OPERATOR_STOP")
            self.ledger.update_order(client_id, "REJECTED", {"filled": 0., "reason": "OPERATOR_STOP"})
            return {"status": "HALTED", "reason": "OPERATOR_STOP"}
        if self.clock_ms() - quote.timestamp_ms > cfg.quote_max_age_seconds * 1000:
            self.ledger.update_order(client_id, "REJECTED", {"filled": 0., "reason": "QUOTE_EXPIRED_BEFORE_SUBMIT"})
            return {"status": "BLOCKED", "reason": "QUOTE_EXPIRED_BEFORE_SUBMIT"}
        if not state.get("halted") and not risk_reduction and self.clock_ms() - bar_ms - _timeframe_to_milliseconds(cfg.timeframe) > cfg.entry_grace_seconds * 1000:
            self.ledger.update_order(client_id, "REJECTED", {"filled": 0., "reason": "ENTRY_WINDOW_EXPIRED"})
            return {"status": "WAITING", "reason": "ENTRY_WINDOW_EXPIRED"}
        try:
            result = self.exchange.submit_order(intent, client_id)
            self._validate_receipt({"id": client_id, "intent": intent}, result)
            self.ledger.update_order(client_id, result["status"], result)
        except Exception as exc:
            self.ledger.update_order(client_id, "UNKNOWN", {"error_type": type(exc).__name__})
            return {"status": "BLOCKED", "reason": "SUBMIT_UNRESOLVED", "client_order_id": client_id}
        return {"status": "ORDER_RECORDED", "client_order_id": client_id,
                "order_status": result["status"], "filled": result["filled"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--fold", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--mode", choices=("dry-run", "testnet", "live"), default="dry-run")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.mode != "dry-run":
        # No override: configuration completeness is not promotion evidence.
        # This check precedes file reads, model loading and credential access.
        print(json.dumps({"status": "BLOCKED_BY_MASTER_V3_GATE", "stage": 0,
                          "execution_authorized": False,
                          "reason": "PROMOTION_EVIDENCE_NOT_IMPLEMENTED"}))
        raise SystemExit(2)
    config = LiveConfig(**json.loads(args.config.read_text()))
    policy = FrozenPpoPolicy(args.run_dir, fold=args.fold, seed=args.seed)
    from .live_exchange import CcxtSpotExchange
    from .live_ledger import LiveLedger
    exchange = CcxtSpotExchange(config.exchange_id, config.symbol, mode=args.mode)
    key = getattr(exchange.client, "apiKey", "") if args.mode != "dry-run" else "public-preview"
    identity = {"mode": args.mode, "config": asdict(config), "model": policy.fingerprint,
                "account_fingerprint": hashlib.sha256(str(key).encode()).hexdigest()}
    with LiveLedger(args.journal, identity) as ledger:
        controller = LiveController(exchange, ledger, policy, config, mode=args.mode)
        try:
            while True:
                try:
                    print(json.dumps(controller.cycle(), allow_nan=False), flush=True)
                except Exception as exc:
                    # Exchange exception strings can contain signed URLs. Emit
                    # class only; failed cycles do not authorize a replacement order.
                    print(json.dumps({"status": "STOPPED", "error_type": type(exc).__name__}), flush=True)
                    raise SystemExit(1) from None
                if args.once:
                    break
                time.sleep(config.poll_seconds)
        except KeyboardInterrupt:
            print(json.dumps({"status": "PROCESS_STOPPED", "note": "account holdings are not assumed liquidated"}))


if __name__ == "__main__":
    main()
