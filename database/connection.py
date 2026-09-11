"""
database/connection.py

Manajemen koneksi async ke PostgreSQL menggunakan asyncpg connection pool.
Pool dibuat sekali saat startup dan di-reuse di seluruh modul.
"""
from __future__ import annotations

import os
import asyncio
import logging
from typing import Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """
    Kembalikan connection pool yang sudah ada, atau buat baru jika belum ada.
    Thread-safe menggunakan asyncio.Lock.
    """
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        # Double-check setelah acquire lock
        if _pool is not None:
            return _pool

        dsn = os.getenv("DATABASE_URL", "")
        if not dsn:
            # Bangun DSN dari komponen individual
            host     = os.getenv("DB_HOST", "localhost")
            port     = int(os.getenv("DB_PORT", "5432"))
            user     = os.getenv("DB_USER", "postgres")
            password = os.getenv("DB_PASSWORD", "postgres")
            dbname   = os.getenv("DB_NAME", "db_crypto_learn")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

        # Bersihkan schema param yang tidak dikenal asyncpg
        dsn = dsn.split("?")[0]

        try:
            _pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=2,
                max_size=10,
                command_timeout=30,
                statement_cache_size=100,
            )
            logger.info("[DB] Connection pool ke PostgreSQL berhasil dibuat ✅")
        except (asyncpg.PostgresConnectionFailedError, OSError) as exc:
            logger.error(f"[DB] Gagal koneksi ke PostgreSQL: {exc}")
            raise

    return _pool


async def close_pool() -> None:
    """Tutup connection pool. Panggil saat shutdown bot."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("[DB] Connection pool ditutup.")


async def is_db_available() -> bool:
    """Cek apakah koneksi DB tersedia (non-blocking check)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:
        logger.warning(f"[DB] Database tidak tersedia: {exc}")
        return False
