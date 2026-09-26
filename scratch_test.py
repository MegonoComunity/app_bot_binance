import asyncio
import os
import time
import hashlib
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

    api_key = os.getenv("BITUNIX_API_KEY", "").strip()
    api_secret = os.getenv("BITUNIX_API_SECRET", "").strip()
    print(f"API_KEY length: {len(api_key)}, API_SECRET length: {len(api_secret)}")
    print(f"API_KEY preview: {api_key[:6]}...{api_key[-4:] if len(api_key)>10 else ''}")
    
    if api_key and api_secret:
        adapter = BitunixAdapter(api_key, api_secret)
        try:
            await adapter.init()
            import hmac
            
            ts_ms = str(int(time.time() * 1000))
            nonce = "12345678901234567890123456789012"
            query_params_str = "marginCoinUSDT"
            body_str = ""
            raw_input = f"{nonce}{ts_ms}{api_key}{query_params_str}{body_str}"
            digest = hashlib.sha256(raw_input.encode("utf-8")).hexdigest()

            variations = [
                ("Double SHA256 (digest + secret)", hashlib.sha256(f"{digest}{api_secret}".encode("utf-8")).hexdigest()),
                ("Double SHA256 (secret + digest)", hashlib.sha256(f"{api_secret}{digest}".encode("utf-8")).hexdigest()),
                ("Single SHA256 (raw_input + secret)", hashlib.sha256(f"{raw_input}{api_secret}".encode("utf-8")).hexdigest()),
                ("HMAC-SHA256 on raw_input", hmac.new(api_secret.encode("utf-8"), raw_input.encode("utf-8"), hashlib.sha256).hexdigest()),
                ("HMAC-SHA256 on digest", hmac.new(api_secret.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()),
                ("Double SHA256 without queryParams in digest", hashlib.sha256(f"{hashlib.sha256((nonce + ts_ms + api_key).encode('utf-8')).hexdigest()}{api_secret}".encode("utf-8")).hexdigest()),
            ]

            for desc, s_val in variations:
                headers = {
                    "api-key": api_key,
                    "nonce": nonce,
                    "timestamp": ts_ms,
                    "sign": s_val,
                    "Content-Type": "application/json",
                }
                url = "https://fapi.bitunix.com/api/v1/futures/account"
                async with adapter._session.get(url, params={"marginCoin": "USDT"}, headers=headers) as resp:
                    res_json = await resp.json()
                    print(f"Algorithm: {desc} -> Code={res_json.get('code')}, Msg={res_json.get('msg')}")
                    if res_json.get("code") in (0, "0", 200, "200"):
                        print(f"🎉 SUCCESS WITH: {desc} -> {res_json}")
        except Exception as e:
            print("Error:", e)
        finally:
            await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())

