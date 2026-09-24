from __future__ import annotations
import asyncio
import time
from typing import List, Dict, Union, Optional, Any
import pandas as pd
from binance import AsyncClient
from core.exchanges.base import BaseExchange


async def get_top_futures_by_volume(
    client: Union[BaseExchange, AsyncClient],
    n: Optional[int] = None,
    sort_by: str = "VOLUME_DESC",
) -> List[str]:
    """
    Mengambil daftar koin Futures berdasarkan volume atau change persentase 24 jam.
    Mendukung BaseExchange adapter maupun direct Binance AsyncClient.
    """
    if isinstance(client, BaseExchange):
        return await client.get_top_futures_by_volume(n=n, sort_by=sort_by)

    # Fallback untuk direct Binance AsyncClient
    try:
        exchange_info = await client.futures_exchange_info()
        valid_symbols = {
            s['symbol'] for s in exchange_info['symbols'] 
            if s['status'] == 'TRADING' and s['symbol'].endswith('USDT')
        }
        
        tickers = await client.futures_ticker()
        usdt_pairs = []
        for t in tickers:
            sym = t.get('symbol', '')
            if sym not in valid_symbols or not sym.isascii():
                continue
            quote_vol = float(t.get('quoteVolume', 0) or 0)
            change_pct = float(t.get('priceChangePercent', 0) or 0)
            usdt_pairs.append({
                "symbol": sym,
                "quote_vol": quote_vol,
                "change_pct": change_pct,
                "abs_change": abs(change_pct),
            })

        sort_mode = str(sort_by).upper().strip()
        if sort_mode in ("CHANGE_DESC", "CHANGE", "VOLATILITY"):
            usdt_pairs.sort(key=lambda x: (x["abs_change"], x["quote_vol"]), reverse=True)
        elif sort_mode in ("GAINERS", "TOP_GAINERS"):
            usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=True)
        elif sort_mode in ("LOSERS", "TOP_LOSERS"):
            usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=False)
        else:
            usdt_pairs.sort(key=lambda x: x["quote_vol"], reverse=True)

        symbols = [pair['symbol'] for pair in usdt_pairs]
        if n is not None and isinstance(n, int) and n > 0:
            return symbols[:n]
        return symbols
    except Exception as e:
        print(f"Error fetching top coins: {e}")
        return []


_ohlcv_cache: Dict[str, tuple[float, pd.DataFrame]] = {}

async def fetch_ohlcv(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    interval: str,
    limit: int = 100,
    use_cache: bool = True
) -> pd.DataFrame:
    """
    Mengambil data OHLCV dengan in-memory caching untuk Higher Timeframes (1h, 4h, 1d)
    agar menghemat kuota / request weight API (Binance/Bitunix) hingga 60-70%.
    Mendukung BaseExchange adapter maupun direct Binance AsyncClient.
    """
    cache_key = f"{getattr(client, 'exchange_name', 'DEFAULT')}_{symbol}_{interval}_{limit}"
    now = time.time()
    
    # TTL Cache: 1d = 900s (15 min), 1h/4h = 180s (3 min), lower TF (5m/1m) = 0s
    cache_ttl = 0
    if interval in {"1d", "1w"}:
        cache_ttl = 900
    elif interval in {"1h", "4h", "2h"}:
        cache_ttl = 180
        
    if use_cache and cache_ttl > 0 and cache_key in _ohlcv_cache:
        cached_time, cached_df = _ohlcv_cache[cache_key]
        if now - cached_time < cache_ttl:
            return cached_df.copy()

    try:
        if isinstance(client, BaseExchange):
            df = await client.fetch_ohlcv(symbol=symbol, interval=interval, limit=limit)
        else:
            klines = await client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
                
        if cache_ttl > 0 and not df.empty:
            _ohlcv_cache[cache_key] = (now, df)
            
        return df
    except Exception as e:
        print(f"Error fetching OHLCV for {symbol} ({interval}): {e}")
        # Jika kena error/rate limit dan ada cache lama, gunakan cache lama sebagai fallback
        if cache_key in _ohlcv_cache:
            return _ohlcv_cache[cache_key][1].copy()
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
        return {'bids_usdt': 100000.0, 'asks_usdt': 100000.0, 'mid_price': 0, 'best_bid': 0, 'best_ask': 0}
