import asyncio
import time
import os
import csv
from collections import defaultdict
from datetime import datetime
from binance import AsyncClient
import pandas as pd

from config.settings import (
    BINANCE_API_KEY, BINANCE_API_SECRET, TRADING_MODE,
    SCAN_INTERVAL_SECONDS, TIMEFRAME, API_REQUEST_DELAY,
    TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_ERROR_CHAT_ID, MARGIN_USDT,
    bot_config, HTF_TIMEFRAME, TOP_N_COINS_ENV, SCAN_BATCH_SIZE_ENV,
    SMART_BUY_LOOKBACK_DAYS_ENV, SMART_BUY_TOLERANCE_ENV,
    AUTO_CLOSE_PROFIT_HOURS_ENV, POSITION_MONITOR_INTERVAL_ENV
)

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
from indicators.patterns import detect_candlestick_patterns, is_bull_trap
from indicators.trend import get_htf_trend
from core.learner import is_pattern_reliable, record_trade_result
from core.risk_manager import (
    calculate_risk_margin,
    calculate_volatility_adjusted_leverage,
    calculate_computed_position_size,
    count_open_positions,
    daily_loss_limit_reached,
    total_position_notional,
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
    from database.trade_repo import migrate_from_json as migrate_trades
    from database.pattern_repo import migrate_from_json as migrate_patterns
    from database.ohlcv_repo import upsert_candles
    from core.ohlcv_scraper import run_initial_scrape, run_short_term_scraper, run_long_term_scraper
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


async def scanner_loop():
    """
    Loop utama untuk melakukan scanning market
    """
    testnet = TRADING_MODE == 'TESTNET'
    client = await AsyncClient.create(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=testnet
    )
    bot_state["client"] = client
    bot_state["active_trade_reasons"] = {}
    bot_state["state"] = "PAUSED"
    
    print(f"Bot Started in {TRADING_MODE} Mode. Timeframe: {TIMEFRAME}. Status: PAUSED.")
    
    # Kirim Notifikasi Awal ke Telegram Admin
    try:
        startup_msg = (
            "✅ **Sistem Bot Telah Dinyalakan (Host Started)!**\n\n"
            "Status saat ini: 🛑 **PAUSED (BERHENTI)**.\n"
            "Bot tidak akan melakukan *scan* koin hingga Anda memerintahkannya.\n\n"
            "Ketik `/resume` atau tekan tombol **⏯️ Pause / Resume** untuk memulai bot."
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
                account_snapshot = await client.futures_account()
                daily_stats = trade_summary()
                if daily_loss_limit_reached(
                    daily_stats["daily_net_pnl"],
                    float(account_snapshot["totalMarginBalance"]),
                    bot_config.max_daily_loss_percent,
                ):
                    bot_state["is_running"] = False
                    bot_state["state"] = "KILL_SWITCH"
                    await send_error_log(
                        bot,
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"DAILY CIRCUIT BREAKER: realized PnL {daily_stats['daily_net_pnl']:+.4f} USDT",
                    )
                    await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                    continue

                # 1. Dapatkan semua koin
                all_symbols = await get_top_futures_by_volume(client, TOP_N_COINS_ENV)
                
                batch_size = 1 if bot_config.scanner_mode == "per_coin" else SCAN_BATCH_SIZE_ENV
                for i in range(0, len(all_symbols), batch_size):
                    batch_symbols = all_symbols[i:i+batch_size]
                    tahap = (i // batch_size) + 1
                    batch_signal_found = False
                    
                    if tahap == 1:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan koin top {i+1} - {i+len(batch_symbols)}")
                    else:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan tahap ke-{tahap} scan koin top {i+1} - {i+len(batch_symbols)}")
                        
                    for idx, symbol in enumerate(batch_symbols):
                        print(f"Koin top {i + idx + 1} {symbol}")
                        
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
                        df = df[df["close_time"] < now_ms]
                        df_htf = df_htf[df_htf["close_time"] < now_ms]
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
                            is_tp = False
                            is_sl = False
                            
                            if v_trade['tipe'] == 'LONG':
                                if current_price >= v_trade['tp_price']: is_tp = True
                                elif current_price <= v_trade['sl_price']: is_sl = True
                            elif v_trade['tipe'] == 'SHORT':
                                if current_price <= v_trade['tp_price']: is_tp = True
                                elif current_price >= v_trade['sl_price']: is_sl = True
                                
                            if is_tp:
                                msg = f"📚 HASIL BELAJAR ({v_trade['tipe']}): Koin {symbol} berhasil mencapai Target (TP)! Alasan masuk sebelumnya: {v_trade['alasan']}. Strategi ini valid."
                                print(msg)
                                await safe_send_message(bot, TELEGRAM_ERROR_CHAT_ID, msg)
                                
                                record_trade_result(v_trade['alasan'], is_profit=True)
                                
                                # Simpan ke CSV (SUCCESS)
                                with open("virtual_success_log.csv", "a", newline="") as f:
                                    writer = csv.writer(f)
                                    writer.writerow([v_trade['time'], symbol, v_trade['tipe'], v_trade['entry_price'], v_trade['tp_price'], v_trade['alasan'], "SUCCESS"])
                                    
                                del virtual_trades[symbol]
                            elif is_sl:
                                msg = f"📚 HASIL BELAJAR ({v_trade['tipe']}): Koin {symbol} gagal dan menyentuh Stop Loss. Alasan masuk sebelumnya: {v_trade['alasan']}. Perlu dievaluasi."
                                print(msg)
                                await safe_send_message(bot, TELEGRAM_ERROR_CHAT_ID, msg)
                                
                                record_trade_result(v_trade['alasan'], is_profit=False)
                                
                                # Simpan ke CSV (FAILED)
                                with open("virtual_success_log.csv", "a", newline="") as f:
                                    writer = csv.writer(f)
                                    writer.writerow([v_trade['time'], symbol, v_trade['tipe'], v_trade['entry_price'], v_trade['sl_price'], v_trade['alasan'], "FAILED"])
                                    
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
                        
                        last_row = df.iloc[-1]
                        current_price = last_row['close']
                        
                        # 4. Cek Kondisi Teknikal Entry LONG
                        near_lower_bb = last_row.get('is_near_lower_band', False)
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
                        
                        # 4. Cek Kondisi Teknikal Entry SHORT
                        upper_band = last_row.get('upper_band')
                        near_upper_bb = current_price >= upper_band * 0.995 if pd.notnull(upper_band) else False
                        resistance_zones = detect_resistance_zones(df)
                        near_resistance = is_near_resistance(current_price, resistance_zones)
                        is_overbought = rsi_value > bot_config.rsi_overbought
                        
                        pattern_detected = pattern_info['detected']
                        pattern_name = pattern_info['pattern']
                        pattern_type = pattern_info['type']
                        vol_ratio = pattern_info.get('volume_ratio', 1.0)
                        has_volume_surge = vol_ratio >= 1.5

                        # Hitung Volatilitas ATR
                        atr_val = calculate_atr(df, period=14)
                        dynamic_leverage = calculate_volatility_adjusted_leverage(
                            atr_val, current_price, base_leverage=bot_config.leverage
                        )
                        
                        # Skenario Tier-A Reversal & Breakout untuk LONG
                        syarat_teknikal_long = near_lower_bb and near_support and is_oversold and htf_trend in ["UPTREND", "SIDEWAYS"]
                        syarat_pola_long = near_support and pattern_detected and pattern_type == 'LONG' and htf_trend in ["UPTREND", "SIDEWAYS"]
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
                        
                        # Skenario untuk SHORT
                        syarat_breakout_short = (
                            breakout["ready"]
                            and breakout["score"] >= bot_config.breakout_min_score
                            and last_row["close"] < last_row["open"]
                            and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        )
                        syarat_teknikal_short = near_upper_bb and near_resistance and is_overbought and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        syarat_pola_short = near_resistance and pattern_detected and pattern_type == 'SHORT' and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        
                        # Anti-Bull Trap (Untuk LONG)
                        bull_trap_detected = is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']) or (pattern_type == 'CLOSE_LONG')
                        
                        if syarat_breakout_long:
                            alasan_long = (
                                f"Dormant breakout score {breakout['score']:.1f} "
                                f"(vol {breakout['volume_spike']:.2f}x, HTF: {htf_trend})"
                            )
                        elif syarat_pola_long:
                            alasan_long = f"Pola Tier-A {pattern_name} (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        elif syarat_smart_buy_long:
                            alasan_long = (
                                f"Smart Buy level {smart_buy_level['level']:.8f} "
                                f"({smart_buy_level['touches']}x open/close 20D, HTF: {htf_trend})"
                            )
                        else:
                            alasan_long = f"RSI Oversold ({rsi_value:.2f}) di Lower BB (HTF: {htf_trend})"

                        if syarat_breakout_short:
                            alasan_short = (
                                f"Dormant breakout score {breakout['score']:.1f} "
                                f"(vol {breakout['volume_spike']:.2f}x, HTF: {htf_trend})"
                            )
                        elif syarat_pola_short:
                            alasan_short = f"Pola Reversal {pattern_name} (Vol: {vol_ratio:.2f}x, HTF: {htf_trend})"
                        else:
                            alasan_short = f"RSI Overbought ({rsi_value:.2f}) di Upper BB (HTF: {htf_trend})"
                        
                        # Evaluasi Keandalan dari Learner
                        reliable_long = is_pattern_reliable(alasan_long) if (syarat_teknikal_long or syarat_pola_long or syarat_smart_buy_long or syarat_breakout_long) else True
                        reliable_short = is_pattern_reliable(alasan_short) if (syarat_teknikal_short or syarat_pola_short or syarat_breakout_short) else True
                        
                        trigger_long = (syarat_teknikal_long or syarat_pola_long or syarat_smart_buy_long or syarat_breakout_long) and not bull_trap_detected and reliable_long
                        trigger_short = (syarat_teknikal_short or syarat_pola_short or syarat_breakout_short) and reliable_short
                        
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
                            }

                            # Evaluasi Pattern Memory: tolak jika pola terbukti buruk (>= 3 sample, WR < 45%)
                            if is_pattern_memory_blacklisted(conditions_snapshot):
                                print(f"🚫 [PATTERN MEMORY] Sinyal {trade_type} pada {symbol} DITOLAK karena pola historis memiliki Win Rate rendah!")
                                continue

                            batch_signal_found = True
                            print(f"SETUP TEKNIKAL {trade_type} DITEMUKAN PADA {symbol}! Alasan: {alasan}")
                            
                            # 5. Cek Modal & Hitung Sizing Computed
                            try:
                                account_info = await client.futures_account()
                                modal = float(account_info['totalMarginBalance'])
                                if bot_config.simulated_modal is not None and bot_config.simulated_modal > 0:
                                    modal = bot_config.simulated_modal
                                
                                # Cek posisi terbuka
                                positions = account_info.get('positions', [])
                                is_position_open = False
                                active_longs = 0
                                active_shorts = 0
                                
                                for pos in positions:
                                    amt = float(pos['positionAmt'])
                                    if amt > 0:
                                        active_longs += 1
                                    elif amt < 0:
                                        active_shorts += 1
                                        
                                    if pos['symbol'] == symbol and amt != 0:
                                        is_position_open = True
                                
                                if is_position_open:
                                    print(f"⏩ Lewati {symbol}: Sudah ada posisi terbuka.")
                                    continue

                                total_exposure = total_position_notional(positions)
                                exposure_limit = modal * bot_config.max_total_exposure_percent / 100
                                if total_exposure >= exposure_limit:
                                    print(f"[RISK] Lewati {symbol}: exposure {total_exposure:.2f} >= limit {exposure_limit:.2f}")
                                    continue

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
                                
                                # Batasan maksimal open posisi
                                active_positions = count_open_positions(positions)
                                if active_positions >= bot_config.max_open_positions:
                                    if symbol not in virtual_trades:
                                        if len(virtual_trades) < 15:
                                            print(f"⏩ Lewati {symbol}: Limit {bot_config.max_open_positions} posisi tercapai. Memasukkan ke mode Paper Trading.")
                                            
                                            pm_tp = (bot_config.tp_percent / 100) / dynamic_leverage
                                            pm_sl = (bot_config.sl_percent / 100) / dynamic_leverage
                                            
                                            virtual_trades[symbol] = {
                                                'tipe': trade_type,
                                                'entry_price': current_price,
                                                'tp_price': current_price * (1 + pm_tp) if trade_type == 'LONG' else current_price * (1 - pm_tp),
                                                'sl_price': current_price * (1 - pm_sl) if trade_type == 'LONG' else current_price * (1 + pm_sl),
                                                'alasan': alasan,
                                                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                            }
                                        else:
                                            print(f"⏩ Lewati {symbol}: Kapasitas Paper Trading penuh (15 koin).")
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
                                        min_margin=1.0,
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
                                    # Mode FIXED dengan safety cap
                                    fixed_margin = bot_config.margin_usdt
                                    max_allowed = modal * bot_config.max_position_equity_ratio
                                    current_margin = min(fixed_margin, max_allowed)
                                    if current_margin < 1.0:
                                        print(f"[RISK] {symbol}: Margin FIXED ({current_margin:.2f} USDT) di bawah minimum 1 USDT")
                                        continue
                                    print(
                                        f"[FIXED SIZING] {symbol}: Modal={modal:.2f} USDT | "
                                        f"Margin={current_margin:.2f} USDT | Lev={dynamic_leverage}x"
                                    )

                            except Exception as e_bal:
                                print(f"[ERROR] Gagal menghitung sizing modal: {e_bal}")
                                continue
                                
                            # 6. Eksekusi Order
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
                                
                                # Convert configured margin ROI into deterministic price movement.
                                pm_tp = (bot_config.tp_percent / 100) / actual_leverage
                                pm_sl = (bot_config.sl_percent / 100) / actual_leverage
                                
                                if trade_type == "LONG":
                                    tp_price = entry_price * (1 + pm_tp)
                                    sl_price = entry_price * (1 - pm_sl)
                                    tp_sl_side = 'SELL'
                                else:
                                    tp_price = entry_price * (1 - pm_tp)
                                    sl_price = entry_price * (1 + pm_sl)
                                    tp_sl_side = 'BUY'
                                
                                # Pasang TP / SL
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

                                signal_score = round(
                                    sum([
                                        htf_trend in (["UPTREND", "SIDEWAYS"] if trade_type == "LONG" else ["DOWNTREND", "SIDEWAYS"]),
                                        near_support if trade_type == "LONG" else near_resistance,
                                        is_oversold if trade_type == "LONG" else is_overbought,
                                        syarat_pola_long if trade_type == "LONG" else syarat_pola_short,
                                        has_volume_surge,
                                        not bull_trap_detected,
                                    ]) * (100 / 6),
                                    1,
                                )
                                trade_data = {
                                    'symbol': symbol,
                                    'direction': trade_type,
                                    'price': f"{entry_price:.4f}",
                                    'entry_price': entry_price,
                                    'quantity': quantity,
                                    'margin_usdt': current_margin,
                                    'notional_usdt': current_margin * actual_leverage,
                                    'tp_price': tp_price,
                                    'sl_price': sl_price,
                                    'score': signal_score,
                                    'confidence': f"{signal_score:.1f}%",
                                    'tf': TIMEFRAME,
                                    'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    'margin': f"{current_margin:.2f} (Modal: {modal:.2f})",
                                    'leverage': actual_leverage,
                                    'tp_sl_info': f"TP: {tp_price:.4f} ({bot_config.tp_percent}%), SL: {sl_price:.4f} ({bot_config.sl_percent}%)",
                                    'syarat_1': f"Area {'Support' if trade_type == 'LONG' else 'Resistance'} Divalidasi. Tren {HTF_TIMEFRAME}: {htf_trend}",
                                    'syarat_2': alasan,
                                    'pola_ml': pattern_name if pattern_detected else "Computed Sizing",
                                    'ai_evaluation': ai_eval_text,
                                    'method': alasan,
                                }
                                await send_trade_notification(bot, TELEGRAM_ADMIN_CHAT_ID, trade_data)
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
                                )

                                bot_state.setdefault("active_trade_meta", {})[symbol] = {
                                    "entry_time": datetime.now(),
                                    "entry_price": entry_price,
                                    "side": trade_type,
                                    "margin_usdt": current_margin,
                                    "leverage": actual_leverage,
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
                            
                    # Jeda request API sesuai limit setiap selesai 1 batch (10 koin)
                    await asyncio.sleep(API_REQUEST_DELAY)
                                    
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
                
            # Jeda sebelum scan ulang
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            
    finally:
        await client.close_connection()

from binance import BinanceSocketManager
from telegram.notifier import send_order_filled_notification

async def user_data_stream_loop():
    """
    Mendengarkan event order dari Binance secara real-time.
    """
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
                    reconnect_delay = 2
                    print("Berhasil terhubung ke WebSocket Binance.")
                    while True:
                        res = await stream.recv()

                        if res.get("e") == "ORDER_TRADE_UPDATE":
                            order_info = res.get("o", {})
                            if order_info.get("X") != "FILLED":
                                continue

                            order_type = order_info.get("o", "")
                            is_protective_close = "TAKE_PROFIT" in order_type or "STOP" in order_type
                            is_reduce_only_market = (
                                order_type == "MARKET"
                                and str(order_info.get("R", "")).lower() == "true"
                            )
                            if not is_protective_close and not is_reduce_only_market:
                                continue

                            symbol = order_info.get("s")
                            realized_pnl = float(order_info.get("rp", "0.0"))
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
                            await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)

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
                            for pos in res.get("a", {}).get("P", []):
                                pos_amt = float(pos.get("pa", 0))
                                entry_price = float(pos.get("ep", 0))
                                if pos_amt == 0 or entry_price <= 0:
                                    continue

                                unrealized_pnl = float(pos.get("up", 0))
                                symbol = pos.get("s")
                                meta = bot_state.setdefault("active_trade_meta", {}).get(symbol)
                                if meta is not None:
                                    meta["mfe"] = max(float(meta.get("mfe", 0)), unrealized_pnl)
                                    meta["mae"] = min(float(meta.get("mae", 0)), unrealized_pnl)
                                loss_percent = (unrealized_pnl / (entry_price * abs(pos_amt) / bot_config.leverage)) * 100
                                if loss_percent <= -(bot_config.sl_percent * 0.8):
                                    print(f"[WARNING] {pos.get('s')} mendekati Stop Loss! (Loss: {loss_percent:.2f}%).")
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
        await client.close_connection()


async def profitable_position_monitor_loop():
    """Close positions profitable for at least the configured holding period."""
    testnet = TRADING_MODE == "TESTNET"
    client = await AsyncClient.create(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=testnet,
    )
    try:
        while True:
            try:
                account_info = await client.futures_account()
                now_ms = int(time.time() * 1000)
                max_age_ms = int(AUTO_CLOSE_PROFIT_HOURS_ENV * 60 * 60 * 1000)

                # Ambil semua open orders sekaligus dalam 1 request (Hemat 90% bobot API)
                orders_by_symbol = defaultdict(list)
                try:
                    all_open_orders = await client.futures_get_open_orders()
                    for o in all_open_orders:
                        orders_by_symbol[o.get("symbol")].append(o)
                except Exception as e_ord:
                    print(f"[POSITION MONITOR] Gagal batch fetch open orders: {e_ord}")

                for position in account_info.get("positions", []):
                    amount = float(position.get("positionAmt", 0))
                    profit = float(position.get("unrealizedProfit", 0))
                    entry_price = float(position.get("entryPrice", 0))
                    symbol = position.get("symbol")
                    
                    # Update MFE / MAE secara real-time pada loop monitor posisi
                    if amount != 0:
                        meta = bot_state.setdefault("active_trade_meta", {}).get(symbol)
                        if meta is not None:
                            meta["mfe"] = max(float(meta.get("mfe", 0.0)), profit)
                            meta["mae"] = min(float(meta.get("mae", 0.0)), profit)

                    initial_margin = abs(amount) * entry_price / bot_config.leverage if entry_price > 0 else 0
                    roi_percent = (profit / initial_margin * 100) if initial_margin > 0 else 0
                    reached_take_profit = roi_percent >= bot_config.tp_percent
                    reached_stop_loss = roi_percent <= -bot_config.sl_percent
                    if amount != 0 and (reached_take_profit or reached_stop_loss):
                        close_result = await emergency_close_position(
                            client,
                            symbol,
                            "SELL" if amount > 0 else "BUY",
                            abs(amount),
                        )
                        reason = "TP" if reached_take_profit else "SL"
                        print(
                            f"[ROI AUTO CLOSE] {symbol} {reason}: "
                            f"ROI={roi_percent:+.2f}% status={close_result.get('status')}"
                        )
                        await send_error_log(
                            bot,
                            TELEGRAM_ADMIN_CHAT_ID,
                            f"ROI AUTO CLOSE {symbol}: {reason} tercapai "
                            f"({roi_percent:+.2f}%). Status: {close_result.get('status')}",
                        )
                        continue

                    # Evaluasi Time-Based Risk Exit (Hold > 2h & ROI <= -5% atau Hold > 4h & ROI >= +20%)
                    meta = bot_state.get("active_trade_meta", {}).get(symbol, {})
                    entry_time = meta.get("entry_time")
                    if entry_time:
                        hold_duration_hours = (datetime.now() - entry_time).total_seconds() / 3600.0
                    else:
                        update_time_ms = int(position.get("updateTime", 0))
                        hold_duration_hours = ((now_ms - update_time_ms) / 3600000.0) if update_time_ms > 0 else 0.0

                    should_time_close, time_close_reason = evaluate_time_based_exit(
                        hold_duration_hours=hold_duration_hours,
                        roi_percent=roi_percent,
                        loss_limit_percent=-5.0,
                        loss_time_limit_hours=2.0,
                        profit_target_percent=20.0,
                        profit_time_limit_hours=4.0,
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
                        await send_error_log(
                            bot,
                            TELEGRAM_ADMIN_CHAT_ID,
                            f"⏱️ TIME-BASED AUTO CLOSE {symbol}\n{time_close_reason}\nStatus: {close_result.get('status')}",
                        )
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
                            if has_any_close_protection:
                                continue
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
                            if protection.get("status") == "existing":
                                suppressed_symbols.add(symbol)

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
                    await send_error_log(
                        bot,
                        TELEGRAM_ADMIN_CHAT_ID,
                        f"AUTO CLOSE {symbol}: posisi profit {profit:.4f} USDT "
                        f"dan umur {age_ms / 3600000:.2f} jam. Status: {status}",
                    )
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
                        f"⚠️ **BINANCE RATE LIMIT (-1003)**: Batas request tercapai. Position monitor otomatis jeda {cooldown_sec} detik untuk mendinginkan IP.",
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
        await client.close_connection()

async def _startup_database(client: AsyncClient) -> None:
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

    # Jalankan initial scrape di background (tidak blokir startup)
    asyncio.ensure_future(run_initial_scrape(client))

    print("[DB] ✅ Database siap!")


async def main():
    # Menjalankan Polling Telegram, Scanner, dan WebSocket secara bersamaan
    # Buat Binance client dulu untuk scraper
    is_testnet = (TRADING_MODE == "TESTNET")
    _startup_client = await AsyncClient.create(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=is_testnet,
    )

    # Startup database & OHLCV scraper
    await _startup_database(_startup_client)

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

    try:
        from database.send_backup_to_telegram import daily_backup_scheduler_loop
        await asyncio.gather(
            dp.start_polling(bot),
            scanner_loop(),
            user_data_stream_loop(),
            profitable_position_monitor_loop(),
            daily_backup_scheduler_loop(bot),
            # Background scraper (hanya Daily 1d agar database hemat)
            run_long_term_scraper(_startup_client) if DB_MODULES_LOADED else asyncio.sleep(0),
        )
    finally:
        if dashboard_runner:
            await dashboard_runner.cleanup()
        await _startup_client.close_connection()
        if DB_MODULES_LOADED:
            await close_pool()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot dihentikan oleh user.")
