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
)
from core.exchanges.base import BaseExchange
from core.exchanges.binance_adapter import BinanceAdapter
from core.exchanges.bitunix_adapter import BitunixAdapter

_current_exchange_instance: Optional[BaseExchange] = None


def get_exchange_adapter(exchange_name: Optional[str] = None) -> BaseExchange:
    """
    Factory function untuk membuat atau mengambil instance ExchangeAdapter (Singleton per active name).
    Mendukung 'BINANCE' dan 'BITUNIX'.
    """
    global _current_exchange_instance
    target_exchange = (exchange_name or ACTIVE_EXCHANGE or "BINANCE").upper().strip()

    # Jika instance sudah ada dan tipe exchange sama, kembalikan instance yang ada
    if _current_exchange_instance is not None:
        if _current_exchange_instance.exchange_name == target_exchange:
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
        is_testnet = (TRADING_MODE == "TESTNET")
        _current_exchange_instance = BinanceAdapter(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET,
            testnet=is_testnet,
        )
    else:
        raise ValueError(f"Exchange '{target_exchange}' tidak didukung. Pilihan: BINANCE, BITUNIX")

    return _current_exchange_instance
