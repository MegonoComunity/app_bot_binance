"""
database/pattern_repo.py

Repository untuk tabel pattern_memory dan pattern_entries.
Menyimpan hasil pembelajaran bot (WIN/LOSS berdasarkan kondisi indikator).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from database.connection import get_pool

logger = logging.getLogger(__name__)


# ─── Pattern Entries ─────────────────────────────────────────────────────────

async def record_entry(
    entry_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    fingerprint: str,
    alasan: str,
    conditions: dict,
) -> bool:
    """Simpan snapshot kondisi indikator saat entry trade."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pattern_entries
                    (entry_id, symbol, side, entry_price, fingerprint, alasan, conditions)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (entry_id) DO NOTHING
                """,
                entry_id,
                symbol,
                side,
                float(entry_price),
                fingerprint,
                alasan,
                json.dumps(conditions, ensure_ascii=False),
            )
        return True
    except Exception as exc:
        logger.error(f"[DB] Gagal record entry {entry_id}: {exc}")
        return False


async def record_result(entry_id: str, is_win: bool, pnl: float) -> bool:
    """
    Update hasil trade pada pattern_entries, lalu upsert pattern_memory.
    """
    try:
        pool = await get_pool()
        result_str = "WIN" if is_win else "LOSS"

        async with pool.acquire() as conn:
            # 1. Update pattern_entries
            await conn.execute(
                """
                UPDATE pattern_entries
                SET result = $1, pnl = $2, closed_at = NOW()
                WHERE entry_id = $3
                """,
                result_str,
                float(pnl),
                entry_id,
            )

            # 2. Ambil fingerprint & conditions dari entry
            row = await conn.fetchrow(
                "SELECT fingerprint, conditions FROM pattern_entries WHERE entry_id = $1",
                entry_id,
            )
            if not row:
                return False

            fp         = row["fingerprint"]
            conditions = row["conditions"]  # sudah JSONB string

            # 3. Upsert pattern_memory
            await conn.execute(
                """
                INSERT INTO pattern_memory (fingerprint, wins, losses, total, total_pnl, conditions, last_seen)
                VALUES ($1, $2, $3, 1, $4, $5::jsonb, NOW())
                ON CONFLICT (fingerprint) DO UPDATE
                    SET wins      = pattern_memory.wins      + $2,
                        losses    = pattern_memory.losses    + $3,
                        total     = pattern_memory.total     + 1,
                        total_pnl = pattern_memory.total_pnl + $4,
                        win_rate  = ROUND(
                            (pattern_memory.wins + $2)::NUMERIC /
                            NULLIF(pattern_memory.total + 1, 0) * 100, 2
                        ),
                        last_seen  = NOW(),
                        updated_at = NOW()
                """,
                fp,
                1 if is_win else 0,
                0 if is_win else 1,
                float(pnl),
                conditions,
            )
        logger.debug(f"[DB] Hasil {entry_id} ({result_str}) direkam ke pattern_memory")
        return True
    except Exception as exc:
        logger.error(f"[DB] Gagal record result {entry_id}: {exc}")
        return False


# ─── Pattern Memory Queries ──────────────────────────────────────────────────

async def get_top_patterns(top_n: int = 10, min_samples: int = 3) -> list[dict]:
    """Ambil pola terbaik berdasarkan win_rate dari DB."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fingerprint, wins, losses, total,
                       win_rate, total_pnl / NULLIF(total, 0) AS avg_pnl,
                       last_seen
                FROM pattern_memory
                WHERE total >= $1
                ORDER BY win_rate DESC, total DESC
                LIMIT $2
                """,
                min_samples,
                top_n,
            )
        return [
            {
                "fingerprint": r["fingerprint"],
                "win":         r["wins"],
                "loss":        r["losses"],
                "total":       r["total"],
                "win_rate":    float(r["win_rate"] or 0),
                "avg_pnl":     round(float(r["avg_pnl"] or 0), 4),
                "last_seen":   str(r["last_seen"])[:19] if r["last_seen"] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil top patterns: {exc}")
        return []


async def get_pattern_score(fingerprint: str, min_samples: int = 3) -> Optional[float]:
    """
    Ambil win_rate untuk fingerprint tertentu dari DB.
    Return None jika belum ada cukup sampel.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT wins, total, total_pnl FROM pattern_memory WHERE fingerprint = $1",
                fingerprint,
            )
        if not row or row["total"] < min_samples:
            return None
        win_rate = row["wins"] / row["total"]
        avg_pnl  = float(row["total_pnl"] or 0) / row["total"]
        score    = win_rate * 80 + (20 if avg_pnl > 0 else 0)
        return round(min(score, 100.0), 1)
    except Exception as exc:
        logger.error(f"[DB] Gagal ambil pattern score: {exc}")
        return None


async def is_pattern_blacklisted(fingerprint: str, min_samples: int = 3, min_win_rate: float = 0.45) -> bool:
    """Cek apakah pola masuk blacklist (win rate terlalu rendah)."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT wins, total FROM pattern_memory WHERE fingerprint = $1",
                fingerprint,
            )
        if not row or row["total"] < min_samples:
            return False
        return (row["wins"] / row["total"]) < min_win_rate
    except Exception as exc:
        logger.error(f"[DB] Gagal cek blacklist: {exc}")
        return False


# ─── Migration ───────────────────────────────────────────────────────────────

async def migrate_from_json(json_path: str = "data/pattern_memory.json") -> int:
    """
    Import data pattern_memory.json yang lama ke PostgreSQL.
    Hanya dijalankan jika tabel masih kosong.
    """
    if not os.path.exists(json_path):
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM pattern_memory")
        if count and count > 0:
            logger.info(f"[DB] pattern_memory sudah berisi {count} pola, skip migrasi JSON.")
            return 0

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        patterns  = data.get("patterns", {})
        imported  = 0
        pool = await get_pool()
        async with pool.acquire() as conn:
            for fp, p in patterns.items():
                total = p.get("total", 0)
                wins  = p.get("win", 0)
                if total == 0:
                    continue
                await conn.execute(
                    """
                    INSERT INTO pattern_memory
                        (fingerprint, wins, losses, total, total_pnl, win_rate, conditions, last_seen)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                    ON CONFLICT (fingerprint) DO NOTHING
                    """,
                    fp,
                    wins,
                    p.get("loss", 0),
                    total,
                    float(p.get("total_pnl", 0)),
                    round(wins / total * 100, 2),
                    json.dumps(p.get("conditions_sample", {})),
                    datetime.now(),
                )
                imported += 1

        # Migrate entries
        entries = data.get("entries", [])
        entry_count = 0
        async with pool.acquire() as conn:
            for e in entries:
                if not e.get("id"):
                    continue
                try:
                    entered_at_raw = e.get("time")
                    try:
                        entered_at = datetime.strptime(str(entered_at_raw), "%Y-%m-%d %H:%M:%S")
                    except (ValueError, TypeError):
                        entered_at = datetime.now()
                    await conn.execute(
                        """
                        INSERT INTO pattern_entries
                            (entry_id, symbol, side, entry_price, fingerprint, alasan, conditions, result, pnl, entered_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)
                        ON CONFLICT (entry_id) DO NOTHING
                        """,
                        e["id"],
                        e.get("symbol", ""),
                        e.get("side", "LONG"),
                        float(e.get("entry_price", 0) or 0),
                        e.get("fingerprint", ""),
                        e.get("alasan", ""),
                        json.dumps(e.get("conditions", {})),
                        e.get("result"),
                        float(e.get("pnl", 0) or 0) if e.get("pnl") is not None else None,
                        entered_at,
                    )
                    entry_count += 1
                except Exception:
                    continue

        logger.info(f"[DB] Migrasi pattern: {imported} pola, {entry_count} entries diimport")
        print(f"[DB] Migrasi pattern_memory.json -> DB: {imported} pola, {entry_count} entries - OK")
        return imported
    except Exception as exc:
        logger.error(f"[DB] Migrasi pattern JSON gagal: {exc}")
        return 0
