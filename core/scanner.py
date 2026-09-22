from __future__ import annotations
import asyncio
from typing import List, Dict, Union, Optional, Any
import pandas as pd
from binance import AsyncClient
from core.exchanges.base import BaseExchange


async def get_top_futures_by_volume(client: Union[BaseExchange, AsyncClient], n: Optional[int] = None) -> List[str]:
    """
    Mengambil top koin Futures berdasarkan volume 24 jam terakhir.
    Mendukung BaseExchange adapter maupun direct Binance AsyncClient.
    """
    if isinstance(client, BaseExchange):
        return await client.get_top_futures_by_volume(n=n)

    # Fallback untuk direct Binance AsyncClient
    try:
        exchange_info = await client.futures_exchange_info()
        valid_symbols = {
            s['symbol'] for s in exchange_info['symbols'] 
            if s['status'] == 'TRADING' and s['symbol'].endswith('USDT')
        }
        
        tickers = await client.futures_ticker()
        usdt_pairs = [
            t for t in tickers
            if t['symbol'] in valid_symbols and t['symbol'].isascii()
        ]
        usdt_pairs.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
        
        if n is not None:
            return [pair['symbol'] for pair in usdt_pairs[:n]]
        return [pair['symbol'] for pair in usdt_pairs]
    except Exception as e:
        print(f"Error fetching top coins: {e}")
        return []


async def fetch_ohlcv(client: Union[BaseExchange, AsyncClient], symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Mengambil data OHLCV.
    Mendukung BaseExchange adapter maupun direct Binance AsyncClient.
    """
    if isinstance(client, BaseExchange):
        return await client.fetch_ohlcv(symbol=symbol, interval=interval, limit=limit)

    try:
        klines = await client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        print(f"Error fetching OHLCV for {symbol}: {e}")
        return pd.DataFrame()


async def get_funding_rate(client: Union[BaseExchange, AsyncClient], symbol: str) -> float:
    """
    Mengambil funding rate terakhir.
    """
    try:
        binance_client = client.client if hasattr(client, 'client') else client
        if hasattr(binance_client, 'futures_funding_rate'):
            funding = await binance_client.futures_funding_rate(symbol=symbol, limit=1)
            if funding:
                return float(funding[0]['fundingRate'])
        return 0.0
    except Exception as e:
        print(f"Error fetching funding rate for {symbol}: {e}")
        return 0.0


async def check_order_book_depth(client: Union[BaseExchange, AsyncClient], symbol: str, radius_percent: float = 1.0) -> dict:
    """
    Mengecek likuiditas di order book dalam radius tertentu (misal 1% dari harga tengah).
    Mengembalikan total USDT di bids dan asks.
    """
    try:
        binance_client = client.client if hasattr(client, 'client') else client
        if hasattr(binance_client, 'futures_order_book'):
            depth = await binance_client.futures_order_book(symbol=symbol, limit=100)
            bids = depth.get('bids', [])
            asks = depth.get('asks', [])
            
            if not bids or not asks:
                return {'bids_usdt': 0, 'asks_usdt': 0}
                
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            
            min_price = mid_price * (1 - (radius_percent / 100))
            max_price = mid_price * (1 + (radius_percent / 100))
            
            total_bids_usdt = sum(float(price) * float(qty) for price, qty in bids if float(price) >= min_price)
            total_asks_usdt = sum(float(price) * float(qty) for price, qty in asks if float(price) <= max_price)
            
            return {
                'bids_usdt': total_bids_usdt,
                'asks_usdt': total_asks_usdt,
                'mid_price': mid_price,
                'best_bid': best_bid,
                'best_ask': best_ask
            }
        # Fallback default untuk exchange yang belum query orderbook depth
        return {'bids_usdt': 100000.0, 'asks_usdt': 100000.0, 'mid_price': 0, 'best_bid': 0, 'best_ask': 0}
    except Exception as e:
        print(f"Error checking order book depth for {symbol}: {e}")
        return {'bids_usdt': 0, 'asks_usdt': 0, 'mid_price': 0, 'best_bid': 0, 'best_ask': 0}
