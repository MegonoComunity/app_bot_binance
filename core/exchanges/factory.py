from __future__ import annotations
from typing import Optional
from config.settings import (
    ACTIVE_EXCHANGE,
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    TRADING_MODE,
    BITUNIX_API_KEY,
    BITUNIX_API_SECRET,
    BITUNIX_BASE_URL,
    BITUNIX_PROXY,
    bot_config,
)
from core.exchanges.base import BaseExchange
from core.exchanges.binance_adapter import BinanceAdapter
from core.exchanges.bitunix_adapter import BitunixAdapter

_current_exchange_instance: Optional[BaseExchange] = None


def get_exchange_adapter(
    exchange_name: Optional[str] = None,
    is_testnet: Optional[bool] = None,
    force_recreate: bool = False,
) -> BaseExchange:
    """
    Factory function untuk membuat atau mengambil instance ExchangeAdapter (Singleton per active name & testnet state).
    Mendukung 'BINANCE' (Real & Testnet Demo API) dan 'BITUNIX' (Real & Paper).
    """
    global _current_exchange_instance
    target_exchange = (exchange_name or getattr(bot_config, "active_exchange", ACTIVE_EXCHANGE) or "BINANCE").upper().strip()
    
    current_mode = getattr(bot_config, "trading_mode", TRADING_MODE).upper()
    if is_testnet is None:
        is_testnet = (current_mode in ("TESTNET", "DEMO"))

    # Jika instance sudah ada dan tipe exchange + testnet flag sama, gunakan yang ada
    if not force_recreate and _current_exchange_instance is not None:
        if _current_exchange_instance.exchange_name == target_exchange:
            if target_exchange == "BINANCE":
                if getattr(_current_exchange_instance, "_testnet", False) == is_testnet:
                    return _current_exchange_instance
            else:
                return _current_exchange_instance

    if target_exchange == "BITUNIX":
        if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
            raise ValueError("BITUNIX_API_KEY atau BITUNIX_API_SECRET belum diatur di file .env")
        _current_exchange_instance = BitunixAdapter(
            api_key=BITUNIX_API_KEY,
            api_secret=BITUNIX_API_SECRET,
            base_url=BITUNIX_BASE_URL,
            proxy=BITUNIX_PROXY,
        )
    elif target_exchange == "BINANCE":
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise ValueError("BINANCE_API_KEY atau BINANCE_API_SECRET belum diatur di file .env")
        _current_exchange_instance = BinanceAdapter(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET,
            testnet=is_testnet,
        )
    else:
        raise ValueError(f"Exchange '{target_exchange}' tidak didukung. Pilihan: BINANCE, BITUNIX")

    return _current_exchange_instance
