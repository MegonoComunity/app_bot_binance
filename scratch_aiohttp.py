import asyncio
import aiohttp
import socket
import time

async def main():
    connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        ssl=False,
        happy_eyeballs_delay=None,
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    t0 = time.time()
    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
        async with session.get("https://fapi.bitunix.com/api/v1/futures/market/trading_pairs", headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            data = await resp.json()
            print(f"AIOHTTP happy_eyeballs=None Elapsed: {time.time()-t0:.2f}s | Pairs: {len(data.get('data', []))}")

if __name__ == "__main__":
    asyncio.run(main())
