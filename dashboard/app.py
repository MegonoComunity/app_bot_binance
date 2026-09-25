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
    sync_exchange_trades_to_db,
)
from database.pattern_repo import get_all_patterns, get_top_patterns
from database.ohlcv_repo import get_candles, get_available_symbols, get_ohlcv_stats
from core.exchanges.base import BaseExchange
from core.exchanges.binance_adapter import BinanceAdapter
from core.exchanges.bitunix_adapter import BitunixAdapter
from core.confluence_engine import calculate_confluence_score
from core.risk_manager import calculate_volatility_adjusted_leverage
from indicators.market_structure import analyze_market_structure, calculate_dynamic_swing_avwap, detect_ema21_pullback
from indicators.dormant_breakout import calculate_dormant_breakout_score
from indicators.pre_pump_detector import detect_explosive_pre_pump
from indicators.rsi import calculate_rsi
from indicators.bollinger import calculate_bollinger_bands
from indicators.patterns import detect_candlestick_patterns
from indicators.trend import get_htf_trend
from telegram.bot_handler import bot_state
from config.settings import (
    bot_config,
    BINANCE_API_KEY, BINANCE_API_SECRET,
    BITUNIX_API_KEY, BITUNIX_API_SECRET, BITUNIX_UID_USER,
    ACTIVE_EXCHANGE,
)

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
    if client and hasattr(client, "get_history_positions"):
        try:
            await sync_exchange_trades_to_db(client, limit=30)
        except Exception as e_sync:
            logger.debug(f"[DASHBOARD] Sync error in overview: {e_sync}")

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
                    leverage = int(meta.get("leverage") or p.get("leverage") or 20)
                    init_margin = float(meta.get("margin_usdt") or (abs(amt) * entry / leverage if leverage > 0 else 0))
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
        "pre_pump_alerts": bot_state.get("pre_pump_alerts", []),
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

    client = bot_state.get("client")
    if client and hasattr(client, "get_history_positions"):
        try:
            await sync_exchange_trades_to_db(client, limit=limit)
        except Exception as e_sync:
            logger.debug(f"[DASHBOARD] Sync error in trades: {e_sync}")

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


from core.scanner_logger import get_scanner_snapshot, clear_scanner_logs, add_scanner_log


async def api_scanner_logs(request: web.Request) -> web.Response:
    """Mengembalikan live stream log scanner, status progress, dan config scanner terkini."""
    snapshot = get_scanner_snapshot()
    snapshot["trading_mode"] = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    snapshot["active_exchange"] = getattr(bot_config, "active_exchange", "BITUNIX")
    snapshot["min_confluence_score"] = getattr(bot_config, "min_confluence_score", 60.0)
    snapshot["scan_target"] = getattr(bot_config, "scan_target_coins", "ALL")
    snapshot["scan_sort"] = getattr(bot_config, "scan_sort_order", "VOLUME_DESC")
    snapshot["timeframe"] = getattr(bot_config, "timeframe", "5m")
    snapshot["htf_timeframe"] = getattr(bot_config, "htf_timeframe", "1h")
    return web.json_response(snapshot)


async def api_scanner_control(request: web.Request) -> web.Response:
    """
    Endpoint remote control VPS untuk mengontrol bot scanner langsung dari Webbase Dashboard.
    Actions:
    - resume: Mulai / lanjutkan scanning
    - pause: Jeda scanning
    - set_mode: Ganti mode trading (REAL / PAPER_TRADING)
    - set_confluence: Ubah skor minimum konfluensi (misal: 60)
    - set_target: Ubah target universe (ALL, 50, 100, 200)
    - set_sort: Ubah urutan sorting scanner (VOLUME_DESC, CHANGE_DESC, GAINERS, LOSERS)
    - clear_logs: Bersihkan tampilan log console
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    action = data.get("action", "").lower().strip()
    msg = ""
    success = True

    if action == "resume":
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        msg = "Bot Scanner DILANJUTKAN (RUNNING). Scanning koin aktif."
        add_scanner_log("INFO", "SYSTEM", "🟢 [WEB CONTROL] Scanner di-RESUME via Web Dashboard.")
    elif action == "pause":
        bot_state["is_running"] = False
        bot_state["state"] = "PAUSED"
        msg = "Bot Scanner DIJEDA (PAUSED)."
        add_scanner_log("WARN", "SYSTEM", "⏸️ [WEB CONTROL] Scanner di-PAUSE via Web Dashboard.")
    elif action == "set_mode":
        new_mode = str(data.get("mode", "PAPER_TRADING")).upper().strip()
        if new_mode in ("REAL", "PAPER_TRADING", "SIMULATION", "TESTNET"):
            bot_config.trading_mode = new_mode
            msg = f"Trading Mode diubah menjadi: {new_mode}"
            add_scanner_log("INFO", "CONFIG", f"🔧 [WEB CONTROL] Mode Trading diubah ke {new_mode}.")
        else:
            success = False
            msg = f"Mode '{new_mode}' tidak valid."
    elif action == "set_confluence":
        try:
            new_score = float(data.get("score", 60.0))
            if 30.0 <= new_score <= 100.0:
                bot_config.min_confluence_score = new_score
                msg = f"Min Confluence Score diubah menjadi: {new_score}"
                add_scanner_log("INFO", "CONFIG", f"🔧 [WEB CONTROL] Min Confluence Score diset ke {new_score}/100.")
            else:
                success = False
                msg = "Score harus antara 30 dan 100."
        except ValueError:
            success = False
            msg = "Nilai score tidak valid."
    elif action == "set_target":
        new_target = str(data.get("target", "ALL")).strip()
        bot_config.scan_target_coins = new_target
        msg = f"Scan Target diubah menjadi: {new_target}"
        add_scanner_log("INFO", "CONFIG", f"🌐 [WEB CONTROL] Scan Target diset ke {new_target}.")
    elif action == "set_sort":
        new_sort = str(data.get("sort", "VOLUME_DESC")).upper().strip()
        bot_config.scan_sort_order = new_sort
        msg = f"Scan Sort Order diubah menjadi: {new_sort}"
        add_scanner_log("INFO", "CONFIG", f"🌐 [WEB CONTROL] Scan Sort diset ke {new_sort}.")
    elif action == "clear_logs":
        clear_scanner_logs()
        msg = "Log scanner berhasil dibersihkan."
    else:
        success = False
        msg = f"Aksi '{action}' tidak dikenal."

    return web.json_response({
        "success": success,
        "message": msg,
        "bot_state": bot_state.get("state", "RUNNING"),
        "is_running": bot_state.get("is_running", False),
        "trading_mode": getattr(bot_config, "trading_mode", "PAPER_TRADING"),
        "min_confluence_score": getattr(bot_config, "min_confluence_score", 60.0),
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


async def api_analyze_coin(request: web.Request) -> web.Response:
    """
    Full AI & Database Live Coin Analysis.
    Parameter query: ?symbol=BTCUSDT
    """
    symbol = request.query.get("symbol", "BTCUSDT").upper().strip()
    if not symbol:
        symbol = "BTCUSDT"
    if not symbol.endswith("USDT") and not symbol.endswith("BUSD"):
        symbol += "USDT"

    client = bot_state.get("client")
    temp_client = None
    if not client:
        # Fallback adapter jika bot scanner utama belum start
        if ACTIVE_EXCHANGE == "BITUNIX":
            temp_client = BitunixAdapter(BITUNIX_API_KEY, BITUNIX_API_SECRET)
        else:
            temp_client = BinanceAdapter(BINANCE_API_KEY, BINANCE_API_SECRET)
        await temp_client.init()
        active_client = temp_client
    else:
        active_client = client

    try:
        # 1. Fetch live klines (5m dan 1h)
        df_5m = await active_client.fetch_ohlcv(symbol, "5m", limit=100)
        df_1h = await active_client.fetch_ohlcv(symbol, "1h", limit=50)

        # Fallback jika exchange aktif gagal klines
        if (df_5m is None or df_5m.empty) and getattr(active_client, "exchange_name", "") != "BINANCE":
            try:
                binance_fb = BinanceAdapter(BINANCE_API_KEY, BINANCE_API_SECRET)
                await binance_fb.init()
                df_5m = await binance_fb.fetch_ohlcv(symbol, "5m", limit=100)
                df_1h = await binance_fb.fetch_ohlcv(symbol, "1h", limit=50)
                await binance_fb.close()
            except Exception:
                pass

        if df_5m is None or df_5m.empty:
            return web.json_response({
                "success": False,
                "error": f"Gagal mengambil data candle untuk symbol {symbol}. Pastikan symbol futures valid (contoh: BTCUSDT, LTCUSDT, SOLUSDT)."
            }, status=400)

        # Hitung indikator 5M
        df_5m = calculate_bollinger_bands(df_5m)
        df_5m = calculate_rsi(df_5m, length=14)

        # Hitung indikator 1H
        if df_1h is not None and not df_1h.empty:
            df_1h = calculate_rsi(df_1h, length=14)
            htf_trend = get_htf_trend(df_1h)
            rsi_1h = round(float(df_1h.iloc[-1].get("RSI", 50.0)), 2)
        else:
            htf_trend = "SIDEWAYS"
            rsi_1h = 50.0

        last_5m = df_5m.iloc[-1]
        curr_price = float(last_5m["close"])
        rsi_5m = round(float(last_5m.get("RSI", 50.0)), 2)

        upper_bb = float(last_5m.get("upper_band", last_5m.get("bb_upper", curr_price * 1.02)))
        lower_bb = float(last_5m.get("lower_band", last_5m.get("bb_lower", curr_price * 0.98)))
        mid_bb = float(last_5m.get("middle_band", last_5m.get("bb_middle", curr_price)))

        bb_zone = "LOWER" if curr_price <= lower_bb * 1.005 else ("UPPER" if curr_price >= upper_bb * 0.995 else "MID")

        # ATR & Volatilitas
        high_low = df_5m["high"] - df_5m["low"]
        atr_val = float(high_low.rolling(14).mean().iloc[-1]) if len(high_low) >= 14 else 0.0
        atr_pct = round((atr_val / curr_price * 100.0), 2) if curr_price > 0 else 0.0

        # Volume RVOL
        vol_sma = df_5m["volume"].rolling(20).mean().iloc[-1] if len(df_5m) >= 20 else df_5m["volume"].mean()
        curr_vol = float(df_5m["volume"].iloc[-1])
        rvol = round(float(curr_vol / vol_sma), 2) if vol_sma > 0 else 1.0

        # Pola Candlestick & Squeeze
        breakout = calculate_dormant_breakout_score(df_5m)
        squeeze_score = round(float(breakout.get("score", 0.0)), 1)
        pump_intel = detect_explosive_pre_pump(df_5m, symbol=symbol)
        pattern_info = detect_candlestick_patterns(df_5m)

        pattern_name = pattern_info.get("pattern", "NONE")
        pattern_type = pattern_info.get("type", "NEUTRAL")

        # SMC Structure & AVWAP
        smc_res = analyze_market_structure(df_5m)
        smc_regime = smc_res.get("regime", "SIDEWAYS")
        avwap_res = calculate_dynamic_swing_avwap(df_5m)
        ema_res = detect_ema21_pullback(df_5m)

        # Confluence Scores
        conf_long = calculate_confluence_score(
            df_5m=df_5m,
            df_htf=df_1h,
            side="LONG",
            pattern_name=pattern_name,
            pattern_type=pattern_type,
            near_lower_bb=(curr_price <= lower_bb * 1.005),
            near_upper_bb=(curr_price >= upper_bb * 0.995),
            is_oversold=(rsi_5m <= 35),
            is_overbought=(rsi_5m >= 75),
            vol_ratio=rvol,
            breakout_info=breakout,
            htf_trend=htf_trend,
            pump_info=pump_intel,
        )

        conf_short = calculate_confluence_score(
            df_5m=df_5m,
            df_htf=df_1h,
            side="SHORT",
            pattern_name=pattern_name,
            pattern_type=pattern_type,
            near_lower_bb=(curr_price <= lower_bb * 1.005),
            near_upper_bb=(curr_price >= upper_bb * 0.995),
            is_oversold=(rsi_5m <= 35),
            is_overbought=(rsi_5m >= 75),
            vol_ratio=rvol,
            breakout_info=breakout,
            htf_trend=htf_trend,
            pump_info=pump_intel,
        )

        score_long = round(float(conf_long.get("total_score", 0.0)), 1)
        score_short = round(float(conf_short.get("total_score", 0.0)), 1)

        dynamic_lev = calculate_volatility_adjusted_leverage(atr_val, curr_price, base_leverage=int(getattr(bot_config, "leverage", 20) or 20))

        # Keputusan AI
        if score_long >= 70.0 and score_long >= score_short:
            verdict = "STRONG_BUY_LONG"
            verdict_label = "🟢 STRONG BUY / LONG"
            verdict_side = "LONG"
            active_conf = conf_long
            best_score = score_long
        elif score_short >= 70.0 and score_short > score_long:
            verdict = "STRONG_SELL_SHORT"
            verdict_label = "🔴 STRONG SELL / SHORT"
            verdict_side = "SHORT"
            active_conf = conf_short
            best_score = score_short
        elif score_long >= 55.0 and score_long >= score_short:
            verdict = "MODERATE_LONG"
            verdict_label = "🟡 POTENTIAL LONG (WATCH PULLBACK)"
            verdict_side = "LONG"
            active_conf = conf_long
            best_score = score_long
        elif score_short >= 55.0:
            verdict = "MODERATE_SHORT"
            verdict_label = "🟡 POTENTIAL SHORT (WATCH RESISTANCE)"
            verdict_side = "SHORT"
            active_conf = conf_short
            best_score = score_short
        else:
            verdict = "NEUTRAL_WAIT"
            verdict_label = "⚪ NEUTRAL / WAIT CONFIRMATION"
            verdict_side = "WAIT"
            active_conf = conf_long if score_long >= score_short else conf_short
            best_score = max(score_long, score_short)

        # Target TP / SL
        tp_pct = float(getattr(bot_config, "tp_percent", 45.0) or 45.0)
        sl_pct = float(getattr(bot_config, "sl_percent", 25.0) or 25.0)
        tp_dist = (tp_pct / 100.0) / dynamic_lev
        sl_dist = (sl_pct / 100.0) / dynamic_lev

        if verdict_side == "SHORT":
            tp1_p = round(curr_price * (1.0 - tp_dist), 6)
            tp2_p = round(curr_price * (1.0 - (tp_dist * 1.5)), 6)
            sl_p = round(curr_price * (1.0 + sl_dist), 6)
        else:
            tp1_p = round(curr_price * (1.0 + tp_dist), 6)
            tp2_p = round(curr_price * (1.0 + (tp_dist * 1.5)), 6)
            sl_p = round(curr_price * (1.0 - sl_dist), 6)

        # 2. Query Memory PostgreSQL Database
        db_memory = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "recent_entries": []
        }
        pool = await get_pool()
        if pool:
            try:
                async with pool.acquire() as conn:
                    tot = await conn.fetchval("SELECT count(*) FROM pattern_entries WHERE symbol = $1", symbol) or 0
                    w = await conn.fetchval("SELECT count(*) FROM pattern_entries WHERE symbol = $1 AND result = 'WIN'", symbol) or 0
                    l = await conn.fetchval("SELECT count(*) FROM pattern_entries WHERE symbol = $1 AND result = 'LOSS'", symbol) or 0
                    wr = round((w / tot * 100.0), 1) if tot > 0 else 0.0

                    recent_rows = await conn.fetch(
                        "SELECT entry_id, side, entry_price, result, pnl, alasan, entered_at FROM pattern_entries WHERE symbol = $1 ORDER BY id DESC LIMIT 5",
                        symbol
                    )
                    recent_list = []
                    for r in recent_rows:
                        recent_list.append({
                            "entry_id": r["entry_id"],
                            "side": r["side"],
                            "entry_price": float(r["entry_price"] or 0),
                            "result": r["result"] or "OPEN / PENDING",
                            "pnl": round(float(r["pnl"] or 0), 2) if r["pnl"] is not None else "-",
                            "alasan": r["alasan"] or "-",
                            "time": r["entered_at"].strftime("%Y-%m-%d %H:%M:%S") if r.get("entered_at") else "-"
                        })
                    db_memory = {
                        "total_trades": tot,
                        "wins": w,
                        "losses": l,
                        "win_rate": wr,
                        "recent_entries": recent_list
                    }
            except Exception as e_db:
                logger.warning(f"[ANALYZE COIN] DB memory query error: {e_db}")

        return web.json_response({
            "success": True,
            "symbol": symbol,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB"),
            "current_price": curr_price,
            "verdict": {
                "code": verdict,
                "label": verdict_label,
                "side": verdict_side,
                "confluence_score": best_score,
                "score_long": score_long,
                "score_short": score_short,
            },
            "setup": {
                "entry_price": curr_price,
                "tp1_price": tp1_p,
                "tp2_price": tp2_p,
                "sl_price": sl_p,
                "tp_percent": tp_pct,
                "sl_percent": sl_pct,
                "recommended_leverage": dynamic_lev,
                "recommended_margin": float(getattr(bot_config, "margin_usdt", 1.0) or 1.0),
                "risk_reward_ratio": f"1 : {round(tp_pct / sl_pct, 2)}",
            },
            "pillars": {
                "htf_trend": htf_trend,
                "smc_market_structure": smc_regime,
                "rsi_5m": rsi_5m,
                "rsi_1h": rsi_1h,
                "bb_zone": bb_zone,
                "bb_upper": round(upper_bb, 6),
                "bb_middle": round(mid_bb, 6),
                "bb_lower": round(lower_bb, 6),
                "atr_percent": atr_pct,
                "rvol": rvol,
                "squeeze_score": squeeze_score,
                "candlestick_pattern": pattern_name,
                "pattern_type": pattern_type,
                "pre_pump_tier": pump_intel.get("tier", "NONE"),
                "pre_pump_score": round(float(pump_intel.get("score", 0)), 1),
                "avwap_fair_value": round(float(avwap_res.get("avwap", curr_price)), 6),
                "is_ema21_pullback": bool(ema_res.get("is_pullback", False)),
            },
            "confluence_breakdown": active_conf.get("breakdown", {}),
            "db_memory": db_memory,
        })
    except Exception as exc:
        logger.error(f"[ANALYZE COIN] Gagal analisis {symbol}: {exc}", exc_info=True)
        return web.json_response({"success": False, "error": f"Error menganalisis {symbol}: {str(exc)}"}, status=500)
    finally:
        if temp_client:
            await temp_client.close()


def create_dashboard_app() -> web.Application:
    """Factory untuk instance aiohttp web application."""
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/patterns", api_patterns)
    app.router.add_get("/api/symbols", api_symbols)
    app.router.add_get("/api/candles/{symbol}", api_candles)
    app.router.add_get("/api/analyze-coin", api_analyze_coin)
    app.router.add_get("/api/trades", api_trades)
    app.router.add_get("/api/pnl-chart", api_pnl_chart)
    app.router.add_get("/api/scanner/logs", api_scanner_logs)
    app.router.add_post("/api/scanner/control", api_scanner_control)
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
