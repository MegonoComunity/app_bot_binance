import asyncio
import os
import json
import time
import hashlib
import hmac
import datetime
from dotenv import load_dotenv
load_dotenv()
from core.exchanges.bitunix_adapter import BitunixAdapter

async def main():
    api_key = os.getenv("BITUNIX_API_KEY", "")
    api_secret = os.getenv("BITUNIX_API_SECRET", "")
    adapter = BitunixAdapter(api_key, api_secret)
    try:
        await adapter.init()
        bal = await adapter.get_account_balance()
        print("Bitunix Balance:", bal)
    except Exception as e:
        print("Error:", e)
    finally:
        await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())
