import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import asyncio
from config.settings import bot_config
from core.exchanges.factory import get_exchange_adapter

async def main():
    bot_config.active_exchange = "BITUNIX"
    client = get_exchange_adapter("BITUNIX", force_recreate=True)
    await client.init()
    top_coins = await client.get_top_futures_by_volume(5)
    print("✅ Bitunix Top 5 Coins:", top_coins)
    df = await client.fetch_ohlcv("BTCUSDT", "5m", 10)
    print(f"✅ Bitunix BTCUSDT 5m: {len(df)} candles fetched successfully.")
    print(df.tail(2))
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
