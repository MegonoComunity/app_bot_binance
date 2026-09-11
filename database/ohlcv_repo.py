"""
database/ohlcv_repo.py

Repository untuk tabel ohlcv_candles.
Menyimpan data candlestick OHLCV dari Binance Futures ke PostgreSQL.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from database.connection import get_pool

logger = logging.getLogger(__name__)


async def upsert_candles(
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
) -> int:
    """
    Bulk upsert candle data ke tabel ohlcv_candles.
    Candle yang sudah ada (same symbol+timeframe+open_time) di-skip.

    Args:
        symbol:    Contoh: 'BTCUSDT'
        timeframe: Contoh: '1d', '1w', '1h', '5m'
        candles:   List dict dengan keys: open_time, open, high, low, close, volume, close_time

    Return: jumlah candle yang berhasil di-insert
    """
    if not candles:
        return 0
    try:
        pool = await get_pool()
        rows = [
            (
                symbol,
                timeframe,
                _parse_ts(c.get("open_time")),
                float(c.get("open", 0)),
                float(c.get("high", 0)),
                float(c.get("low", 0)),
                float(c.get("close", 0)),
                float(c.get("volume", 0)),
                _parse_ts(c.get("close_time")),
            )
            for c in candles
            if c.get("open_time") is not None
        ]

        async with pool.acquire() as conn:
            result = await conn.executemany(
                """
                INSERT INTO ohlcv_candles
                    (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
                """,
                rows,
            )
        inserted = int(result.split()[-1]) if result else len(rows)
        logger.debug(f"[DB] OHLCV {symbol} {timeframe}: {inserted} candle upserted")
        return inserted
    except Exception as exc:
        logger.error(f"[DB] Gagal upsert OHLCV {symbol} {timeframe}: {exc}")
        return 0


async def get_candles(
    symbol: str,
    timeframe: str,
    limit: int = 500,
) -> list[dict]:
    """
    Ambil data candle terbaru dari DB untuk dashboard/analisis.

    Return: list dict dengan keys: open_time, open, high, low, close, volume
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT open_time, open, high, low, close, volume, close_time
                FROM ohlcv_candles
                WHERE symbol = $1 AND timeframe = $2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                symbol,
                timeframe,
                limit,
            )
        return [
            {
                "open_time":  str(r["open_time"]),
                "open":       float(r["open"]),
                "high":       float(r["high"]),
                "low":        float(r["low"]),
                "close":      float(r["close"]),
                "volume":     float(r["volume"]),
                "close_time": str(r["close_time"]) if r["close_time"] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil candles {symbol} {timeframe}: {exc}")
        return []


async def get_available_symbols(timeframe: str = "1d") -> list[str]:
    """Daftar symbol yang sudah tersedia di DB untuk timeframe tertentu."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT symbol FROM ohlcv_candles WHERE timeframe = $1 ORDER BY symbol",
                timeframe,
            )
        return [r["symbol"] for r in rows]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil available symbols: {exc}")
        return []


async def get_ohlcv_stats() -> dict:
    """Statistik jumlah candle per symbol & timeframe (untuk monitoring)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, timeframe, COUNT(*) AS count,
                       MIN(open_time) AS oldest, MAX(open_time) AS newest
                FROM ohlcv_candles
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
                """
            )
        return {
            f"{r['symbol']}_{r['timeframe']}": {
                "count":  r["count"],
                "oldest": str(r["oldest"])[:10],
                "newest": str(r["newest"])[:10],
            }
            for r in rows
        }
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil OHLCV stats: {exc}")
        return {}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_ts(value: Any) -> datetime:
    """Konversi timestamp Binance (ms epoch int atau datetime) ke datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Binance mengembalikan milliseconds epoch
        return datetime.utcfromtimestamp(value / 1000)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.utcnow()
