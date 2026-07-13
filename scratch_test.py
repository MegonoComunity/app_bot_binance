import asyncio
from binance.client import AsyncClient
import os

async def test():
    client = await AsyncClient.create()
    try:
        await client.futures_time()
        print(client.response.headers.get('x-mbx-used-weight-1m', 'N/A'))
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close_connection()

asyncio.run(test())
