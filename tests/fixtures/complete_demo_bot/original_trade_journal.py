"""SQLite trade journal — every paper order and fill is persisted."""

from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    qty             REAL NOT NULL,
    limit_price     REAL,
    status          TEXT NOT NULL,
    strategy_id     TEXT,
    hypothesis_id   TEXT
);
CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id     TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    price       REAL NOT NULL,
    qty         REAL NOT NULL,
    fee         REAL NOT NULL,
    FOREIGN KEY(order_id) REFERENCES paper_orders(order_id)
);
CREATE TABLE IF NOT EXISTS equity_curve (
    ts      TEXT PRIMARY KEY,
    equity  REAL NOT NULL,
    cash    REAL NOT NULL,
    pos_val REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    ts          TEXT NOT NULL,
    strategy_id TEXT,
    decision    TEXT,
    net_alpha   REAL,
    confidence  REAL,
    reasons     TEXT
);
"""


class TradeJournal:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.executescript(SCHEMA)

    def log_order(self, order_id: str, symbol: str, side: str, qty: float,
                  limit_price: Optional[float] = None, strategy_id: str = "",
                  hypothesis_id: str = "", order_type: str = "limit"):
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """INSERT OR IGNORE INTO paper_orders
                   (order_id, created_at, symbol, side, order_type, qty,
                    limit_price, status, strategy_id, hypothesis_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (order_id, datetime.now(timezone.utc).isoformat(),
                 symbol, side, order_type, qty, limit_price,
                 "ACKNOWLEDGED", strategy_id, hypothesis_id)
            )

    def log_fill(self, fill_id: str, order_id: str, price: float,
                 qty: float, fee: float = 0.0):
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """INSERT INTO paper_fills (fill_id, order_id, ts, price, qty, fee)
                   VALUES (?,?,?,?,?,?)""",
                (fill_id, order_id, datetime.now(timezone.utc).isoformat(),
                 price, qty, fee)
            )
            con.execute(
                "UPDATE paper_orders SET status='FILLED' WHERE order_id=?",
                (order_id,)
            )

    def log_signal(self, strategy_id: str, decision: str, net_alpha: float,
                   confidence: float, reasons: str = ""):
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """INSERT INTO signals (ts, strategy_id, decision, net_alpha, confidence, reasons)
                   VALUES (?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), strategy_id,
                 decision, net_alpha, confidence, reasons)
            )

    def snapshot(self, equity: float, cash: float, pos_val: float):
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """INSERT OR REPLACE INTO equity_curve (ts, equity, cash, pos_val)
                   VALUES (?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), equity, cash, pos_val)
            )

    def summary(self) -> dict:
        with sqlite3.connect(self.db_path) as con:
            fills = con.execute("SELECT COUNT(*), COALESCE(SUM(fee),0) FROM paper_fills").fetchone()
            orders = con.execute("SELECT COUNT(*) FROM paper_orders WHERE status='FILLED'").fetchone()
            eq = con.execute("SELECT equity FROM equity_curve ORDER BY ts DESC LIMIT 1").fetchone()
        return {
            "filled_orders": orders[0] if orders else 0,
            "total_fills": fills[0] if fills else 0,
            "total_fees": fills[1] if fills else 0.0,
            "last_equity": eq[0] if eq else None,
        }
