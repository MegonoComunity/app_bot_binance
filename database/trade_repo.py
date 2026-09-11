"""
database/trade_repo.py

Repository untuk tabel trade_history.
Menyimpan hasil trade (closed positions) WIN/LOSS ke PostgreSQL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from database.connection import get_pool

logger = logging.getLogger(__name__)


# ─── Insert / Query ──────────────────────────────────────────────────────────

async def insert_trade(trade: dict) -> bool:
    """
    Simpan satu record trade ke tabel trade_history.
    Return True jika berhasil, False jika gagal (tidak raise exception agar bot tetap jalan).
    """
    try:
        pool = await get_pool()
        pnl = float(trade.get("realized_pnl", 0) or 0)
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        # Parse waktu
        closed_at_raw = trade.get("time") or trade.get("closed_at")
        try:
            closed_at = datetime.strptime(str(closed_at_raw), "%Y-%m-%d %H:%M:%S") if closed_at_raw else datetime.now()
        except (ValueError, TypeError):
            closed_at = datetime.now()

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trade_history
                    (symbol, side, entry_price, exit_price, realized_pnl,
                     commission, margin_usdt, leverage, mfe, mae,
                     duration_minutes, order_type, result, closed_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT DO NOTHING
                """,
                trade.get("symbol", "UNKNOWN"),
                trade.get("side", "LONG"),
                float(trade.get("entry_price", 0) or 0) or None,
                float(trade.get("exit_price", 0) or 0) or None,
                pnl,
                float(trade.get("commission", 0) or 0),
                float(trade.get("margin_usdt", 0) or 0) or None,
                int(trade.get("leverage", 0) or 0) or None,
                float(trade.get("mfe", 0) or 0) if trade.get("mfe") is not None else None,
                float(trade.get("mae", 0) or 0) if trade.get("mae") is not None else None,
                float(trade.get("duration_minutes", 0) or 0) if trade.get("duration_minutes") is not None else None,
                trade.get("order_type", "MARKET"),
                result,
                closed_at,
            )
        logger.debug(f"[DB] Trade {trade.get('symbol')} ({result}) disimpan ke trade_history")
        return True
    except Exception as exc:
        logger.error(f"[DB] Gagal insert trade: {exc}")
        return False


async def get_trade_summary() -> dict:
    """Agregasi statistik dari tabel trade_history."""
    try:
        pool = await get_pool()
        today = datetime.now().date()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN result = 'WIN'  THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                    SUM(realized_pnl - commission) AS net_pnl,
                    SUM(commission) AS commission_total,
                    SUM(CASE WHEN closed_at::date = $1 THEN realized_pnl - commission ELSE 0 END) AS daily_net_pnl,
                    COUNT(CASE WHEN closed_at::date = $1 THEN 1 END) AS daily_total,
                    SUM(CASE WHEN closed_at::date = $1 AND result='WIN' THEN 1 ELSE 0 END) AS daily_wins
                FROM trade_history
                """,
                today,
            )
        total = row["total"] or 0
        wins  = row["wins"]  or 0
        losses = row["losses"] or 0
        return {
            "total":        total,
            "wins":         wins,
            "losses":       losses,
            "win_rate":     round(wins / total * 100, 1) if total > 0 else 0.0,
            "net_pnl":      round(float(row["net_pnl"] or 0), 4),
            "commission":   round(float(row["commission_total"] or 0), 4),
            "daily_net_pnl": round(float(row["daily_net_pnl"] or 0), 4),
            "daily_total":  row["daily_total"] or 0,
            "daily_wins":   row["daily_wins"]  or 0,
            "daily_losses": (row["daily_total"] or 0) - (row["daily_wins"] or 0),
        }
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil trade summary: {exc}")
        return {}


async def migrate_from_json(json_path: str = "data/trade_stats.json") -> int:
    """
    Import data lama dari trade_stats.json ke tabel trade_history.
    Aman dijalankan berulang kali (ON CONFLICT DO NOTHING tidak bisa dipakai
    karena tidak ada UNIQUE constraint selain PK — kita cek count dulu).
    Return: jumlah record yang berhasil diimport.
    """
    if not os.path.exists(json_path):
        return 0
    try:
        pool = await get_pool()
        # Cek apakah sudah pernah dimigrasi
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM trade_history")
        if count and count > 0:
            logger.info(f"[DB] trade_history sudah berisi {count} record, skip migrasi JSON.")
            return 0

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("trades", [])
        imported = 0
        for trade in trades:
            ok = await insert_trade(trade)
            if ok:
                imported += 1

        logger.info(f"[DB] Migrasi selesai: {imported}/{len(trades)} trade diimport dari JSON")
        print(f"[DB] Migrasi trade_stats.json -> trade_history: {imported} record - OK")
        return imported
    except Exception as exc:
        logger.error(f"[DB] Migrasi JSON gagal: {exc}")
        return 0
