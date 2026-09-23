"""
dashboard/app.py

Web Server & REST API untuk Dashboard Analitika Trading Bot Crypto.
Mendukung isolasi data Multi-Exchange (Binance Demo, Bitunix Simulation, Bitunix Real).
Dibangun menggunakan aiohttp.web (asynchronous, cepat, tanpa dependency tambahan berat).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional
from aiohttp import web

from database.connection import get_pool, is_db_available
from database.trade_repo import (
    get_trade_summary,
    get_recent_trades,
    get_available_exchanges,
    get_pnl_growth_curve,
    get_monthly_trade_stats,
    get_daily_trade_stats,
)
from database.pattern_repo import get_all_patterns, get_top_patterns
from database.ohlcv_repo import get_candles, get_available_symbols, get_ohlcv_stats
from core.exchanges.base import BaseExchange
from telegram.bot_handler import bot_state
from config.settings import bot_config

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")


# ─── API Routes ──────────────────────────────────────────────────────────────

async def api_overview(request: web.Request) -> web.Response:
    """Mengembalikan ringkasan statistik performa bot lengkap dengan filter exchange."""
    exchange_param = request.query.get("exchange", "ALL")
    if exchange_param.upper() in ("ALL", "*", ""):
        filter_ex = None
    else:
        filter_ex = exchange_param.upper()

    summary = await get_trade_summary(exchange=filter_ex)
    available_exchanges = await get_available_exchanges()
    pnl_curve = await get_pnl_growth_curve(limit=150, exchange=filter_ex)
    
    # Ambil virtual log count
    virtual_count = 0
    if os.path.exists("virtual_success_log.csv"):
        try:
            with open("virtual_success_log.csv", "r", encoding="utf-8") as f:
                virtual_count = max(0, sum(1 for _ in f) - 1)
        except Exception:
            pass

    active_positions = []
    total_floating_pnl = 0.0
    client = bot_state.get("client")
    active_meta = bot_state.get("active_trade_meta", {})

    # 1. Masukkan posisi virtual / paper trading yang sedang aktif
    for sym, meta in active_meta.items():
        if meta.get("is_paper"):
            entry = float(meta.get("entry_price", 0.0))
            amt = float(meta.get("quantity", 0.0))
            side = meta.get("side", "LONG").upper()
            mfe_val = float(meta.get("mfe", 0.0) or 0.0)
            mae_val = float(meta.get("mae", 0.0) or 0.0)
            lev = int(meta.get("leverage", 10))
            m_usdt = float(meta.get("margin_usdt", 0.0))
            entry_time = meta.get("entry_time")
            duration_min = round((datetime.now() - entry_time).total_seconds() / 60, 1) if entry_time else 0
            
            pnl = mfe_val if mfe_val != 0 else mae_val
            pnl_pct = (pnl / m_usdt * 100) if m_usdt > 0 else 0.0
            total_floating_pnl += pnl

            active_positions.append({
                "symbol": sym,
                "side": side,
                "amount": abs(amt),
                "entry_price": entry,
                "mark_price": entry,
                "margin_usdt": round(m_usdt, 4),
                "leverage": lev,
                "unrealized_pnl": round(pnl, 4),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "mfe": round(mfe_val, 4),
                "mae": round(mae_val, 4),
                "duration_minutes": duration_min,
                "exchange": meta.get("exchange", "BITUNIX_SIM"),
                "alasan": meta.get("alasan", "Paper Trading Simulation"),
            })

    # 2. Masukkan posisi real dari exchange adapter
    if client:
        try:
            if hasattr(client, "get_open_positions"):
                positions = await client.get_open_positions()
                for p in positions:
                    amt = float(p.get("position_amt", 0))
                    if amt == 0:
                        continue
                    sym = p.get("symbol")
                    pnl = float(p.get("unrealized_pnl", 0))
                    total_floating_pnl += pnl
                    entry = float(p.get("entry_price", 0))
                    mark = float(p.get("mark_price", entry))
                    meta = active_meta.get(sym, {})
                    entry_time = meta.get("entry_time")
                    duration_min = round((datetime.now() - entry_time).total_seconds() / 60, 1) if entry_time else 0
                    leverage = float(p.get("leverage", 0) or meta.get("leverage", 10))
                    init_margin = abs(amt) * entry / leverage if leverage > 0 else 0
                    pnl_pct = (pnl / init_margin * 100) if init_margin > 0 else 0

                    mfe_val = max(float(meta.get("mfe", 0) or 0), pnl)
                    mae_val = min(float(meta.get("mae", 0) or 0), pnl)
                    if meta:
                        meta["mfe"] = mfe_val
                        meta["mae"] = mae_val

                    active_positions.append({
                        "symbol": sym,
                        "side": p.get("side", "LONG"),
                        "amount": abs(amt),
                        "entry_price": entry,
                        "mark_price": mark,
                        "margin_usdt": round(init_margin, 4),
                        "leverage": int(leverage),
                        "unrealized_pnl": round(pnl, 4),
                        "unrealized_pnl_pct": round(pnl_pct, 2),
                        "mfe": round(mfe_val, 4),
                        "mae": round(mae_val, 4),
                        "duration_minutes": duration_min,
                        "exchange": getattr(client, "exchange_name", "EXCHANGE"),
                        "alasan": meta.get("alasan", "Sinyal Multi-Indikator AI"),
                    })
            elif hasattr(client, "futures_account"):
                account_info = await client.futures_account()
                for p in account_info.get("positions", []):
                    amt = float(p.get("positionAmt", 0))
                    if amt == 0:
                        continue
                    sym = p.get("symbol")
                    pnl = float(p.get("unrealizedProfit", 0))
                    total_floating_pnl += pnl
                    entry = float(p.get("entryPrice", 0))
                    mark = entry + (pnl / amt) if amt != 0 and entry > 0 else entry
                    meta = active_meta.get(sym, {})
                    entry_time = meta.get("entry_time")
                    duration_min = round((datetime.now() - entry_time).total_seconds() / 60, 1) if entry_time else 0
                    leverage = float(p.get("leverage", 0) or meta.get("leverage", 10))
                    init_margin = abs(amt) * entry / leverage if leverage > 0 else 0
                    pnl_pct = (pnl / init_margin * 100) if init_margin > 0 else 0

                    mfe_val = max(float(meta.get("mfe", 0) or 0), pnl)
                    mae_val = min(float(meta.get("mae", 0) or 0), pnl)
                    if meta:
                        meta["mfe"] = mfe_val
                        meta["mae"] = mae_val

                    active_positions.append({
                        "symbol": sym,
                        "side": "LONG" if amt > 0 else "SHORT",
                        "amount": abs(amt),
                        "entry_price": entry,
                        "mark_price": mark,
                        "margin_usdt": round(init_margin, 4),
                        "leverage": int(leverage),
                        "unrealized_pnl": round(pnl, 4),
                        "unrealized_pnl_pct": round(pnl_pct, 2),
                        "mfe": round(mfe_val, 4),
                        "mae": round(mae_val, 4),
                        "duration_minutes": duration_min,
                        "exchange": "BINANCE",
                        "alasan": meta.get("alasan", "Sinyal Multi-Indikator AI"),
                    })
        except Exception as e_pos:
            logger.warning(f"[DASHBOARD] Gagal fetch live active positions: {e_pos}")

    # Breakdown Strategi Aktif & AI Learning Stats
    strategies = []
    if os.path.exists("data/pattern_stats.json"):
        try:
            with open("data/pattern_stats.json", "r", encoding="utf-8") as f:
                stats = json.load(f)
                for name, s in stats.items():
                    tot = s.get("total", 0)
                    if tot > 0:
                        w = s.get("win", 0)
                        l = s.get("loss", 0)
                        wr = round((w / tot) * 100, 1)
                        status = "🟢 TIER-A (HIGH WR)" if wr >= 60 else ("🟡 NORMAL" if wr >= 45 else "🔴 BLACKLISTED")
                        strategies.append({
                            "name": name,
                            "total": tot,
                            "wins": w,
                            "losses": l,
                            "win_rate": wr,
                            "status": status,
                        })
        except Exception as e_st:
            logger.warning(f"[DASHBOARD] Gagal load pattern_stats: {e_st}")
    strategies.sort(key=lambda x: (x["win_rate"], x["total"]), reverse=True)

    # Ambil statistik historis Bulanan & Harian
    monthly_stats = await get_monthly_trade_stats(exchange=filter_ex)
    daily_stats = await get_daily_trade_stats(days=14, exchange=filter_ex)

    # AI Market Intelligence & Radar
    default_intel = {
        "btc_price": 86377.90,
        "btc_trend_1h": "UPTREND",
        "btc_rsi_1h": 64.96,
        "btc_rsi_5m": 65.55,
        "market_regime": "BULLISH_OVERBOUGHT",
        "regime_title": "BULLISH EXPANSION (Jenuh Beli Lokal)",
        "recommendation": "Mayoritas altcoin (77%) menempel di Upper Bollinger Band dengan RSI > 65. Bot saat ini bersiaga menunggu konfirmasi koreksi/pullback ke Support & Lower BB agar tidak terkena Bull Trap.",
        "risk_level": "MODERATE - CAUTION",
        "action_plan": "Fokus pada konfluensi koin yang retest support atau membentuk reversal Tier-A (Hammer, Morning Star).",
        "scanned_stats": {
            "total_analyzed": 80,
            "uptrend_count": 57,
            "downtrend_count": 21,
            "sideways_count": 2,
            "upper_bb_pct": 77.5,
            "oversold_count": 4,
            "overbought_count": 34,
            "patterns_detected": 32,
        }
    }
    market_intel = bot_state.get("market_intel", default_intel)

    data = {
        "status": "online",
        "bot_state": bot_state.get("state", "RUNNING"),
        "active_exchange": getattr(bot_config, "active_exchange", "BINANCE"),
        "selected_exchange": exchange_param.upper(),
        "available_exchanges": available_exchanges,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_summary": summary or {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "daily_net_pnl": 0.0, "profit_factor": 0.0,
            "daily_profit_factor": 0.0, "daily_win_rate": 0.0
        },
        "floating_pnl": round(total_floating_pnl, 4),
        "virtual_signals_count": virtual_count,
        "simulated_modal": float(bot_config.simulated_modal or 100.0),
        "trading_mode": getattr(bot_config, "trading_mode", "PAPER_TRADING"),
        "active_positions": active_positions,
        "strategies": strategies,
        "pnl_curve": pnl_curve,
        "monthly_stats": monthly_stats,
        "daily_stats": daily_stats,
        "market_intel": market_intel,
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
    """Mengembalikan riwayat transaksi real dan virtual dengan filter exchange."""
    limit = int(request.query.get("limit", 50))
    exchange_param = request.query.get("exchange", "ALL")
    filter_ex = None if exchange_param.upper() in ("ALL", "*", "") else exchange_param.upper()

    real_trades = await get_recent_trades(limit=limit, exchange=filter_ex)

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
        "exchange_filter": exchange_param.upper(),
        "real_trades": real_trades,
        "virtual_trades": list(reversed(virtual_trades[-50:])),
    })


async def api_pnl_chart(request: web.Request) -> web.Response:
    """Mengembalikan data deret waktu kurva PnL kumulatif."""
    exchange_param = request.query.get("exchange", "ALL")
    filter_ex = None if exchange_param.upper() in ("ALL", "*", "") else exchange_param.upper()
    curve = await get_pnl_growth_curve(limit=200, exchange=filter_ex)
    return web.json_response({
        "exchange": exchange_param.upper(),
        "data": curve,
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
    app.router.add_get("/api/pnl-chart", api_pnl_chart)
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
