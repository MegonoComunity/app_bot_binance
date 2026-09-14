"""
core/ohlcv_scraper.py

OHLCV Scraper — mengambil data candlestick dari Binance Futures dan menyimpannya ke PostgreSQL.

Scraping:
  - Top 30 koin Futures berdasarkan volume tertinggi
  - 4 Timeframe: 1d (Daily), 1w (Weekly), 1h (Hourly), 5m (5 Menit)
  - 500 candle historis per symbol per timeframe

Schedule (via asyncio loop):
  - 1h & 5m : setiap 5 menit
  - 1d & 1w : setiap 6 jam
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from binance import AsyncClient
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

async def _get_top_symbols(client: AsyncClient) -> list[str]:
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
        tickers = await client.futures_ticker()
        usdt_pairs = [
            t for t in tickers
            if t["symbol"].endswith("USDT") and not t["symbol"].startswith("1000")
        ]
        # Urutkan berdasarkan quoteVolume turun
        usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        _top_symbols = [t["symbol"] for t in usdt_pairs[:TOP_N_COINS]]
        _last_symbol_refresh = now
        logger.info(f"[OHLCV] Top {TOP_N_COINS} symbols diperbarui: {_top_symbols[:5]}...")
    except Exception as exc:
        logger.error(f"[OHLCV] Gagal ambil top symbols: {exc}")
        if not _top_symbols:
            # Fallback ke daftar statis major coins
            _top_symbols = [
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
            ]
    return _top_symbols


def _parse_klines(raw_klines: list) -> list[dict]:
    """
    Konversi response klines Binance ke list dict standar.
    Format Binance: [open_time, open, high, low, close, volume, close_time, ...]
    """
    candles = []
    for k in raw_klines:
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
        except (IndexError, ValueError, TypeError):
            continue
    return candles


async def _scrape_one_symbol(
    client: AsyncClient,
    symbol: str,
    timeframe: str,
) -> int:
    """Scrape satu symbol untuk satu timeframe. Return jumlah candle yang disimpan."""
    try:
        raw = await client.futures_klines(
            symbol=symbol,
            interval=timeframe,
            limit=CANDLE_LIMIT,
        )
        candles = _parse_klines(raw)
        saved = await upsert_candles(symbol, timeframe, candles)
        return saved
    except Exception as exc:
        logger.warning(f"[OHLCV] {symbol} {timeframe}: {exc}")
        return 0


async def scrape_all_timeframes(client: AsyncClient) -> dict[str, int]:
    """
    Scrape semua Top N symbols untuk semua 4 timeframe.
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

async def run_short_term_scraper(client: AsyncClient) -> None:
    """
    Background task untuk timeframe pendek (5m, 1h).
    Berjalan setiap 5 menit.
    """
    short_tfs = [tf for tf, interval in TIMEFRAME_SCHEDULE if interval == 5 * 60]
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
        await asyncio.sleep(5 * 60)  # Tunggu 5 menit


async def run_long_term_scraper(client: AsyncClient) -> None:
    """
    Background task untuk timeframe panjang (1d, 1w).
    Berjalan setiap 6 jam. Jalankan sekali langsung saat startup.
    """
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
        await asyncio.sleep(6 * 60 * 60)  # Tunggu 6 jam


async def run_initial_scrape(client: AsyncClient) -> None:
    """
    Scrape pertama kali saat startup — ambil semua data historis (500 candle × 4 TF × 30 koin).
    Berjalan di background, tidak memblokir startup bot.
    """
    try:
        logger.info("[OHLCV] Initial scrape dimulai (500 candle × 4 TF × Top 30 coins)...")
        print("[OHLCV] 📊 Mengambil data historis candle... (background)")
        summary = await scrape_all_timeframes(client)
        total = sum(summary.values())
        print(f"[OHLCV] ✅ Initial scrape selesai: {total} candles disimpan → {summary}")
        logger.info(f"[OHLCV] Initial scrape selesai: {summary}")
    except Exception as exc:
        logger.error(f"[OHLCV] Initial scrape gagal: {exc}")
