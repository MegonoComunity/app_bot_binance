"""
core/ohlcv_scraper.py

OHLCV Scraper — mengambil data candlestick dari Exchange Futures dan menyimpannya ke PostgreSQL.
Mendukung multi-exchange (Binance, Bitunix, dll).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Union, List, Dict, Any

from binance import AsyncClient
from core.exchanges.base import BaseExchange
from database.ohlcv_repo import upsert_candles

logger = logging.getLogger(__name__)

# ─── Konfigurasi ─────────────────────────────────────────────────────────────

TOP_N_COINS       = 30      # Jumlah koin teratas yang di-scrape
CANDLE_LIMIT      = 100     # Candle per symbol untuk daily (20-100 hari)

# Hanya timeframe Daily (1d) agar data database tidak membengkak
TIMEFRAME_SCHEDULE = [
    ("1d",  6 * 60 * 60),    # Setiap 6 jam
]

DELAY_BETWEEN_SYMBOLS = 0.3   # detik delay antar API call (rate limit safety)

# ─── State ───────────────────────────────────────────────────────────────────

_top_symbols: list[str] = []
_last_symbol_refresh: Optional[datetime] = None
_SYMBOL_REFRESH_HOURS = 6    # Refresh daftar top coins setiap 6 jam


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_top_symbols(client: Union[BaseExchange, AsyncClient]) -> list[str]:
    """
    Ambil Top N koin Futures berdasarkan quoteVolume 24H.
    Cache selama 6 jam.
    """
    global _top_symbols, _last_symbol_refresh

    now = datetime.now(timezone.utc)
    if _top_symbols and _last_symbol_refresh:
        age_hours = (now - _last_symbol_refresh).total_seconds() / 3600
        if age_hours < _SYMBOL_REFRESH_HOURS:
            return _top_symbols

    try:
        if isinstance(client, BaseExchange):
            top_coins = await client.get_top_futures_by_volume(n=TOP_N_COINS)
            _top_symbols = [s for s in top_coins if not s.startswith("1000")][:TOP_N_COINS]
        else:
            tickers = await client.futures_ticker()
            usdt_pairs = [
                t for t in tickers
                if t["symbol"].endswith("USDT") and not t["symbol"].startswith("1000")
            ]
            usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            _top_symbols = [t["symbol"] for t in usdt_pairs[:TOP_N_COINS]]

        _last_symbol_refresh = now
        logger.info(f"[OHLCV] Top {TOP_N_COINS} symbols diperbarui: {_top_symbols[:5]}...")
    except Exception as exc:
        logger.error(f"[OHLCV] Gagal ambil top symbols: {exc}")
        if not _top_symbols:
            _top_symbols = [
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
            ]
    return _top_symbols


def _df_to_candles(df) -> list[dict]:
    """Konversi DataFrame OHLCV ke list dict format database."""
    candles = []
    if df is None or df.empty:
        return candles
    for _, row in df.iterrows():
        try:
            ts = int(row['timestamp'].timestamp() * 1000) if hasattr(row['timestamp'], 'timestamp') else int(row['timestamp'])
            candles.append({
                "open_time":  ts,
                "open":       float(row['open']),
                "high":       float(row['high']),
                "low":        float(row['low']),
                "close":      float(row['close']),
                "volume":     float(row['volume']),
                "close_time": ts + 86400000,
            })
        except Exception:
            continue
    return candles


async def _scrape_one_symbol(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    timeframe: str,
) -> int:
    """Scrape satu symbol untuk satu timeframe. Return jumlah candle yang disimpan."""
    try:
        if isinstance(client, BaseExchange):
            df = await client.fetch_ohlcv(symbol=symbol, interval=timeframe, limit=CANDLE_LIMIT)
            candles = _df_to_candles(df)
        else:
            raw = await client.futures_klines(
                symbol=symbol,
                interval=timeframe,
                limit=CANDLE_LIMIT,
            )
            candles = []
            for k in raw:
                try:
                    candles.append({
                        "open_time":  int(k[0]),
                        "open":       float(k[1]),
                        "high":       float(k[2]),
                        "low":        float(k[3]),
                        "close":      float(k[4]),
                        "volume":     float(k[5]),
                        "close_time": int(k[6]),
                    })
                except Exception:
                    continue

        saved = await upsert_candles(symbol, timeframe, candles)
        return saved
    except Exception as exc:
        logger.warning(f"[OHLCV] {symbol} {timeframe}: {exc}")
        return 0


async def scrape_all_timeframes(client: Union[BaseExchange, AsyncClient]) -> dict[str, int]:
    """
    Scrape semua Top N symbols untuk timeframe terdaftar.
    Return dict {timeframe: total_candles_saved}
    """
    symbols = await _get_top_symbols(client)
    summary: dict[str, int] = {}

    for timeframe, _ in TIMEFRAME_SCHEDULE:
        tf_total = 0
        for symbol in symbols:
            saved = await _scrape_one_symbol(client, symbol, timeframe)
            tf_total += saved
            await asyncio.sleep(DELAY_BETWEEN_SYMBOLS)
        summary[timeframe] = tf_total
        logger.info(f"[OHLCV] {timeframe}: {tf_total} candles disimpan untuk {len(symbols)} coins")

    return summary


# ─── Background Tasks ────────────────────────────────────────────────────────

async def run_short_term_scraper(client: Union[BaseExchange, AsyncClient]) -> None:
    """Background task untuk timeframe pendek."""
    short_tfs = [tf for tf, interval in TIMEFRAME_SCHEDULE if interval == 5 * 60]
    if not short_tfs:
        return
    logger.info(f"[OHLCV] Short-term scraper aktif: {short_tfs} (setiap 5 menit)")

    while True:
        try:
            symbols = await _get_top_symbols(client)
            for timeframe in short_tfs:
                tf_total = 0
                for symbol in symbols:
                    saved = await _scrape_one_symbol(client, symbol, timeframe)
                    tf_total += saved
                    await asyncio.sleep(DELAY_BETWEEN_SYMBOLS)
                if tf_total > 0:
                    logger.debug(f"[OHLCV] {timeframe}: {tf_total} candles diperbarui")
        except asyncio.CancelledError:
            logger.info("[OHLCV] Short-term scraper dihentikan.")
            break
        except Exception as exc:
            logger.error(f"[OHLCV] Error di short-term scraper: {exc}")
        await asyncio.sleep(5 * 60)


async def run_long_term_scraper(client: Union[BaseExchange, AsyncClient]) -> None:
    """Background task untuk timeframe panjang."""
    long_tfs = [tf for tf, interval in TIMEFRAME_SCHEDULE if interval == 6 * 60 * 60]
    logger.info(f"[OHLCV] Long-term scraper aktif: {long_tfs} (setiap 6 jam)")

    while True:
        try:
            symbols = await _get_top_symbols(client)
            for timeframe in long_tfs:
                tf_total = 0
                for symbol in symbols:
                    saved = await _scrape_one_symbol(client, symbol, timeframe)
                    tf_total += saved
                    await asyncio.sleep(DELAY_BETWEEN_SYMBOLS)
                logger.info(f"[OHLCV] {timeframe}: {tf_total} candles historis tersimpan ({len(symbols)} coins)")
            print(f"[OHLCV] Daily/Weekly data diperbarui: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        except asyncio.CancelledError:
            logger.info("[OHLCV] Long-term scraper dihentikan.")
            break
        except Exception as exc:
            logger.error(f"[OHLCV] Error di long-term scraper: {exc}")
        await asyncio.sleep(6 * 60 * 60)


async def run_initial_scrape(client: Union[BaseExchange, AsyncClient]) -> None:
    """Scrape pertama kali saat startup."""
    try:
        logger.info("[OHLCV] Initial scrape dimulai...")
        print("[OHLCV] 📊 Mengambil data historis candle... (background)")
        summary = await scrape_all_timeframes(client)
        total = sum(summary.values())
        print(f"[OHLCV] ✅ Initial scrape selesai: {total} candles disimpan → {summary}")
        logger.info(f"[OHLCV] Initial scrape selesai: {summary}")
    except Exception as exc:
        logger.error(f"[OHLCV] Initial scrape gagal: {exc}")
