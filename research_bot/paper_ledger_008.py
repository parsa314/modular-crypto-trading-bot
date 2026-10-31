"""Single-process-independent SQLite paper ledger; never contacts a venue.

Engineering prototype. All accounting is Decimal, with idempotent order IDs.
No result from this module establishes broker, live or prospective execution.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3

class PaperLedger:
    def __init__(self, path: Path, initial_cash: Decimal = Decimal("10000")):
        if not initial_cash.is_finite() or initial_cash <= 0:
            raise ValueError("INVALID_INITIAL_CASH")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL, quantity TEXT NOT NULL, basis TEXT NOT NULL, realized TEXT NOT NULL, fees TEXT NOT NULL, peak TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_fills (order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_fee_events (event_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_journal_meta (id INTEGER PRIMARY KEY CHECK(id=1), opening_cash TEXT NOT NULL, verified INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS paper_risk_state (id INTEGER PRIMARY KEY CHECK(id=1), utc_day TEXT, day_open TEXT, anchor_valid INTEGER NOT NULL DEFAULT 0, daily_halt INTEGER NOT NULL DEFAULT 0, hard_halt INTEGER NOT NULL DEFAULT 0, observed_at TEXT, observed_price TEXT)")
            db.execute("INSERT OR IGNORE INTO paper_risk_state(id) VALUES (1)")
            db.execute("INSERT OR IGNORE INTO paper_account VALUES (1,?,'0','0','0','0',?)", (str(initial_cash),str(initial_cash)))
            if not db.execute("SELECT 1 FROM paper_journal_meta WHERE id=1").fetchone():
                row=db.execute("SELECT cash,quantity,basis,realized,fees FROM paper_account WHERE id=1").fetchone()
                existing=db.execute("SELECT 1 FROM paper_fills LIMIT 1").fetchone()
                valid=not existing and all(Decimal(v)==0 for v in row[1:])
                db.execute("INSERT INTO paper_journal_meta VALUES (1,?,?)",(row[0],int(valid)))

    @staticmethod
    def _reconcile_locked(db) -> dict:
        opening,verified=db.execute("SELECT opening_cash,verified FROM paper_journal_meta WHERE id=1").fetchone()
        if not verified:
            raise ValueError("LEGACY_LEDGER_UNVERIFIED")
        cash, held, basis, realized, fees = Decimal(opening),Decimal(0),Decimal(0),Decimal(0),Decimal(0)
        for kind, payload in db.execute("SELECT kind,payload FROM paper_events ORDER BY sequence"):
            event=json.loads(payload)
            if kind == "FILL":
                qty,price,fee=map(Decimal,(event["quantity"],event["fill_price"],event["fee"]))
                notional=qty*price
                if event["side"] == "BUY":
                    cash-=notional+fee; basis+=notional+fee; held+=qty
                elif event["side"] == "SELL" and 0 < qty <= held:
                    released=basis*qty/held; basis-=released; held-=qty
                    cash+=notional-fee; realized+=notional-fee-released
                else:
                    raise ValueError("INVALID_JOURNAL_FILL")
                fees+=fee
            elif kind == "FEE":
                amount=Decimal(event["amount"]); cash-=amount; fees+=amount
                if event["order_side"] == "BUY": basis+=amount
                elif event["order_side"] == "SELL": realized-=amount
                else: raise ValueError("INVALID_JOURNAL_FEE")
            else:
                raise ValueError("UNKNOWN_JOURNAL_EVENT")
        actual=tuple(map(Decimal,db.execute("SELECT cash,quantity,basis,realized,fees FROM paper_account WHERE id=1").fetchone()))
        expected=(cash,held,basis,realized,fees)
        if expected != actual:
            raise ValueError("PAPER_ACCOUNTING_MISMATCH")
        return {"cash":str(cash),"quantity":str(held),"basis":str(basis),"realized":str(realized),"fees":str(fees)}

    def reconcile(self) -> dict:
        with localcontext() as ctx, sqlite3.connect(self.path) as db:
            ctx.prec=80
            return self._reconcile_locked(db)

    @staticmethod
    def _assert_no_other_pending(db, order_id: str | None = None) -> None:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_pending_replay'").fetchone():
            return
        for (payload,) in db.execute("SELECT payload FROM paper_pending_replay"):
            if order_id is None or json.loads(payload)["intent"]["order_id"] != order_id:
                raise ValueError("PAPER_PENDING_RECONCILIATION_REQUIRED")

    def snapshot(self) -> dict[str, str]:
        with sqlite3.connect(self.path) as db:
            r = db.execute("SELECT cash,quantity,basis,realized,fees,peak FROM paper_account WHERE id=1").fetchone()
        return dict(zip(("cash","quantity","basis","realized","fees","peak"),r))

    def latest_fill_time(self) -> datetime | None:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT result FROM paper_fills").fetchall()
        times = []
        for (result,) in rows:
            value = json.loads(result).get("fill_at")
            if not value:
                raise ValueError("UNTRUSTED_LEDGER_HISTORY")
            times.append(datetime.fromisoformat(value))
        return max(times) if times else None

    def settle_additional_fee(self, *, event_id: str, order_id: str, amount: Decimal,
                              currency: str, source: str, observed_at: datetime) -> dict:
        """Apply one explicitly incremental paper fee; ambiguous lots fail closed.

        This is not a broker fee adapter. Cumulative/order/trade reports must not
        be passed as separate fees without prior reconciliation.
        """
        if amount is None:
            raise ValueError("UNKNOWN_FEE")
        if not isinstance(amount,Decimal) or not amount.is_finite() or amount < 0:
            raise ValueError("INVALID_FEE")
        if not event_id or not order_id or not source or currency != "USDT":
            raise ValueError("UNVERIFIED_FEE_EVIDENCE")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("TIMEZONE_REQUIRED")
        body={"event_id":event_id,"order_id":order_id,"amount":str(amount),"currency":currency,
              "source":source,"observed_at":observed_at.astimezone(timezone.utc).isoformat()}
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        with localcontext() as ctx, sqlite3.connect(self.path) as db:
            ctx.prec=80
            db.execute("BEGIN IMMEDIATE")
            self._reconcile_locked(db)
            self._assert_no_other_pending(db)
            prior=db.execute("SELECT fingerprint,result FROM paper_fee_events WHERE event_id=?",(event_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("FEE_EVENT_CONFLICT")
                return {**json.loads(prior[1]),"duplicate":True}
            if db.execute("SELECT 1 FROM paper_fee_events WHERE order_id=?",(order_id,)).fetchone():
                raise ValueError("ORDER_FEE_ALREADY_ADJUSTED")
            row=db.execute("SELECT rowid,result FROM paper_fills WHERE order_id=?",(order_id,)).fetchone()
            if row is None:
                raise ValueError("FILL_NOT_FOUND")
            fill=json.loads(row[1])
            if observed_at < datetime.fromisoformat(fill["fill_at"]):
                raise ValueError("FEE_BEFORE_FILL")
            cash, held, basis, realized, fees, peak = map(Decimal,db.execute(
                "SELECT cash,quantity,basis,realized,fees,peak FROM paper_account WHERE id=1").fetchone())
            if cash < amount:
                raise ValueError("INSUFFICIENT_CASH_FOR_FEE")
            if fill["side"] == "BUY":
                if db.execute("SELECT 1 FROM paper_fills WHERE rowid>? LIMIT 1",(row[0],)).fetchone():
                    raise ValueError("LATE_FEE_REQUIRES_LOT_RECONCILIATION")
                basis += amount
            else:
                realized -= amount
            cash -= amount
            fees += amount
            # A later fee changes equity without a fresh mark; no new entries
            # may be admitted until a fresh, valid daily anchor is available.
            opening,valid,daily,hard,last_at,last_price=db.execute(
                "SELECT day_open,anchor_valid,daily_halt,hard_halt,observed_at,observed_price FROM paper_risk_state WHERE id=1").fetchone()
            if last_at and observed_at < datetime.fromisoformat(last_at):
                raise ValueError("STALE_FEE_OBSERVATION")
            if last_price is not None:
                last_mark_equity=cash+held*Decimal(last_price)
                if valid and last_mark_equity <= Decimal(opening)*Decimal("0.98"): daily=1
                if last_mark_equity <= peak*Decimal("0.95"): hard=1
            db.execute("UPDATE paper_risk_state SET anchor_valid=0,daily_halt=?,hard_halt=? WHERE id=1",(daily,hard))
            db.execute("UPDATE paper_account SET cash=?,basis=?,realized=?,fees=? WHERE id=1",
                       tuple(map(str,(cash,basis,realized,fees))))
            result={**body,"cash":str(cash),"basis":str(basis),"realized":str(realized),
                    "fees":str(fees),"duplicate":False}
            db.execute("INSERT INTO paper_fee_events VALUES (?,?,?,?)",
                       (event_id,order_id,fingerprint,json.dumps(result,sort_keys=True)))
            db.execute("INSERT INTO paper_events(kind,event_id,payload) VALUES ('FEE',?,?)",
                       ("fee:"+event_id,json.dumps({"amount":str(amount),"order_side":fill["side"]},sort_keys=True)))
            self._reconcile_locked(db)
        return result

    def risk_status(self) -> dict:
        with sqlite3.connect(self.path) as db:
            day, opening, valid, daily, hard, observed = db.execute(
                "SELECT utc_day,day_open,anchor_valid,daily_halt,hard_halt,observed_at FROM paper_risk_state WHERE id=1").fetchone()
        return {"utc_day":day, "day_open":opening, "anchor_valid":bool(valid),
                "daily_halt":bool(daily), "hard_halt":bool(hard), "observed_at":observed}

    def mark(self, price: Decimal, *, observed_at: datetime | None = None) -> dict[str, str]:
        if not price.is_finite() or price <= 0:
            raise ValueError("INVALID_MARK_PRICE")
        with localcontext() as ctx, sqlite3.connect(self.path) as db:
            ctx.prec = 80
            db.execute("BEGIN IMMEDIATE")
            self._reconcile_locked(db)
            cash, quantity, peak = map(Decimal, db.execute(
                "SELECT cash,quantity,peak FROM paper_account WHERE id=1").fetchone())
            equity = cash + quantity * price
            peak = max(peak, equity)
            db.execute("UPDATE paper_account SET peak=? WHERE id=1", (str(peak),))
            if observed_at is not None:
                if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                    raise ValueError("TIMEZONE_REQUIRED")
                at=observed_at.astimezone(timezone.utc)
                day, opening, valid, daily, hard, previous, previous_price = db.execute(
                    "SELECT utc_day,day_open,anchor_valid,daily_halt,hard_halt,observed_at,observed_price FROM paper_risk_state WHERE id=1").fetchone()
                if previous and at < datetime.fromisoformat(previous):
                    raise ValueError("PAPER_RISK_TIME_REGRESSION")
                if previous and at == datetime.fromisoformat(previous) and previous_price is not None and price != Decimal(previous_price):
                    raise ValueError("CONFLICTING_PRICE_SAME_TIME")
                if day != at.date().isoformat():
                    # A carried position needs a mark exactly at UTC open.
                    valid = int(quantity == 0 or (at.hour == 0 and at.minute == 0 and at.second == 0))
                    opening, daily = str(equity), 0
                    day = at.date().isoformat()
                if valid and equity <= Decimal(opening)*Decimal("0.98"):
                    daily = 1
                if equity <= peak*Decimal("0.95"):
                    hard = 1
                db.execute("UPDATE paper_risk_state SET utc_day=?,day_open=?,anchor_valid=?,daily_halt=?,hard_halt=?,observed_at=?,observed_price=? WHERE id=1",
                           (day,opening,valid,daily,hard,at.isoformat(),str(price)))
        return self.snapshot()

    def fill(self, *, order_id: str, side: str, quantity: Decimal, reference_price: Decimal,
             decision_at: datetime, fill_at: datetime, fee_bps: Decimal, spread_bps: Decimal,
             slippage_bps: Decimal) -> dict:
        if not order_id or side not in {"BUY","SELL"}:
            raise ValueError("INVALID_PAPER_ORDER")
        if decision_at.tzinfo is None or fill_at.tzinfo is None or fill_at <= decision_at:
            raise ValueError("FILL_MUST_FOLLOW_DECISION")
        if fee_bps is None:
            raise ValueError("UNKNOWN_FEE")
        if not all(x.is_finite() for x in (quantity, reference_price, fee_bps, spread_bps, slippage_bps)):
            raise ValueError("NONFINITE_ORDER")
        if quantity <= 0 or reference_price <= 0 or min(fee_bps, spread_bps, slippage_bps) < 0:
            raise ValueError("INVALID_ORDER_AMOUNT_OR_COST")
        request = {"id":order_id,"side":side,"quantity":str(quantity),"reference_price":str(reference_price),
                   "decision_at":decision_at.isoformat(),"fill_at":fill_at.isoformat(),
                   "fee_bps":str(fee_bps),"spread_bps":str(spread_bps),"slippage_bps":str(slippage_bps)}
        fingerprint = hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        self.reconcile()
        # Existing orders remain readable even when a newer market mark exists.
        with sqlite3.connect(self.path) as db:
            self._assert_no_other_pending(db,order_id)
            prior=db.execute("SELECT fingerprint,result FROM paper_fills WHERE order_id=?",(order_id,)).fetchone()
        if prior:
            if prior[0] != fingerprint:
                raise ValueError("ORDER_ID_CONFLICT")
            return {**json.loads(prior[1]), "duplicate":True}
        # Persist risk triggers separately before any potential rejected fill.
        self.mark(reference_price, observed_at=fill_at)
        with localcontext() as ctx, sqlite3.connect(self.path) as db:
            ctx.prec = 80
            db.execute("BEGIN IMMEDIATE")
            self._reconcile_locked(db)
            self._assert_no_other_pending(db,order_id)
            prior = db.execute("SELECT fingerprint,result FROM paper_fills WHERE order_id=?",(order_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("ORDER_ID_CONFLICT")
                return {**json.loads(prior[1]), "duplicate": True}
            row = db.execute("SELECT cash,quantity,basis,realized,fees,peak FROM paper_account WHERE id=1").fetchone()
            cash, held, basis, realized, fees, peak = map(Decimal,row)
            day_open, anchor_valid, daily_halt, hard_halt, observed = db.execute(
                "SELECT day_open,anchor_valid,daily_halt,hard_halt,observed_at FROM paper_risk_state WHERE id=1").fetchone()
            if observed != fill_at.astimezone(timezone.utc).isoformat():
                raise ValueError("STALE_PAPER_RISK_SNAPSHOT")
            if side == "BUY" and (not anchor_valid or daily_halt or hard_halt):
                raise ValueError("PAPER_RISK_HALTED")
            if spread_bps > 35 or slippage_bps > 25:
                raise ValueError("RISK_REJECTED:EXECUTION_COST")
            adjustment = (spread_bps / 2 + slippage_bps) / Decimal(10000)
            if adjustment >= 1:
                raise ValueError("INVALID_EXECUTION_COST")
            price = reference_price * (1 + adjustment if side == "BUY" else 1 - adjustment)
            notional = price * quantity
            fee = notional * fee_bps / Decimal(10000)
            if side == "BUY":
                if cash < notional + fee:
                    raise ValueError("INSUFFICIENT_CASH")
                cash -= notional + fee
                basis += notional + fee
                held += quantity
            else:
                if quantity > held:
                    raise ValueError("SELL_EXCEEDS_POSITION")
                released = basis * quantity / held
                basis -= released
                held -= quantity
                cash += notional - fee
                realized += notional - fee - released
            fees += fee
            equity = cash + held * reference_price
            peak = max(peak,equity)
            if equity <= 0:
                raise ValueError("INVALID_EQUITY")
            position_value = held * reference_price
            if side == "BUY" and (equity <= peak*Decimal("0.95") or
                (anchor_valid and equity <= Decimal(day_open)*Decimal("0.98")) or
                position_value > equity*Decimal("0.35") or notional > equity*Decimal("0.50")):
                raise ValueError("RISK_REJECTED:PORTFOLIO_LIMIT")
            result = {"order_id":order_id,"side":side,"quantity":str(quantity),"fill_price":str(price),
                      "fee":str(fee),"cash":str(cash),"position":str(held),"equity":str(equity),
                      "realized":str(realized),"decision_at":decision_at.isoformat(),
                      "fill_at":fill_at.isoformat(),"duplicate":False}
            db.execute("UPDATE paper_account SET cash=?,quantity=?,basis=?,realized=?,fees=?,peak=? WHERE id=1",
                       tuple(map(str,(cash,held,basis,realized,fees,peak))))
            db.execute("INSERT INTO paper_fills VALUES (?,?,?)",(order_id,fingerprint,json.dumps(result,sort_keys=True)))
            db.execute("INSERT INTO paper_events(kind,event_id,payload) VALUES ('FILL',?,?)",
                       ("fill:"+order_id,json.dumps(result,sort_keys=True)))
            self._reconcile_locked(db)
        return result
