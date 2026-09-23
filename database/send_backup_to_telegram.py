"""
database/send_backup_to_telegram.py

Script untuk membuat dump database PostgreSQL db_crypto_learn,
mengompresnya menjadi ZIP, dan mengirimkannya langsung ke Telegram Admin.
"""
import asyncio
import os
import subprocess
import zipfile
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import FSInputFile

from urllib.parse import urlparse
import json

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

DB_URL = os.getenv("DATABASE_URL", "")
if DB_URL:
    _parsed = urlparse(DB_URL)
    DB_HOST = _parsed.hostname or "localhost"
    DB_PORT = str(_parsed.port or 5432)
    DB_USER = _parsed.username or "postgres"
    DB_PASSWORD = _parsed.password or "postgres"
    DB_NAME = _parsed.path.lstrip("/") or "db_trade_bot"
else:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
    DB_NAME = os.getenv("DB_NAME", "db_trade_bot")


async def _dump_database_with_asyncpg(sql_path: str) -> bool:
    """Fallback dump seluruh tabel PostgreSQL ke file SQL menggunakan asyncpg."""
    try:
        from database.connection import get_pool
        pool = await get_pool()
        
        tables = [
            "trade_history",
            "pattern_memory",
            "pattern_entries",
            "trade_analysis_session",
            "ohlcv_candles",
        ]
        
        with open(sql_path, "w", encoding="utf-8") as f:
            f.write(f"-- PostgreSQL Database Backup\n")
            f.write(f"-- Database: {DB_NAME}\n")
            f.write(f"-- Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            async with pool.acquire() as conn:
                for tbl in tables:
                    # Cek apakah tabel ada
                    exists = await conn.fetchval(
                        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)", tbl
                    )
                    if not exists:
                        continue
                    
                    f.write(f"\n-- ----------------------------\n-- Table: {tbl}\n-- ----------------------------\n")
                    rows = await conn.fetch(f"SELECT * FROM {tbl}")
                    if not rows:
                        f.write(f"-- No data in {tbl}\n")
                        continue
                    
                    cols = list(rows[0].keys())
                    cols_joined = ", ".join(f'"{c}"' for c in cols)
                    
                    for r in rows:
                        vals = []
                        for c in cols:
                            v = r[c]
                            if v is None:
                                vals.append("NULL")
                            elif isinstance(v, (int, float)):
                                vals.append(str(v))
                            elif isinstance(v, bool):
                                vals.append("TRUE" if v else "FALSE")
                            elif isinstance(v, dict):
                                json_str = json.dumps(v, ensure_ascii=False).replace("'", "''")
                                vals.append(f"'{json_str}'::jsonb")
                            elif isinstance(v, datetime):
                                vals.append(f"'{v.isoformat()}'")
                            else:
                                clean_v = str(v).replace("'", "''")
                                vals.append(f"'{clean_v}'")
                        vals_joined = ", ".join(vals)
                        f.write(f"INSERT INTO {tbl} ({cols_joined}) VALUES ({vals_joined}) ON CONFLICT DO NOTHING;\n")
                        
        return True
    except Exception as exc:
        print(f"[ERROR] asyncpg fallback dump gagal: {exc}")
        return False


async def execute_database_backup(bot: Bot | None = None, chat_id: str | None = None) -> bool:
    """
    Eksekusi dump database, kompres ZIP, dan kirim ke Telegram.
    Dapat dipanggil langsung oleh scheduler atau command Telegram.
    """
    target_chat_id = chat_id or TELEGRAM_ADMIN_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        print("[ERROR] TELEGRAM_BOT_TOKEN atau target chat_id belum diatur di .env")
        return False

    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    os.makedirs("database", exist_ok=True)
    sql_file = f"database/backup_{timestamp}.sql"
    zip_file = f"database/backup_{DB_NAME}_{timestamp}.zip"

    print(f"[1/3] Mendump database '{DB_NAME}' ke {sql_file}...")
    dump_success = False
    
    # Coba pg_dump terlebih dahulu
    pg_dump_paths = [
        r"C:\laragon\bin\postgresql\pgsql-11\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
        "pg_dump"
    ]
    for pg_dump in pg_dump_paths:
        if os.path.exists(pg_dump) or pg_dump == "pg_dump":
            cmd = f'set PGPASSWORD={DB_PASSWORD}&& "{pg_dump}" -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -d {DB_NAME} --clean --if-exists --inserts -f "{sql_file}"'
            ret = os.system(cmd)
            if ret == 0 and os.path.exists(sql_file) and os.path.getsize(sql_file) > 0:
                dump_success = True
                print(f"[OK] pg_dump berhasil menggunakan {pg_dump}.")
                break

    # Jika pg_dump tidak berhasil, gunakan asyncpg exporter
    if not dump_success:
        print("[INFO] Menjalankan fallback native export asyncpg...")
        dump_success = await _dump_database_with_asyncpg(sql_file)

    if not dump_success or not os.path.exists(sql_file):
        print(f"[ERROR] Seluruh metode dump database gagal.")
        return False

    sql_size_mb = os.path.getsize(sql_file) / (1024 * 1024)
    print(f"[OK] Dump selesai ({sql_size_mb:.2f} MB).")

    print(f"[2/3] Mengompres ke {zip_file}...")
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(sql_file, arcname=os.path.basename(sql_file))
    
    zip_size_mb = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"[OK] Kompresi ZIP selesai ({zip_size_mb:.2f} MB).")

    print(f"[3/3] Mengirim file backup ke Telegram (Chat ID: {target_chat_id})...")
    local_bot = bot is None
    active_bot = bot or Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        doc = FSInputFile(zip_file)
        caption = (
            f"📦 **DATABASE AUTO-BACKUP (03:00 WIB) — CRYPTO BOT**\n"
            f"──────────────\n"
            f"🗄️ Database: `{DB_NAME}`\n"
            f"⏱️ Waktu: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} WIB`\n"
            f"📊 Ukuran SQL: `{sql_size_mb:.2f} MB` → ZIP: `{zip_size_mb:.2f} MB`\n"
            f"──────────────\n"
            f"💡 **Tabel Termasuk:**\n"
            f"• `trade_history` (Riwayat transaksi lengkap)\n"
            f"• `pattern_memory` (AI Sidik jari & win rate pola)\n"
            f"• `pattern_entries` (Detail parameter entry)\n"
            f"• `trade_analysis_session` (Tabel analisa sesi baru)\n"
            f"• `ohlcv_candles` (Data candle harian)\n\n"
            f"✅ *Backup harian otomatis terkirim.*"
        )
        await active_bot.send_document(
            chat_id=target_chat_id,
            document=doc,
            caption=caption,
            parse_mode="Markdown"
        )
        print("[SUCCESS] File backup database telah terkirim ke Telegram.")
        return True
    except Exception as exc:
        print(f"[ERROR] Gagal mengirim file ke Telegram: {exc}")
        return False
    finally:
        if local_bot:
            await active_bot.session.close()
        if os.path.exists(sql_file):
            os.remove(sql_file)
        if os.path.exists(zip_file):
            os.remove(zip_file)


async def daily_backup_scheduler_loop(bot: Bot) -> None:
    """
    Loop otomatis yang berjalan di background dan mengirimkan backup database
    setiap hari tepat pada jam 03:00 WIB / server time.
    """
    last_backup_date = None
    print("[SCHEDULER] ⏰ Jadwal Auto-Backup Database setiap jam 03:00 WIB aktif.")
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            # Cek jika jam 03:00 s/d 03:05 dan belum dibackup hari ini
            if now.hour == 3 and now.minute == 0 and last_backup_date != today_str:
                print(f"[SCHEDULER] ⏰ Memulai Auto-Backup Database harian ({today_str} 03:00)...")
                success = await execute_database_backup(bot=bot)
                if success:
                    last_backup_date = today_str
                    print(f"[SCHEDULER] ✅ Auto-Backup harian {today_str} selesai & terkirim.")
            await asyncio.sleep(30)
        except Exception as e:
            print(f"[SCHEDULER] Error in daily_backup_scheduler_loop: {e}")
            await asyncio.sleep(60)


async def main():
    await execute_database_backup()


if __name__ == "__main__":
    asyncio.run(main())
