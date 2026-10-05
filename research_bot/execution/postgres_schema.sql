-- PG journal V1. Public replay evidence only; no PAPER/LIVE authorization.
-- TEXT preserves the same canonical JSON/numeric representation as SQLite.
CREATE TABLE IF NOT EXISTS convergence_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    version INTEGER NOT NULL CHECK (version=1),
    schema_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS convergence_journals (
    journal_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL UNIQUE,
    identity_json TEXT NOT NULL CHECK (jsonb_typeof(identity_json::jsonb)='object'),
    timeframe_ms BIGINT CHECK (timeframe_ms>0),
    migration_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS convergence_decisions (
    journal_id TEXT NOT NULL REFERENCES convergence_journals(journal_id),
    decision_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (jsonb_typeof(payload_json::jsonb)='object'),
    PRIMARY KEY (journal_id, decision_id)
);
CREATE TABLE IF NOT EXISTS convergence_orders (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    journal_id TEXT NOT NULL REFERENCES convergence_journals(journal_id),
    client_order_id TEXT NOT NULL,
    intent_json TEXT NOT NULL CHECK (jsonb_typeof(intent_json::jsonb)='object'),
    state TEXT NOT NULL CHECK (state IN ('SUBMITTING','UNKNOWN','OPEN','PARTIAL','FILLED','CANCELLED','REJECTED')),
    result_json TEXT NOT NULL CHECK (jsonb_typeof(result_json::jsonb)='object'),
    PRIMARY KEY (journal_id, client_order_id)
);
CREATE TABLE IF NOT EXISTS convergence_account_state (
    journal_id TEXT PRIMARY KEY REFERENCES convergence_journals(journal_id),
    state_json TEXT NOT NULL CHECK (jsonb_typeof(state_json::jsonb)='object')
);
-- Cumulative receipt revisions, NOT independent exchange trade IDs.
CREATE TABLE IF NOT EXISTS convergence_fill_revisions (
    journal_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    revision_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    PRIMARY KEY (journal_id, client_order_id, revision_sha256),
    FOREIGN KEY (journal_id, client_order_id) REFERENCES convergence_orders(journal_id, client_order_id)
);
-- Atomic projection of account_state.positions; the full state is authoritative.
CREATE TABLE IF NOT EXISTS convergence_positions (
    journal_id TEXT NOT NULL REFERENCES convergence_journals(journal_id),
    symbol TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity>=0 AND quantity<'Infinity'::float8),
    PRIMARY KEY (journal_id, symbol)
);
CREATE TABLE IF NOT EXISTS convergence_heartbeats (
    journal_id TEXT PRIMARY KEY REFERENCES convergence_journals(journal_id),
    observed_at TIMESTAMPTZ NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS convergence_candles (
    journal_id TEXT NOT NULL REFERENCES convergence_journals(journal_id),
    timestamp_ms BIGINT NOT NULL CHECK (timestamp_ms>=0),
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (journal_id,timestamp_ms),
    CHECK (low>0 AND high>='0'::float8 AND high<'Infinity'::float8 AND low<=open AND low<=close AND high>=open AND high>=close),
    CHECK (volume>=0 AND volume<'Infinity'::float8)
);
