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

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_NAME = os.getenv("DB_NAME", "db_crypto_learn")


async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        print("[ERROR] TELEGRAM_BOT_TOKEN atau TELEGRAM_ADMIN_CHAT_ID belum diatur di .env")
        return

    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    os.makedirs("database", exist_ok=True)
    sql_file = f"database/backup_{timestamp}.sql"
    zip_file = f"database/backup_db_crypto_learn_{timestamp}.zip"

    print(f"[1/3] Mendump database '{DB_NAME}' ke {sql_file}...")
    pg_dump = r"C:\laragon\bin\postgresql\pgsql-11\bin\pg_dump.exe"
    if not os.path.exists(pg_dump):
        pg_dump = "pg_dump"

    cmd = f'set PGPASSWORD={DB_PASSWORD}&& "{pg_dump}" -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -d {DB_NAME} --clean --if-exists --inserts -f "{sql_file}"'
    ret = os.system(cmd)
    if ret != 0 or not os.path.exists(sql_file):
        print(f"[ERROR] pg_dump gagal dengan return code {ret}")
        return

    sql_size_mb = os.path.getsize(sql_file) / (1024 * 1024)
    print(f"[OK] Dump selesai ({sql_size_mb:.2f} MB).")

    print(f"[2/3] Mengompres ke {zip_file}...")
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(sql_file, arcname=os.path.basename(sql_file))
    
    zip_size_mb = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"[OK] Kompresi ZIP selesai ({zip_size_mb:.2f} MB).")

    print(f"[3/3] Mengirim file backup ke Telegram (Chat ID: {TELEGRAM_ADMIN_CHAT_ID})...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        doc = FSInputFile(zip_file)
        caption = (
            f"📦 **DATABASE BACKUP — CRYPTO BOT**\n"
            f"──────────────\n"
            f"🗄️ Database: `{DB_NAME}`\n"
            f"⏱️ Waktu: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} WIB`\n"
            f"📊 Ukuran SQL: `{sql_size_mb:.2f} MB` → ZIP: `{zip_size_mb:.2f} MB`\n"
            f"──────────────\n"
            f"💡 **Cara Restore di Komputer Lain:**\n"
            f"1. Ekstrak file zip ini\n"
            f"2. Jalankan: `psql -U postgres -d {DB_NAME} -f <nama_file.sql>`"
        )
        await bot.send_document(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            document=doc,
            caption=caption,
            parse_mode="Markdown"
        )
        print("[SUCCESS] File backup database telah terkirim ke Telegram.")
    except Exception as exc:
        print(f"[ERROR] Gagal mengirim file ke Telegram: {exc}")
    finally:
        await bot.session.close()
        if os.path.exists(sql_file):
            os.remove(sql_file)
        if os.path.exists(zip_file):
            os.remove(zip_file)


if __name__ == "__main__":
    asyncio.run(main())
