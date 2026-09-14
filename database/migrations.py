"""
database/migrations.py

Buat semua tabel yang dibutuhkan di PostgreSQL jika belum ada.
Dipanggil sekali saat startup bot (idempotent - aman dijalankan berulang).
"""
from __future__ import annotations

import logging
from database.connection import get_pool

logger = logging.getLogger(__name__)

# ─── DDL Statements ─────────────────────────────────────────────────────────

_CREATE_TRADE_HISTORY = """
CREATE TABLE IF NOT EXISTS trade_history (
    id               SERIAL PRIMARY KEY,
    symbol           VARCHAR(20)    NOT NULL,
    side             VARCHAR(5)     DEFAULT 'LONG',
    entry_price      NUMERIC(20, 8),
    exit_price       NUMERIC(20, 8),
    realized_pnl     NUMERIC(20, 8),
    commission       NUMERIC(20, 8),
    funding_fee      NUMERIC(20, 8) DEFAULT 0,
    net_pnl          NUMERIC(20, 8),
    margin_usdt      NUMERIC(10, 2),
    leverage         INT,
    mfe              NUMERIC(20, 8),
    mae              NUMERIC(20, 8),
    duration_minutes NUMERIC(10, 2),
    order_type       VARCHAR(20),
    result           VARCHAR(10),
    closed_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);
ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS funding_fee NUMERIC(20, 8) DEFAULT 0;
ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS net_pnl NUMERIC(20, 8);
"""

_CREATE_PATTERN_MEMORY = """
CREATE TABLE IF NOT EXISTS pattern_memory (
    id          SERIAL PRIMARY KEY,
    fingerprint TEXT           NOT NULL UNIQUE,
    wins        INT            DEFAULT 0,
    losses      INT            DEFAULT 0,
    total       INT            DEFAULT 0,
    total_pnl   NUMERIC(20, 8) DEFAULT 0,
    win_rate    NUMERIC(5, 2)  DEFAULT 0,
    conditions  JSONB,
    last_seen   TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ    DEFAULT NOW()
);
"""

_CREATE_PATTERN_ENTRIES = """
CREATE TABLE IF NOT EXISTS pattern_entries (
    id          SERIAL PRIMARY KEY,
    entry_id    VARCHAR(60)    UNIQUE NOT NULL,
    symbol      VARCHAR(20),
    side        VARCHAR(5),
    entry_price NUMERIC(20, 8),
    fingerprint TEXT,
    alasan      TEXT,
    conditions  JSONB,
    result      VARCHAR(10),
    pnl         NUMERIC(20, 8),
    entered_at  TIMESTAMPTZ    DEFAULT NOW(),
    closed_at   TIMESTAMPTZ
);
"""

_CREATE_OHLCV_CANDLES = """
CREATE TABLE IF NOT EXISTS ohlcv_candles (
    id         BIGSERIAL PRIMARY KEY,
    symbol     VARCHAR(20)    NOT NULL,
    timeframe  VARCHAR(5)     NOT NULL,
    open_time  TIMESTAMPTZ    NOT NULL,
    open       NUMERIC(20, 8),
    high       NUMERIC(20, 8),
    low        NUMERIC(20, 8),
    close      NUMERIC(20, 8),
    volume     NUMERIC(30, 8),
    close_time TIMESTAMPTZ,
    UNIQUE (symbol, timeframe, open_time)
);
"""

_CREATE_OHLCV_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf
    ON ohlcv_candles (symbol, timeframe, open_time DESC);
"""

# ─── Public ──────────────────────────────────────────────────────────────────

async def create_tables() -> None:
    """
    Buat semua tabel jika belum ada.
    Aman untuk dipanggil berulang kali (idempotent via IF NOT EXISTS).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_CREATE_TRADE_HISTORY)
            await conn.execute(_CREATE_PATTERN_MEMORY)
            await conn.execute(_CREATE_PATTERN_ENTRIES)
            await conn.execute(_CREATE_OHLCV_CANDLES)
            await conn.execute(_CREATE_OHLCV_INDEX)

    logger.info("[DB] Semua tabel berhasil dibuat / sudah ada")
    print("[DB] Tabel PostgreSQL: trade_history, pattern_memory, pattern_entries, ohlcv_candles - OK")
