import asyncio
import os
import json
from dotenv import load_dotenv

load_dotenv()

from core.exchanges.bitunix_adapter import BitunixAdapter

async def main():
    api_key = os.getenv("BITUNIX_API_KEY", "")
    api_secret = os.getenv("BITUNIX_API_SECRET", "")
    adapter = BitunixAdapter(api_key, api_secret)
    try:
        res = await adapter._request("GET", "/api/v1/futures/market/trading_pairs")
        pairs = res.get("data", [])
        for sym in ["CRVUSDT", "DOGEUSDT", "BTCUSDT", "ETHFIUSDT"]:
            p = next((x for x in pairs if x.get("symbol") == sym), None)
            if p:
                print(f"{sym}: basePrecision={p.get('basePrecision')}, quotePrecision={p.get('quotePrecision')}, minTradeVolume={p.get('minTradeVolume')}")
    finally:
        await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())
