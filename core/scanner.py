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
        
        # Filter hanya USDT margin yang valid dan berstatus TRADING
        usdt_pairs = [t for t in tickers if t['symbol'] in valid_symbols]
        
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
