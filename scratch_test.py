import asyncio
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv('DATABASE_URL').split('?')[0]

async def check():
    conn = await asyncpg.connect(db_url)
    table_names = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    print("=== STATUS TABEL DATABASE (db_trade_bot) ===")
    for t in table_names:
        name = t['table_name']
        count = await conn.fetchval(f"SELECT COUNT(*) FROM {name}")
        print(f"[OK] Table '{name}': {count:,} rows")
    await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
