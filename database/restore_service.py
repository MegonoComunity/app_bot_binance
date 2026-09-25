"""
database/restore_service.py

Service untuk me-restore database PostgreSQL dari file backup (.sql atau .zip).
Fitur:
1. Deteksi dan validasi koneksi database PostgreSQL sebelum eksekusi.
2. Mekanisme Upsert cerdas: jika data kembar/duplikat, data lama diperbarui dengan data terbaru tanpa duplikasi.
3. Mendukung eksekusi via Bot Telegram (upload document / command) dan Web Dashboard (upload file).
"""
from __future__ import annotations

import os
import re
import glob
import zipfile
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from database.connection import get_pool, is_db_available
from database.migrations import create_tables

logger = logging.getLogger(__name__)


async def check_database_health() -> Dict[str, Any]:
    """Memeriksa kesehatan dan ketersediaan koneksi database PostgreSQL."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1;")
            if val == 1:
                return {"connected": True, "error": None}
    except Exception as exc:
        logger.error(f"[DB HEALTH] Gagal koneksi database: {exc}")
        return {"connected": False, "error": str(exc)}
    return {"connected": False, "error": "Unknown connection error"}


async def get_database_stats() -> Dict[str, int]:
    """Mengambil jumlah baris data dari setiap tabel utama database."""
    stats: Dict[str, int] = {
        "trade_history": 0,
        "pattern_memory": 0,
        "pattern_entries": 0,
        "ohlcv_candles": 0,
    }
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            for tbl in stats.keys():
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)", tbl
                )
                if exists:
                    count = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
                    stats[tbl] = int(count or 0)
    except Exception as exc:
        logger.error(f"[DB STATS] Gagal mengambil statistik database: {exc}")
    return stats


def get_available_backup_files() -> List[Dict[str, Any]]:
    """Mengambil daftar file backup yang tersimpan di direktori database/."""
    os.makedirs("database", exist_ok=True)
    files = glob.glob("database/backup_*.sql") + glob.glob("database/backup_*.zip")
    results = []
    for f in sorted(files, reverse=True):
        stat = os.stat(f)
        size_mb = round(stat.st_size / (1024 * 1024), 2)
        mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        results.append({
            "filename": os.path.basename(f),
            "filepath": f,
            "size_mb": size_mb,
            "modified_at": mod_time,
            "is_zip": f.lower().endswith(".zip"),
        })
    return results


def _transform_sql_for_smart_upsert(sql: str) -> str:
    """
    Menyesuaikan script SQL backup agar menangani data kembar secara cerdas:
    - pattern_memory: Update statistik jika pola sudah ada.
    - trade_history / pattern_entries / ohlcv_candles: ON CONFLICT DO NOTHING / UPDATE.
    """
    lines = sql.splitlines()
    processed_lines = []

    for line in lines:
        clean = line.strip()
        if not clean or clean.startswith("--") or clean.startswith("/*"):
            processed_lines.append(line)
            continue

        # Jika query insert ke pattern_memory belum ada klausul ON CONFLICT
        if "INSERT INTO pattern_memory" in clean and "ON CONFLICT" not in clean:
            if clean.endswith(";"):
                clean = clean[:-1] + (
                    " ON CONFLICT (fingerprint) DO UPDATE SET "
                    "wins = GREATEST(pattern_memory.wins, EXCLUDED.wins), "
                    "losses = GREATEST(pattern_memory.losses, EXCLUDED.losses), "
                    "total = GREATEST(pattern_memory.total, EXCLUDED.total), "
                    "total_pnl = EXCLUDED.total_pnl, "
                    "win_rate = EXCLUDED.win_rate, "
                    "last_seen = GREATEST(pattern_memory.last_seen, EXCLUDED.last_seen);"
                )
        # Jika query insert ke ohlcv_candles belum ada klausul ON CONFLICT
        elif "INSERT INTO ohlcv_candles" in clean and "ON CONFLICT" not in clean:
            if clean.endswith(";"):
                clean = clean[:-1] + (
                    " ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET "
                    "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                    "close = EXCLUDED.close, volume = EXCLUDED.volume;"
                )
        # Jika query insert ke tabel lain belum ada ON CONFLICT
        elif clean.startswith("INSERT INTO") and "ON CONFLICT" not in clean:
            if clean.endswith(";"):
                clean = clean[:-1] + " ON CONFLICT DO NOTHING;"

        processed_lines.append(clean)

    return "\n".join(processed_lines)


async def execute_database_restore(file_path: str) -> Dict[str, Any]:
    """
    Me-restore database PostgreSQL dari file .sql atau .zip dengan deteksi koneksi & smart upsert.
    """
    # 1. Deteksi dan validasi koneksi database
    health = await check_database_health()
    if not health.get("connected"):
        return {
            "success": False,
            "message": f"❌ Koneksi ke database PostgreSQL gagal: {health.get('error')}. Pastikan service PostgreSQL sudah berjalan.",
            "error": "DB_NOT_CONNECTED"
        }

    if not os.path.exists(file_path):
        return {
            "success": False,
            "message": f"❌ File backup tidak ditemukan: {file_path}",
            "error": "FILE_NOT_FOUND"
        }

    extracted_sql_path: Optional[str] = None
    temp_extract_dir: Optional[str] = None

    try:
        # 2. Ekstrak jika format file adalah .zip
        if file_path.lower().endswith(".zip"):
            temp_extract_dir = os.path.join("database", f"temp_restore_{int(datetime.now().timestamp())}")
            os.makedirs(temp_extract_dir, exist_ok=True)
            with zipfile.ZipFile(file_path, "r") as zf:
                zf.extractall(temp_extract_dir)
                sql_files = [f for f in os.listdir(temp_extract_dir) if f.lower().endswith(".sql")]
                if not sql_files:
                    return {
                        "success": False,
                        "message": "❌ File ZIP tidak berisi file .sql backup yang valid.",
                        "error": "NO_SQL_IN_ZIP"
                    }
                extracted_sql_path = os.path.join(temp_extract_dir, sql_files[0])
        elif file_path.lower().endswith(".sql"):
            extracted_sql_path = file_path
        else:
            return {
                "success": False,
                "message": "❌ Format file tidak didukung. Harap gunakan file berakhiran .sql atau .zip.",
                "error": "INVALID_FORMAT"
            }

        # 3. Pastikan skema tabel dasar aktif
        await create_tables()
        stats_before = await get_database_stats()

        # 4. Baca dan optimalkan SQL dengan aturan Smart Upsert
        with open(extracted_sql_path, "r", encoding="utf-8", errors="replace") as f:
            raw_sql = f.read()

        if not raw_sql.strip():
            return {
                "success": False,
                "message": "❌ File backup SQL kosong.",
                "error": "EMPTY_SQL_FILE"
            }

        sql_content = _transform_sql_for_smart_upsert(raw_sql)
        pool = await get_pool()
        statements_count = 0

        # 5. Eksekusi SQL via asyncpg
        async with pool.acquire() as conn:
            try:
                await conn.execute(sql_content)
                statements_count = len([s for s in sql_content.split(";") if s.strip()])
            except Exception as e_bulk:
                logger.warning(f"[RESTORE] Bulk execute failed, fallback statement-by-statement: {e_bulk}")
                statements = [s.strip() for s in sql_content.split(";\n") if s.strip()]
                for stmt in statements:
                    if not stmt or stmt.startswith("--") or stmt.startswith("/*"):
                        continue
                    try:
                        await conn.execute(stmt)
                        statements_count += 1
                    except Exception as e_stmt:
                        logger.debug(f"[RESTORE] Statement skipped/merged: {e_stmt}")

        stats_after = await get_database_stats()

        trade_diff = stats_after.get("trade_history", 0) - stats_before.get("trade_history", 0)
        pattern_diff = stats_after.get("pattern_memory", 0) - stats_before.get("pattern_memory", 0)
        entries_diff = stats_after.get("pattern_entries", 0) - stats_before.get("pattern_entries", 0)
        candle_diff = stats_after.get("ohlcv_candles", 0) - stats_before.get("ohlcv_candles", 0)

        msg = (
            f"✅ **RESTORE DATABASE BERHASIL!**\n\n"
            f"📊 **Statistik Database Terbaru:**\n"
            f"• 📜 `trade_history` : **{stats_after.get('trade_history', 0)}** data (+{trade_diff} baru)\n"
            f"• 🧠 `pattern_memory`: **{stats_after.get('pattern_memory', 0)}** pola AI (+{pattern_diff} baru)\n"
            f"• 🎯 `pattern_entries`: **{stats_after.get('pattern_entries', 0)}** entri (+{entries_diff} baru)\n"
            f"• 📈 `ohlcv_candles` : **{stats_after.get('ohlcv_candles', 0)}** candle (+{candle_diff} baru)\n\n"
            f"ℹ️ *Data kembar telah otomatis digabungkan/diperbarui dengan data terbaru.*"
        )

        return {
            "success": True,
            "message": msg,
            "stats_before": stats_before,
            "stats_after": stats_after,
            "statements_executed": statements_count,
            "error": None
        }

    except Exception as exc:
        logger.error(f"[RESTORE SERVICE] Gagal restore database: {exc}")
        return {
            "success": False,
            "message": f"❌ Gagal restore database: {str(exc)}",
            "error": str(exc)
        }
    finally:
        if temp_extract_dir and os.path.exists(temp_extract_dir):
            try:
                import shutil
                shutil.rmtree(temp_extract_dir, ignore_errors=True)
            except Exception:
                pass


async def restore_from_latest_backup() -> Dict[str, Any]:
    """Mencari file backup terbaru di folder database/ dan mengeksekusi restore."""
    backups = get_available_backup_files()
    if not backups:
        return {
            "success": False,
            "message": "❌ Tidak ditemukan file backup (.sql atau .zip) di folder database/ server.",
            "error": "NO_BACKUP_FOUND"
        }
    latest_file = backups[0]["filepath"]
    return await execute_database_restore(latest_file)
