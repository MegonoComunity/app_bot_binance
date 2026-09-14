"""
dashboard/app.py

Web Server & REST API untuk Dashboard Analitika Trading Bot Crypto.
Dibangun menggunakan aiohttp.web (asynchronous, cepat, tanpa dependency tambahan berat).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from aiohttp import web

from database.connection import get_pool, is_db_available
from database.trade_repo import get_trade_summary, get_recent_trades
from database.pattern_repo import get_all_patterns, get_top_patterns
from database.ohlcv_repo import get_candles, get_available_symbols, get_ohlcv_stats
from telegram.bot_handler import bot_state

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")


# ─── API Routes ──────────────────────────────────────────────────────────────

async def api_overview(request: web.Request) -> web.Response:
    """Mengembalikan ringkasan statistik performa bot."""
    summary = await get_trade_summary()
    
    # Ambil virtual log count
    virtual_count = 0
    if os.path.exists("virtual_success_log.csv"):
        try:
            with open("virtual_success_log.csv", "r", encoding="utf-8") as f:
                virtual_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            pass

    active_positions = []
    # Jika bot_state memiliki active meta
    active_meta = bot_state.get("active_trade_meta", {})
    for sym, meta in active_meta.items():
        entry_time = meta.get("entry_time")
        duration_min = round((datetime.now() - entry_time).total_seconds() / 60, 1) if entry_time else 0
        active_positions.append({
            "symbol": sym,
            "side": meta.get("side", "LONG"),
            "entry_price": meta.get("entry_price", 0),
            "mfe": meta.get("mfe", 0),
            "mae": meta.get("mae", 0),
            "duration_minutes": duration_min,
        })

    data = {
        "status": "online",
        "bot_state": bot_state.get("state", "RUNNING"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_summary": summary or {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "daily_net_pnl": 0.0
        },
        "virtual_signals_count": virtual_count,
        "active_positions": active_positions,
    }
    return web.json_response(data)


async def api_patterns(request: web.Request) -> web.Response:
    """Mengembalikan daftar semua pola pembelajaran AI."""
    patterns = await get_all_patterns(limit=150)
    return web.json_response({"patterns": patterns, "total": len(patterns)})


async def api_symbols(request: web.Request) -> web.Response:
    """Mengembalikan daftar symbol koin yang tersedia di database."""
    tf = request.query.get("tf", "1d")
    symbols = await get_available_symbols(timeframe=tf)
    if not symbols:
        # Fallback list jika DB belum di-scrape penuh
        symbols = [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "VVVUSDT", "XANUSDT", "PLUMEUSDT"
        ]
    return web.json_response({"symbols": symbols})


async def api_candles(request: web.Request) -> web.Response:
    """
    Mengambil data candlestick OHLCV untuk visualisasi chart.
    Query params: ?symbol=BTCUSDT&tf=1d&limit=500
    """
    symbol = request.match_info.get("symbol", "BTCUSDT").upper()
    tf = request.query.get("tf", "1d")
    limit = int(request.query.get("limit", 500))

    candles = await get_candles(symbol=symbol, timeframe=tf, limit=limit)
    
    # Format untuk TradingView Lightweight Charts (urutan waktu menaik: ASC)
    # Lightweight Charts butuh waktu dalam timestamp detik integer atau format YYYY-MM-DD
    formatted = []
    for c in reversed(candles):
        try:
            ot_dt = datetime.fromisoformat(c["open_time"])
            time_val = int(ot_dt.timestamp())
        except Exception:
            time_val = c["open_time"]
            
        formatted.append({
            "time": time_val,
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
        })

    return web.json_response({
        "symbol": symbol,
        "timeframe": tf,
        "count": len(formatted),
        "candles": formatted
    })


async def api_trades(request: web.Request) -> web.Response:
    """Mengembalikan riwayat transaksi real dan virtual."""
    limit = int(request.query.get("limit", 50))
    real_trades = await get_recent_trades(limit=limit)

    # Virtual trades dari CSV
    virtual_trades = []
    if os.path.exists("virtual_success_log.csv"):
        try:
            import csv
            with open("virtual_success_log.csv", "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    virtual_trades.append(row)
        except Exception:
            pass

    return web.json_response({
        "real_trades": real_trades,
        "virtual_trades": list(reversed(virtual_trades[-50:])),
    })


# ─── HTML Page ───────────────────────────────────────────────────────────────

async def index_handler(request: web.Request) -> web.Response:
    """Serve Single Page Application Dashboard HTML."""
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.exists(html_path):
        return web.Response(text="Dashboard template not found.", status=404)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(text=content, content_type="text/html")


def create_dashboard_app() -> web.Application:
    """Factory untuk instance aiohttp web application."""
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/patterns", api_patterns)
    app.router.add_get("/api/symbols", api_symbols)
    app.router.add_get("/api/candles/{symbol}", api_candles)
    app.router.add_get("/api/trades", api_trades)
    return app


async def start_dashboard_server(host: str = "0.0.0.0", port: int = 8000) -> web.AppRunner:
    """Menjalankan web server dashboard secara asynchronous di event loop bot."""
    app = create_dashboard_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\n=======================================================")
    print(f"🚀 DASHBOARD ANALITIKA AKTIF DI: http://localhost:{port}")
    print(f"=======================================================\n")
    return runner


if __name__ == "__main__":
    app = create_dashboard_app()
    web.run_app(app, host="127.0.0.1", port=8000)
