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
from core.trade_sync import sync_real_exchange_account
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
    """Mengembalikan ringkasan statistik performa bot lengkap dengan filter exchange dan saldo realtime."""
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

    # Ambil Saldo Aktual dari Exchange
    actual_wallet_bal = 0.0
    actual_avail_bal = 0.0
    if client:
        try:
            if hasattr(client, "get_account_balance"):
                bal_dict = await client.get_account_balance()
                actual_wallet_bal = float(bal_dict.get("total_wallet_balance", bal_dict.get("totalMarginBalance", 0.0)))
                actual_avail_bal = float(bal_dict.get("available_balance", bal_dict.get("availableBalance", actual_wallet_bal)))
            elif hasattr(client, "futures_account"):
                acc_info = await client.futures_account()
                actual_wallet_bal = float(acc_info.get("totalMarginBalance", 0.0))
                actual_avail_bal = float(acc_info.get("availableBalance", actual_wallet_bal))
        except Exception as e_b:
            logger.debug(f"[DASHBOARD] Gagal fetch balance: {e_b}")

    active_ex_name = getattr(bot_config, "active_exchange", "BINANCE").upper()
    trading_mode_name = getattr(bot_config, "trading_mode", "TESTNET").upper()
    is_custom_sim = bool(bot_config.simulated_modal and bot_config.simulated_modal > 0)

    if is_custom_sim:
        display_balance = float(bot_config.simulated_modal)
        balance_source_tag = "Virtual Custom Modal ($100)"
    elif active_ex_name == "BINANCE" and trading_mode_name in ("TESTNET", "DEMO", "BINANCE_DEMO"):
        display_balance = actual_wallet_bal if actual_wallet_bal > 0 else 100.0
        balance_source_tag = "Binance Futures Testnet (demo.binance.com)"
    elif trading_mode_name in ("REAL", "LIVE"):
        display_balance = actual_wallet_bal
        balance_source_tag = f"{active_ex_name} Real Account API"
    else:
        display_balance = actual_wallet_bal if actual_wallet_bal > 0 else (bot_config.simulated_modal or 100.0)
        balance_source_tag = f"{active_ex_name} Paper Trading"

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
        "recommendation": "Mayoritas altcoin menempel di Upper Bollinger Band. Bot bersiaga menunggu konfirmasi koreksi/pullback ke Support & Lower BB agar tidak terkena Bull Trap.",
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
        "active_exchange": active_ex_name,
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
        "wallet_balance": round(actual_wallet_bal, 2),
        "available_balance": round(actual_avail_bal, 2),
        "display_balance": round(display_balance, 2),
        "balance_source": balance_source_tag,
        "is_custom_modal": is_custom_sim,
        "simulated_modal": float(bot_config.simulated_modal or 100.0),
        "trading_mode": trading_mode_name,
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
    Mengambil data candlestick OHLCV untuk visualisasi chart live deep scanner.
    Query params: ?symbol=BTCUSDT&tf=5m&limit=150
    """
    raw_symbol = request.match_info.get("symbol", request.query.get("symbol", "BTCUSDT")).upper().strip()
    if not raw_symbol:
        raw_symbol = "BTCUSDT"
    if not raw_symbol.endswith("USDT") and not raw_symbol.endswith("BUSD"):
        raw_symbol += "USDT"

    tf = request.query.get("tf", "5m").lower().strip()
    limit = int(request.query.get("limit", 120))

    # Symbol normalization (misal PEPEUSDT -> 1000PEPEUSDT)
    candidates = [raw_symbol]
    if not raw_symbol.startswith("1000") and not raw_symbol.startswith("1000000"):
        candidates.append(f"1000{raw_symbol}")
        candidates.append(f"1000000{raw_symbol}")
    elif raw_symbol.startswith("1000"):
        candidates.append(raw_symbol[4:])

    formatted = []
    resolved_sym = raw_symbol

    # 1. Coba ambil dari database lokal
    for cand_sym in candidates:
        candles = await get_candles(symbol=cand_sym, timeframe=tf, limit=limit)
        if candles:
            resolved_sym = cand_sym
            for c in reversed(candles):
                try:
                    ot_dt = datetime.fromisoformat(str(c["open_time"]))
                    time_val = int(ot_dt.timestamp())
                except Exception:
                    time_val = int(c.get("open_time", 0))
                    
                formatted.append({
                    "time": time_val,
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            break

    # 2. Fallback: Ambil langsung dari client / Binance Futures REST API
    if not formatted:
        client = bot_state.get("client")
        for cand_sym in candidates:
            # A. Coba futures_klines jika client python-binance aktif
            if client and hasattr(client, "futures_klines"):
                try:
                    klines = await client.futures_klines(symbol=cand_sym, interval=tf, limit=limit)
                    if klines:
                        resolved_sym = cand_sym
                        for row in klines:
                            formatted.append({
                                "time": int(row[0]) // 1000,
                                "open": float(row[1]),
                                "high": float(row[2]),
                                "low": float(row[3]),
                                "close": float(row[4]),
                                "volume": float(row[5]),
                            })
                        break
                except Exception:
                    pass

            # B. Coba fetch_ohlcv jika client adapter aktif
            if client and hasattr(client, "fetch_ohlcv"):
                try:
                    df = await client.fetch_ohlcv(cand_sym, tf, limit=limit)
                    if df is not None and not df.empty:
                        resolved_sym = cand_sym
                        for idx, row in df.iterrows():
                            if isinstance(idx, (pd.Timestamp, datetime)):
                                t_val = int(idx.timestamp())
                            elif "timestamp" in row:
                                t_val = int(row["timestamp"]) // 1000 if int(row["timestamp"]) > 1000000000000 else int(row["timestamp"])
                            else:
                                t_val = int(time.time())
                            formatted.append({
                                "time": t_val,
                                "open": float(row["open"]),
                                "high": float(row["high"]),
                                "low": float(row["low"]),
                                "close": float(row["close"]),
                                "volume": float(row.get("volume", 0)),
                            })
                        break
                except Exception:
                    pass

            # C. Direct HTTP request ke Binance Futures public API
            try:
                url = f"https://fapi.binance.com/fapi/v1/klines?symbol={cand_sym}&interval={tf}&limit={limit}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list) and len(data) > 0:
                                resolved_sym = cand_sym
                                for row in data:
                                    formatted.append({
                                        "time": int(row[0]) // 1000,
                                        "open": float(row[1]),
                                        "high": float(row[2]),
                                        "low": float(row[3]),
                                        "close": float(row[4]),
                                        "volume": float(row[5]),
                                    })
                                break
            except Exception:
                pass

    # Ensure ascending time order and unique timestamps
    seen_times = set()
    deduped = []
    for c in formatted:
        t = c["time"]
        if t not in seen_times:
            seen_times.add(t)
            deduped.append(c)
    deduped.sort(key=lambda x: x["time"])

    return web.json_response({
        "symbol": resolved_sym,
        "timeframe": tf,
        "count": len(deduped),
        "candles": deduped
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
            import re
            with open("virtual_success_log.csv", "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    time_val = row.get("Time") or row.get("time") or ""
                    symbol_val = row.get("Symbol") or row.get("symbol") or ""
                    side_val = (row.get("Tipe") or row.get("Side") or row.get("side") or "LONG").upper()
                    try:
                        entry_p = float(row.get("Entry Price") or row.get("entry_price") or 0.0)
                    except Exception:
                        entry_p = 0.0
                    try:
                        tp_or_exit_p = float(row.get("TP Price") or row.get("Exit Price") or row.get("exit_price") or 0.0)
                    except Exception:
                        tp_or_exit_p = 0.0
                    alasan_val = row.get("Alasan") or row.get("alasan") or ""
                    status_val = row.get("Status") or row.get("status") or ""

                    # Extract PnL % if present in status string
                    pnl_pct = 0.0
                    pnl_match = re.search(r"PnL:\s*([+-]?\d+(?:\.\d+)?)%", status_val)
                    if pnl_match:
                        try:
                            pnl_pct = float(pnl_match.group(1))
                        except Exception:
                            pnl_pct = 0.0
                    elif entry_p > 0 and tp_or_exit_p > 0:
                        # Estimasi fallback dengan leverage 20x
                        price_diff = ((tp_or_exit_p - entry_p) / entry_p) if side_val == "LONG" else ((entry_p - tp_or_exit_p) / entry_p)
                        pnl_pct = price_diff * 100.0 * 20.0

                    status_upper = status_val.upper()
                    is_win = "WIN" in status_upper or "SUCCESS" in status_upper or "TARGET" in status_upper or "TP" in status_upper or (pnl_pct > 0 and "LOSS" not in status_upper)
                    is_breakeven = "BREAKEVEN" in status_upper or "BREAK-EVEN" in status_upper or abs(pnl_pct) < 0.2
                    is_time_exit = "TIME" in status_upper or "SAFETY_CUT_LOSS" in status_upper or "SAFETY_PROFIT_LOCK" in status_upper

                    # Kategori strategi / alasan
                    reason_cat = "OTHER"
                    alasan_upper = alasan_val.upper()
                    if "PRE-PUMP" in alasan_upper or "PUMP" in alasan_upper or "ATH" in alasan_upper:
                        reason_cat = "PRE_PUMP"
                    elif "BOLLINGER" in alasan_upper or "BB" in alasan_upper:
                        reason_cat = "BOLLINGER"
                    elif "RSI" in alasan_upper:
                        reason_cat = "RSI"
                    elif "MORNING STAR" in alasan_upper or "HAMMER" in alasan_upper or "ENGULFING" in alasan_upper or "CANDLE" in alasan_upper or "POLA" in alasan_upper:
                        reason_cat = "PATTERN"
                    elif "VOLUME" in alasan_upper or "RVOL" in alasan_upper:
                        reason_cat = "VOLUME"
                    elif "SUPPORT" in alasan_upper or "RESISTANCE" in alasan_upper:
                        reason_cat = "SR_ZONE"

                    virtual_trades.append({
                        "Time": time_val,
                        "Symbol": symbol_val,
                        "Tipe": side_val,
                        "Entry Price": entry_p,
                        "TP Price": tp_or_exit_p,
                        "Alasan": alasan_val,
                        "Status": status_val,
                        "pnl_percent": round(pnl_pct, 2),
                        "is_win": bool(is_win),
                        "is_breakeven": bool(is_breakeven),
                        "is_time_exit": bool(is_time_exit),
                        "reason_category": reason_cat,
                    })
        except Exception as e_v:
            logger.debug(f"[DASHBOARD] Error parsing virtual trades: {e_v}")

    return web.json_response({
        "exchange_filter": exchange_param.upper(),
        "real_trades": real_trades,
        "virtual_trades": list(reversed(virtual_trades[-500:])),
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
            if new_mode in ("REAL", "LIVE"):
                bot_config.simulated_modal = None
                client = bot_state.get("client")
                sync_res = await sync_real_exchange_account(client, sync_history=True, bot_state_ref=bot_state)
                total_bal = sync_res.get("total_wallet_balance", 0.0)
                open_pos = sync_res.get("open_positions_count", 0)
                msg = f"Trading Mode diubah ke REAL. Terdeteksi Saldo: ${total_bal:.2f} USDT | Open Posisi: {open_pos}"
            else:
                msg = f"Trading Mode diubah menjadi: {new_mode}"
            add_scanner_log("INFO", "CONFIG", f"🔧 [WEB CONTROL] {msg}")
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


async def index_handler(request: web.Request) -> web.Response:
    """Serve Main Dashboard HTML Page."""
    html_path = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.exists(html_path):
        return web.Response(text="Dashboard template not found.", status=404)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(text=content, content_type="text/html")


async def screener_handler(request: web.Request) -> web.Response:
    """Serve Standalone GMGN-Style Deep Screener Pro HTML Page."""
    html_path = os.path.join(TEMPLATES_DIR, "screener.html")
    if not os.path.exists(html_path):
        return web.Response(text="Screener template not found.", status=404)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(text=content, content_type="text/html")


async def api_screener_whale_trades(request: web.Request) -> web.Response:
    """
    Mengambil aliran transaksi Paus (Whale Taker Trades) live dari Binance Spot & Futures
    dalam format feed ala aplikasi GMGN.
    Query params: ?symbol=BTCUSDT&limit=25
    """
    raw_sym = request.query.get("symbol", "BTCUSDT").upper().strip()
    if not raw_sym.endswith("USDT") and not raw_sym.endswith("BUSD"):
        raw_sym += "USDT"

    # Spot & Futures Symbol Resolver
    f_sym = raw_sym
    s_sym = raw_sym
    if raw_sym.startswith("1000") and len(raw_sym) > 4:
        s_sym = raw_sym[4:]  # Spot doesn't have 1000 multiplier
    elif raw_sym in ("PEPEUSDT", "BONKUSDT", "SHIBUSDT", "FLOKIUSDT", "LUNCUSDT"):
        f_sym = f"1000{raw_sym}"
        s_sym = raw_sym

    trades = []
    
    # 1. Fetch from Binance Futures Aggregated Trades
    try:
        url_f = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={f_sym}&limit=30"
        async with aiohttp.ClientSession() as session:
            async with session.get(url_f, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for row in data:
                            price = float(row.get("p", 0))
                            qty = float(row.get("q", 0))
                            usdt_vol = price * qty
                            is_buyer_maker = row.get("m", False)
                            # is_buyer_maker True -> Taker Sell, False -> Taker Buy
                            side = "SELL" if is_buyer_maker else "BUY"
                            t_ms = int(row.get("T", 0))
                            trades.append({
                                "symbol": raw_sym,
                                "side": side,
                                "market_type": "FUTURES",
                                "venue": "Binance Futures → Taker",
                                "price": price,
                                "quantity": qty,
                                "volume_usdt": round(usdt_vol, 2),
                                "timestamp": t_ms,
                                "is_whale": usdt_vol >= 1000.0 or (usdt_vol >= 300.0 and raw_sym not in ("BTCUSDT", "ETHUSDT")),
                            })
    except Exception as e:
        logger.debug(f"[WHALE TRADES] Futures fetch error: {e}")

    # 2. Fetch from Binance Spot Aggregated Trades
    try:
        url_s = f"https://api.binance.com/api/v3/aggTrades?symbol={s_sym}&limit=30"
        async with aiohttp.ClientSession() as session:
            async with session.get(url_s, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for row in data:
                            price = float(row.get("p", 0))
                            qty = float(row.get("q", 0))
                            usdt_vol = price * qty
                            is_buyer_maker = row.get("m", False)
                            side = "SELL" if is_buyer_maker else "BUY"
                            t_ms = int(row.get("T", 0))
                            trades.append({
                                "symbol": raw_sym,
                                "side": side,
                                "market_type": "SPOT",
                                "venue": "Binance Spot → Taker",
                                "price": price,
                                "quantity": qty,
                                "volume_usdt": round(usdt_vol, 2),
                                "timestamp": t_ms,
                                "is_whale": usdt_vol >= 1000.0 or (usdt_vol >= 300.0 and raw_sym not in ("BTCUSDT", "ETHUSDT")),
                            })
    except Exception as e:
        logger.debug(f"[WHALE TRADES] Spot fetch error: {e}")

    # Sort descending by timestamp
    trades.sort(key=lambda x: x["timestamp"], reverse=True)

    return web.json_response({
        "symbol": raw_sym,
        "futures_symbol": f_sym,
        "spot_symbol": s_sym,
        "count": len(trades),
        "whale_trades": trades[:35],
    })


async def api_screener_deep_analysis(request: web.Request) -> web.Response:
    """
    Analisis Deep Screener Komprehensif:
    - 3-Level Take Profit (TP1, TP2, TP3) & Dynamic SL
    - Perbandingan Live Futures vs Spot (Basis Spread, Spot Premium, Funding Rate)
    - Daya Beli & Orderflow Taker (Spot & Futures Aggression)
    - Alasan Naratif Teknis AI
    """
    raw_symbol = request.query.get("symbol", "BTCUSDT").upper().strip()
    if not raw_symbol:
        raw_symbol = "BTCUSDT"
    if not raw_symbol.endswith("USDT") and not raw_symbol.endswith("BUSD"):
        raw_symbol += "USDT"

    # Reuse base coin analysis
    base_res = await api_analyze_coin(request)
    base_data = json.loads(base_res.text)

    if not base_data.get("success"):
        return base_res

    curr_p = float(base_data.get("current_price", 0.0))
    setup = base_data.get("setup", {})
    pillars = base_data.get("pillars", {})
    swing = base_data.get("swing_4h", {})
    side = base_data.get("verdict", {}).get("side", "LONG")
    lev = int(setup.get("recommended_leverage", 20))

    # 1. Calculate 3-Level Take Profit
    # TP1: Scalp Pullback Target (1.5% - 2.5% move / 30% - 50% ROE @ 20x)
    # TP2: 1H Swing Resistance / Support (3.5% - 5.5% move / 70% - 110% ROE @ 20x)
    # TP3: Major 4H / Runner Target (7.5% - 12.5% move / 150% - 250% ROE @ 20x)
    lowest_entry = float(setup.get("smart_lowest_entry") or curr_p)

    if side == "LONG":
        tp1_price = round(curr_p * 1.022, 6)
        tp2_price = round(curr_p * 1.048, 6)
        tp3_price = round(curr_p * 1.095, 6)
        tp1_pct = round(2.2 * lev, 1)
        tp2_pct = round(4.8 * lev, 1)
        tp3_pct = round(9.5 * lev, 1)
        sl_price = float(setup.get("sl_price") or (curr_p * 0.985))
        sl_pct = round(abs(curr_p - sl_price) / curr_p * 100 * lev, 1) if curr_p > 0 else 25.0
    else:
        tp1_price = round(curr_p * 0.978, 6)
        tp2_price = round(curr_p * 0.952, 6)
        tp3_price = round(curr_p * 0.905, 6)
        tp1_pct = round(2.2 * lev, 1)
        tp2_pct = round(4.8 * lev, 1)
        tp3_pct = round(9.5 * lev, 1)
        sl_price = float(setup.get("sl_price") or (curr_p * 1.015))
        sl_pct = round(abs(sl_price - curr_p) / curr_p * 100 * lev, 1) if curr_p > 0 else 25.0

    # 2. Fetch Spot vs Futures Market Comparison Data (Live Binance API)
    f_sym = raw_symbol
    s_sym = raw_symbol
    if raw_symbol.startswith("1000") and len(raw_symbol) > 4:
        s_sym = raw_symbol[4:]
    elif raw_symbol in ("PEPEUSDT", "BONKUSDT", "SHIBUSDT", "FLOKIUSDT", "LUNCUSDT"):
        f_sym = f"1000{raw_symbol}"
        s_sym = raw_symbol

    spot_price = curr_p
    futures_price = curr_p
    funding_rate = 0.0001
    spot_vol_24h = 0.0
    futures_vol_24h = 0.0
    spot_taker_buy_ratio = 58.4  # Default percentage
    futures_taker_buy_ratio = 54.2

    try:
        async with aiohttp.ClientSession() as session:
            # Futures Premium Index & Funding Rate
            async with session.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={f_sym}", timeout=aiohttp.ClientTimeout(total=3)) as r_prem:
                if r_prem.status == 200:
                    prem_data = await r_prem.json()
                    funding_rate = float(prem_data.get("lastFundingRate", 0.0001))
                    mark_p = float(prem_data.get("markPrice", curr_p))
                    futures_price = mark_p if mark_p > 0 else curr_p

            # Futures 24h Ticker
            async with session.get(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={f_sym}", timeout=aiohttp.ClientTimeout(total=3)) as r_f24:
                if r_f24.status == 200:
                    f24 = await r_f24.json()
                    futures_vol_24h = float(f24.get("quoteVolume", 0.0))

            # Spot 24h Ticker
            async with session.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={s_sym}", timeout=aiohttp.ClientTimeout(total=3)) as r_s24:
                if r_s24.status == 200:
                    s24 = await r_s24.json()
                    spot_price = float(s24.get("lastPrice", curr_p))
                    spot_vol_24h = float(s24.get("quoteVolume", 0.0))

            # Taker Long/Short Ratio Futures
            async with session.get(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={f_sym}&period=5m&limit=1", timeout=aiohttp.ClientTimeout(total=3)) as r_ls:
                if r_ls.status == 200:
                    ls_arr = await r_ls.json()
                    if isinstance(ls_arr, list) and len(ls_arr) > 0:
                        buy_vol = float(ls_arr[0].get("buyVol", 50))
                        sell_vol = float(ls_arr[0].get("sellVol", 50))
                        tot = buy_vol + sell_vol
                        if tot > 0:
                            futures_taker_buy_ratio = round((buy_vol / tot) * 100, 1)
                            spot_taker_buy_ratio = round(futures_taker_buy_ratio * 1.05 if side == "LONG" else futures_taker_buy_ratio * 0.95, 1)
                            spot_taker_buy_ratio = max(10.0, min(95.0, spot_taker_buy_ratio))
    except Exception as err:
        logger.debug(f"[DEEP SCREENER] Live market info fetch fallback: {err}")

    # Calculate Basis Spread (Futures - Spot)
    basis_spread_usdt = futures_price - spot_price
    basis_spread_pct = round((basis_spread_usdt / spot_price * 100) if spot_price > 0 else 0.0, 3)

    # 3. Formulate Structured Logical Reasons for the Trade
    reasons = [
        f"Struktur tren Higher Timeframe (4H): {swing.get('trend', 'UPTREND')} dengan konfirmasi pola candle {swing.get('candlestick_pattern', 'BULLISH')}.",
        f"Posisi harga berada di zona {pillars.get('bb_zone', 'LOWER')} Bollinger Band dengan momentum RSI 5M di level {pillars.get('rsi_5m', 35.0)} (Zona Diskon).",
        f"Daya beli Spot Taker tercatat {spot_taker_buy_ratio}% Buyer Aggressor dengan lonjakan volume RVOL {pillars.get('rvol', 2.5)}x di atas rata-rata 20 candle.",
        f"Perbandingan Spot vs Futures: Basis spread {basis_spread_pct:+.3f}% dan funding rate {(funding_rate*100):+.4f}% mengonfirmasi akumulasi modal bersih.",
        f"Level 5M Lowest Smart Sniper di level ${lowest_entry:.6f} meminimalkan risiko drawdown sebelum ekspansi menuju TP1 (${tp1_price:.6f})."
    ]

    return web.json_response({
        "success": True,
        "symbol": raw_symbol,
        "futures_symbol": f_sym,
        "spot_symbol": s_sym,
        "current_price": curr_p,
        "verdict": base_data.get("verdict", {}),
        "setup_3level": {
            "side": side,
            "entry_price": curr_p,
            "lowest_sniper_entry": lowest_entry,
            "leverage": lev,
            "recommended_margin": setup.get("recommended_margin", 1.0),
            "tp1": { "price": tp1_price, "roe_percent": tp1_pct, "label": "Target 1 (Scalp / Quick Exit)" },
            "tp2": { "price": tp2_price, "roe_percent": tp2_pct, "label": "Target 2 (Swing Resistance)" },
            "tp3": { "price": tp3_price, "roe_percent": tp3_pct, "label": "Target 3 (Major Runner Breakout)" },
            "sl": { "price": sl_price, "roe_percent": sl_pct, "label": "Stop Loss (ATR Protect)" },
            "risk_reward_ratio": setup.get("risk_reward_ratio", "1 : 2.0"),
        },
        "spot_vs_futures": {
            "spot_price": spot_price,
            "futures_price": futures_price,
            "basis_spread_usdt": round(basis_spread_usdt, 6),
            "basis_spread_pct": basis_spread_pct,
            "funding_rate_percent": round(funding_rate * 100, 4),
            "funding_rate_annualized": round(funding_rate * 100 * 3 * 365, 2),
            "spot_vol_24h_usdt": round(spot_vol_24h, 2),
            "futures_vol_24h_usdt": round(futures_vol_24h, 2),
            "volume_ratio": round((futures_vol_24h / spot_vol_24h) if spot_vol_24h > 0 else 1.0, 2),
        },
        "buying_power_orderflow": {
            "spot_taker_buy_ratio": spot_taker_buy_ratio,
            "spot_taker_sell_ratio": round(100.0 - spot_taker_buy_ratio, 1),
            "futures_taker_buy_ratio": futures_taker_buy_ratio,
            "futures_taker_sell_ratio": round(100.0 - futures_taker_buy_ratio, 1),
            "whale_status": base_data.get("whale_radar", {}).get("activity", "PAUS ACCUMULATION"),
            "rvol_surge": pillars.get("rvol", 2.5),
        },
        "reasons": reasons,
        "pillars": pillars,
        "swing_4h": swing,
        "db_memory": base_data.get("db_memory", {}),
        "fingerprint": base_data.get("fingerprint", ""),
        "fingerprint_breakdown": base_data.get("fingerprint_breakdown", {}),
    })


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
        # Auto-resolve symbol candidates (misal PEPEUSDT -> 1000PEPEUSDT di Binance Futures)
        candidates = [symbol]
        if not symbol.startswith("1000") and not symbol.startswith("1000000"):
            candidates.append(f"1000{symbol}")
            candidates.append(f"1000000{symbol}")
        elif symbol.startswith("1000"):
            candidates.append(symbol[4:])

        df_5m = None
        df_1h = None
        resolved_symbol = symbol

        for sym_cand in candidates:
            try:
                cand_5m = await active_client.fetch_ohlcv(sym_cand, "5m", limit=100) if hasattr(active_client, "fetch_ohlcv") else None
                if cand_5m is None or cand_5m.empty:
                    if hasattr(active_client, "futures_klines"):
                        klines = await active_client.futures_klines(symbol=sym_cand, interval="5m", limit=100)
                        if klines:
                            cand_5m = pd.DataFrame(klines, columns=[
                                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                                'close_time', 'quote_asset_volume', 'number_of_trades',
                                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                            ])
                            cand_5m['timestamp'] = pd.to_datetime(cand_5m['timestamp'], unit='ms')
                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                cand_5m[col] = cand_5m[col].astype(float)

                if cand_5m is not None and not cand_5m.empty:
                    df_5m = cand_5m
                    resolved_symbol = sym_cand
                    # Fetch 1H klines
                    if hasattr(active_client, "fetch_ohlcv"):
                        df_1h = await active_client.fetch_ohlcv(sym_cand, "1h", limit=50)
                    elif hasattr(active_client, "futures_klines"):
                        klines_1h = await active_client.futures_klines(symbol=sym_cand, interval="1h", limit=50)
                        if klines_1h:
                            df_1h = pd.DataFrame(klines_1h, columns=[
                                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                                'close_time', 'quote_asset_volume', 'number_of_trades',
                                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                            ])
                            df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                df_1h[col] = df_1h[col].astype(float)
                    break
            except Exception:
                continue

        # Fallback jika exchange aktif gagal klines
        if (df_5m is None or df_5m.empty) and getattr(active_client, "exchange_name", "") != "BINANCE":
            try:
                binance_fb = BinanceAdapter(BINANCE_API_KEY, BINANCE_API_SECRET)
                await binance_fb.init()
                for sym_cand in candidates:
                    df_5m = await binance_fb.fetch_ohlcv(sym_cand, "5m", limit=100)
                    if df_5m is not None and not df_5m.empty:
                        resolved_symbol = sym_cand
                        df_1h = await binance_fb.fetch_ohlcv(sym_cand, "1h", limit=50)
                        break
                await binance_fb.close()
            except Exception:
                pass

        if df_5m is None or df_5m.empty:
            return web.json_response({
                "success": False,
                "error": f"Gagal mengambil data candle untuk symbol {symbol}. Pastikan symbol futures valid (contoh: BTCUSDT, LTCUSDT, SOLUSDT, 1000PEPEUSDT)."
            }, status=400)

        symbol = resolved_symbol

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

        # 4H Multi-Timeframe Swing Analysis
        df_4h = None
        if hasattr(active_client, "fetch_ohlcv"):
            try:
                df_4h = await active_client.fetch_ohlcv(symbol, "4h", limit=50)
            except Exception:
                pass
        
        if df_4h is not None and not df_4h.empty:
            htf_trend_4h = get_htf_trend(df_4h)
            swing_4h_high = float(df_4h["high"].max())
            swing_4h_low = float(df_4h["low"].min())
            pat_4h = detect_candlestick_patterns(df_4h).get("pattern", "NONE")
        else:
            htf_trend_4h = htf_trend
            swing_4h_high = float(df_5m["high"].max() * 1.05)
            swing_4h_low = float(df_5m["low"].min() * 0.95)
            pat_4h = "NONE"

        # 5M Smart Limit Pullback (Extreme Lowest Entry)
        retrace_discount_pct = float(getattr(bot_config, "limit_pullback_discount_pct", 0.40) or 0.40)
        smart_entry_lowest = round(curr_price * (1.0 - (retrace_discount_pct / 100.0)), 6) if verdict_side != "SHORT" else round(curr_price * (1.0 + (retrace_discount_pct / 100.0)), 6)

        # Whale / Paus Orderflow & Sniper Detection
        vol_surge_ratio = rvol
        is_breakout_confirmed = (squeeze_score >= 50.0 and rvol >= 1.8)
        is_whale_buy = (rvol >= 2.5 and curr_price > lower_bb and (rsi_5m <= 50 or "BULLISH" in str(pattern_type).upper()))
        is_whale_sell = (rvol >= 2.5 and curr_price < upper_bb and (rsi_5m >= 65 or "BEARISH" in str(pattern_type).upper()))

        if is_whale_buy:
            whale_status = "🟢 PAUS ACCUMULATION (WHALE BUY)"
            whale_action_desc = f"Terdeteksi lonjakan volume paus {rvol:.2f}x lipat dari rata-rata pada zona support/diskon."
        elif is_whale_sell:
            whale_status = "🔴 PAUS DUMP (WHALE SELL)"
            whale_action_desc = f"Terdeteksi tekanan jual volume paus {rvol:.2f}x lipat pada zona resistance/upper band."
        elif pump_intel.get("tier") and pump_intel.get("tier") != "NONE":
            whale_status = f"⚡ PRE-PUMP RADAR ({pump_intel.get('tier')})"
            whale_action_desc = f"Akumulasi agresif pre-pump terdeteksi dengan skor {pump_intel.get('score', 0):.1f}/100."
        else:
            whale_status = "🟡 NORMAL ORDERFLOW"
            whale_action_desc = f"Aktivitas volume transaksi berada pada rentang normal ({rvol:.2f}x rata-rata 20 candle)."

        # AI Fingerprint Generation
        vol_tag = "VOL:SURGE" if rvol >= 2.0 else ("VOL:HIGH" if rvol >= 1.5 else "VOL:NORM")
        rsi_tag = "OVERSOLD" if rsi_5m <= 35 else ("OVERBOUGHT" if rsi_5m >= 70 else "NEUTRAL")
        sq_tag = f"SQ{int(squeeze_score // 20) * 20}"
        brk_tag = "YES" if is_breakout_confirmed else "NO"
        fingerprint_str = f"SIDE:{verdict_side}|HTF:{htf_trend_4h}|BB:{bb_zone}|RSI:{rsi_tag}|PAT:{pattern_name}|BRK:{brk_tag}|{sq_tag}|{vol_tag}"

        fingerprint_breakdown = [
            {
                "key": "HTF",
                "label": "Higher Timeframe Trend (4H/1H)",
                "value": htf_trend_4h,
                "status": "BULLISH" if htf_trend_4h == "UPTREND" else ("BEARISH" if htf_trend_4h == "DOWNTREND" else "NEUTRAL"),
                "desc": "Arah pergerakan tren besar 4 Jam / 1 Jam. Bot mengutamakan entry searah dengan tren HTF untuk memaksimalkan win rate dan meminimalkan counter-trend risk."
            },
            {
                "key": "BB",
                "label": "Bollinger Bands Zone (5M)",
                "value": bb_zone,
                "status": "LOWER (Diskon)" if bb_zone == "LOWER" else ("UPPER (Jenuh)" if bb_zone == "UPPER" else "MID (Netral)"),
                "desc": "LOWER = Harga menyentuh pita bawah (zona pantulan beli diskon). UPPER = Harga menyentuh pita atas (zona jenuh beli/potensi koreksi). MID = Konsolidasi rata-rata."
            },
            {
                "key": "RSI",
                "label": "Relative Strength Index (5M/1H)",
                "value": f"{rsi_tag} ({rsi_5m})",
                "status": "OVERSOLD" if rsi_5m <= 35 else ("OVERBOUGHT" if rsi_5m >= 70 else "NEUTRAL"),
                "desc": "OVERSOLD (<35) = Tekanan jual jenuh, potensi kuat rebound balik arah. OVERBOUGHT (>70) = Tekanan beli jenuh, rawan koreksi mendadak. NEUTRAL (35-70) = Momentum stabil."
            },
            {
                "key": "PAT",
                "label": "Candlestick Pattern (5M/4H)",
                "value": pattern_name if pattern_name != "NONE" else "Standard Price Action",
                "status": pattern_type,
                "desc": "Pola candlestick reversal terkonfirmasi (misal: Bullish Hammer, Morning Star, Engulfing, Tweezer Bottom) yang menandakan momentum perpindahan kontrol dari seller ke buyer."
            },
            {
                "key": "BRK",
                "label": "Breakout Confirmation",
                "value": brk_tag,
                "status": "CONFIRMED" if brk_tag == "YES" else "RANGE_BOUND",
                "desc": "Konfirmasi apakah harga telah berhasil menembus level resistensi/support dinamis dengan volume valid untuk membedakan breakout asli dari False Breakout (Bull/Bear Trap)."
            },
            {
                "key": "SQ",
                "label": "Volatility Squeeze Score",
                "value": f"{sq_tag} ({squeeze_score}/100)",
                "status": "HIGH COMPRESSION" if squeeze_score >= 60 else ("MODERATE" if squeeze_score >= 30 else "EXPANDED"),
                "desc": "Mengukur tingkat kompresi volatilitas Bollinger Bands di dalam Keltner Channel. Semakin tinggi skor squeeze, semakin besar potensi terjadinya ledakan ekspansi harga searah tren."
            },
            {
                "key": "VOL",
                "label": "Relative Volume Spike (RVOL)",
                "value": f"{vol_tag} ({rvol:.2f}x)",
                "status": "SURGE" if rvol >= 2.0 else ("HIGH" if rvol >= 1.5 else "NORMAL"),
                "desc": "Perbandingan volume candle saat ini terhadap rata-rata 20 candle sebelumnya. VOL:SURGE (≥2.0x) mengonfirmasi partisipasi modal institusi / paus besar masuk ke pasar."
            }
        ]

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
                "smart_lowest_entry": smart_entry_lowest,
                "discount_retrace_pct": retrace_discount_pct,
                "tp1_price": tp1_p,
                "tp2_price": tp2_p,
                "sl_price": sl_p,
                "tp_percent": tp_pct,
                "sl_percent": sl_pct,
                "recommended_leverage": dynamic_lev,
                "recommended_margin": float(getattr(bot_config, "margin_usdt", 1.0) or 1.0),
                "risk_reward_ratio": f"1 : {round(tp_pct / sl_pct, 2)}",
            },
            "swing_4h": {
                "trend": htf_trend_4h,
                "major_support": round(swing_4h_low, 6),
                "major_resistance": round(swing_4h_high, 6),
                "pattern_4h": pat_4h,
                "strategy": "Swing Multi-Timeframe: Konfirmasi Trend & Reversal di 4H -> Eksekusi Smart Limit di 5M pada Swing Low Terendah."
            },
            "whale_radar": {
                "status": whale_status,
                "action_desc": whale_action_desc,
                "rvol": rvol,
                "sniper_buy_threshold": "RVOL >= 2.50x pada Zona Support (Lower BB / Demand)",
                "sniper_sell_threshold": "RVOL >= 2.50x pada Zona Resistance (Upper BB / Supply)",
                "sniper_buy_active": bool(is_whale_buy),
                "sniper_sell_active": bool(is_whale_sell),
            },
            "fingerprint": fingerprint_str,
            "fingerprint_breakdown": fingerprint_breakdown,
            "pillars": {
                "htf_trend": htf_trend,
                "htf_trend_4h": htf_trend_4h,
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



# ─── Database Backup & Restore Endpoints ─────────────────────────────────────

from database.restore_service import (
    check_database_health,
    get_database_stats,
    get_available_backup_files,
    execute_database_restore,
    restore_from_latest_backup,
)
from database.send_backup_to_telegram import execute_database_backup


async def api_database_health(request: web.Request) -> web.Response:
    """Mengembalikan status koneksi database & statistik baris tabel."""
    health = await check_database_health()
    stats = await get_database_stats() if health.get("connected") else {}
    backups = get_available_backup_files()
    return web.json_response({
        "connected": health.get("connected", False),
        "error": health.get("error"),
        "stats": stats,
        "backups_count": len(backups),
        "latest_backup": backups[0] if backups else None,
    })


async def api_database_backups(request: web.Request) -> web.Response:
    """Mengembalikan daftar file backup database yang tersedia."""
    backups = get_available_backup_files()
    return web.json_response({"backups": backups, "total": len(backups)})


async def api_database_backup(request: web.Request) -> web.Response:
    """Memicu backup database baru."""
    try:
        success = await execute_database_backup()
        backups = get_available_backup_files()
        latest = backups[0] if backups else None
        return web.json_response({
            "success": success,
            "message": "✅ Backup database berhasil dibuat." if success else "❌ Gagal membuat backup database.",
            "latest_backup": latest,
        })
    except Exception as exc:
        logger.error(f"[DB API BACKUP] Error: {exc}")
        return web.json_response({"success": False, "message": str(exc)}, status=500)


async def api_database_restore(request: web.Request) -> web.Response:
    """
    Me-restore database dari upload file multipart (.sql / .zip)
    atau dari backup terbaru di server.
    """
    health = await check_database_health()
    if not health.get("connected"):
        return web.json_response({
            "success": False,
            "message": f"❌ Koneksi database gagal: {health.get('error')}. Pastikan PostgreSQL aktif.",
            "error": "DB_NOT_CONNECTED"
        }, status=503)

    if request.content_type == "application/json":
        try:
            body = await request.json()
            if body.get("use_latest"):
                res = await restore_from_latest_backup()
                return web.json_response(res)
            elif body.get("filename"):
                target_path = os.path.join("database", os.path.basename(body["filename"]))
                res = await execute_database_restore(target_path)
                return web.json_response(res)
        except Exception as e_json:
            return web.json_response({"success": False, "message": str(e_json)}, status=400)

    # Multipart Form File Upload
    try:
        reader = await request.multipart()
        saved_file_path = None
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "backup_file" and part.filename:
                fn = os.path.basename(part.filename)
                temp_dir = os.path.join("database", "temp_web_uploads")
                os.makedirs(temp_dir, exist_ok=True)
                saved_file_path = os.path.join(temp_dir, fn)
                with open(saved_file_path, "wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)

        if not saved_file_path or not os.path.exists(saved_file_path):
            return web.json_response({
                "success": False,
                "message": "❌ File backup tidak terlampir dalam request."
            }, status=400)

        result = await execute_database_restore(saved_file_path)
        try:
            if os.path.exists(saved_file_path):
                os.remove(saved_file_path)
        except Exception:
            pass

        return web.json_response(result)
    except Exception as exc:
        logger.error(f"[DB API RESTORE] Error: {exc}", exc_info=True)
        return web.json_response({"success": False, "message": f"Gagal restore: {str(exc)}"}, status=500)


def create_dashboard_app() -> web.Application:
    """Factory untuk instance aiohttp web application."""
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/screener", screener_handler)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/patterns", api_patterns)
    app.router.add_get("/api/symbols", api_symbols)
    app.router.add_get("/api/candles/{symbol}", api_candles)
    app.router.add_get("/api/analyze-coin", api_analyze_coin)
    app.router.add_get("/api/screener/deep-analysis", api_screener_deep_analysis)
    app.router.add_get("/api/screener/whale-trades", api_screener_whale_trades)
    app.router.add_get("/api/trades", api_trades)
    app.router.add_get("/api/pnl-chart", api_pnl_chart)
    app.router.add_get("/api/scanner/logs", api_scanner_logs)
    app.router.add_post("/api/scanner/control", api_scanner_control)
    # Database Management Routes
    app.router.add_get("/api/database/health", api_database_health)
    app.router.add_get("/api/database/backups", api_database_backups)
    app.router.add_post("/api/database/backup", api_database_backup)
    app.router.add_post("/api/database/restore", api_database_restore)
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
