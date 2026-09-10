import asyncio
import time
import os
import sys
import csv
from datetime import datetime
from binance import AsyncClient
import pandas as pd

# Fix encoding agar emoji/karakter Unicode tidak crash di console Windows (charmap)
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
from core.risk_manager import calculate_risk_margin, count_open_positions, check_daily_loss_limit, calculate_atr_based_stop_loss
from core.trade_stats import record_closed_trade
from indicators.smart_buy import find_frequent_open_close_level, is_near_frequent_level
from indicators.volatility_breakout import calculate_squeeze_score, check_breakout
from core.scanner import get_funding_rate, check_order_book_depth
from core.pattern_memory import record_entry, record_result, score_entry, is_pattern_blacklisted

from ml_vision.chart_renderer import render_ohlcv_to_image
from ml_vision.preprocessor import preprocess_chart_image
from ml_vision.model import get_model
from ml_vision.inference import predict_candle_pattern

from telegram.bot_handler import bot, dp, bot_state
from telegram.notifier import (
    send_trade_notification,
    send_error_log,
    send_position_analysis_alert,
    send_early_close_notification,
)
from core.position_monitor import analyze_position

# Inisialisasi file log sukses virtual trading
if not os.path.exists("virtual_success_log.csv"):
    with open("virtual_success_log.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Symbol", "Tipe", "Entry Price", "TP Price", "Alasan", "Status"])

virtual_trades = {}

# Muat ML Model sekali di awal
ml_model = get_model("ml_vision/candle_model.pth") # Bisa diisi parameter model_path jika sudah ada weight

from collections import deque

class RateLimiter:
    def __init__(self, max_requests=200, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()

    async def wait_if_needed(self):
        now = time.time()
        
        while self.requests and now - self.requests[0] > self.time_window:
            self.requests.popleft()
            
        if len(self.requests) >= self.max_requests:
            sleep_time = self.time_window - (now - self.requests[0])
            if sleep_time > 0:
                print(f"[RATE LIMIT] Mencapai {len(self.requests)}/{self.max_requests} request per menit. Pause {sleep_time:.2f} detik...")
                await asyncio.sleep(sleep_time)
                
            now = time.time()
            while self.requests and now - self.requests[0] > self.time_window:
                self.requests.popleft()
                
        self.requests.append(now)
        return len(self.requests)

# Mengatur batas aman: misal 200 request per menit
rate_limiter = RateLimiter(max_requests=200, time_window=60)

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
                        if df.empty:
                            continue
                        
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
                                await bot.send_message(TELEGRAM_ERROR_CHAT_ID, msg)
                                
                                record_trade_result(v_trade['alasan'], is_profit=True)
                                # Update pattern memory: pola ini WIN
                                if 'entry_id' in v_trade:
                                    record_result(v_trade['entry_id'], is_win=True,
                                                  pnl=abs(v_trade['entry_price'] * 0.01))
                                
                                # Simpan ke CSV
                                with open("virtual_success_log.csv", "a", newline="") as f:
                                    writer = csv.writer(f)
                                    writer.writerow([v_trade['time'], symbol, v_trade['tipe'], v_trade['entry_price'], v_trade['tp_price'], v_trade['alasan'], "SUCCESS"])
                                    
                                del virtual_trades[symbol]
                            elif is_sl:
                                msg = f"📚 HASIL BELAJAR ({v_trade['tipe']}): Koin {symbol} gagal dan menyentuh Stop Loss. Alasan masuk sebelumnya: {v_trade['alasan']}. Perlu dievaluasi."
                                print(msg)
                                await bot.send_message(TELEGRAM_ERROR_CHAT_ID, msg)
                                
                                record_trade_result(v_trade['alasan'], is_profit=False)
                                # Update pattern memory: pola ini LOSS
                                if 'entry_id' in v_trade:
                                    record_result(v_trade['entry_id'], is_win=False,
                                                  pnl=-abs(v_trade['entry_price'] * 0.005))
                                del virtual_trades[symbol]
                        # -------------------------------------------------
                        
                        # Filter Anti Koin Receh / Micin
                        if current_price < 0.01:
                            # Agar terminal tidak spam, kita tidak perlu print terus-menerus
                            continue
                            
                        # 3. Hitung Indikator (Bollinger, Support, RSI, & Patterns)
                        df = calculate_bollinger_bands(df)
                        df = calculate_rsi(df, length=bot_config.rsi_length)
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
                        rsi_value = last_row.get('RSI', 50)
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
                        
                        squeeze_score = calculate_squeeze_score(df)
                        breakout_info = check_breakout(df)
                        
                        syarat_teknikal_long = near_lower_bb and near_support and is_oversold and htf_trend in ["UPTREND", "SIDEWAYS"]
                        syarat_pola_long = near_support and pattern_detected and pattern_type == 'LONG' and htf_trend in ["UPTREND", "SIDEWAYS"]
                        syarat_smart_buy_long = (
                            near_smart_buy_level
                            and htf_trend in ["UPTREND", "SIDEWAYS"]
                            and (last_row['close'] > last_row['open'] or is_oversold)
                        )
                        
                        syarat_teknikal_short = near_upper_bb and near_resistance and is_overbought and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        syarat_pola_short = near_resistance and pattern_detected and pattern_type == 'SHORT' and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        
                        # Gabungkan logika Breakout
                        is_breakout_long = breakout_info['detected'] and htf_trend in ["UPTREND", "SIDEWAYS"]
                        is_breakout_short = breakout_info['detected'] and htf_trend in ["DOWNTREND", "SIDEWAYS"]
                        
                        # Anti-Bull Trap (Untuk LONG)
                        bull_trap_detected = is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close'])
                        
                        if is_breakout_long:
                            alasan_long = f"Breakout Squeeze ({squeeze_score}) - {breakout_info['reason']} (HTF: {htf_trend})"
                        elif syarat_smart_buy_long:
                            alasan_long = (
                                f"Smart Buy level {smart_buy_level['level']:.8f} "
                                f"({smart_buy_level['touches']}x open/close 20D, HTF: {htf_trend})"
                            )
                        elif syarat_pola_long:
                            alasan_long = f"Pola {pattern_name} Terdeteksi! (HTF: {htf_trend})"
                        else:
                            alasan_long = f"RSI Oversold ({rsi_value:.2f}) (HTF: {htf_trend})"
                            
                        if is_breakout_short:
                            alasan_short = f"Breakout Squeeze ({squeeze_score}) - {breakout_info['reason']} (HTF: {htf_trend})"
                        elif syarat_pola_short:
                            alasan_short = f"Pola {pattern_name} Terdeteksi! (HTF: {htf_trend})"
                        else:
                            alasan_short = f"RSI Overbought ({rsi_value:.2f}) (HTF: {htf_trend})"
                        
                        # Evaluasi Keandalan dari Learner
                        reliable_long = is_pattern_reliable(alasan_long) if (syarat_teknikal_long or syarat_pola_long or syarat_smart_buy_long or is_breakout_long) else True
                        reliable_short = is_pattern_reliable(alasan_short) if (syarat_teknikal_short or syarat_pola_short or is_breakout_short) else True
                        
                        trigger_long = (syarat_teknikal_long or syarat_pola_long or syarat_smart_buy_long or is_breakout_long) and not bull_trap_detected and reliable_long
                        trigger_short = (syarat_teknikal_short or syarat_pola_short or is_breakout_short) and reliable_short
                        
                        if trigger_long or trigger_short:
                            batch_signal_found = True
                            trade_type = "LONG" if trigger_long else "SHORT"
                            alasan = alasan_long if trade_type == "LONG" else alasan_short
                            
                            print(f"SETUP {trade_type} DITEMUKAN PADA {symbol}! Squeeze Score: {squeeze_score}. Alasan: {alasan}")

                            # ── Pattern Memory: Susun kondisi saat ini untuk scoring ──
                            bb_zone = "LOWER" if near_lower_bb else ("UPPER" if near_upper_bb else "MID")
                            rsi_zone = ("OVERSOLD" if is_oversold else ("OVERBOUGHT" if is_overbought else "NEUTRAL"))
                            current_conditions = {
                                "side": trade_type,
                                "htf_trend": htf_trend,
                                "bb_zone": bb_zone,
                                "rsi_zone": rsi_zone,
                                "pattern": pattern_name if pattern_detected else "NONE",
                                "is_breakout": breakout_info.get('detected', False),
                                "squeeze_score": squeeze_score,
                            }
                            # Cek apakah pola ini masuk blacklist (sering rugi)
                            if is_pattern_blacklisted(current_conditions):
                                print(f"🚫 [PATTERN MEMORY] {symbol} {trade_type}: Pola ini masuk BLACKLIST berdasarkan riwayat. Skip.")
                                continue
                            # Hitung skor kecocokan dengan pola historis
                            pattern_score = score_entry(current_conditions)
                            print(f"🧠 [PATTERN MEMORY] {symbol} {trade_type}: Pattern Score = {pattern_score:.1f}")
                            
                            # Validasi Ekstra Lanjutan: Funding Rate & Liquidity
                            funding_rate = await get_funding_rate(client, symbol)
                            if abs(funding_rate) > bot_config.max_funding_rate_percent / 100.0:
                                print(f"⏩ Lewati {symbol}: Funding Rate terlalu tinggi ({funding_rate*100:.3f}%).")
                                continue
                                
                            liquidity = await check_order_book_depth(client, symbol, 1.0)
                            if trade_type == "LONG" and liquidity['asks_usdt'] < bot_config.min_order_book_depth_usdt:
                                print(f"⏩ Lewati {symbol}: Liquidity asks terlalu tipis (${liquidity['asks_usdt']:.2f}).")
                                continue
                            elif trade_type == "SHORT" and liquidity['bids_usdt'] < bot_config.min_order_book_depth_usdt:
                                print(f"⏩ Lewati {symbol}: Liquidity bids terlalu tipis (${liquidity['bids_usdt']:.2f}).")
                                continue
                            
                            # 5.5 Cek Modal dan Sesuaikan Margin (Modal harus 50x Margin)
                            try:
                                account_info = await client.futures_account()
                                modal = float(account_info['totalMarginBalance'])
                                
                                # Cek posisi terbuka dan hitung total posisi aktif
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
                                
                                # Cek Daily Loss Limit (Circuit Breaker)
                                limit_reached, current_loss = await check_daily_loss_limit(client, bot_config.daily_loss_limit_percent, modal)
                                if limit_reached:
                                    print(f"🛑 DAILY LOSS LIMIT TERCAPAI ({current_loss:.2f}%). Pause bot...")
                                    bot_state["is_running"] = False
                                    bot_state["state"] = "PAUSED"
                                    await bot.send_message(
                                        TELEGRAM_ADMIN_CHAT_ID,
                                        f"🛑 **CIRCUIT BREAKER AKTIF!**\nKerugian hari ini telah mencapai batas: `{current_loss:.2f}%` (Limit: {bot_config.daily_loss_limit_percent}%).\n\n"
                                        f"Bot otomatis dihentikan sementara untuk menghindari kerugian lebih lanjut.\n"
                                        f"Ketik `/resume` jika Anda ingin tetap melanjutkan."
                                    )
                                    break # Keluar dari loop koin
                                
                                # Batasan maksimal open posisi
                                active_positions = count_open_positions(positions)
                                if active_positions >= bot_config.max_open_positions:
                                    if symbol not in virtual_trades:
                                        if len(virtual_trades) < 15:
                                            print(f"⏩ Lewati {symbol}: Limit {bot_config.max_open_positions} posisi tercapai. Memasukkan ke mode Paper Trading.")
                                            
                                            # Kalkulasi pergerakan harga berbasis ROI
                                            pm_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                                            pm_sl = (bot_config.sl_percent / 100) / bot_config.leverage
                                            
                                            virtual_trades[symbol] = {
                                                'tipe': trade_type,
                                                'entry_price': current_price,
                                                'tp_price': current_price * (1 + pm_tp) if trade_type == 'LONG' else current_price * (1 - pm_tp),
                                                'sl_price': current_price * (1 - pm_sl) if trade_type == 'LONG' else current_price * (1 + pm_sl),
                                                'alasan': alasan,
                                                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                'conditions': current_conditions,
                                                'entry_id': record_entry(symbol, trade_type, current_price, current_conditions, alasan),
                                            }
                                        else:
                                            print(f"⏩ Lewati {symbol}: Kapasitas Paper Trading penuh (15 koin).")
                                    continue
                                    
                                current_margin = MARGIN_USDT
                                if modal < (50 * current_margin):
                                    current_margin = modal / 50
                                    print(f"[INFO] Modal ({modal:.2f} USDT) kurang dari 50x margin default ({MARGIN_USDT} USDT). Margin diubah ke: {current_margin:.2f} USDT")
                                    
                                if current_margin < 1:
                                    print(f"[WARNING] Margin ({current_margin:.2f}) terlalu kecil, batal beli.")
                                    continue

                                # Hitung Stop Price berbasis ATR untuk Volatility sizing
                                atr_value = float(df['ATR'].iloc[-1]) if 'ATR' in df else (current_price * 0.015)
                                planned_sl_price = calculate_atr_based_stop_loss(
                                    current_price, atr_value, bot_config.atr_multiplier_sl, trade_type
                                )
                                
                                risk_margin = calculate_risk_margin(
                                    modal,
                                    current_price,
                                    planned_sl_price,
                                    bot_config.leverage,
                                    bot_config.risk_per_trade_percent,
                                )
                                current_margin = min(current_margin, risk_margin)
                                if current_margin < 1:
                                    print(f"[WARNING] Risk budget menghasilkan margin terlalu kecil: {current_margin:.2f} USDT")
                                    continue
                            except Exception as e_bal:
                                print(f"[ERROR] Gagal mengecek saldo: {e_bal}")
                                continue
                                
                            # Tentukan apakah pakai Limit atau Market
                            use_limit_order = (trade_type == "LONG" and liquidity['asks_usdt'] < bot_config.min_order_book_depth_usdt * 2) or \
                                              (trade_type == "SHORT" and liquidity['bids_usdt'] < bot_config.min_order_book_depth_usdt * 2)
                            
                            limit_price = liquidity['best_ask'] if trade_type == "LONG" else liquidity['best_bid']
                            execution_price = limit_price if use_limit_order else current_price

                            # 6. Eksekusi Order
                            if trade_type == "LONG":
                                order_res = await place_long_order(
                                    client, symbol, execution_price, current_margin, bot_config.leverage,
                                    use_limit=use_limit_order, limit_timeout=bot_config.limit_order_timeout_seconds
                                )
                            else:
                                order_res = await place_short_order(
                                    client, symbol, execution_price, current_margin, bot_config.leverage,
                                    use_limit=use_limit_order, limit_timeout=bot_config.limit_order_timeout_seconds
                                )
                                
                            if order_res.get('status') == 'success':
                                quantity = order_res['quantity']
                                entry_price = order_res['price']
                                
                                # Hitung pergerakan harga TP dan jadikan Stop Price sebelumnya sebagai SL
                                if bot_config.use_trailing_stop:
                                    pm_tp = (bot_config.ts_activation_percent / 100) / bot_config.leverage
                                else:
                                    pm_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                                    
                                if trade_type == "LONG":
                                    tp_price = entry_price * (1 + pm_tp)
                                    sl_price = planned_sl_price
                                    tp_sl_side = 'SELL'
                                else:
                                    tp_price = entry_price * (1 - pm_tp)
                                    sl_price = planned_sl_price
                                    tp_sl_side = 'BUY'
                                
                                # Pasang TP / SL
                                protection_result = await place_take_profit_stop_loss(
                                    client, symbol, tp_sl_side, quantity, tp_price, sl_price,
                                    use_trailing_stop=bot_config.use_trailing_stop,
                                    callback_rate=bot_config.ts_callback_rate
                                )

                                if protection_result.get("status") != "success":
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
                                
                                # 7. Simpan snapshot ke Pattern Memory
                                entry_id = record_entry(
                                    symbol=symbol,
                                    side=trade_type,
                                    entry_price=entry_price,
                                    conditions=current_conditions,
                                    alasan=alasan,
                                )
                                bot_state.setdefault("active_trade_entry_ids", {})[symbol] = entry_id

                                # 8. Kirim Notifikasi
                                signal_score = round(
                                    sum([
                                        htf_trend in (["UPTREND", "SIDEWAYS"] if trade_type == "LONG" else ["DOWNTREND", "SIDEWAYS"]),
                                        near_support if trade_type == "LONG" else near_resistance,
                                        is_oversold if trade_type == "LONG" else is_overbought,
                                        syarat_smart_buy_long if trade_type == "LONG" else syarat_pola_short,
                                        not bull_trap_detected,
                                    ]) * 20 + pattern_score * 0.2,  # Bobot pattern memory
                                    1,
                                )
                                signal_score = min(signal_score, 100)
                                trade_data = {
                                    'symbol': symbol,
                                    'direction': trade_type,
                                    'price': f"{entry_price:.4f}",
                                    'entry_price': entry_price,
                                    'quantity': quantity,
                                    'margin_usdt': current_margin,
                                    'notional_usdt': current_margin * bot_config.leverage,
                                    'tp_price': tp_price,
                                    'sl_price': sl_price,
                                    'score': signal_score,
                                    'confidence': f"{signal_score:.1f}%",
                                    'tf': TIMEFRAME,
                                    'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    'margin': f"{current_margin:.2f} (Modal: {modal:.2f})",
                                    'leverage': bot_config.leverage,
                                    'tp_sl_info': f"TP: {tp_price:.4f} ({bot_config.tp_percent}%), SL: {sl_price:.4f} ({bot_config.sl_percent}%)",
                                    'syarat_1': f"Area {'Support' if trade_type == 'LONG' else 'Resistance'} Divalidasi. Tren {HTF_TIMEFRAME}: {htf_trend}",
                                    'syarat_2': alasan,
                                    'pola_ml': "-"
                                }
                                await send_trade_notification(bot, TELEGRAM_ADMIN_CHAT_ID, trade_data)
                                bot_state["active_trade_reasons"][symbol] = alasan
                                bot_state.setdefault("active_trade_meta", {})[symbol] = {
                                    "entry_time": datetime.now(),
                                    "entry_price": entry_price,
                                    "side": trade_type,
                                    "mfe": 0.0,
                                    "mae": 0.0,
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
                error_msg = f"Error in scanner loop: {str(loop_error)}"
                print(error_msg)
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
                            order_data = {
                                "symbol": symbol,
                                "order_type": order_type,
                                "price": order_info.get("ap"),
                                "quantity": order_info.get("q"),
                                "realized_pnl": f"{realized_pnl:.2f}",
                                "commission": commission,
                                "mfe": f"{float(meta.get('mfe', 0)):+.4f}",
                                "mae": f"{float(meta.get('mae', 0)):+.4f}",
                                "duration": f"{duration_minutes:.1f} menit" if duration_minutes is not None else "N/A",
                            }
                            record_closed_trade({
                                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "order_type": order_type,
                                "exit_price": order_data["price"],
                                "realized_pnl": realized_pnl,
                                "commission": commission,
                                "mfe": meta.get("mfe"),
                                "mae": meta.get("mae"),
                                "duration_minutes": duration_minutes,
                            })
                            await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)

                            alasan = bot_state.get("active_trade_reasons", {}).get(symbol)
                            if alasan:
                                record_trade_result(alasan, realized_pnl > 0)
                                del bot_state["active_trade_reasons"][symbol]

                            import csv
                            waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            if not os.path.exists("real_history_log.csv"):
                                with open("real_history_log.csv", "w", newline="", encoding="utf-8") as file:
                                    csv.writer(file).writerow(["Waktu", "Symbol", "Tipe", "Harga Eksekusi", "PnL"])

                            with open("real_history_log.csv", "a", newline="", encoding="utf-8") as file:
                                csv.writer(file).writerow([
                                    waktu_sekarang,
                                    order_data["symbol"],
                                    order_type,
                                    order_data["price"],
                                    order_data["realized_pnl"],
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

                for position in account_info.get("positions", []):
                    amount = float(position.get("positionAmt", 0))
                    profit = float(position.get("unrealizedProfit", 0))
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
                print(f"[POSITION MONITOR] {monitor_error}")
                await send_error_log(
                    bot,
                    TELEGRAM_ADMIN_CHAT_ID,
                    f"Position monitor error: {monitor_error}",
                )

            await asyncio.sleep(POSITION_MONITOR_INTERVAL_ENV)
    finally:
        await client.close_connection()


SMART_MONITOR_INTERVAL = 60  # cek posisi aktif tiap 60 detik

async def smart_position_monitor_loop():
    """
    Loop cerdas: memantau posisi yang sudah terbuka dan menjalankan
    analisa teknikal ulang. Jika sinyal berbalik → tutup posisi lebih awal.
    Juga mengirim peringatan jika Health Score mulai menurun.
    """
    testnet = TRADING_MODE == "TESTNET"
    client = await AsyncClient.create(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=testnet,
    )
    # Simpan health score sebelumnya agar tidak spam notif jika tidak berubah
    last_health: dict[str, float] = {}

    print("[SMART MONITOR] Loop analisa posisi aktif dimulai.")
    try:
        while True:
            await asyncio.sleep(SMART_MONITOR_INTERVAL)
            if not bot_state.get("is_running", False):
                continue
            try:
                account_info = await client.futures_account()
                positions = account_info.get("positions", [])
                active_positions = [
                    p for p in positions if float(p.get("positionAmt", 0)) != 0
                ]

                if not active_positions:
                    continue

                print(f"[SMART MONITOR] Menganalisa {len(active_positions)} posisi aktif...")

                for pos in active_positions:
                    symbol = pos["symbol"]
                    amt = float(pos["positionAmt"])
                    entry_price = float(pos["entryPrice"])
                    mark_price = float(pos.get("markPrice", entry_price))
                    unr_pnl = float(pos.get("unrealizedProfit", 0))
                    leverage = float(pos.get("leverage", bot_config.leverage))
                    initial_margin = (abs(amt) * entry_price / leverage) if leverage > 0 else 1
                    pnl_pct = (unr_pnl / initial_margin * 100) if initial_margin > 0 else 0
                    side = "LONG" if amt > 0 else "SHORT"

                    # Ambil OHLCV untuk analisa
                    df = await fetch_ohlcv(client, symbol, interval=TIMEFRAME, limit=100)
                    df_htf = await fetch_ohlcv(client, symbol, interval=HTF_TIMEFRAME, limit=60)
                    if df.empty or df_htf.empty or len(df) < 20:
                        continue

                    # Jalankan analisa posisi
                    result = analyze_position(
                        side=side,
                        entry_price=entry_price,
                        current_price=mark_price,
                        df=df,
                        df_htf=df_htf,
                        rsi_oversold=bot_config.rsi_oversold,
                        rsi_overbought=bot_config.rsi_overbought,
                        rsi_length=bot_config.rsi_length,
                        close_score_threshold=60.0,
                    )

                    print(
                        f"[SMART MONITOR] {symbol} {side} | "
                        f"Health: {result.health_score:.0f} | "
                        f"Decision: {result.decision} | "
                        f"Reasons: {result.reasons}"
                    )

                    # ── CLOSE: tutup posisi lebih awal ───────────────────────
                    if result.decision == "CLOSE":
                        print(f"[SMART EXIT] Menutup posisi {symbol} {side} — {result.reasons}")
                        close_side = "SELL" if side == "LONG" else "BUY"
                        close_res = await emergency_close_position(
                            client, symbol, close_side, abs(amt)
                        )
                        if close_res.get("status") == "success":
                            await send_early_close_notification(
                                bot, TELEGRAM_ADMIN_CHAT_ID,
                                {
                                    "symbol": symbol,
                                    "side": side,
                                    "health_score": result.health_score,
                                    "confidence": result.confidence,
                                    "reasons": result.reasons,
                                    "entry_price": entry_price,
                                    "close_price": mark_price,
                                    "pnl": unr_pnl,
                                    "pnl_pct": pnl_pct,
                                }
                            )
                            last_health.pop(symbol, None)
                        else:
                            await send_error_log(
                                bot, TELEGRAM_ADMIN_CHAT_ID,
                                f"[SMART EXIT GAGAL] {symbol}: {close_res.get('message')}"
                            )
                        continue

                    # ── HOLD: cek apakah health turun signifikan → kirim alert ─
                    prev_health = last_health.get(symbol, 100.0)
                    health_drop = prev_health - result.health_score

                    # Kirim alert hanya jika:
                    # - Ada peringatan aktif DAN
                    # - Health turun >= 20 poin dari sebelumnya (bukan spam)
                    if result.alerts and health_drop >= 20:
                        await send_position_analysis_alert(
                            bot, TELEGRAM_ADMIN_CHAT_ID,
                            {
                                "symbol": symbol,
                                "side": side,
                                "health_score": result.health_score,
                                "alerts": result.alerts,
                                "entry_price": entry_price,
                                "current_price": mark_price,
                                "pnl": unr_pnl,
                                "pnl_pct": pnl_pct,
                            }
                        )

                    last_health[symbol] = result.health_score
                    await asyncio.sleep(1)  # jeda antar koin agar tidak rate limit

            except Exception as smart_err:
                print(f"[SMART MONITOR ERROR] {smart_err}")
                await send_error_log(bot, TELEGRAM_ADMIN_CHAT_ID, f"Smart Monitor Error: {smart_err}")
    finally:
        await client.close_connection()

async def main():
    # Menjalankan Polling Telegram, Scanner, WebSocket, dan Smart Monitor secara bersamaan
    await asyncio.gather(
        dp.start_polling(bot),
        scanner_loop(),
        user_data_stream_loop(),
        profitable_position_monitor_loop(),
        smart_position_monitor_loop(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot dihentikan oleh user.")
