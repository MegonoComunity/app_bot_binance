import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from database.connection import get_pool
from config.settings import BotSettings
from core.exchanges.bitunix_adapter import BitunixAdapter

async def main():
    cfg = BotSettings()
    print(f"=== BOT SETTINGS ===")
    print(f"TRADING_MODE: {cfg.trading_mode}")
    print(f"ACTIVE_EXCHANGE: {cfg.active_exchange}")
    print(f"MAX_OPEN_POSITIONS: {cfg.max_open_positions}")
    print(f"MARGIN_USDT: {cfg.margin_usdt} | LEVERAGE: {cfg.leverage}x | TP: {cfg.tp_percent}% | SL: {cfg.sl_percent}%")

    pool = await get_pool()
    async with pool.acquire() as conn:
        recent_trades = await conn.fetch("SELECT * FROM trade_history LIMIT 5;")
        print(f"\n=== RECENT 5 TRADES IN DATABASE ===")
        for r in recent_trades:
            print(dict(r))

    api_key = os.getenv("BITUNIX_API_KEY", "")
    api_secret = os.getenv("BITUNIX_API_SECRET", "")
    if api_key and api_secret:
        adapter = BitunixAdapter(api_key, api_secret)
        try:
            await adapter.init()
            pos = await adapter.get_open_positions()
            print(f"\n=== REAL BITUNIX OPEN POSITIONS ({len(pos)}) ===")
            for p in pos:
                print(p)
        except Exception as e:
            print("Bitunix check error:", e)
        finally:
            await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())

