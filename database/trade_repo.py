"""
database/trade_repo.py

Repository untuk tabel trade_history.
Menyimpan hasil trade (closed positions) WIN/LOSS ke PostgreSQL dengan dukungan isolasi Multi-Exchange (BINANCE, BITUNIX_SIM, BITUNIX_REAL, dll).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

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
        commission = float(trade.get("commission", 0) or 0)
        funding_fee = float(trade.get("funding_fee", 0) or 0)
        net_pnl = float(trade.get("net_pnl", pnl - commission + funding_fee) or 0)
        result = "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "BREAKEVEN")
        exchange = str(trade.get("exchange", "BINANCE")).upper().strip()

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
                     commission, funding_fee, net_pnl, margin_usdt, leverage, mfe, mae,
                     duration_minutes, order_type, result, closed_at, exchange)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                ON CONFLICT DO NOTHING
                """,
                trade.get("symbol", "UNKNOWN"),
                trade.get("side", "LONG"),
                float(trade.get("entry_price", 0) or 0) or None,
                float(trade.get("exit_price", 0) or 0) or None,
                pnl,
                commission,
                funding_fee,
                net_pnl,
                float(trade.get("margin_usdt", 0) or 0) or None,
                int(trade.get("leverage", 0) or 0) or None,
                float(trade.get("mfe", 0) or 0) if trade.get("mfe") is not None else None,
                float(trade.get("mae", 0) or 0) if trade.get("mae") is not None else None,
                float(trade.get("duration_minutes", 0) or 0) if trade.get("duration_minutes") is not None else None,
                trade.get("order_type", "MARKET"),
                result,
                closed_at,
                exchange,
            )
            
            # Simpan juga ke tabel analisa sesi jika relevan
            try:
                await conn.execute(
                    """
                    INSERT INTO trade_analysis_session
                        (session_id, symbol, side, entry_price, exit_price, realized_pnl,
                         net_pnl, margin_usdt, leverage, mfe, mae, duration_minutes,
                         order_type, result, closed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                    f"SESSION_{exchange}",
                    trade.get("symbol", "UNKNOWN"),
                    trade.get("side", "LONG"),
                    float(trade.get("entry_price", 0) or 0) or None,
                    float(trade.get("exit_price", 0) or 0) or None,
                    pnl,
                    net_pnl,
                    float(trade.get("margin_usdt", 0) or 0) or None,
                    int(trade.get("leverage", 0) or 0) or None,
                    float(trade.get("mfe", 0) or 0) if trade.get("mfe") is not None else None,
                    float(trade.get("mae", 0) or 0) if trade.get("mae") is not None else None,
                    float(trade.get("duration_minutes", 0) or 0) if trade.get("duration_minutes") is not None else None,
                    trade.get("order_type", "MARKET"),
                    result,
                    closed_at,
                )
            except Exception as e_session:
                logger.debug(f"[DB] Insert trade_analysis_session skipped: {e_session}")

        logger.debug(f"[DB] Trade {trade.get('symbol')} ({result}) [{exchange}] disimpan ke trade_history")
        return True
    except Exception as exc:
        logger.error(f"[DB] Gagal insert trade: {exc}")
        return False


async def get_available_exchanges() -> List[str]:
    """Mengambil daftar semua exchange yang memiliki riwayat trade di database."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT DISTINCT COALESCE(exchange, 'BINANCE') AS ex FROM trade_history ORDER BY ex ASC")
            exchanges = [r["ex"] for r in rows if r["ex"]]
            if not exchanges:
                exchanges = ["BINANCE", "BITUNIX_SIM"]
            return exchanges
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil available exchanges: {exc}")
        return ["BINANCE", "BITUNIX_SIM"]


async def get_trade_summary(exchange: Optional[str] = None) -> dict:
    """Agregasi statistik komprehensif dari tabel trade_history, opsional filter per exchange."""
    try:
        pool = await get_pool()
        today = datetime.now().date()
        
        where_clause = ""
        params: List[Any] = [today]
        if exchange and exchange.upper() not in ("ALL", "*"):
            where_clause = "AND COALESCE(exchange, 'BINANCE') = $2"
            params.append(exchange.upper())

        query = f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN result = 'WIN'  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) AS losses,
            SUM(COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0))) AS net_pnl,
            SUM(CASE WHEN COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) > 0 THEN COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) ELSE 0 END) AS alltime_gross_profit,
            SUM(CASE WHEN COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) < 0 THEN ABS(COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0))) ELSE 0 END) AS alltime_gross_loss,
            SUM(commission) AS commission_total,
            SUM(COALESCE(funding_fee, 0)) AS funding_fee_total,
            SUM(CASE WHEN closed_at::date = $1 THEN COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) ELSE 0 END) AS daily_net_pnl,
            SUM(CASE WHEN closed_at::date = $1 AND COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) > 0 THEN COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) ELSE 0 END) AS daily_gross_profit,
            SUM(CASE WHEN closed_at::date = $1 AND COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) < 0 THEN ABS(COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0))) ELSE 0 END) AS daily_gross_loss,
            COUNT(CASE WHEN closed_at::date = $1 THEN 1 END) AS daily_total,
            SUM(CASE WHEN closed_at::date = $1 AND result='WIN' THEN 1 ELSE 0 END) AS daily_wins
        FROM trade_history
        WHERE 1=1 {where_clause}
        """

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)

        total = row["total"] or 0
        wins  = row["wins"]  or 0
        losses = row["losses"] or 0
        alltime_gp = float(row["alltime_gross_profit"] or 0)
        alltime_gl = float(row["alltime_gross_loss"] or 0)
        alltime_pf = round(alltime_gp / alltime_gl, 2) if alltime_gl > 0 else (round(alltime_gp, 2) if alltime_gp > 0 else 0.0)

        daily_total = row["daily_total"] or 0
        daily_wins  = row["daily_wins"]  or 0
        daily_losses = daily_total - daily_wins
        daily_gp = float(row["daily_gross_profit"] or 0)
        daily_gl = float(row["daily_gross_loss"] or 0)
        daily_pf = round(daily_gp / daily_gl, 2) if daily_gl > 0 else (round(daily_gp, 2) if daily_gp > 0 else 0.0)

        return {
            "exchange":             exchange.upper() if exchange else "ALL",
            "total":                total,
            "wins":                 wins,
            "losses":               losses,
            "win_rate":             round(wins / total * 100, 1) if total > 0 else 0.0,
            "profit_factor":        alltime_pf,
            "net_pnl":              round(float(row["net_pnl"] or 0), 4),
            "commission":           round(float(row["commission_total"] or 0), 4),
            "funding_fee":          round(float(row["funding_fee_total"] or 0), 4),
            "daily_net_pnl":        round(float(row["daily_net_pnl"] or 0), 4),
            "daily_total":          daily_total,
            "daily_wins":           daily_wins,
            "daily_losses":         daily_losses,
            "daily_win_rate":       round(daily_wins / daily_total * 100, 1) if daily_total > 0 else 0.0,
            "daily_profit_factor":  daily_pf,
            "daily_gross_profit":   round(daily_gp, 4),
            "daily_gross_loss":     round(daily_gl, 4),
        }
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil trade summary: {exc}")
        return {}


async def get_recent_trades(limit: int = 50, exchange: Optional[str] = None) -> list[dict]:
    """Mengambil riwayat trade terakhir untuk dashboard, opsional filter per exchange."""
    try:
        pool = await get_pool()
        where_clause = ""
        params: List[Any] = [limit]
        if exchange and exchange.upper() not in ("ALL", "*"):
            where_clause = "WHERE COALESCE(exchange, 'BINANCE') = $2"
            params.append(exchange.upper())

        query = f"""
        SELECT id, symbol, side, entry_price, exit_price, realized_pnl,
               commission, COALESCE(funding_fee, 0) AS funding_fee,
               COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) AS net_pnl,
               margin_usdt, leverage, mfe, mae,
               duration_minutes, order_type, result, closed_at,
               COALESCE(exchange, 'BINANCE') AS exchange
        FROM trade_history
        {where_clause}
        ORDER BY closed_at DESC
        LIMIT $1
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            
        return [
            {
                "id": r["id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "entry_price": float(r["entry_price"]) if r["entry_price"] else None,
                "exit_price": float(r["exit_price"]) if r["exit_price"] else None,
                "realized_pnl": float(r["realized_pnl"]),
                "commission": float(r["commission"]),
                "funding_fee": float(r["funding_fee"]),
                "net_pnl": round(float(r["net_pnl"]), 4),
                "margin_usdt": float(r["margin_usdt"]) if r["margin_usdt"] else None,
                "leverage": r["leverage"],
                "mfe": float(r["mfe"]) if r["mfe"] is not None else None,
                "mae": float(r["mae"]) if r["mae"] is not None else None,
                "duration_minutes": float(r["duration_minutes"]) if r["duration_minutes"] is not None else None,
                "order_type": r["order_type"],
                "result": r["result"],
                "exchange": r["exchange"],
                "closed_at": str(r["closed_at"]),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil recent trades: {exc}")
        return []


async def get_pnl_growth_curve(limit: int = 100, exchange: Optional[str] = None) -> list[dict]:
    """Mengambil data kurva pertumbuhan PnL kumulatif untuk grafik."""
    try:
        pool = await get_pool()
        where_clause = ""
        params: List[Any] = [limit]
        if exchange and exchange.upper() not in ("ALL", "*"):
            where_clause = "WHERE COALESCE(exchange, 'BINANCE') = $2"
            params.append(exchange.upper())

        query = f"""
        SELECT closed_at,
               COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)) AS net_pnl,
               symbol, result
        FROM trade_history
        {where_clause}
        ORDER BY closed_at ASC
        LIMIT $1
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        cumulative = 0.0
        curve = []
        for r in rows:
            pnl = float(r["net_pnl"] or 0)
            cumulative += pnl
            curve.append({
                "time": r["closed_at"].strftime("%Y-%m-%d %H:%M") if hasattr(r["closed_at"], "strftime") else str(r["closed_at"]),
                "pnl": round(pnl, 4),
                "cumulative_pnl": round(cumulative, 4),
                "symbol": r["symbol"],
                "result": r["result"],
            })
        return curve
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil pnl growth curve: {exc}")
        return []


async def get_monthly_trade_stats(exchange: Optional[str] = None) -> list[dict]:
    """Mengambil riwayat performa bulanan (Win Rate, Total Trade, Net PnL)."""
    try:
        pool = await get_pool()
        where_clause = ""
        params: List[Any] = []
        if exchange and exchange.upper() not in ("ALL", "*"):
            where_clause = "WHERE COALESCE(exchange, 'BINANCE') = $1"
            params.append(exchange.upper())

        query = f"""
        SELECT TO_CHAR(closed_at, 'YYYY-MM') as month,
               COUNT(*) as total_trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
               ROUND(SUM(CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END) / COUNT(*) * 100, 1) as win_rate,
               ROUND(SUM(COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)))::numeric, 4) as net_pnl
        FROM trade_history
        {where_clause}
        GROUP BY TO_CHAR(closed_at, 'YYYY-MM')
        ORDER BY month DESC
        LIMIT 12
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [
            {
                "month": r["month"],
                "total_trades": int(r["total_trades"]),
                "wins": int(r["wins"]),
                "losses": int(r["losses"]),
                "win_rate": float(r["win_rate"] or 0.0),
                "net_pnl": float(r["net_pnl"] or 0.0),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil monthly trade stats: {exc}")
        return []


async def get_daily_trade_stats(days: int = 14, exchange: Optional[str] = None) -> list[dict]:
    """Mengambil riwayat performa harian (Win Rate, Total Trade, Net PnL)."""
    try:
        pool = await get_pool()
        where_clause = ""
        params: List[Any] = [days]
        if exchange and exchange.upper() not in ("ALL", "*"):
            where_clause = "AND COALESCE(exchange, 'BINANCE') = $2"
            params.append(exchange.upper())

        query = f"""
        SELECT TO_CHAR(closed_at, 'YYYY-MM-DD') as day,
               COUNT(*) as total_trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
               ROUND(SUM(CASE WHEN result='WIN' THEN 1.0 ELSE 0.0 END) / COUNT(*) * 100, 1) as win_rate,
               ROUND(SUM(COALESCE(net_pnl, realized_pnl - commission + COALESCE(funding_fee, 0)))::numeric, 4) as net_pnl
        FROM trade_history
        WHERE closed_at >= CURRENT_DATE - ($1 * INTERVAL '1 day') {where_clause}
        GROUP BY TO_CHAR(closed_at, 'YYYY-MM-DD')
        ORDER BY day ASC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [
            {
                "day": r["day"],
                "total_trades": int(r["total_trades"]),
                "wins": int(r["wins"]),
                "losses": int(r["losses"]),
                "win_rate": float(r["win_rate"] or 0.0),
                "net_pnl": float(r["net_pnl"] or 0.0),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil daily trade stats: {exc}")
        return []



async def migrate_from_json(json_path: str = "data/trade_stats.json") -> int:
    """Migrasi data lama dari file data/trade_stats.json ke PostgreSQL."""
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("trades", [])
        if not trades:
            return 0
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM trade_history")
            if count and count > 0:
                return 0
            inserted = 0
            for t in trades:
                ok = await insert_trade(t)
                if ok:
                    inserted += 1
            logger.info(f"[DB] Migrasi {inserted}/{len(trades)} trade dari JSON ke PostgreSQL selesai")
            return inserted
    except Exception as exc:
        logger.error(f"[DB] Migrasi trade_stats.json gagal: {exc}")
        return 0
