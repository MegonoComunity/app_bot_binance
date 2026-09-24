from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import pandas as pd


class BaseExchange(ABC):
    """
    Abstraksi Base Exchange untuk mendukung multi-exchange (Binance, Bitunix, dll).
    Semua fungsi I/O wajib asynchronous (async/await) dan mengembalikan tipe data standar.
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Nama identifier exchange (misal: 'BINANCE', 'BITUNIX')."""
        pass

    @abstractmethod
    async def init(self) -> None:
        """Inisialisasi koneksi / validasi kredensial exchange."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Menutup koneksi / session exchange."""
        pass

    async def close_connection(self) -> None:
        """Alias backward-compatibility untuk client.close_connection()."""
        await self.close()

    @abstractmethod
    async def get_top_futures_by_volume(self, n: Optional[int] = None, sort_by: str = "VOLUME_DESC") -> List[str]:
        """
        Mengambil daftar simbol koin Futures berpasangan USDT teratas berdasarkan volume atau change persentase 24 jam.
        """
        pass

    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        """
        Mengambil data candlestick OHLCV dan mengembalikannya sebagai pd.DataFrame
        dengan kolom standar: ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
        """
        pass

    @abstractmethod
    async def get_symbol_price(self, symbol: str) -> float:
        """
        Mengambil harga pasar terkini (last/mark price) untuk suatu simbol.
        """
        pass

    @abstractmethod
    async def get_account_balance(self) -> Dict[str, float]:
        """
        Mengambil saldo akun futures.
        Mengembalikan dict standar: {'total_wallet_balance': float, 'available_balance': float, 'unrealized_pnl': float}
        """
        pass

    @abstractmethod
    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Mengambil daftar posisi futures yang sedang aktif (posisi terbuka).
        Setiap item memiliki key minimal:
        {
            'symbol': str,
            'side': 'LONG' | 'SHORT',
            'position_amt': float,
            'entry_price': float,
            'mark_price': float,
            'unrealized_pnl': float,
            'leverage': int,
            'liquidation_price': float
        }
        """
        pass

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> int:
        """
        Mengatur leverage untuk symbol tertentu. Mengembalikan leverage aktual yang disetujui.
        """
        pass

    @abstractmethod
    async def set_margin_type(self, symbol: str, margin_type: str = 'ISOLATED') -> None:
        """
        Mengatur tipe margin ('ISOLATED' atau 'CROSSED').
        """
        pass

    @abstractmethod
    async def get_symbol_precision(self, symbol: str) -> Dict[str, int]:
        """
        Mengambil presisi kuantitas ('qty') dan harga ('price') untuk suatu simbol.
        Format return: {'qty': int, 'price': int}
        """
        pass

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,           # 'BUY' atau 'SELL'
        order_type: str,     # 'MARKET' atau 'LIMIT'
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Mengeksekusi order futures.
        """
        pass

    @abstractmethod
    async def place_tp_sl(
        self,
        symbol: str,
        side: str,           # 'BUY' (penutup short) atau 'SELL' (penutup long)
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Memasang Take Profit dan/atau Stop Loss order.
        """
        pass

    @abstractmethod
    async def emergency_close_position(self, symbol: str) -> Dict[str, Any]:
        """
        Menutup seluruh posisi aktif untuk suatu koin secara instan (Market Close).
        """
        pass
