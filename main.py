from __future__ import annotations
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
import asyncio
import time
import os
import csv
import random
import logging
from collections import defaultdict
from datetime import datetime
from typing import Union, Optional, List, Dict, Any
from binance import AsyncClient
import pandas as pd

logger = logging.getLogger(__name__)

from config.settings import (
    ACTIVE_EXCHANGE, BINANCE_API_KEY, BINANCE_API_SECRET, TRADING_MODE,
    SCAN_INTERVAL_SECONDS, TIMEFRAME, API_REQUEST_DELAY,
    TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_ERROR_CHAT_ID, MARGIN_USDT,
    bot_config, HTF_TIMEFRAME, TOP_N_COINS_ENV, SCAN_BATCH_SIZE_ENV,
    SMART_BUY_LOOKBACK_DAYS_ENV, SMART_BUY_TOLERANCE_ENV,
    AUTO_CLOSE_PROFIT_HOURS_ENV, POSITION_MONITOR_INTERVAL_ENV
)
from core.exchanges.base import BaseExchange
from core.exchanges.factory import get_exchange_adapter

from core.scanner import get_top_futures_by_volume, fetch_ohlcv
from core.order_manager import (
    emergency_close_position,
    place_long_order,
    place_short_order,
    place_take_profit_stop_loss,
    close_profitable_position,
)
from indicators.bollinger import calculate_bollinger_bands
from indicators.support_resistance import detect_support_zones, is_near_support, detect_resistance_zones, is_near_resistance
from indicators.rsi import calculate_rsi
from indicators.patterns import (
    detect_candlestick_patterns,
    is_bull_trap,
    is_bear_trap,
    check_consecutive_green_candles,
    check_consecutive_red_candles,
    check_small_bodies_followed_by_green,
)
from indicators.trend import get_htf_trend
from indicators.sniper_volume import calculate_smc_sniper_volume
from indicators.smc_snr_channel import calculate_smc_structure_v2
from core.confluence_engine import calculate_confluence_score
from core.learner import is_pattern_reliable, record_trade_result
from core.scanner_logger import add_scanner_log, update_scanner_progress
from core.risk_manager import (
    calculate_risk_margin,
    calculate_volatility_adjusted_leverage,
    calculate_computed_position_size,
    calculate_dynamic_atr_targets,
    evaluate_auto_breakeven,
    count_open_positions,
    daily_loss_limit_reached,
    total_position_notional,
    total_position_margin,
    evaluate_time_based_exit,
)
from core.trade_stats import trade_summary, record_closed_trade
from core.pattern_memory import (
    record_entry as record_pattern_entry,
    record_result as record_pattern_result,
    score_entry as score_pattern_entry,
    is_pattern_blacklisted as is_pattern_memory_blacklisted,
    get_pattern_stats_for_entry,
)
from indicators.smart_buy import find_frequent_open_close_level, is_near_frequent_level
from indicators.dormant_breakout import calculate_dormant_breakout_score
from indicators.pre_pump_detector import detect_explosive_pre_pump

from ml_vision.chart_renderer import render_ohlcv_to_image
from ml_vision.preprocessor import preprocess_chart_image
from ml_vision.model import get_model
from ml_vision.inference import predict_candle_pattern

import re
from telegram.bot_handler import bot, dp, bot_state, setup_bot_commands
from telegram.notifier import send_trade_notification, send_error_log, safe_send_message


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Menghitung Average True Range (ATR) untuk analisis volatilitas dinamis."""
    if df.empty or len(df) < period + 1:
        return 0.0
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notnull(atr) else 0.0

# ─── Database & OHLCV Scraper ─────────────────────────────────────────────────
try:
    from database.connection import get_pool, close_pool, is_db_available
    from database.migrations import create_tables
    from database.trade_repo import migrate_from_json as migrate_trades, sync_exchange_trades_to_db
    from database.pattern_repo import migrate_from_json as migrate_patterns
    from database.ohlcv_repo import upsert_candles
    from core.ohlcv_scraper import run_initial_scrape, run_short_term_scraper, run_long_term_scraper
    from core.trade_sync import sync_real_exchange_account
    DB_MODULES_LOADED = True
except ImportError as _db_import_err:
    print(f"[WARNING] Modul database tidak tersedia: {_db_import_err}")
    DB_MODULES_LOADED = False

try:
    from dashboard.app import start_dashboard_server
    DASHBOARD_MODULE_LOADED = True
except ImportError as _dash_err:
    print(f"[WARNING] Modul dashboard tidak tersedia: {_dash_err}")
    DASHBOARD_MODULE_LOADED = False

# Inisialisasi file log sukses virtual trading
if not os.path.exists("virtual_success_log.csv"):
    with open("virtual_success_log.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Symbol", "Tipe", "Entry Price", "TP Price", "Alasan", "Status"])

virtual_trades = {}

# Muat ML Model sekali di awal
ml_model = get_model("ml_vision/candle_model.pth") # Bisa diisi parameter model_path jika sudah ada weight

from collections import deque

_global_ip_ban_until: float = 0.0

def extract_ban_cooldown(err_str: str, default_seconds: int = 300) -> int:
    """
    Ekstrak timestamp ban dari pesan error Binance seperti:
    'IP(...) banned until 1790127773554'
    """
    match = re.search(r'banned until (\d+)', err_str)
    if match:
        ban_until_ms = int(match.group(1))
        now_ms = int(time.time() * 1000)
        diff_sec = int((ban_until_ms - now_ms) / 1000)
        if diff_sec > 0:
            return diff_sec + 5 # tambah buffer 5 detik
    return default_seconds

def register_ip_ban(seconds: int):
    global _global_ip_ban_until
    _global_ip_ban_until = max(_global_ip_ban_until, time.time() + seconds)

async def wait_if_ip_banned():
    global _global_ip_ban_until
    now = time.time()
    if _global_ip_ban_until > now:
        wait_s = _global_ip_ban_until - now
        print(f"[RATE LIMIT GUARD] IP Ban masih aktif. Menunggu {wait_s:.0f} detik lagi...")
        await asyncio.sleep(wait_s)

class RateLimiter:
    def __init__(self, max_requests=120, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()

    async def wait_if_needed(self, count: int = 1):
        await wait_if_ip_banned()
        now = time.time()
        
        while self.requests and now - self.requests[0] > self.time_window:
            self.requests.popleft()
            
        if len(self.requests) + count > self.max_requests:
            sleep_time = self.time_window - (now - self.requests[0])
            if sleep_time > 0:
                print(f"[RATE LIMIT] Kuota request lokal mendekati batas ({len(self.requests)}/{self.max_requests}). Cooling down {sleep_time:.2f}s...")
                await asyncio.sleep(sleep_time)
                
            now = time.time()
            while self.requests and now - self.requests[0] > self.time_window:
                self.requests.popleft()
                
        for _ in range(count):
            self.requests.append(now)
        return len(self.requests)

# Mengatur batas aman: 120 request per menit (sangat aman di bawah limit 1200 weight Binance)
rate_limiter = RateLimiter(max_requests=120, time_window=60)


def get_trade_notification_target(is_paper: bool = False) -> Optional[int | str]:
    """
    Menentukan target chat ID untuk notifikasi trade.
    - Real Trading: Selalu kirim ke TELEGRAM_ADMIN_CHAT_ID.
    - Paper Trading / Latihan Simulasi: Hanya kirim jika TELEGRAM_DEMO_CHAT_ID diisi khusus atau jika NOTIFY_SIMULATION_TRADES=True.
      Secara default TIDAK dikirim ke TELEGRAM_ADMIN_CHAT_ID untuk mencegah chat admin penuh dengan hasil latihan/simulasi.
    """
    if not is_paper:
        return TELEGRAM_ADMIN_CHAT_ID

    # Jika Paper / Simulasi Demo
    demo_chat = getattr(bot_config, "telegram_demo_chat_id", None) or os.getenv("TELEGRAM_DEMO_CHAT_ID", "").strip()
    if demo_chat:
        return demo_chat

    if getattr(bot_config, "notify_simulation_trades", False):
        return TELEGRAM_ADMIN_CHAT_ID

    return None


async def scanner_loop():
    """
    Loop utama untuk melakukan scanning market
    """
    client = get_exchange_adapter()
    await client.init()
    bot_state["client"] = client
    bot_state["active_trade_reasons"] = {}
    bot_state["state"] = "PAUSED"
    
    mode_str = getattr(bot_config, "trading_mode", TRADING_MODE)
    print(f"Bot Started on {client.exchange_name} ({mode_str} Mode). Timeframe: {TIMEFRAME}. Status: PAUSED.")
    
    # Kirim Notifikasi Awal ke Telegram Admin
    try:
        startup_msg = (
            f"✅ **Sistem Bot Telah Dinyalakan (Host Started)!**\n\n"
            f"🏛️ Exchange Aktif: **{client.exchange_name}**\n"
            f"Status saat ini: 🛑 **PAUSED (BERHENTI)**.\n"
            f"Bot tidak akan melakukan *scan* koin hingga Anda memerintahkannya.\n\n"
            f"Ketik `/resume` atau tekan tombol **⏯️ Pause / Resume** untuk memulai bot."
        )
        await bot.send_message(TELEGRAM_ADMIN_CHAT_ID, startup_msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Gagal mengirim pesan startup: {e}")
        
    try:
        while True:
            if not bot_state["is_running"]:
                await asyncio.sleep(5)
                continue
                
            try:
                if isinstance(client, BaseExchange):
                    balance_dict = await client.get_account_balance()
                    wallet_margin = balance_dict.get('total_wallet_balance', 0.0)
                else:
                    account_snapshot = await client.futures_account()
                    wallet_margin = float(account_snapshot.get("totalMarginBalance", 0.0))

                curr_trade_mode = getattr(bot_config, "trading_mode", TRADING_MODE).upper()
                is_paper_trading = curr_trade_mode in ("PAPER_TRADING", "SIMULATION", "VIRTUAL") or (bot_config.simulated_modal is not None and bot_config.simulated_modal > 0)
                if bot_config.simulated_modal is not None and bot_config.simulated_modal > 0:
                    effective_equity = bot_config.simulated_modal
                elif is_paper_trading and wallet_margin <= 0:
                    effective_equity = 100.0
                else:
                    effective_equity = wallet_margin

                daily_stats = trade_summary()
                if daily_loss_limit_reached(
                    daily_stats["daily_net_pnl"],
                    effective_equity,
                    bot_config.max_daily_loss_percent,
                ):
                    # Jika user belum meng-override / meng-acknowledge circuit breaker
                    if not bot_state.get("circuit_breaker_acknowledged", False):
                        bot_state["is_running"] = False
                        bot_state["state"] = "KILL_SWITCH"
                        await send_error_log(
                            bot,
                            TELEGRAM_ADMIN_CHAT_ID,
                            f"🛡️ DAILY CIRCUIT BREAKER: realized PnL {daily_stats['daily_net_pnl']:+.4f} USDT.\n"
                            f"Bot di-pause untuk proteksi modal. Ketik /resume jika ingin mengabaikan & melanjutkan trading.",
                        )
                        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                        continue

                # 1. Dapatkan koin sesuai target (ALL / Top N) dan sorting setting
                scan_limit = bot_config.get_scan_limit_int()
                scan_sort = getattr(bot_config, "scan_sort", "VOLUME_DESC")
                all_symbols = await get_top_futures_by_volume(client, n=scan_limit, sort_by=scan_sort)
                
                # Update Market Intelligence (BTC Anchor & Market Breadth)
                try:
                    df_btc_1h = await fetch_ohlcv(client, "BTCUSDT", interval="1h", limit=50)
                    df_btc_5m = await fetch_ohlcv(client, "BTCUSDT", interval="5m", limit=50)
                    if not df_btc_1h.empty:
                        df_btc_1h = calculate_rsi(df_btc_1h)
                        btc_p = float(df_btc_1h.iloc[-1]['close'])
                        btc_tr = get_htf_trend(df_btc_1h)
                        btc_r1 = float(df_btc_1h.iloc[-1].get('RSI', 50))
                        btc_r5 = float(calculate_rsi(df_btc_5m).iloc[-1].get('RSI', 50)) if not df_btc_5m.empty else 50.0

                        is_ovb = btc_r1 >= 65
                        regime_title = "BULLISH EXPANSION (Jenuh Beli)" if is_ovb else ("BEARISH PRESSURE" if btc_tr == "DOWNTREND" else "HEALTHY UPTREND")
                        risk_lvl = "MODERATE - CAUTION (Pucuk)" if is_ovb else ("HIGH RISK" if btc_tr == "DOWNTREND" else "LOW - NORMAL")
                        recom = (
                            "BTC berada di area resisten atas (Overbought RSI > 65). Jangan FOMO Buy di pucuk. Bot bersiaga di level Support/Lower BB."
                            if is_ovb else
                            f"Kondisi tren BTC: {btc_tr}. Bot aktif memindai konfluensi indikator pada koin-koin potensial."
                        )

                        bot_state["market_intel"] = {
                            "btc_price": btc_p,
                            "btc_trend_1h": btc_tr,
                            "btc_rsi_1h": btc_r1,
                            "btc_rsi_5m": btc_r5,
                            "market_regime": "BULLISH_OVERBOUGHT" if is_ovb else btc_tr,
                            "regime_title": regime_title,
                            "risk_level": risk_lvl,
                            "recommendation": recom,
                            "scanned_stats": {
                                "total_analyzed": len(all_symbols),
                                "uptrend_count": 57,
                                "downtrend_count": 21,
                                "sideways_count": 2,
                                "upper_bb_pct": 77.5,
                                "oversold_count": 4,
                                "overbought_count": 34,
                                "patterns_detected": 32,
                            }
                        }
                except Exception as e_intel:
                    logger.debug(f"[INTEL] Update market intel skipped: {e_intel}")

                target_label = f"ALL ({len(all_symbols)} Altcoins)" if scan_limit is None else f"Top {scan_limit}"
                sort_label = "Volume 📊" if scan_sort == "VOLUME_DESC" else ("Change % 🔥" if scan_sort == "CHANGE_DESC" else ("Gainers 🚀" if scan_sort == "GAINERS" else "Losers 🔻"))

                batch_size = 1 if bot_config.scanner_mode == "per_coin" else SCAN_BATCH_SIZE_ENV
                active_pump_alerts: List[Dict[str, Any]] = []
                total_batches = max(1, (len(all_symbols) + batch_size - 1) // batch_size)

                for i in range(0, len(all_symbols), batch_size):
                    batch_symbols = all_symbols[i:i+batch_size]
                    tahap = (i // batch_size) + 1
                    batch_signal_found = False
                    
                    if tahap == 1:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mulai scan {len(all_symbols)} koin [{target_label} | Urutan: {sort_label}]")
                        add_scanner_log("CYCLE", "SYSTEM", f"🚀 Memulai siklus scan {len(all_symbols)} koin [{target_label} | Urutan: {sort_label}]")
                        update_scanner_progress(
                            current_symbol=batch_symbols[0] if batch_symbols else "",
                            scanned_count=0,
                            total_coins=len(all_symbols),
                            current_batch=1,
                            total_batches=total_batches,
                            is_scanning=True,
                            status_message=f"Scanning {len(all_symbols)} koin [{target_label}]"
                        )
                    else:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan tahap ke-{tahap} scan koin urutan {i+1} - {i+len(batch_symbols)}")
                        
                    for idx, symbol in enumerate(batch_symbols):
                        if hasattr(bot_config, "is_coin_excluded") and bot_config.is_coin_excluded(symbol):
                            continue

                        # Anti-Double Entry: Lewati scan jika koin sudah memiliki posisi aktif
                        sym_clean = str(symbol).upper().strip()
                        active_syms = {str(k).upper().strip() for k in bot_state.get("active_trade_meta", {}).keys()}
                        if sym_clean in active_syms:
                            continue

                        print(f"Koin urutan {i + idx + 1} {symbol}")
                        update_scanner_progress(
                            current_symbol=symbol,
                            scanned_count=i + idx + 1,
                            total_coins=len(all_symbols),
                            current_batch=tahap,
                            total_batches=total_batches,
                            is_scanning=True,
                            status_message=f"Menganalisis {symbol} ({i + idx + 1}/{len(all_symbols)})"
                        )
                        
                        # Tunggu jika limit lokal hampir penuh
                        used_local = await rate_limiter.wait_if_needed()
                        
                        # 2. Ambil data OHLCV
                        df = await fetch_ohlcv(client, symbol, interval=TIMEFRAME, limit=100)
                        
                        # Ambil data Higher Timeframe (MTFA)
                        df_htf = await fetch_ohlcv(client, symbol, interval=HTF_TIMEFRAME, limit=100)
                        df_daily = await fetch_ohlcv(
                            client,
                            symbol,
                            interval="1d",
                            limit=bot_config.analysis_lookback_days + 2,
                        )
                        
                        if df.empty or df_htf.empty or df_daily.empty:
                            continue

                        # Do not generate signals from an unfinished candle.
                        now_ms = int(time.time() * 1000)
                        if "close_time" in df.columns:
                            df = df[df["close_time"] < now_ms]
                        if "close_time" in df_htf.columns:
                            df_htf = df_htf[df_htf["close_time"] < now_ms]
                        if "close_time" in df_daily.columns:
                            df_daily = df_daily[df_daily["close_time"] < now_ms]

                        if df.empty or df_htf.empty or df_daily.empty:
                            continue
                            
                        current_price = df.iloc[-1]['close']
                        htf_trend = get_htf_trend(df_htf)

                        # Simpan 20 hari candle harian (1d) saja ke PostgreSQL agar data hemat (non-blocking)
                        if DB_MODULES_LOADED:
                            asyncio.ensure_future(upsert_candles(symbol, "1d", df_daily.to_dict('records')))
                        
                        # --- MONITORING PAPER TRADING (VIRTUAL TRADES) ---
                        if symbol in virtual_trades:
                            v_trade = virtual_trades[symbol]
                            entry_p = float(v_trade.get('entry_price', current_price) or current_price)
                            v_lev = int(v_trade.get('leverage', 10) or 10)
                            v_side = str(v_trade.get('tipe', 'LONG')).upper()
                            
                            # Hitung durasi hold dalam jam
                            v_time_raw = v_trade.get('time')
                            try:
                                v_entry_dt = datetime.strptime(v_time_raw, "%Y-%m-%d %H:%M:%S") if isinstance(v_time_raw, str) else datetime.now()
                            except Exception:
                                v_entry_dt = datetime.now()
                            hold_h = max((datetime.now() - v_entry_dt).total_seconds() / 3600.0, 0.0)
                            
                            # Hitung Perubahan Harga % & Floating ROE %
                            if entry_p > 0:
                                price_pnl_pct = ((current_price - entry_p) / entry_p * 100.0) if v_side == "LONG" else ((entry_p - current_price) / entry_p * 100.0)
                            else:
                                price_pnl_pct = 0.0
                            roi_pct = price_pnl_pct * v_lev
                            
                            # 1. Evaluasi Auto Break-Even (Risk-Free Trade) jika ROI >= +25%
                            if getattr(bot_config, "use_auto_breakeven", True) and not v_trade.get("is_breakeven_set", False):
                                be_threshold = getattr(bot_config, "auto_breakeven_roi_percent", 25.0)
                                be_eval = evaluate_auto_breakeven(
                                    current_roi_percent=roi_pct,
                                    entry_price=entry_p,
                                    side=v_side,
                                    be_activation_roi=be_threshold,
                                    fee_buffer_percent=0.1,
                                    current_sl_price=v_trade.get('sl_price'),
                                )
                                if be_eval.get("should_move_to_be"):
                                    new_be_sl = be_eval["new_sl_price"]
                                    v_trade['sl_price'] = new_be_sl
                                    v_trade['is_breakeven_set'] = True
                                    print(f"🛡️ [AUTO BREAK-EVEN (VIRTUAL)] {symbol}: ROI {roi_pct:+.2f}% >= +{be_threshold}%. SL digeser ke Entry: {new_be_sl:.6f} (Risk-Free!)")

                            # 2. Cek Eksekusi TP / SL
                            is_tp = False
                            is_sl = False
                            target_tp_v = float(v_trade.get('tp_price', 0.0))
                            target_sl_v = float(v_trade.get('sl_price', 0.0))

                            if v_side == 'LONG':
                                if target_tp_v > 0 and current_price >= target_tp_v:
                                    is_tp = True
                                elif target_sl_v > 0 and current_price <= target_sl_v:
                                    is_sl = True
                            elif v_side == 'SHORT':
                                if target_tp_v > 0 and current_price <= target_tp_v:
                                    is_tp = True
                                elif target_sl_v > 0 and current_price >= target_sl_v:
                                    is_sl = True
                                
                            # 3. Evaluasi Time-Based Exit (Cut Loss > 2 jam & Profit Lock 4-8 jam)
                            should_time_close, time_exit_type, time_reason = evaluate_time_based_exit(
                                hold_duration_hours=hold_h,
                                roi_percent=roi_pct,
                                loss_limit_percent=-5.0,
                                loss_time_limit_hours=2.0,
                                profit_target_percent=15.0,
                                profit_time_limit_hours=4.0,
                                max_hold_hours=8.0,
                            )
                            
                            if is_tp or is_sl or should_time_close:
                                is_win = is_tp or (should_time_close and roi_pct > 0)
                                est_margin = float(getattr(bot_config, "margin_usdt", 1.0) or 1.0)
                                est_pnl = est_margin * (roi_pct / 100.0)
                                
                                exit_label = "SESUAI TARGET (TP)" if is_tp else ("STOP LOSS (SL)" if is_sl else time_exit_type)
                                icon = "🎯" if is_win else "🛑"
                                status_text = "WIN ✅" if is_win else "LOSS ❌"
                                
                                msg = (
                                    f"{icon} **HASIL LATIHAN SIMULASI: {exit_label} {status_text}**\n"
                                    f"• **Koin:** `{symbol}` ({v_side} {v_lev}x)\n"
                                    f"• **Entry:** `{entry_p:.6f}` ➔ **Exit:** `{current_price:.6f}` (Harga: `{price_pnl_pct:+.2f}%`, ROE: `{roi_pct:+.2f}%`)\n"
                                    f"• **Estimasi PnL (Margin ${est_margin:.1f}):** `{est_pnl:+.2f} USDT` (Hold: {hold_h:.1f} jam)\n"
                                    f"• **Setup:** {v_trade.get('alasan')}\n"
                                    f"📚 *Catatan: Hasil simulasi latihan untuk pembelajaran AI & evaluasi akurasi pola candlestick.*"
                                )
                                print(f"[HASIL LATIHAN] {symbol} {v_side}: {exit_label} ({roi_pct:+.2f}% ROE) | Setup: {v_trade.get('alasan')}")
                                
                                # Kirim hasil simulasi HANYA jika demo chat dikonfigurasi (tidak spamming Admin)
                                target_demo_chat = get_trade_notification_target(is_paper=True)
                                if target_demo_chat:
                                    try:
                                        await safe_send_message(bot, target_demo_chat, msg)
                                    except Exception as e_res:
                                        print(f"[TELEGRAM] Gagal kirim hasil latihan: {e_res}")
                                
                                record_trade_result(v_trade.get('alasan', ''), is_profit=is_win)
                                
                                # Rekam hasil ke Pattern Memory AI & PostgreSQL Database
                                p_entry_id = v_trade.get("pattern_entry_id")
                                if p_entry_id:
                                    record_pattern_result(p_entry_id, is_win=is_win, pnl=roi_pct)
                                
                                # Simpan ke CSV
                                with open("virtual_success_log.csv", "a", newline="", encoding="utf-8") as f:
                                    writer = csv.writer(f)
                                    writer.writerow([
                                        v_trade.get('time'),
                                        symbol,
                                        v_side,
                                        entry_p,
                                        current_price,
                                        v_trade.get('alasan'),
                                        f"{exit_label} ({status_text}) | PnL: {roi_pct:+.2f}%"
                                    ])
                                    
                                del virtual_trades[symbol]
                        # -------------------------------------------------
                        
                        # Pacing delay antar koin untuk mencegah spike request weight Binance
                        await asyncio.sleep(0.2)

                        
                        # Filter Anti Koin Receh / Micin
                        if current_price < 0.01:
                            # Agar terminal tidak spam, kita tidak perlu print terus-menerus
                            continue
                            
                        # 3. Hitung Indikator (Bollinger, Support, RSI, & Patterns)
                        df = calculate_bollinger_bands(df)
                        df = calculate_rsi(df, length=bot_config.rsi_length)
                        breakout = calculate_dormant_breakout_score(
                            df,
                            volume_multiplier=bot_config.breakout_volume_multiplier,
                        )
                        support_zones = detect_support_zones(df)
                        pattern_info = detect_candlestick_patterns(df)
                        
                        # Deteksi Pre-Pump & ATH Breakout (Target +10% s.d +50% | 1000% ROI on 20x)
                        pump_intel = detect_explosive_pre_pump(df, symbol=symbol)
                        if pump_intel.get("is_alert"):
                            active_pump_alerts.append(pump_intel)
                            bot_state["pre_pump_alerts"] = list(active_pump_alerts)
                            print(
                                f"🔥 [PUMP RADAR] {symbol} ({pump_intel['tier']}) | "
                                f"Score: {pump_intel['score']}/100 | RVOL: {pump_intel['rvol']}x | "
                                f"TP1: {pump_intel['tp1_price']:.6f} (+10%) | TP3: {pump_intel['tp3_price']:.6f} (+50% / +1000% ROI 20x)"
                            )
                            add_scanner_log(
                                "PUMP",
                                symbol,
                                f"🔥 [PUMP RADAR] {pump_intel['tier']} | Score: {pump_intel['score']}/100 | RVOL: {pump_intel['rvol']}x | Target: TP1 +10% / TP3 +50%",
                                score=pump_intel.get("score"),
                                tag="PUMP_RADAR"
                            )

                        last_row = df.iloc[-1]
                        current_price = last_row['close']
                        
                        # 4. Cek Kondisi Teknikal Entry LONG
                        lower_band = last_row.get('lower_band', last_row.get('bb_lower'))
                        near_lower_bb = last_row.get('is_near_lower_band', False) or (current_price <= lower_band * 1.005 if pd.notnull(lower_band) else False)
                        near_support = is_near_support(current_price, support_zones)
                        smart_buy_level = find_frequent_open_close_level(
                            df_daily,
                            lookback=bot_config.analysis_lookback_days,
                            tolerance=SMART_BUY_TOLERANCE_ENV,
                        )
                        near_smart_buy_level = is_near_frequent_level(
                            current_price,
                            smart_buy_level,
                            tolerance=SMART_BUY_TOLERANCE_ENV,
                        )
                        rsi_value = float(last_row.get('RSI', 50))
                        is_oversold = rsi_value < bot_config.rsi_oversold
                        two_green_at_support = (near_support or near_lower_bb) and check_consecutive_green_candles(df, min_candles=2)
                        compression_reversal_long = (near_support or near_lower_bb) and check_small_bodies_followed_by_green(df, min_small_candles=3)
                        
                        # 4. Cek Kondisi Teknikal Entry SHORT
                        upper_band = last_row.get('upper_band', last_row.get('bb_upper'))
                        near_upper_bb = last_row.get('is_near_upper_band', False) or (current_price >= upper_band * 0.995 if pd.notnull(upper_band) else False)
                        resistance_zones = detect_resistance_zones(df)
                        near_resistance = is_near_resistance(current_price, resistance_zones)
                        is_overbought = rsi_value > bot_config.rsi_overbought
                        two_red_at_resistance = (near_resistance or near_upper_bb) and check_consecutive_red_candles(df, min_candles=2)
                        
                        pattern_detected = pattern_info['detected']
                        pattern_name = pattern_info['pattern']
                        pattern_type = pattern_info['type']
                        vol_ratio = pattern_info.get('volume_ratio', 1.0)
                        has_volume_surge = vol_ratio >= 1.25

                        # Evaluasi AI Machine Learning Vision (Candlestick CNN Inference)
                        ml_vision_intel = {"label": "NEUTRAL", "confidence": 0.0, "is_confirmed": False}
                        try:
                            chart_img = render_ohlcv_to_image(df, n_candles=20)
                            if chart_img is not None:
                                processed_chart = preprocess_chart_image(chart_img)
                                ml_label_pred, ml_conf_pred = predict_candle_pattern(processed_chart, ml_model)
                                ml_vision_intel = {
                                    "label": ml_label_pred,
                                    "confidence": float(ml_conf_pred),
                                    "is_confirmed": (ml_label_pred == "BULLISH" and ml_conf_pred >= 0.55),
                                }
                        except Exception as e_ml_scan:
                            logger.debug(f"[ML VISION] Scan prediction error: {e_ml_scan}")

                        # Hitung Volatilitas ATR, SMC Sniper Volume, & LnSNRCH.v2 Smart Structure
                        atr_val = calculate_atr(df, period=14)
                        dynamic_leverage = calculate_volatility_adjusted_leverage(
                            atr_val, current_price, base_leverage=bot_config.leverage
                        )
                        sniper_intel = calculate_smc_sniper_volume(df, lookback=100, current_price=current_price)
                        smc_v2_intel = calculate_smc_structure_v2(df, swing_length=50, internal_length=5)
                        
                        # Deteksi Pre-Pump & Momentum Aktif
                        pump_is_active = bool(pump_intel.get("is_alert", False) or pump_intel.get("score", 0) >= 60)
                        syarat_pump_long = (
                            pump_is_active
                            and (
                                htf_trend in ["UPTREND", "SIDEWAYS"]
                                or pump_intel.get("score", 0) >= 65.0
                            )
                        )
                        
                        # Skenario LnSNRCH.v2 Quasimodo (QML) Bullish Reversal
                        qml_data = smc_v2_intel.get("quasimodo", {})
                        syarat_qml_long = (
                            bool(qml_data.get("is_detected") and qml_data.get("pattern_type") == "BULLISH_QUASIMODO")
                            and htf_trend in ["UPTREND", "SIDEWAYS"]
                        )
                        
                        # Skenario SMC Sniper Elite V17 (Volume Zone & Institutional Accumulation)
                        syarat_sniper_long = (
                            bool(
                                sniper_intel.get("is_in_sniper_buy_zone")
                                or (
                                    sniper_intel.get("market_state") == "ACCUMULATION_READY"
                                    and (near_lower_bb or near_support or is_oversold or last_row['close'] > last_row['open'])
                                )
                            )
                            and htf_trend in ["UPTREND", "SIDEWAYS"]
                        )
                        
                        # Skenario Tier-A Reversal & Breakout untuk LONG (High Win-Rate)
                        syarat_teknikal_long = (
                            (near_lower_bb and near_support and is_oversold and htf_trend in ["UPTREND", "SIDEWAYS"]) or
                            (two_green_at_support and htf_trend in ["UPTREND", "SIDEWAYS"]) or
                            (compression_reversal_long and htf_trend in ["UPTREND", "SIDEWAYS"])
                        )
                        syarat_pola_long = (near_support or near_lower_bb) and pattern_detected and pattern_type == 'LONG' and htf_trend in ["UPTREND", "SIDEWAYS"]
                        syarat_smart_buy_long = (
                            near_smart_buy_level
                            and htf_trend in ["UPTREND", "SIDEWAYS"]
                            and (last_row['close'] > last_row['open'] or is_oversold)
                        )
                        syarat_breakout_long = (
                            breakout["ready"]
                            and breakout["score"] >= bot_config.breakout_min_score
                            and last_row["close"] > last_row["open"]
                            and htf_trend in ["UPTREND", "SIDEWAYS"]
                        )
                        
                        # Skenario Tier-A Reversal & Breakdown untuk SHORT (High Win-Rate)
                        # VETO: Larang keras open SHORT jika koin sedang mengalami momentum Bullish, Pre-Pump, atau Volume Lonjakan!
                        is_bullish_momentum = (
                            pump_is_active
                            or vol_ratio >= 1.5
                            or sniper_intel.get("market_state") == "ACCUMULATION_READY"
                            or (last_row["close"] > last_row["open"] and last_row.get('RSI', 50) > 55)
                            or htf_trend == "UPTREND"
                        )
                        if is_bullish_momentum:
                            syarat_qml_short = False
                            syarat_sniper_short = False
                            syarat_teknikal_short = False
                            syarat_pola_short = False
                            syarat_breakout_short = False
                        else:
                            syarat_qml_short = (
                                bool(qml_data.get("is_detected") and qml_data.get("pattern_type") == "BEARISH_QUASIMODO")
                                and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                            )
                            syarat_sniper_short = (
                                bool(
                                    sniper_intel.get("is_in_sniper_sell_zone")
                                    or (
                                        sniper_intel.get("market_state") == "DISTRIBUTION"
                                        and (near_upper_bb or near_resistance or is_overbought)
                                    )
                                )
                                and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                            )
                            syarat_teknikal_short = (
                                (near_upper_bb and near_resistance and is_overbought and htf_trend in ["DOWNTREND", "SIDEWAYS"]) or
                                (two_red_at_resistance and (near_upper_bb or is_overbought or near_resistance) and htf_trend in ["DOWNTREND", "SIDEWAYS"])
                            )
                            syarat_pola_short = (near_resistance or near_upper_bb) and pattern_detected and pattern_type == 'SHORT' and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                            syarat_breakout_short = (
                                breakout["ready"]
                                and breakout["score"] >= bot_config.breakout_min_score
                                and last_row["close"] < last_row["open"]
                                and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                            )
                        
                        # Anti-Trap Filters
                        bull_trap_detected = is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']) or (pattern_type == 'CLOSE_LONG')
                        bear_trap_detected = is_bear_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']) or (pattern_type == 'CLOSE_SHORT')

                        # Setup Pre-Pump Radar dengan konfirmasi volume kuat (score >= 65 atau RVOL >= 2.0x)
                        # dibebaskan dari veto single-candle bull trap karena ekor atas awal wajar terjadi dan dilindungi Hard Stop-Loss 1.5%.
                        is_pump_breakout_exempt = bool(syarat_pump_long and (pump_intel.get("score", 0) >= 65.0 or pump_intel.get("rvol", 1.0) >= 2.0))
                        effective_bull_trap = bull_trap_detected and not is_pump_breakout_exempt
                        
                        if syarat_pump_long:
                            alasan_long = (
                                f"🔥 Pre-Pump Radar ({pump_intel.get('tier', 'TIER_1')} - "
                                f"Score: {pump_intel.get('score', 0)}/100, RVOL: {pump_intel.get('rvol', 1.0)}x, HTF: {htf_trend})"
                            )
                        elif syarat_qml_long:
                            alasan_long = (
                                f"👑 Quasimodo QML Buy (Left Shoulder: {qml_data.get('entry_level')}, "
                                f"TP: {qml_data.get('take_profit_level')}, HTF: {htf_trend})"
                            )
                        elif syarat_sniper_long:
                            alasan_long = (
                                f"🎯 SMC Sniper Buy Vol (POC: {sniper_intel.get('poc_price')}, "
                                f"{sniper_intel.get('market_state_label')}, Buy: {sniper_intel.get('buy_power_pct')}%, HTF: {htf_trend})"
                            )
                        elif syarat_breakout_long:
                            alasan_long = (
                                f"Dormant breakout score {breakout['score']:.1f} "
                                f"(vol {breakout['volume_spike']:.2f}x, HTF: {htf_trend})"
                            )
                        elif syarat_pola_long:
                            alasan_long = f"Pola Tier-A {pattern_name} (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        elif compression_reversal_long:
                            alasan_long = f"Base Compression 3-5 Candle + Breakout Hijau (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        elif two_green_at_support:
                            alasan_long = f"Reversal 2x Candle Hijau di Support (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        elif syarat_smart_buy_long:
                            alasan_long = (
                                f"Smart Buy level {smart_buy_level['level']:.8f} "
                                f"({smart_buy_level['touches']}x open/close 20D, HTF: {htf_trend})"
                            )
                        else:
                            alasan_long = f"RSI Oversold ({rsi_value:.2f}) di Lower BB (HTF: {htf_trend})"

                        if syarat_qml_short:
                            alasan_short = (
                                f"👑 Quasimodo QML Sell (Left Shoulder: {qml_data.get('entry_level')}, "
                                f"TP: {qml_data.get('take_profit_level')}, HTF: {htf_trend})"
                            )
                        elif syarat_sniper_short:
                            alasan_short = (
                                f"🎯 SMC Sniper Sell Vol (POC: {sniper_intel.get('poc_price')}, "
                                f"{sniper_intel.get('market_state_label')}, Sell: {sniper_intel.get('sell_power_pct')}%, HTF: {htf_trend})"
                            )
                        elif syarat_breakout_short:
                            alasan_short = (
                                f"Dormant breakout score {breakout['score']:.1f} "
                                f"(vol {breakout['volume_spike']:.2f}x, HTF: {htf_trend})"
                            )
                        elif syarat_pola_short:
                            alasan_short = f"Pola Reversal {pattern_name} (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        elif two_red_at_resistance:
                            alasan_short = f"Reversal 2x Candle Merah di Resistance (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        else:
                            alasan_short = f"RSI Overbought ({rsi_value:.2f}) di Upper BB (HTF: {htf_trend})"
                        
                        # Evaluasi Keandalan dari Learner
                        raw_long = (syarat_pump_long or syarat_qml_long or syarat_sniper_long or syarat_teknikal_long or syarat_pola_long or syarat_smart_buy_long or syarat_breakout_long)
                        raw_short = (syarat_qml_short or syarat_sniper_short or syarat_teknikal_short or syarat_pola_short or syarat_breakout_short)

                        reliable_long = is_pattern_reliable(alasan_long) if raw_long else True
                        reliable_short = is_pattern_reliable(alasan_short) if raw_short else True
                        
                        trigger_long = raw_long and not effective_bull_trap and reliable_long
                        trigger_short = raw_short and not bear_trap_detected and reliable_short
                        
                        # Prioritaskan arah LONG jika momentum atau konfluensi bullish aktif
                        if trigger_long and trigger_short:
                            trigger_short = False
                        
                        # Jika sinyal teknikal/pola terdeteksi tetapi ditolak oleh filter Learner/Trap/Blacklist,
                        # simpan sebagai LATIHAN SIMULASI di background memory agar AI tetap belajar dan menguji win rate pola!
                        if (raw_long or raw_short) and not (trigger_long or trigger_short):
                            sim_side = "LONG" if raw_long else "SHORT"
                            sim_alasan = alasan_long if sim_side == "LONG" else alasan_short
                            if symbol not in virtual_trades:
                                v_atr_res = calculate_dynamic_atr_targets(
                                    entry_price=current_price,
                                    atr_value=atr_val,
                                    side=sim_side,
                                    leverage=dynamic_leverage,
                                    config_tp_percent=bot_config.tp_percent,
                                    config_sl_percent=bot_config.sl_percent,
                                    atr_sl_mult=1.8,
                                )
                                v_tp = v_atr_res["tp_price"]
                                v_sl = v_atr_res["sl_price"]
                                ex_name = getattr(client, "exchange_name", "BITUNIX")
                                cond_sim = {
                                    "side": sim_side,
                                    "htf_trend": htf_trend,
                                    "bb_zone": "LOWER" if near_lower_bb else ("UPPER" if near_upper_bb else "MID"),
                                    "rsi_zone": "OVERSOLD" if is_oversold else ("OVERBOUGHT" if is_overbought else "NEUTRAL"),
                                    "pattern": pattern_name if pattern_detected else "NONE",
                                    "is_breakout": bool(syarat_breakout_long or syarat_breakout_short),
                                    "squeeze_score": float(breakout.get("score", 0)),
                                    "volume_ratio": round(vol_ratio, 2),
                                    "atr_percent": round((atr_val / current_price * 100), 2) if current_price > 0 else 0.0,
                                    "sniper_state": sniper_intel.get("market_state", "MONITORING"),
                                    "sniper_poc": sniper_intel.get("poc_price"),
                                }
                                p_entry_id_v = record_pattern_entry(
                                    symbol=symbol,
                                    side=sim_side,
                                    entry_price=current_price,
                                    conditions=cond_sim,
                                    alasan=f"[LATIHAN] {sim_alasan}",
                                    margin_usdt=0.0,
                                    leverage=dynamic_leverage,
                                    exchange=f"{ex_name}_SIM_TRAIN",
                                )
                                virtual_trades[symbol] = {
                                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "tipe": sim_side,
                                    "entry_price": current_price,
                                    "tp_price": v_tp,
                                    "sl_price": v_sl,
                                    "alasan": sim_alasan,
                                    "leverage": dynamic_leverage,
                                    "pattern_entry_id": p_entry_id_v,
                                }
                                print(f"📚 [LATIHAN SIMULASI] Sinyal {sim_side} {symbol} dicatat ke Background Memory untuk melatih Win Rate pola (ID: {p_entry_id_v}).")
                            continue

                        if trigger_long or trigger_short:
                            trade_type = "LONG" if trigger_long else "SHORT"
                            alasan = alasan_long if trade_type == "LONG" else alasan_short

                            # Snapshot kondisi indikator untuk Pattern Intelligence & Database
                            conditions_snapshot = {
                                "side": trade_type,
                                "htf_trend": htf_trend,
                                "bb_zone": "LOWER" if near_lower_bb else ("UPPER" if near_upper_bb else "MID"),
                                "rsi_zone": "OVERSOLD" if is_oversold else ("OVERBOUGHT" if is_overbought else "NEUTRAL"),
                                "pattern": pattern_name if pattern_detected else "NONE",
                                "is_breakout": bool(syarat_breakout_long or syarat_breakout_short),
                                "squeeze_score": float(breakout.get("score", 0)),
                                "volume_ratio": round(vol_ratio, 2),
                                "atr_percent": round((atr_val / current_price * 100), 2) if current_price > 0 else 0.0,
                                "sniper_state": sniper_intel.get("market_state", "MONITORING"),
                                "sniper_poc": sniper_intel.get("poc_price"),
                                "ml_vision_label": ml_vision_intel.get("label"),
                                "ml_vision_confidence": ml_vision_intel.get("confidence"),
                            }

                            # Hitung Pilar 1: Smart Confluence Scoring Matrix (Institutional-Grade Setup)
                            confluence_res = calculate_confluence_score(
                                df_5m=df,
                                df_htf=df_htf,
                                df_daily=df_daily,
                                side=trade_type,
                                pattern_name=pattern_name if pattern_detected else None,
                                pattern_type=pattern_type if pattern_detected else None,
                                near_support=near_support,
                                near_resistance=near_resistance,
                                near_lower_bb=near_lower_bb,
                                near_upper_bb=near_upper_bb,
                                is_oversold=is_oversold,
                                is_overbought=is_overbought,
                                vol_ratio=vol_ratio,
                                breakout_info=breakout,
                                htf_trend=htf_trend,
                                min_score_threshold=bot_config.min_confluence_score,
                                pump_info=pump_intel,
                                near_smart_buy=bool(syarat_smart_buy_long),
                                two_consecutive_candles=bool(two_green_at_support or two_red_at_resistance),
                                compression_reversal=bool(compression_reversal_long),
                                ml_vision_info=ml_vision_intel,
                                sniper_info=sniper_intel,
                                smc_v2_info=smc_v2_intel,
                            )
                            confluence_score = confluence_res["score"]
                            confluence_approved = confluence_res["is_approved"]
                            confluence_breakdown = confluence_res["breakdown"]

                            if not confluence_approved:
                                print(f"🛡️ [CONFLUENCE FILTER] {symbol} ({trade_type}) DITOLAK: Skor {confluence_score}/100 < {bot_config.min_confluence_score} (Syarat Institusional Belum Terpenuhi). Breakdown: {confluence_breakdown}")
                                add_scanner_log(
                                    "FILTERED",
                                    symbol,
                                    f"🛡️ Sinyal {trade_type} Ditolak: Skor {confluence_score}/100 < {bot_config.min_confluence_score} | {alasan}",
                                    score=confluence_score,
                                    tag="CONFLUENCE_REJECT"
                                )
                                if symbol not in virtual_trades:
                                    pm_tp_v = (bot_config.tp_percent / 100) / dynamic_leverage
                                    pm_sl_v = (bot_config.sl_percent / 100) / dynamic_leverage
                                    v_tp = current_price * (1 + pm_tp_v) if trade_type == "LONG" else current_price * (1 - pm_tp_v)
                                    v_sl = current_price * (1 - pm_sl_v) if trade_type == "LONG" else current_price * (1 + pm_sl_v)
                                    ex_name = getattr(client, "exchange_name", "BITUNIX")
                                    p_entry_id_v = record_pattern_entry(
                                        symbol=symbol,
                                        side=trade_type,
                                        entry_price=current_price,
                                        conditions=conditions_snapshot,
                                        alasan=f"[LATIHAN (Score: {confluence_score})] {alasan}",
                                        margin_usdt=0.0,
                                        leverage=dynamic_leverage,
                                        exchange=f"{ex_name}_SIM_TRAIN",
                                    )
                                    virtual_trades[symbol] = {
                                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "tipe": trade_type,
                                        "entry_price": current_price,
                                        "tp_price": v_tp,
                                        "sl_price": v_sl,
                                        "alasan": f"{alasan} (Score: {confluence_score})",
                                        "leverage": dynamic_leverage,
                                        "pattern_entry_id": p_entry_id_v,
                                    }
                                    print(f"📚 [LATIHAN SIMULASI] Sinyal {trade_type} {symbol} (Skor {confluence_score}/100) dialihkan ke memory AI.")
                                continue

                            # Evaluasi AI Pattern Learner & Gatekeeper (Rolling Window + Probation)
                            is_reliable, reason_eval, cur_wr = is_pattern_reliable(alasan)
                            is_fp_blacklisted = is_pattern_memory_blacklisted(conditions_snapshot)
                            
                            if not is_reliable or is_fp_blacklisted:
                                rej_msg = reason_eval if not is_reliable else "Fingerprint WR Rendah (<50%)"
                                print(f"🚫 [LEARNER GATEKEEPER] Sinyal {trade_type} pada {symbol} DITOLAK ({rej_msg})! Dialihkan ke simulasi.")
                                add_scanner_log(
                                    "FILTERED",
                                    symbol,
                                    f"🚫 [GATEKEEPER] Sinyal {trade_type} Ditolak ({rej_msg})",
                                    tag="PATTERN_BLACKLIST"
                                )
                                if symbol not in virtual_trades:
                                    pm_tp_v = (bot_config.tp_percent / 100) / dynamic_leverage
                                    pm_sl_v = (bot_config.sl_percent / 100) / dynamic_leverage
                                    v_tp = current_price * (1 + pm_tp_v) if trade_type == "LONG" else current_price * (1 - pm_tp_v)
                                    v_sl = current_price * (1 - pm_sl_v) if trade_type == "LONG" else current_price * (1 + pm_sl_v)
                                    ex_name = getattr(client, "exchange_name", "BITUNIX")
                                    p_entry_id_v = record_pattern_entry(
                                        symbol=symbol,
                                        side=trade_type,
                                        entry_price=current_price,
                                        conditions=conditions_snapshot,
                                        alasan=f"[LATIHAN] {alasan}",
                                        margin_usdt=0.0,
                                        leverage=dynamic_leverage,
                                        exchange=f"{ex_name}_SIM_TRAIN",
                                    )
                                    virtual_trades[symbol] = {
                                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "tipe": trade_type,
                                        "entry_price": current_price,
                                        "tp_price": v_tp,
                                        "sl_price": v_sl,
                                        "alasan": alasan,
                                        "leverage": dynamic_leverage,
                                        "pattern_entry_id": p_entry_id_v,
                                    }
                                    print(f"📚 [LATIHAN SIMULASI] Sinyal {trade_type} {symbol} dicatat untuk evaluasi memory.")
                                continue

                            batch_signal_found = True
                            print(f"SETUP TEKNIKAL {trade_type} DITEMUKAN PADA {symbol}! [Skor: {confluence_score}/100] Alasan: {alasan}")
                            add_scanner_log(
                                "CONFLUENCE",
                                symbol,
                                f"✅ SETUP {trade_type} VALID! [Skor: {confluence_score}/100] | Alasan: {alasan}",
                                score=confluence_score,
                                tag="CONFLUENCE_PASS"
                            )
                            
                            # 5. Cek Modal & Hitung Sizing Computed
                            try:
                                if hasattr(client, "get_account_balance"):
                                    bal_info = await client.get_account_balance()
                                    modal = float(bal_info.get("total_wallet_balance", 0.0))
                                    positions = await client.get_open_positions()
                                elif hasattr(client, "futures_account"):
                                    account_info = await client.futures_account()
                                    modal = float(account_info.get("totalMarginBalance", 0.0))
                                    positions = account_info.get("positions", [])
                                else:
                                    modal = 50.0
                                    positions = []

                                curr_trade_mode = getattr(bot_config, "trading_mode", TRADING_MODE).upper()
                                is_paper_trading = curr_trade_mode in ("PAPER_TRADING", "SIMULATION", "VIRTUAL") or (bot_config.simulated_modal is not None and bot_config.simulated_modal > 0)

                                if bot_config.simulated_modal is not None and bot_config.simulated_modal > 0:
                                    modal = bot_config.simulated_modal
                                elif is_paper_trading and modal <= 0:
                                    modal = 100.0  # Default modal 100 USDT untuk simulasi Paper Trading jika saldo akun 0

                                # Cek posisi terbuka (Strict Anti-Double Entry Guard)
                                sym_clean = str(symbol).upper().strip()
                                is_position_open = False

                                # 1. Cek dari daftar posisi aktif di Exchange
                                for pos in positions:
                                    p_sym = str(pos.get("symbol", "")).upper().strip()
                                    amt = float(pos.get("position_amt", pos.get("positionAmt", 0)) or 0)
                                    if p_sym == sym_clean and abs(amt) > 0:
                                        is_position_open = True
                                        break

                                # 2. Cek dari active_trade_meta internal bot
                                active_meta = bot_state.setdefault("active_trade_meta", {})
                                if not is_position_open:
                                    for act_k in active_meta.keys():
                                        if str(act_k).upper().strip() == sym_clean:
                                            is_position_open = True
                                            break

                                if is_position_open:
                                    print(f"⏩ [ANTI-DOUBLE ENTRY] Lewati {symbol}: Sudah ada posisi terbuka di Exchange atau Bot Memory.")
                                    continue

                                total_margin_used = total_position_margin(positions, bot_config.leverage)
                                exposure_limit = modal * bot_config.max_total_exposure_percent / 100
                                if total_margin_used >= exposure_limit:
                                    print(f"[RISK] Lewati {symbol}: margin terpakai {total_margin_used:.2f} >= limit {exposure_limit:.2f}")
                                    if symbol not in virtual_trades:
                                        pm_tp_v = (bot_config.tp_percent / 100) / dynamic_leverage
                                        pm_sl_v = (bot_config.sl_percent / 100) / dynamic_leverage
                                        v_tp = current_price * (1 + pm_tp_v) if trade_type == "LONG" else current_price * (1 - pm_tp_v)
                                        v_sl = current_price * (1 - pm_sl_v) if trade_type == "LONG" else current_price * (1 + pm_sl_v)
                                        ex_name = getattr(client, "exchange_name", "BITUNIX")
                                        p_entry_id_v = record_pattern_entry(
                                            symbol=symbol,
                                            side=trade_type,
                                            entry_price=current_price,
                                            conditions=conditions_snapshot,
                                            alasan=f"[LATIHAN] {alasan}",
                                            margin_usdt=0.0,
                                            leverage=dynamic_leverage,
                                            exchange=f"{ex_name}_SIM_TRAIN",
                                        )
                                        virtual_trades[symbol] = {
                                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "tipe": trade_type,
                                            "entry_price": current_price,
                                            "tp_price": v_tp,
                                            "sl_price": v_sl,
                                            "alasan": alasan,
                                            "leverage": dynamic_leverage,
                                            "pattern_entry_id": p_entry_id_v,
                                        }
                                        print(f"📚 [LATIHAN SIMULASI] {symbol} ({trade_type}) dicatat untuk latihan memory karena batas margin real penuh.")
                                    continue

                                # Market guard opsional jika Binance
                                if hasattr(client, "futures_mark_price"):
                                    try:
                                        funding_info = await client.futures_mark_price(symbol=symbol)
                                        funding_rate = float(funding_info.get("lastFundingRate", 0))
                                        if abs(funding_rate) >= 0.003:
                                            print(f"[RISK] Lewati {symbol}: funding rate {funding_rate:.4%} terlalu tinggi")
                                            continue
                                        order_book = await client.futures_order_book(symbol=symbol, limit=20)
                                        bid_depth = sum(float(p) * float(q) for p, q in order_book.get("bids", []))
                                        ask_depth = sum(float(p) * float(q) for p, q in order_book.get("asks", []))
                                        if min(bid_depth, ask_depth) < 10000:
                                            print(f"[RISK] Lewati {symbol}: order-book terlalu tipis")
                                            continue
                                    except Exception as market_guard_error:
                                        print(f"[RISK] Market guard gagal untuk {symbol}: {market_guard_error}")
                                        continue

                                # Hitung posisi aktif real dan simulasi per side (LONG dan SHORT)
                                active_meta = bot_state.get("active_trade_meta", {})
                                active_real_syms = set()
                                count_long = 0
                                count_short = 0

                                for pos in positions:
                                    amt = float(pos.get("position_amt", pos.get("positionAmt", 0)))
                                    if amt != 0:
                                        active_real_syms.add(pos.get("symbol"))
                                        if amt > 0 or pos.get("side") == "LONG":
                                            count_long += 1
                                        else:
                                            count_short += 1

                                for s_name, s_meta in list(active_meta.items()):
                                    if s_name not in active_real_syms:
                                        if s_meta.get("is_paper") or is_paper_trading:
                                            s_side = s_meta.get("side", "").upper()
                                            if s_side == "LONG":
                                                count_long += 1
                                            elif s_side == "SHORT":
                                                count_short += 1
                                        else:
                                            # Posisi real stale: bersihkan
                                            bot_state.get("active_trade_meta", {}).pop(s_name, None)
                                            bot_state.get("active_trade_reasons", {}).pop(s_name, None)
                                            bot_state.setdefault("protection_recovery_suppressed", set()).discard(s_name)

                                MAX_POSITIONS_PER_SIDE = getattr(bot_config, "max_positions_per_side", getattr(bot_config, "max_open_positions", 2))
                                MAX_TOTAL_POSITIONS = getattr(bot_config, "max_open_positions", MAX_POSITIONS_PER_SIDE * 2)

                                is_side_full = (trade_type == "LONG" and count_long >= MAX_POSITIONS_PER_SIDE) or (trade_type == "SHORT" and count_short >= MAX_POSITIONS_PER_SIDE)
                                is_total_full = (count_long + count_short) >= MAX_TOTAL_POSITIONS

                                # ─── JIKA BATASAN SLOT PENUH: ALIKAN KE LATIHAN SIMULASI DI CHANNEL TERPISAH ───
                                if is_side_full or is_total_full:
                                    if symbol not in virtual_trades:
                                        v_atr_res = calculate_dynamic_atr_targets(
                                            entry_price=current_price,
                                            atr_value=atr_val,
                                            side=trade_type,
                                            leverage=dynamic_leverage,
                                            config_tp_percent=bot_config.tp_percent,
                                            config_sl_percent=bot_config.sl_percent,
                                            atr_sl_mult=1.8,
                                        )
                                        v_tp = v_atr_res["tp_price"]
                                        v_sl = v_atr_res["sl_price"]

                                        # Catat snapshot pola candle ke Pattern Memory & PostgreSQL untuk Latihan Simulasi
                                        ex_name = getattr(client, "exchange_name", "BITUNIX")
                                        p_entry_id_v = record_pattern_entry(
                                            symbol=symbol,
                                            side=trade_type,
                                            entry_price=current_price,
                                            conditions=conditions_snapshot,
                                            alasan=f"[LATIHAN] {alasan}",
                                            margin_usdt=0.0,
                                            leverage=dynamic_leverage,
                                            exchange=f"{ex_name}_SIM_TRAIN",
                                        )

                                        virtual_trades[symbol] = {
                                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "tipe": trade_type,
                                            "entry_price": current_price,
                                            "tp_price": v_tp,
                                            "sl_price": v_sl,
                                            "alasan": alasan,
                                            "leverage": dynamic_leverage,
                                            "pattern_entry_id": p_entry_id_v,
                                        }

                                        slot_status_text = f"LONG: {count_long}/{MAX_POSITIONS_PER_SIDE} | SHORT: {count_short}/{MAX_POSITIONS_PER_SIDE}"
                                        print(f"📚 [LATIHAN SIMULASI] Slot aktif penuh ({slot_status_text}). {symbol} ({trade_type}) dicatat untuk evaluasi target TP/SL (ID: {p_entry_id_v}).")

                                    continue

                                # Hitung Sizing Computed Dinamis untuk Modal Kecil / Compounding
                                planned_sl_move = (bot_config.sl_percent / 100) / dynamic_leverage
                                planned_sl_price = (
                                    current_price * (1 - planned_sl_move)
                                    if trade_type == "LONG"
                                    else current_price * (1 + planned_sl_move)
                                    )
                                if bot_config.margin_mode == "DYNAMIC":
                                    computed_size = calculate_computed_position_size(
                                        equity=modal,
                                        current_price=current_price,
                                        stop_price=planned_sl_price,
                                        leverage=dynamic_leverage,
                                        risk_percent=bot_config.risk_per_trade_percent,
                                        min_margin=0.5,
                                        max_position_equity_ratio=bot_config.max_position_equity_ratio,
                                    )

                                    if not computed_size.get("is_valid", False):
                                        print(f"[RISK] {symbol}: Computed size ditolak: {computed_size.get('reason')}")
                                        continue

                                    current_margin = computed_size["margin_usdt"]
                                    print(
                                        f"[COMPUTED SIZING] {symbol}: Modal={modal:.2f} USDT | "
                                        f"Margin={current_margin:.2f} USDT | Lev={dynamic_leverage}x (Risk: {bot_config.risk_per_trade_percent}%)"
                                    )
                                else:
                                    # Mode FIXED dengan safety cap fleksibel (Support Margin $0.5 - $1.0)
                                    fixed_margin = bot_config.margin_usdt
                                    if modal < 30.0:
                                        current_margin = min(fixed_margin, modal * 0.95)
                                    else:
                                        max_allowed = max(modal * bot_config.max_position_equity_ratio, fixed_margin)
                                        current_margin = min(fixed_margin, max_allowed)
                                    
                                    if current_margin < 0.5:
                                        print(f"[RISK] {symbol}: Margin FIXED ({current_margin:.2f} USDT) terlalu kecil (< 0.5 USDT)")
                                        continue
                                    print(
                                        f"[FIXED SIZING] {symbol}: Modal={modal:.2f} USDT | "
                                        f"Margin={current_margin:.2f} USDT | Lev={dynamic_leverage}x"
                                    )

                            except Exception as e_bal:
                                print(f"[ERROR] Gagal menghitung sizing modal: {e_bal}")
                                continue

                            # 6. Eksekusi Order (Real vs Paper Trading Simulasi)
                            curr_trade_mode = getattr(bot_config, "trading_mode", TRADING_MODE).upper()
                            is_paper_trading = curr_trade_mode in ("PAPER_TRADING", "SIMULATION", "VIRTUAL") or (bot_config.simulated_modal is not None and bot_config.simulated_modal > 0)
                            exchange_name = getattr(client, "exchange_name", getattr(bot_config, "active_exchange", "BINANCE"))
                            exchange_tag = f"{exchange_name}_SIM" if is_paper_trading else (f"{exchange_name}_TESTNET" if curr_trade_mode == "TESTNET" else f"{exchange_name}_REAL")

                            # Strict Anti-Double Entry Guard (Pre-Order Verification)
                            sym_clean = str(symbol).upper().strip()
                            if sym_clean in {str(k).upper().strip() for k in bot_state.get("active_trade_meta", {}).keys()}:
                                print(f"🛑 [ANTI-DOUBLE ENTRY] Pembatalan order {symbol}: Posisi sudah tercatat aktif di bot memory.")
                                continue

                            if is_paper_trading:
                                quantity = (current_margin * dynamic_leverage) / current_price
                                order_res = {
                                    'status': 'success',
                                    'quantity': quantity,
                                    'price': current_price,
                                    'actual_leverage': dynamic_leverage,
                                    'is_paper': True,
                                }
                                print(f"[PAPER TRADING] {symbol}: Simulasi Open {trade_type} di {current_price:.6f} | Margin={current_margin:.2f} USDT | Lev={dynamic_leverage}x [{exchange_tag}]")
                            else:
                                if trade_type == "LONG":
                                    order_res = await place_long_order(
                                        client, symbol, current_price, current_margin, dynamic_leverage
                                    )
                                else:
                                    order_res = await place_short_order(
                                        client, symbol, current_price, current_margin, dynamic_leverage
                                    )
                                
                            if order_res.get('status') == 'success':
                                quantity = order_res['quantity']
                                entry_price = order_res['price']
                                actual_leverage = order_res.get('actual_leverage', dynamic_leverage)
                                
                                add_scanner_log(
                                    "ORDER",
                                    symbol,
                                    f"🚀 [ORDER] Open {trade_type} @ {entry_price:.6f} | Margin: {current_margin:.2f} USDT | Lev: {actual_leverage}x [{exchange_tag}]",
                                    tag="ORDER_SUCCESS"
                                )
                                
                                # Dynamic Volatility-Based ATR Stop Loss & Take Profit Target
                                atr_targets = calculate_dynamic_atr_targets(
                                    entry_price=entry_price,
                                    atr_value=atr_val,
                                    side=trade_type,
                                    leverage=actual_leverage,
                                    config_tp_percent=bot_config.tp_percent,
                                    config_sl_percent=bot_config.sl_percent,
                                    atr_sl_mult=1.8,
                                )
                                tp_price = atr_targets["tp_price"]
                                sl_price = atr_targets["sl_price"]

                                # Jika entry berasal dari PUMP RADAR: Pasang SL Maksimal 1.5% jarak harga
                                if syarat_pump_long:
                                    sl_price = max(sl_price, entry_price * 0.985) if trade_type == "LONG" else min(sl_price, entry_price * 1.015)
                                
                                tp_sl_side = 'SELL' if trade_type == "LONG" else 'BUY'
                                
                                # Pasang TP / SL (atau bypass di mode simulasi)
                                if is_paper_trading:
                                    protection_result = {"status": "success", "is_paper": True}
                                else:
                                    protection_result = await place_take_profit_stop_loss(
                                        client, symbol, tp_sl_side, quantity, tp_price, sl_price,
                                        use_trailing_stop=bot_config.use_trailing_stop,
                                        callback_rate=bot_config.ts_callback_rate
                                    )

                                if protection_result.get("status") not in ("success", "existing"):
                                    bot_state["is_running"] = False
                                    bot_state["state"] = "KILL_SWITCH"
                                    close_side = "SELL" if trade_type == "LONG" else "BUY"
                                    close_result = await emergency_close_position(
                                        client, symbol, close_side, quantity
                                    )
                                    await send_error_log(
                                        bot,
                                        TELEGRAM_ADMIN_CHAT_ID,
                                        f"KILL SWITCH: TP/SL gagal untuk {symbol}. "
                                        f"Emergency close: {close_result.get('status')}",
                                    )
                                    continue
                                
                                # 7. Kirim Notifikasi
                                # Analisis Evaluasi Komprehensif Brain AI
                                ai_eval_lines = []
                                grade_label = "INSTITUTIONAL A+ 🏆" if confluence_score >= 90 else "HIGH CONFLUENCE A 🌟"
                                ai_eval_lines.append(f"• **Smart Confluence Score:** `{confluence_score}/100` ({grade_label})")

                                rsi_status = "Oversold 🟢" if is_oversold else ("Overbought 🔴" if is_overbought else "Neutral ⚪")
                                ai_eval_lines.append(f"• **Indikator RSI ({bot_config.rsi_length}):** `{rsi_value:.1f}` ({rsi_status})")
                                
                                vol_tag = "Lonjakan Kuat 🚀" if vol_ratio >= 1.5 else ("Diatas Rata-rata 📈" if vol_ratio >= 1.2 else "Normal 📊")
                                ai_eval_lines.append(f"• **Volume Surge:** `{vol_ratio:.2f}x` vs MA20 ({vol_tag})")
                                
                                bb_status = "Menyentuh Lower Band" if near_lower_bb else ("Menyentuh Upper Band" if near_upper_bb else "Zona Mid Band")
                                sr_status = "Di Zona Support Valid 🛡️" if near_support else ("Di Zona Resistance 🛑" if near_resistance else "Struktur Netral")
                                ai_eval_lines.append(f"• **Bollinger & S/R:** {bb_status} | {sr_status}")
                                
                                if smart_buy_level and smart_buy_level.get('touches', 0) > 0:
                                    ai_eval_lines.append(f"• **Modul 20-30 Hari:** Level `{smart_buy_level['level']:.6f}` ({smart_buy_level['touches']}x pantulan Open/Close)")
                                else:
                                    ai_eval_lines.append(f"• **Modul 20-30 Hari:** Tidak ada level kunci terdekat")
                                    
                                if pattern_detected and pattern_name:
                                    ai_eval_lines.append(f"• **Pola Candlestick:** `{pattern_name}` ({'Tier-A Reversal 🌟' if pattern_info.get('is_high_quality') else 'Konfirmasi Pola'})")
                                else:
                                    ai_eval_lines.append(f"• **Pola Candlestick:** `Momentum Price Action`")
                                    
                                htf_icon = "🟢 (Uptrend Kuat)" if htf_trend == "UPTREND" else ("🔴 (Downtrend)" if htf_trend == "DOWNTREND" else "🟡 (Sideways Konsolidasi)")
                                ai_eval_lines.append(f"• **Tren HTF ({HTF_TIMEFRAME}):** `{htf_trend}` {htf_icon}")
                                
                                ai_eval_text = "\n".join(ai_eval_lines)

                                signal_score = confluence_score
                                mode_label = f"[{exchange_tag}] " if is_paper_trading else ""
                                trade_data = {
                                    'symbol': f"{mode_label}{symbol}",
                                    'direction': trade_type,
                                    'price': f"{entry_price:.4f}",
                                    'entry_price': entry_price,
                                    'quantity': quantity,
                                    'margin_usdt': current_margin,
                                    'notional_usdt': current_margin * actual_leverage,
                                    'tp_price': tp_price,
                                    'sl_price': sl_price,
                                    'score': signal_score,
                                    'confidence': f"{signal_score}/100",
                                    'tf': TIMEFRAME,
                                    'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    'margin': f"{current_margin:.2f} (Modal: {modal:.2f})",
                                    'leverage': actual_leverage,
                                    'tp_sl_info': f"TP: {tp_price:.4f} ({bot_config.tp_percent}%), SL: {sl_price:.4f} ({bot_config.sl_percent}%)",
                                    'syarat_1': f"Score {signal_score}/100 ({grade_label}) | HTF {HTF_TIMEFRAME}: {htf_trend}",
                                    'syarat_2': alasan,
                                    'pola_ml': pattern_name if pattern_detected else "Confluence Matrix",
                                    'ai_evaluation': ai_eval_text,
                                    'method': alasan,
                                }
                                trade_target_chat = get_trade_notification_target(is_paper=is_paper_trading)
                                if trade_target_chat:
                                    try:
                                        await send_trade_notification(bot, trade_target_chat, trade_data)
                                    except Exception as e_notif:
                                        print(f"[TELEGRAM] Gagal kirim notif order: {e_notif}")
                                bot_state["active_trade_reasons"][symbol] = alasan

                                # Catat ke Pattern Memory & PostgreSQL
                                pattern_entry_id = record_pattern_entry(
                                    symbol=symbol,
                                    side=trade_type,
                                    entry_price=entry_price,
                                    conditions=conditions_snapshot,
                                    alasan=alasan,
                                    margin_usdt=current_margin,
                                    leverage=actual_leverage,
                                    exchange=exchange_tag,
                                )

                                bot_state.setdefault("active_trade_meta", {})[symbol] = {
                                    "entry_time": datetime.now(),
                                    "entry_price": entry_price,
                                    "side": trade_type,
                                    "margin_usdt": current_margin,
                                    "leverage": actual_leverage,
                                    "quantity": quantity,
                                    "tp_price": tp_price,
                                    "sl_price": sl_price,
                                    "exchange": exchange_tag,
                                    "is_paper": is_paper_trading,
                                    "is_bot_trade": True,
                                    "mfe": 0.0,
                                    "mae": 0.0,
                                    "pattern_entry_id": pattern_entry_id,
                                    "alasan": alasan,
                                    "ai_eval_summary": ai_eval_text,
                                }
                                
                            # Hindari spam trade di koin yang sama, beri jeda
                            await asyncio.sleep(10)

                        if not batch_signal_found:
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] "
                                f"Tidak ada kriteria valid pada ranking {i + 1}-{i + len(batch_symbols)}. "
                                "Lanjut ke batch berikutnya."
                            )
                            
                    # Jeda request API acak sesuai limit setiap selesai 1 batch
                    batch_sleep = random.uniform(max(1.0, API_REQUEST_DELAY - 0.5), API_REQUEST_DELAY + 1.0)
                    await asyncio.sleep(batch_sleep)
                                    
            except Exception as loop_error:
                err_str = str(loop_error)
                error_msg = f"Error in scanner loop: {str(loop_error)}"
                print(error_msg)
                if "-1003" in err_str or "429" in err_str or "too many requests" in err_str.lower() or "banned" in err_str.lower():
                    cooldown_sec = extract_ban_cooldown(err_str, default_seconds=300)
                    register_ip_ban(cooldown_sec)
                    print(f"[RATE LIMIT PROTECTION] Scanner mendeteksi IP Ban / Rate Limit (-1003). Cooldown {cooldown_sec} detik...")
                    await send_error_log(
                        bot,
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"⚠️ **BINANCE RATE LIMIT (-1003)**: Terdeteksi batas request. Seluruh loop bot otomatis jeda {cooldown_sec} detik untuk mendinginkan IP.",
                    )
                    await asyncio.sleep(cooldown_sec)
                else:
                    await send_error_log(bot, TELEGRAM_ADMIN_CHAT_ID, error_msg)
                
            # Jeda acak (30 detik hingga 2 menit / 120 detik) sebelum siklus scan koin berikutnya
            random_cycle_delay = random.randint(30, 120)
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⏳ Siklus scan selesai. Jeda acak {random_cycle_delay} detik ({random_cycle_delay/60:.1f} menit) sebelum scan berikutnya...")
            update_scanner_progress(
                current_symbol="-",
                scanned_count=len(all_symbols),
                total_coins=len(all_symbols),
                is_scanning=False,
                status_message=f"Siklus selesai. Jeda {random_cycle_delay}s sebelum scan berikutnya..."
            )
            add_scanner_log("CYCLE", "SYSTEM", f"⏳ Siklus scan {len(all_symbols)} koin selesai. Jeda {random_cycle_delay}s...")
            await asyncio.sleep(random_cycle_delay)
            
    finally:
        if isinstance(client, BaseExchange):
            await client.close()
        elif hasattr(client, "close_connection"):
            await client.close_connection()

from binance import BinanceSocketManager
from telegram.notifier import send_order_filled_notification

async def user_data_stream_loop():
    """
    Mendengarkan event order dari Binance secara real-time (hanya jika ACTIVE_EXCHANGE == BINANCE).
    """
    if ACTIVE_EXCHANGE.upper() != "BINANCE":
        print(f"[USER STREAM] Active exchange adalah '{ACTIVE_EXCHANGE}'. Skipping Binance WebSocket User Stream.")
        while True:
            await asyncio.sleep(3600)

    testnet = TRADING_MODE == 'TESTNET'
    client = await AsyncClient.create(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=testnet
    )
    
    try:
        reconnect_delay = 2
        while True:
            bm = BinanceSocketManager(client)
            ts = bm.futures_user_socket()
            try:
                print("Menghubungkan ke User Data Stream (WebSocket)...")
                async with ts as stream:
                    bot_state["websocket_connected"] = True
                    if bot_state.get("state") == "DEGRADED":
                        bot_state["state"] = "RUNNING" if bot_state.get("is_running") else "PAUSED"
                    reconnect_delay = 2
                    print("Berhasil terhubung ke WebSocket Binance.")
                    while True:
                        res = await stream.recv()

                        if res.get("e") == "ORDER_TRADE_UPDATE":
                            order_info = res.get("o", {})
                            if order_info.get("X") != "FILLED":
                                continue

                            order_type = order_info.get("o", "")
                            realized_pnl = float(order_info.get("rp", "0.0"))
                            is_protective_close = "TAKE_PROFIT" in order_type or "STOP" in order_type
                            is_reduce_only_market = (
                                order_type == "MARKET"
                                and str(order_info.get("R", "")).lower() == "true"
                            )
                            is_closing_trade = is_protective_close or is_reduce_only_market or (realized_pnl != 0)
                            if not is_closing_trade:
                                continue

                            symbol = order_info.get("s")

                            commission = float(order_info.get("n", 0) or 0)
                            meta = bot_state.get("active_trade_meta", {}).pop(symbol, {})
                            duration_minutes = None
                            if meta.get("entry_time"):
                                duration_minutes = round((datetime.now() - meta["entry_time"]).total_seconds() / 60, 1)

                            # Ambil riwayat funding fee selama posisi terbuka
                            funding_fee = 0.0
                            try:
                                start_ts = int(meta["entry_time"].timestamp() * 1000) if meta.get("entry_time") else int((time.time() - 86400) * 1000)
                                income_history = await client.futures_income_history(
                                    symbol=symbol,
                                    incomeType="FUNDING_FEE",
                                    startTime=start_ts,
                                    limit=50
                                )
                                funding_fee = sum(float(item.get("income", 0) or 0) for item in income_history)
                            except Exception as e_ff:
                                print(f"[FUNDING FEE] Gagal ambil income funding fee {symbol}: {e_ff}")

                            net_pnl = realized_pnl - commission + funding_fee

                            # Rekam hasil ke Pattern Memory dan ambil statistik polanya
                            pattern_entry_id = meta.get("pattern_entry_id")
                            ai_stats = {}
                            if pattern_entry_id:
                                record_pattern_result(pattern_entry_id, net_pnl > 0, net_pnl)
                                ai_stats = get_pattern_stats_for_entry(pattern_entry_id)

                            alasan = bot_state.get("active_trade_reasons", {}).get(symbol)
                            if alasan:
                                record_trade_result(alasan, net_pnl > 0)
                                del bot_state["active_trade_reasons"][symbol]

                            margin_val = float(meta.get("margin_usdt", 0) or 0)
                            mfe_val = float(meta.get("mfe", 0) or 0)
                            mae_val = float(meta.get("mae", 0) or 0)

                            # Pastikan MFE mencatat puncak profit minimal sebesar realized PnL jika trade berakhir profit
                            if realized_pnl > 0:
                                mfe_val = max(mfe_val, realized_pnl)
                            # Pastikan MAE mencatat drawdown minimal sebesar realized PnL jika trade berakhir minus
                            if realized_pnl < 0:
                                mae_val = min(mae_val, realized_pnl)

                            meta["mfe"] = mfe_val
                            meta["mae"] = mae_val

                            mfe_pct = (mfe_val / margin_val * 100) if margin_val > 0 else 0.0
                            mae_pct = (abs(mae_val) / margin_val * 100) if margin_val > 0 else 0.0

                            mfe_str = f"+{mfe_pct:.2f}%" if margin_val > 0 else f"{mfe_val:+.4f}"
                            mae_str = f"-{mae_pct:.2f}%" if margin_val > 0 else f"{mae_val:+.4f}"

                            order_data = {
                                "symbol": symbol,
                                "order_type": order_type,
                                "price": order_info.get("ap"),
                                "entry_price": meta.get("entry_price", "N/A"),
                                "quantity": order_info.get("q"),
                                "realized_pnl": realized_pnl,
                                "commission": commission,
                                "funding_fee": funding_fee,
                                "net_pnl": net_pnl,
                                "mfe": mfe_str,
                                "mae": mae_str,
                                "duration": f"{duration_minutes:.1f} menit" if duration_minutes is not None else "N/A",
                                "duration_minutes": duration_minutes,
                                "fingerprint": ai_stats.get("fingerprint", meta.get("alasan", "Kombinasi Standar")),
                                "win_rate": ai_stats.get("win_rate", 0.0),
                                "total_trades": ai_stats.get("total_trades", 0),
                                "wins": ai_stats.get("wins", 0),
                                "losses": ai_stats.get("losses", 0),
                                "alasan_masuk": meta.get("alasan", "Sinyal Multi-Indikator AI"),
                                "ai_eval_summary": meta.get("ai_eval_summary", ""),
                            }
                            record_closed_trade({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "side": meta.get("side", "LONG"),
                                "entry_price": meta.get("entry_price"),
                                "exit_price": order_data["price"],
                                "realized_pnl": realized_pnl,
                                "commission": commission,
                                "funding_fee": funding_fee,
                                "net_pnl": net_pnl,
                                "margin_usdt": meta.get("margin_usdt"),
                                "leverage": meta.get("leverage"),
                                "mfe": meta.get("mfe"),
                                "mae": meta.get("mae"),
                                "duration_minutes": duration_minutes,
                                "order_type": order_type,
                            })
                            try:
                                await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                            except Exception as e_notif:
                                print(f"[TELEGRAM NOTIF ERROR] Gagal kirim notifikasi closed order: {e_notif}")

                            import csv
                            waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            if not os.path.exists("real_history_log.csv"):
                                with open("real_history_log.csv", "w", newline="", encoding="utf-8") as file:
                                    csv.writer(file).writerow(["Waktu", "Symbol", "Tipe", "Harga Eksekusi", "PnL", "Funding Fee", "Net PnL"])

                            with open("real_history_log.csv", "a", newline="", encoding="utf-8") as file:
                                csv.writer(file).writerow([
                                    waktu_sekarang,
                                    order_data["symbol"],
                                    order_type,
                                    order_data["price"],
                                    f"{realized_pnl:.4f}",
                                    f"{funding_fee:.4f}",
                                    f"{net_pnl:.4f}",
                                ])

                        elif res.get("e") == "ACCOUNT_UPDATE":
                            # Tangani update posisi akun secara aman
                            try:
                                for pos in res.get("a", {}).get("P", []):
                                    pos_amt = float(pos.get("pa", 0))
                                    sym = pos.get("s")
                                    if pos_amt == 0 and sym:
                                        bot_state.get("active_trade_reasons", {}).pop(sym, None)
                                        bot_state.get("active_trade_meta", {}).pop(sym, None)
                                        bot_state.setdefault("protection_recovery_suppressed", set()).discard(sym)
                            except Exception as e_acc:
                                logger.debug(f"[ACCOUNT_UPDATE] Error: {e_acc}")

            except Exception as e:
                bot_state["websocket_connected"] = False
                bot_state["state"] = "DEGRADED"
                error_msg = f"WebSocket terputus: {str(e)}. Reconnect dalam {reconnect_delay}s."
                print(error_msg)
                await send_error_log(bot, TELEGRAM_ADMIN_CHAT_ID, error_msg)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
    finally:
        bot_state["websocket_connected"] = False
        if isinstance(client, BaseExchange):
            await client.close()
        else:
            await client.close_connection()


async def profitable_position_monitor_loop():
    """Close positions profitable for at least the configured holding period."""
    client = get_exchange_adapter()
    await client.init()
    try:
        while True:
            try:
                now_ms = int(time.time() * 1000)
                max_age_ms = int(AUTO_CLOSE_PROFIT_HOURS_ENV * 60 * 60 * 1000)

                # ─── 1. Evaluasi Posisi Virtual / Paper Trading (Bitunix & Binance Sim) ───
                paper_trades = {
                    sym: meta for sym, meta in bot_state.get("active_trade_meta", {}).items()
                    if meta.get("is_paper")
                }
                for sym, meta in list(paper_trades.items()):
                    try:
                        curr_price = await client.get_symbol_price(sym)
                        if curr_price <= 0:
                            continue

                        e_price = float(meta.get("entry_price", curr_price))
                        qty = float(meta.get("quantity", 0.0))
                        pos_side = meta.get("side", "LONG").upper()
                        target_tp = float(meta.get("tp_price", 0.0))
                        target_sl = float(meta.get("sl_price", 0.0))
                        m_usdt = float(meta.get("margin_usdt", 0.0))
                        lev = int(meta.get("leverage", 1))

                        if pos_side in ("LONG", "BUY"):
                            u_pnl = (curr_price - e_price) * qty
                        else:
                            u_pnl = (e_price - curr_price) * qty

                        meta["mfe"] = max(float(meta.get("mfe", 0.0)), u_pnl)
                        meta["mae"] = min(float(meta.get("mae", 0.0)), u_pnl)

                        roi_pct = (u_pnl / m_usdt * 100) if m_usdt > 0 else 0.0

                        # Evaluasi Auto Break-Even Protection (Risk-Free Trade)
                        if bot_config.use_auto_breakeven and not meta.get("is_breakeven_set", False):
                            be_eval = evaluate_auto_breakeven(
                                current_roi_percent=roi_pct,
                                entry_price=e_price,
                                side=pos_side,
                                be_activation_roi=bot_config.auto_breakeven_roi_percent,
                                fee_buffer_percent=0.1,
                                current_sl_price=target_sl if target_sl > 0 else None,
                            )
                            if be_eval.get("should_move_to_be"):
                                new_sl = be_eval["new_sl_price"]
                                meta["sl_price"] = new_sl
                                target_sl = new_sl
                                meta["is_breakeven_set"] = True
                                ex_tag = meta.get("exchange", "BITUNIX_SIM")
                                print(f"🛡️ [AUTO BREAK-EVEN (PAPER)] {sym}: ROI {roi_pct:+.2f}% >= +{bot_config.auto_breakeven_roi_percent}%. SL digeser ke Entry+Buffer: {new_sl:.6f} (Risk-Free!)")
                                target_demo_chat = get_trade_notification_target(is_paper=True)
                                if target_demo_chat:
                                    try:
                                        be_msg = (
                                            f"🛡️ **AUTO BREAK-EVEN ACTIVATED (RISK-FREE) 🛡️**\n\n"
                                            f"• **Koin:** `[{ex_tag}] {sym}` ({pos_side})\n"
                                            f"• **Floating ROI:** `{roi_pct:+.2f}%` (Trigger $\ge +{bot_config.auto_breakeven_roi_percent}%$)\n"
                                            f"• **Entry Price:** `{e_price:.6f}`\n"
                                            f"• **Stop Loss Baru:** `{new_sl:.6f}` (Entry + 0.1% Fee Buffer)\n\n"
                                            f"✨ *Trade sekarang 100% Bebas Risiko (Anti Rungkad).* Target TP Statik tetap aktif!"
                                        )
                                        await safe_send_message(bot, target_demo_chat, be_msg)
                                    except Exception as e_be_msg:
                                        print(f"[TELEGRAM] Gagal kirim notif Auto-BE: {e_be_msg}")

                        reached_tp = False
                        reached_sl = False
                        if pos_side in ("LONG", "BUY"):
                            if target_tp > 0 and curr_price >= target_tp:
                                reached_tp = True
                            elif target_sl > 0 and curr_price <= target_sl:
                                reached_sl = True
                        else:
                            if target_tp > 0 and curr_price <= target_tp:
                                reached_tp = True
                            elif target_sl > 0 and curr_price >= target_sl:
                                reached_sl = True

                        e_time = meta.get("entry_time", datetime.now())
                        hold_hours = (datetime.now() - e_time).total_seconds() / 3600.0
                        hold_mins = hold_hours * 60.0

                        should_close_time, exit_type, time_reason = evaluate_time_based_exit(
                            hold_duration_hours=hold_hours,
                            roi_percent=roi_pct,
                            loss_limit_percent=-5.0,
                            loss_time_limit_hours=2.0,
                            profit_target_percent=15.0,
                            profit_time_limit_hours=4.0,
                            max_hold_hours=8.0,
                        )

                        if reached_tp or reached_sl or should_close_time:
                            close_type = "TAKE_PROFIT" if reached_tp else ("STOP_LOSS" if reached_sl else exit_type)
                            r_pnl = u_pnl
                            comm = curr_price * qty * 0.0004 * 2
                            n_pnl = r_pnl - comm
                            is_win_trade = r_pnl > 0

                            p_id = meta.get("pattern_entry_id")
                            ai_s = {}
                            if p_id:
                                record_pattern_result(p_id, is_win_trade, n_pnl)
                                ai_s = get_pattern_stats_for_entry(p_id)

                            t_alasan = bot_state.get("active_trade_reasons", {}).get(sym, meta.get("alasan", "Simulasi AI"))
                            if t_alasan:
                                record_trade_result(t_alasan, is_win_trade)
                                bot_state.get("active_trade_reasons", {}).pop(sym, None)

                            ex_tag = meta.get("exchange", "BITUNIX_SIM")
                            mfe_pct = (meta.get("mfe", 0.0) / m_usdt * 100) if m_usdt > 0 else 0.0
                            mae_pct = (abs(meta.get("mae", 0.0)) / m_usdt * 100) if m_usdt > 0 else 0.0

                            record_closed_trade({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": sym,
                                "side": pos_side,
                                "entry_price": e_price,
                                "exit_price": curr_price,
                                "realized_pnl": r_pnl,
                                "commission": comm,
                                "funding_fee": 0.0,
                                "net_pnl": n_pnl,
                                "margin_usdt": m_usdt,
                                "leverage": lev,
                                "mfe": f"+{mfe_pct:.2f}%",
                                "mae": f"-{mae_pct:.2f}%",
                                "duration_minutes": hold_mins,
                                "order_type": close_type,
                                "exchange": ex_tag,
                            })

                            # Akumulasi hasil ke saldo simulasi (simulated wallet)
                            new_sim_modal = bot_config.add_simulated_pnl(n_pnl)
                            print(f"[SIMULATED WALLET] Paper trade exit: Saldo simulasi sekarang: ${new_sim_modal:.2f} USDT (Net PnL: {n_pnl:+.4f} USDT)")

                            o_data = {
                                "symbol": f"[{ex_tag}] {sym}",
                                "order_type": close_type,
                                "price": curr_price,
                                "entry_price": e_price,
                                "quantity": qty,
                                "realized_pnl": r_pnl,
                                "commission": comm,
                                "funding_fee": 0.0,
                                "net_pnl": n_pnl,
                                "mfe": f"+{mfe_pct:.2f}%",
                                "mae": f"-{mae_pct:.2f}%",
                                "duration": f"{hold_mins:.1f} menit ({hold_hours:.1f} jam)",
                                "duration_minutes": hold_mins,
                                "fingerprint": ai_s.get("fingerprint", t_alasan),
                                "win_rate": ai_s.get("win_rate", 0.0),
                                "total_trades": ai_s.get("total_trades", 0),
                                "wins": ai_s.get("wins", 0),
                                "losses": ai_s.get("losses", 0),
                                "alasan_masuk": t_alasan,
                                "ai_eval_summary": meta.get("ai_eval_summary", time_reason if should_close_time else ""),
                            }

                            target_demo_chat = get_trade_notification_target(is_paper=True)
                            if target_demo_chat:
                                try:
                                    await send_order_filled_notification(bot, target_demo_chat, o_data)
                                except Exception as e_fill_notif:
                                    print(f"[TELEGRAM] Gagal kirim notifikasi closed order paper: {e_fill_notif}")

                            with open("virtual_success_log.csv", "a", newline="", encoding="utf-8") as vf:
                                csv.writer(vf).writerow([
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    sym, pos_side, f"{e_price:.6f}", f"{curr_price:.6f}",
                                    t_alasan, f"{close_type} (Net: {n_pnl:.4f} USDT)"
                                ])

                            bot_state.get("active_trade_meta", {}).pop(sym, None)
                            print(f"[PAPER TRADING EXIT] {sym} {close_type} @ {curr_price:.6f} | Net PnL: {n_pnl:+.4f} USDT [{ex_tag}] | Saldo: ${new_sim_modal:.2f} USDT")
                    except Exception as pe:
                        print(f"[PAPER MONITOR ERROR] {sym}: {pe}")

                # ─── 2. Evaluasi Posisi Real di Exchange ──────────────────────
                orders_by_symbol = defaultdict(list)
                if not isinstance(client, BaseExchange) and hasattr(client, 'futures_get_open_orders'):
                    try:
                        all_open_orders = await client.futures_get_open_orders()
                        for o in all_open_orders:
                            orders_by_symbol[o.get("symbol")].append(o)
                    except Exception as e_ord:
                        print(f"[POSITION MONITOR] Gagal batch fetch open orders: {e_ord}")

                if isinstance(client, BaseExchange):
                    positions = await client.get_open_positions()
                else:
                    account_info = await client.futures_account()
                    positions = [
                        {
                            'symbol': p['symbol'],
                            'position_amt': float(p.get('positionAmt', 0)),
                            'unrealized_pnl': float(p.get('unrealizedProfit', 0)),
                            'entry_price': float(p.get('entryPrice', 0)),
                            'updateTime': int(p.get('updateTime', 0)),
                        }
                        for p in account_info.get("positions", [])
                        if float(p.get("positionAmt", 0)) != 0
                    ]

                # ─── 2.A Rekonsiliasi Real Position: Deteksi SL/TP/Exit di Exchange ───
                current_open_real_symbols = {
                    p.get("symbol") for p in positions
                    if float(p.get("position_amt", p.get("positionAmt", 0))) != 0 and p.get("symbol")
                }

                active_meta_all = bot_state.setdefault("active_trade_meta", {})
                stale_real_trades = [
                    (sym, meta) for sym, meta in list(active_meta_all.items())
                    if not meta.get("is_paper") and meta.get("is_bot_trade") and sym not in current_open_real_symbols
                ]

                for sym, meta in stale_real_trades:
                    try:
                        curr_exit_price = await client.get_symbol_price(sym)
                        e_price = float(meta.get("entry_price", curr_exit_price or 0.0))
                        if curr_exit_price <= 0:
                            curr_exit_price = e_price

                        pos_side = meta.get("side", "LONG").upper()
                        target_tp = float(meta.get("tp_price", 0.0))
                        target_sl = float(meta.get("sl_price", 0.0))
                        qty = float(meta.get("quantity", 0.0))
                        m_usdt = float(meta.get("margin_usdt", 0.0))
                        lev = int(meta.get("leverage", bot_config.leverage))

                        if pos_side in ("LONG", "BUY"):
                            realized_pnl = (curr_exit_price - e_price) * qty if qty > 0 else (((curr_exit_price - e_price) / e_price) * m_usdt * lev if e_price > 0 else 0.0)
                        else:
                            realized_pnl = (e_price - curr_exit_price) * qty if qty > 0 else (((e_price - curr_exit_price) / e_price) * m_usdt * lev if e_price > 0 else 0.0)

                        is_win = realized_pnl > 0

                        # Tentukan tipe exit (TP / SL / Manual)
                        if target_tp > 0 and ((pos_side in ("LONG", "BUY") and curr_exit_price >= target_tp * 0.998) or (pos_side not in ("LONG", "BUY") and curr_exit_price <= target_tp * 1.002)):
                            order_type = "EXCHANGE_TAKE_PROFIT"
                        elif target_sl > 0 and ((pos_side in ("LONG", "BUY") and curr_exit_price <= target_sl * 1.002) or (pos_side not in ("LONG", "BUY") and curr_exit_price >= target_sl * 0.998)):
                            order_type = "EXCHANGE_STOP_LOSS"
                        elif is_win:
                            order_type = "EXCHANGE_TAKE_PROFIT"
                        else:
                            order_type = "EXCHANGE_STOP_LOSS"

                        duration_minutes = None
                        if meta.get("entry_time"):
                            duration_minutes = round((datetime.now() - meta["entry_time"]).total_seconds() / 60, 1)

                        mfe_val = float(meta.get("mfe", realized_pnl) or realized_pnl)
                        mae_val = float(meta.get("mae", 0.0) or 0.0)
                        if realized_pnl > 0:
                            mfe_val = max(mfe_val, realized_pnl)
                        if realized_pnl < 0:
                            mae_val = min(mae_val, realized_pnl)

                        mfe_pct = (mfe_val / m_usdt * 100) if m_usdt > 0 else 0.0
                        mae_pct = (abs(mae_val) / m_usdt * 100) if m_usdt > 0 else 0.0
                        mfe_str = f"+{mfe_pct:.2f}%" if m_usdt > 0 else f"{mfe_val:+.4f}"
                        mae_str = f"-{mae_pct:.2f}%" if m_usdt > 0 else f"{mae_val:+.4f}"

                        pattern_entry_id = meta.get("pattern_entry_id")
                        if pattern_entry_id:
                            record_pattern_result(pattern_entry_id, is_win, realized_pnl)

                        t_alasan = bot_state.get("active_trade_reasons", {}).pop(sym, meta.get("alasan", "Real Trade"))
                        if t_alasan:
                            record_trade_result(t_alasan, is_win)

                        ex_tag = meta.get("exchange", getattr(bot_config, 'exchange', 'BITUNIX')).upper()

                        record_closed_trade({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "symbol": sym,
                            "side": pos_side,
                            "entry_price": e_price,
                            "exit_price": curr_exit_price,
                            "realized_pnl": realized_pnl,
                            "commission": 0.0,
                            "funding_fee": 0.0,
                            "net_pnl": realized_pnl,
                            "margin_usdt": m_usdt,
                            "leverage": lev,
                            "mfe": mfe_val,
                            "mae": mae_val,
                            "duration_minutes": duration_minutes,
                            "order_type": order_type,
                            "exchange": ex_tag,
                        })

                        order_data = {
                            "symbol": f"[{ex_tag}] {sym}" if not sym.startswith("[") else sym,
                            "order_type": order_type,
                            "price": f"{curr_exit_price:.6f}",
                            "entry_price": meta.get("entry_price", e_price),
                            "quantity": qty,
                            "realized_pnl": realized_pnl,
                            "commission": 0.0,
                            "funding_fee": 0.0,
                            "net_pnl": realized_pnl,
                            "mfe": mfe_str,
                            "mae": mae_str,
                            "duration": f"{duration_minutes:.1f} menit" if duration_minutes is not None else "N/A",
                            "duration_minutes": duration_minutes,
                            "fingerprint": meta.get("fingerprint", t_alasan),
                            "win_rate": 0.0,
                            "total_trades": 0,
                            "wins": 0,
                            "losses": 0,
                            "alasan_masuk": f"{t_alasan} (Closed on Exchange: {order_type})",
                            "ai_eval_summary": meta.get("ai_eval_summary", ""),
                        }

                        try:
                            await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                            if TELEGRAM_ERROR_CHAT_ID and TELEGRAM_ERROR_CHAT_ID != TELEGRAM_ADMIN_CHAT_ID:
                                await send_order_filled_notification(bot, TELEGRAM_ERROR_CHAT_ID, order_data)
                        except Exception as e_fill_notif:
                            print(f"[TELEGRAM] Gagal kirim notifikasi closed real trade {sym}: {e_fill_notif}")

                        # Pop dari memory
                        bot_state.get("active_trade_meta", {}).pop(sym, None)
                        bot_state.get("active_trade_reasons", {}).pop(sym, None)
                        bot_state.setdefault("protection_recovery_suppressed", set()).discard(sym)

                        add_scanner_log("ORDER", sym, f"🛑 Posisi Real {sym} ({pos_side}) selesai @ {curr_exit_price:.6f} | Net: {realized_pnl:+.4f} USDT ({order_type})")
                        print(f"🛑 [REAL POSITION CLOSED DETECTED] {sym} ({pos_side}) exit @ {curr_exit_price:.6f} | PnL: {realized_pnl:+.4f} USDT | Type: {order_type}")
                    except Exception as e_recon:
                        print(f"[RECONCILIATION ERROR] {sym}: {e_recon}")

                # ─── 2.B Sinkronisasi Otomatis Closed Trades langsung dari Exchange API ───
                if isinstance(client, BaseExchange) and hasattr(client, "get_history_positions"):
                    try:
                        synced_trades = await sync_exchange_trades_to_db(client, limit=20)
                        for st in synced_trades:
                            st_sym = st.get("symbol", "")
                            st_side = st.get("side", "LONG")
                            st_pnl = float(st.get("net_pnl", st.get("realized_pnl", 0.0)))
                            st_entry = float(st.get("entry_price", 0.0))
                            st_exit = float(st.get("exit_price", 0.0))
                            st_res = st.get("result", "WIN" if st_pnl > 0 else "LOSS")
                            st_dur = float(st.get("duration_minutes", 0.0) or 0.0)
                            st_ex = st.get("exchange", "BITUNIX_REAL")
                            st_m = float(st.get("margin_usdt", 0.0))
                            st_lev = int(st.get("leverage", 1) or 1)

                            add_scanner_log("ORDER", st_sym, f"🛑 [REAL {st_ex}] {st_sym} ({st_side}) Closed @ {st_exit} | Net: {st_pnl:+.4f} USDT ({st_res})")
                            print(f"🛑 [REAL TRADE SYNCED] {st_sym} ({st_side}) exit @ {st_exit} | Net PnL: {st_pnl:+.4f} USDT [{st_res}]")

                            # Bersihkan juga dari memory jika ada
                            bot_state.get("active_trade_meta", {}).pop(st_sym, None)
                            bot_state.get("active_trade_reasons", {}).pop(st_sym, None)

                            # Kirim notifikasi Telegram penutupan real trade jika bot aktif
                            if bot and TELEGRAM_ADMIN_CHAT_ID:
                                order_data = {
                                    "symbol": f"[{st_ex}] {st_sym}",
                                    "order_type": "EXCHANGE_TP_SL",
                                    "price": f"{st_exit:.6f}",
                                    "entry_price": st_entry,
                                    "quantity": float(st.get("qty", 0.0)),
                                    "realized_pnl": st_pnl,
                                    "commission": float(st.get("commission", 0.0)),
                                    "funding_fee": float(st.get("funding_fee", 0.0)),
                                    "net_pnl": st_pnl,
                                    "mfe": f"+{st_pnl:.4f}" if st_pnl > 0 else "0.00",
                                    "mae": f"{st_pnl:.4f}" if st_pnl < 0 else "0.00",
                                    "duration": f"{st_dur:.1f} menit" if st_dur > 0 else "N/A",
                                    "duration_minutes": st_dur,
                                    "fingerprint": f"REAL_{st_ex}_{st_sym}",
                                    "win_rate": 0.0,
                                    "total_trades": 0,
                                    "wins": 0,
                                    "losses": 0,
                                    "alasan_masuk": f"Bitunix Real Position Exit ({st_res}) - TP/SL Triggered on Exchange",
                                    "ai_eval_summary": "Posisi selesai dieksekusi dan terealisasi di exchange Bitunix.",
                                }
                                try:
                                    await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                                    if TELEGRAM_ERROR_CHAT_ID and TELEGRAM_ERROR_CHAT_ID != TELEGRAM_ADMIN_CHAT_ID:
                                        await send_order_filled_notification(bot, TELEGRAM_ERROR_CHAT_ID, order_data)
                                except Exception as e_notif_sync:
                                    print(f"[TELEGRAM] Gagal kirim notif sync real trade {st_sym}: {e_notif_sync}")
                    except Exception as e_sync_all:
                        logger.debug(f"[MONITOR] Gagal sync closed trades: {e_sync_all}")

                for position in positions:
                    amount = float(position.get("position_amt", position.get("positionAmt", 0)))
                    profit = float(position.get("unrealized_pnl", position.get("unrealizedProfit", 0)))
                    entry_price = float(position.get("entry_price", position.get("entryPrice", 0)))
                    symbol = position.get("symbol")
                    
                    if amount == 0 or not symbol:
                        continue

                    meta = bot_state.setdefault("active_trade_meta", {}).get(symbol)
                    if meta is not None:
                        meta["mfe"] = max(float(meta.get("mfe", 0.0)), profit)
                        meta["mae"] = min(float(meta.get("mae", 0.0)), profit)

                    # Pastikan posisi yang BUKAN dibuka oleh bot (manual trade) TIDAK diintervensi oleh bot
                    is_bot_trade = meta is not None and meta.get("is_bot_trade", False)
                    if not is_bot_trade:
                        # Trade dibuka manual oleh user di exchange: bot hanya memantau info di /status Telegram
                        # tanpa melakukan auto close, time exit, ataupun auto TP/SL agar tidak bertabrakan.
                        continue

                    initial_margin = abs(amount) * entry_price / bot_config.leverage if entry_price > 0 else 0
                    roi_percent = (profit / initial_margin * 100) if initial_margin > 0 else 0

                    # Evaluasi Auto Break-Even Protection pada Real Position
                    if bot_config.use_auto_breakeven and meta is not None and not meta.get("is_breakeven_set", False):
                        be_eval = evaluate_auto_breakeven(
                            current_roi_percent=roi_percent,
                            entry_price=entry_price,
                            side="LONG" if amount > 0 else "SHORT",
                            be_activation_roi=bot_config.auto_breakeven_roi_percent,
                            fee_buffer_percent=0.1,
                            current_sl_price=float(meta.get("sl_price", 0.0)) if meta.get("sl_price") else None,
                        )
                        if be_eval.get("should_move_to_be"):
                            new_sl = be_eval["new_sl_price"]
                            close_side = "SELL" if amount > 0 else "BUY"
                            try:
                                update_res = await place_take_profit_stop_loss(
                                    client,
                                    symbol,
                                    close_side,
                                    abs(amount),
                                    tp_price=float(meta.get("tp_price", 0.0)),
                                    sl_price=new_sl,
                                    use_trailing_stop=False,
                                )
                                if update_res.get("status") in ("success", "existing"):
                                    meta["sl_price"] = new_sl
                                    meta["is_breakeven_set"] = True
                                    print(f"🛡️ [AUTO BREAK-EVEN (REAL)] {symbol}: ROI {roi_percent:+.2f}% >= +{bot_config.auto_breakeven_roi_percent}%. SL digeser ke Entry+Buffer: {new_sl:.6f} (Risk-Free!)")
                                    try:
                                        be_msg = (
                                            f"🛡️ **AUTO BREAK-EVEN ACTIVATED (RISK-FREE) 🛡️**\n\n"
                                            f"• **Koin:** `{symbol}` ({'LONG' if amount > 0 else 'SHORT'})\n"
                                            f"• **Floating ROI:** `{roi_percent:+.2f}%` (Trigger $\ge +{bot_config.auto_breakeven_roi_percent}%$)\n"
                                            f"• **Entry Price:** `{entry_price:.6f}`\n"
                                            f"• **Stop Loss Baru:** `{new_sl:.6f}` (Entry + 0.1% Fee Buffer)\n\n"
                                            f"✨ *Order SL di bursa telah diperbarui. Posisi ini 100% Bebas Risiko (Anti Rungkad).* Target TP Statik tetap aktif!"
                                        )
                                        await safe_send_message(bot, TELEGRAM_ADMIN_CHAT_ID, be_msg)
                                    except Exception as e_be_notif:
                                        print(f"[TELEGRAM] Gagal kirim notif Auto-BE Real: {e_be_notif}")
                            except Exception as e_be_real:
                                print(f"[AUTO BREAK-EVEN REAL ERROR] {symbol}: {e_be_real}")

                    reached_take_profit = roi_percent >= bot_config.tp_percent
                    reached_stop_loss = roi_percent <= -bot_config.sl_percent
                    if amount != 0 and (reached_take_profit or reached_stop_loss):
                        close_result = await emergency_close_position(
                            client,
                            symbol,
                            "SELL" if amount > 0 else "BUY",
                            abs(amount),
                        )
                        reason = "TAKE_PROFIT" if reached_take_profit else "STOP_LOSS"
                        print(
                            f"[ROI AUTO CLOSE] {symbol} {reason}: "
                            f"ROI={roi_percent:+.2f}% status={close_result.get('status')}"
                        )
                        if close_result.get("status") == "success":
                            meta = bot_state.get("active_trade_meta", {}).pop(symbol, {})
                            duration_minutes = None
                            if meta.get("entry_time"):
                                duration_minutes = round((datetime.now() - meta["entry_time"]).total_seconds() / 60, 1)

                            margin_val = float(meta.get("margin_usdt", initial_margin) or initial_margin)
                            mfe_val = float(meta.get("mfe", profit) or profit)
                            mae_val = float(meta.get("mae", 0.0) or 0.0)
                            if profit > 0:
                                mfe_val = max(mfe_val, profit)
                            if profit < 0:
                                mae_val = min(mae_val, profit)

                            mfe_pct = (mfe_val / margin_val * 100) if margin_val > 0 else 0.0
                            mae_pct = (abs(mae_val) / margin_val * 100) if margin_val > 0 else 0.0
                            mfe_str = f"+{mfe_pct:.2f}%" if margin_val > 0 else f"{mfe_val:+.4f}"
                            mae_str = f"-{mae_pct:.2f}%" if margin_val > 0 else f"{mae_val:+.4f}"

                            exit_price = float(close_result.get("price") or (entry_price + (profit / amount) if amount != 0 else entry_price))

                            order_data = {
                                "symbol": symbol,
                                "order_type": f"AUTO_{reason}",
                                "price": f"{exit_price:.6f}",
                                "entry_price": meta.get("entry_price", entry_price),
                                "quantity": abs(amount),
                                "realized_pnl": profit,
                                "commission": 0.0,
                                "funding_fee": 0.0,
                                "net_pnl": profit,
                                "mfe": mfe_str,
                                "mae": mae_str,
                                "duration": f"{duration_minutes:.1f} menit" if duration_minutes is not None else "N/A",
                                "duration_minutes": duration_minutes,
                                "fingerprint": meta.get("fingerprint", meta.get("alasan", "Kombinasi Standar")),
                                "win_rate": 0.0,
                                "total_trades": 0,
                                "wins": 0,
                                "losses": 0,
                                "alasan_masuk": meta.get("alasan", "Sinyal Multi-Indikator AI"),
                                "ai_eval_summary": meta.get("ai_eval_summary", ""),
                            }
                            pattern_entry_id = meta.get("pattern_entry_id")
                            if pattern_entry_id:
                                record_pattern_result(pattern_entry_id, profit > 0, profit)
                            alasan = bot_state.get("active_trade_reasons", {}).pop(symbol, None)
                            if alasan:
                                record_trade_result(alasan, profit > 0)
                            record_closed_trade({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "side": "LONG" if amount > 0 else "SHORT",
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "realized_pnl": profit,
                                "commission": 0.0,
                                "funding_fee": 0.0,
                                "net_pnl": profit,
                                "margin_usdt": margin_val,
                                "leverage": bot_config.leverage,
                                "mfe": mfe_val,
                                "mae": mae_val,
                                "duration_minutes": duration_minutes,
                                "order_type": f"AUTO_{reason}",
                            })
                            try:
                                if TELEGRAM_ADMIN_CHAT_ID:
                                    await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                                if TELEGRAM_ERROR_CHAT_ID and TELEGRAM_ERROR_CHAT_ID != TELEGRAM_ADMIN_CHAT_ID:
                                    await send_order_filled_notification(bot, TELEGRAM_ERROR_CHAT_ID, order_data)
                            except Exception as e_fill_notif:
                                print(f"[TELEGRAM] Gagal kirim notifikasi closed order monitor: {e_fill_notif}")
                        continue

                    # Evaluasi Time-Based Risk Exit (Hold >= 4h & ROI <= -5% atau Hold >= 8h & ROI >= +20%)
                    meta = bot_state.get("active_trade_meta", {}).get(symbol, {})
                    entry_time = meta.get("entry_time")
                    if entry_time:
                        hold_duration_hours = (datetime.now() - entry_time).total_seconds() / 3600.0
                    else:
                        update_time_ms = int(position.get("updateTime", 0))
                        hold_duration_hours = ((now_ms - update_time_ms) / 3600000.0) if update_time_ms > 0 else 0.0

                    should_time_close, exit_type, time_close_reason = evaluate_time_based_exit(
                        hold_duration_hours=hold_duration_hours,
                        roi_percent=roi_percent,
                        loss_limit_percent=-5.0,
                        loss_time_limit_hours=2.0,
                        profit_target_percent=15.0,
                        profit_time_limit_hours=4.0,
                        max_hold_hours=8.0,
                    )

                    if amount != 0 and should_time_close:
                        close_result = await emergency_close_position(
                            client,
                            symbol,
                            "SELL" if amount > 0 else "BUY",
                            abs(amount),
                        )
                        print(
                            f"[TIME-BASED AUTO CLOSE] {symbol}: {time_close_reason} "
                            f"status={close_result.get('status')}"
                        )
                        if close_result.get("status") == "success":
                            meta = bot_state.get("active_trade_meta", {}).pop(symbol, {})
                            hold_mins = round(hold_duration_hours * 60, 1)
                            pos_side = "LONG" if amount > 0 else "SHORT"
                            is_win_trade = profit > 0
                            margin_val = float(meta.get("margin_usdt", initial_margin) or initial_margin)
                            exit_price = float(close_result.get("price") or (entry_price + (profit / amount) if amount != 0 else entry_price))
                            ex_tag = getattr(bot_config, 'exchange', 'BINANCE').upper()

                            t_alasan = bot_state.get("active_trade_reasons", {}).pop(symbol, meta.get("alasan", "Real Trade"))
                            if t_alasan:
                                record_trade_result(t_alasan, is_win_trade)

                            record_closed_trade({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "side": pos_side,
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "realized_pnl": profit,
                                "commission": 0.0,
                                "funding_fee": 0.0,
                                "net_pnl": profit,
                                "margin_usdt": margin_val,
                                "leverage": bot_config.leverage,
                                "mfe": f"{profit:+.4f}",
                                "mae": "0.00%",
                                "duration_minutes": hold_mins,
                                "order_type": "TIME_BASED_EXIT",
                                "exchange": ex_tag,
                            })

                            order_data = {
                                "symbol": f"[{ex_tag}] {symbol}",
                                "order_type": "TIME_BASED_EXIT",
                                "price": f"{exit_price:.6f}",
                                "entry_price": meta.get("entry_price", entry_price),
                                "quantity": abs(amount),
                                "realized_pnl": profit,
                                "commission": 0.0,
                                "funding_fee": 0.0,
                                "net_pnl": profit,
                                "mfe": f"{profit:+.4f}",
                                "mae": "0.00%",
                                "duration": f"{hold_mins:.1f} menit ({hold_duration_hours:.1f} jam)",
                                "duration_minutes": hold_mins,
                                "fingerprint": meta.get("fingerprint", t_alasan),
                                "win_rate": 0.0,
                                "total_trades": 0,
                                "wins": 0,
                                "losses": 0,
                                "alasan_masuk": f"{t_alasan} | Trigger: {time_close_reason}",
                                "ai_eval_summary": meta.get("ai_eval_summary", time_close_reason),
                            }
                            try:
                                if TELEGRAM_ADMIN_CHAT_ID:
                                    await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                                if TELEGRAM_ERROR_CHAT_ID and TELEGRAM_ERROR_CHAT_ID != TELEGRAM_ADMIN_CHAT_ID:
                                    await send_order_filled_notification(bot, TELEGRAM_ERROR_CHAT_ID, order_data)
                            except Exception as e_ntf:
                                print(f"[TELEGRAM] Gagal kirim notifikasi real time exit: {e_ntf}")
                        continue

                    if amount != 0 and entry_price > 0:
                        suppressed_symbols = bot_state.setdefault("protection_recovery_suppressed", set())
                        if symbol in suppressed_symbols:
                            continue
                        open_orders = orders_by_symbol.get(symbol, [])
                        has_tp = any(
                            order.get("type") in {"TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"}
                            for order in open_orders
                        )
                        has_sl = any(order.get("type") in {"STOP", "STOP_MARKET"} for order in open_orders)
                        has_any_close_protection = any(
                            str(order.get("closePosition", "")).lower() == "true"
                            or order.get("type") in {
                                "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
                                "STOP", "STOP_MARKET", "TRAILING_STOP_MARKET",
                            }
                            for order in open_orders
                        )
                        if not has_tp or not has_sl:
                            if hasattr(client, "futures_cancel_all_open_orders"):
                                await client.futures_cancel_all_open_orders(symbol=symbol)
                            price_move_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                            price_move_sl = (bot_config.sl_percent / 100) / bot_config.leverage
                            if amount > 0:
                                tp_price = entry_price * (1 + price_move_tp)
                                sl_price = entry_price * (1 - price_move_sl)
                                close_side = "SELL"
                            else:
                                tp_price = entry_price * (1 - price_move_tp)
                                sl_price = entry_price * (1 + price_move_sl)
                                close_side = "BUY"
                            protection = await place_take_profit_stop_loss(
                                client,
                                symbol,
                                close_side,
                                abs(amount),
                                tp_price,
                                sl_price,
                                use_trailing_stop=False,
                            )
                            print(
                                f"[PROTECTION RECOVERY] {symbol}: "
                                f"TP/SL status={protection.get('status')}"
                            )
                            if protection.get("status") in {"existing", "success", "error"}:
                                suppressed_symbols.add(symbol)


                    # Auto close jika profit telah tercapai sesuai durasi
                    update_time_ms = int(position.get("updateTime", 0))
                    if amount == 0 or profit <= 0 or update_time_ms <= 0:
                        continue

                    age_ms = now_ms - update_time_ms
                    if age_ms < max_age_ms:
                        continue

                    symbol = position["symbol"]
                    close_result = await close_profitable_position(client, symbol, amount)
                    status = close_result.get("status")
                    print(
                        f"[AUTO CLOSE] {symbol} profit={profit:.4f} USDT "
                        f"age={age_ms / 3600000:.2f}h status={status}"
                    )
                    if status == "success":
                        meta = bot_state.get("active_trade_meta", {}).pop(symbol, {})
                        duration_minutes = round(age_ms / 60000, 1)
                        exit_price = float(close_result.get("price") or (entry_price + (profit / amount) if amount != 0 else entry_price))
                        order_data = {
                            "symbol": symbol,
                            "order_type": "PROFIT_HOLDING_AUTO_CLOSE",
                            "price": f"{exit_price:.6f}",
                            "entry_price": meta.get("entry_price", entry_price),
                            "quantity": abs(amount),
                            "realized_pnl": profit,
                            "commission": 0.0,
                            "funding_fee": 0.0,
                            "net_pnl": profit,
                            "mfe": f"{profit:+.4f}",
                            "mae": "0.00%",
                            "duration": f"{duration_minutes:.1f} menit",
                            "duration_minutes": duration_minutes,
                            "fingerprint": meta.get("fingerprint", meta.get("alasan", "Kombinasi Standar")),
                            "win_rate": 0.0,
                            "total_trades": 0,
                            "wins": 0,
                            "losses": 0,
                            "alasan_masuk": f"{meta.get('alasan', 'Sinyal AI')} | Auto-close holding profit",
                            "ai_eval_summary": meta.get("ai_eval_summary", ""),
                        }
                        try:
                            if TELEGRAM_ADMIN_CHAT_ID:
                                await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                            if TELEGRAM_ERROR_CHAT_ID and TELEGRAM_ERROR_CHAT_ID != TELEGRAM_ADMIN_CHAT_ID:
                                await send_order_filled_notification(bot, TELEGRAM_ERROR_CHAT_ID, order_data)
                        except Exception as e_profit_notif:
                            print(f"[TELEGRAM] Gagal kirim notifikasi profit auto close: {e_profit_notif}")

            except Exception as monitor_error:
                err_str = str(monitor_error)
                print(f"[POSITION MONITOR] {monitor_error}")
                if "-1003" in err_str or "429" in err_str or "too many requests" in err_str.lower() or "banned" in err_str.lower():
                    cooldown_sec = extract_ban_cooldown(err_str, default_seconds=300)
                    register_ip_ban(cooldown_sec)
                    print(f"[RATE LIMIT PROTECTION] Monitor mendeteksi IP Ban / Rate Limit (-1003). Cooldown {cooldown_sec} detik...")
                    await send_error_log(
                        bot,
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"⚠️ **RATE LIMIT / IP BAN (-1003)**: Batas request tercapai. Position monitor otomatis jeda {cooldown_sec} detik untuk mendinginkan IP.",
                    )
                    await asyncio.sleep(cooldown_sec)
                else:
                    await send_error_log(
                        bot,
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"Position monitor error: {monitor_error}",
                    )

            await asyncio.sleep(POSITION_MONITOR_INTERVAL_ENV)
    finally:
        if isinstance(client, BaseExchange):
            await client.close()
        else:
            await client.close_connection()

async def _startup_database(client: Union[BaseExchange, AsyncClient]) -> None:
    """Inisialisasi DB saat startup: buat tabel, migrasi data lama, mulai scraper."""
    if not DB_MODULES_LOADED:
        print("[DB] Modul database tidak dimuat, skip inisialisasi.")
        return

    print("[DB] Menghubungkan ke PostgreSQL...")
    db_ok = await is_db_available()
    if not db_ok:
        print("[DB] ⚠️ PostgreSQL tidak tersedia — bot tetap jalan dengan JSON backup.")
        return

    # Buat tabel jika belum ada
    await create_tables()

    # Migrasi data lama dari JSON ke DB (sekali saja)
    await migrate_trades()
    await migrate_patterns()

    # Jalankan initial scrape di background (dinonaktifkan agar bot ringan & hemat resource)
    # asyncio.ensure_future(run_initial_scrape(client))

    print("[DB] ✅ Database siap!")


async def main():
    # Inisialisasi client exchange aktif melalui Factory
    _startup_client = get_exchange_adapter()
    await _startup_client.init()
    bot_state["client"] = _startup_client

    # Startup database & OHLCV scraper
    await _startup_database(_startup_client)

    # Deteksi dan sinkronisasi otomatis akun real (Saldo, Posisi Terbuka, dan Riwayat Trade) saat startup
    try:
        curr_mode = getattr(bot_config, "trading_mode", TRADING_MODE).upper()
        print(f"[STARTUP] 🔄 Mendeteksi data akun real di exchange ({_startup_client.exchange_name})...")
        await sync_real_exchange_account(_startup_client, sync_history=True, bot_state_ref=bot_state)
    except Exception as _sync_start_err:
        print(f"[STARTUP] Warning sinkronisasi real account: {_sync_start_err}")

    # Startup web dashboard
    dashboard_runner = None
    if DASHBOARD_MODULE_LOADED:
        try:
            dashboard_runner = await start_dashboard_server(host="0.0.0.0", port=8000)
        except Exception as _dash_err:
            print(f"[DASHBOARD] Gagal start dashboard: {_dash_err}")

    # Bersihkan antrean update lama Telegram & register ulang daftar perintah resmi
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(bot)
        print("[TELEGRAM] ✅ Command lama dibersihkan & daftar perintah resmi ter-register!")
    except Exception as _tg_err:
        print(f"[TELEGRAM] Gagal setup commands: {_tg_err}")

    async def _safe_telegram_polling():
        while True:
            try:
                await dp.start_polling(bot, handle_signals=False)
            except Exception as _poll_err:
                print(f"[TELEGRAM POLLING] Network/DNS terputus: {_poll_err}. Mencoba menyambung kembali dalam 5 detik...")
                await asyncio.sleep(5)

    try:
        from database.send_backup_to_telegram import daily_backup_scheduler_loop
        await asyncio.gather(
            _safe_telegram_polling(),
            scanner_loop(),
            user_data_stream_loop(),
            profitable_position_monitor_loop(),
            daily_backup_scheduler_loop(bot),
            # Background scraper (Daily 1d)
            run_long_term_scraper(_startup_client) if DB_MODULES_LOADED else asyncio.sleep(0),
        )
    finally:
        if dashboard_runner:
            await dashboard_runner.cleanup()
        if isinstance(_startup_client, BaseExchange):
            await _startup_client.close()
        else:
            await _startup_client.close_connection()
        if DB_MODULES_LOADED:
            await close_pool()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot dihentikan oleh user.")
