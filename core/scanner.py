import asyncio
from binance import AsyncClient
import pandas as pd
from typing import List, Dict

async def get_top_futures_by_volume(client: AsyncClient, n: int = None) -> List[str]:
    """
    Mengambil top koin Futures berdasarkan volume 24 jam terakhir.
    Jika n None, ambil semua.
    """
    try:
        # Ambil info exchange untuk mengecek status koin yang valid (TRADING)
        exchange_info = await client.futures_exchange_info()
        valid_symbols = set([
            s['symbol'] for s in exchange_info['symbols'] 
            if s['status'] == 'TRADING' and s['symbol'].endswith('USDT')
        ])
        
        tickers = await client.futures_ticker()
        
        # Filter hanya USDT margin yang valid, berstatus TRADING, dan hanya karakter ASCII (hindari simbol aneh/China chars)
        usdt_pairs = [
            t for t in tickers
            if t['symbol'] in valid_symbols and t['symbol'].isascii()
        ]
        
        # Urutkan berdasarkan quoteVolume (USDT volume) descending
        usdt_pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
        
        # Ambil top N symbol atau semua
        if n is not None:
            top_symbols = [pair['symbol'] for pair in usdt_pairs[:n]]
        else:
            top_symbols = [pair['symbol'] for pair in usdt_pairs]
            
        return top_symbols
    except Exception as e:
        print(f"Error fetching top coins: {e}")
        return []

async def fetch_ohlcv(client: AsyncClient, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """
    Mengambil data OHLCV.
    """
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

async def get_funding_rate(client: AsyncClient, symbol: str) -> float:
    """
    Mengambil funding rate terakhir.
    """
    try:
        funding = await client.futures_funding_rate(symbol=symbol, limit=1)
        if funding:
            return float(funding[0]['fundingRate'])
        return 0.0
    except Exception as e:
        print(f"Error fetching funding rate for {symbol}: {e}")
        return 0.0

async def check_order_book_depth(client: AsyncClient, symbol: str, radius_percent: float = 1.0) -> dict:
    """
    Mengecek likuiditas di order book dalam radius tertentu (misal 1% dari harga tengah).
    Mengembalikan total USDT di bids dan asks.
    """
    try:
        depth = await client.futures_order_book(symbol=symbol, limit=100)
        
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
    except Exception as e:
        print(f"Error checking order book depth for {symbol}: {e}")
        return {'bids_usdt': 0, 'asks_usdt': 0, 'mid_price': 0, 'best_bid': 0, 'best_ask': 0}
