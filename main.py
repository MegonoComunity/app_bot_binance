import asyncio
import time
import os
import csv
from datetime import datetime
from binance import AsyncClient
import pandas as pd

from config.settings import (
    BINANCE_API_KEY, BINANCE_API_SECRET, TRADING_MODE,
    SCAN_INTERVAL_SECONDS, TIMEFRAME, API_REQUEST_DELAY,
    TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_ERROR_CHAT_ID, MARGIN_USDT,
    bot_config
)

from core.scanner import get_top_futures_by_volume, fetch_ohlcv
from core.order_manager import place_long_order, place_short_order, place_take_profit_stop_loss
from indicators.bollinger import calculate_bollinger_bands
from indicators.support_resistance import detect_support_zones, is_near_support, detect_resistance_zones, is_near_resistance
from indicators.rsi import calculate_rsi
from indicators.patterns import detect_candlestick_patterns, is_bull_trap
from core.learner import is_pattern_reliable, record_trade_result

from ml_vision.chart_renderer import render_ohlcv_to_image
from ml_vision.preprocessor import preprocess_chart_image
from ml_vision.model import get_model
from ml_vision.inference import predict_candle_pattern

from telegram.bot_handler import bot, dp, bot_state
from telegram.notifier import send_trade_notification, send_error_log

# Inisialisasi file log sukses virtual trading
if not os.path.exists("virtual_success_log.csv"):
    with open("virtual_success_log.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Symbol", "Tipe", "Entry Price", "TP Price", "Alasan", "Status"])

virtual_trades = {}

# Muat ML Model sekali di awal
ml_model = get_model() # Bisa diisi parameter model_path jika sudah ada weight

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
    
    print(f"Bot Started in {TRADING_MODE} Mode. Timeframe: {TIMEFRAME}")
    
    try:
        while True:
            if not bot_state["is_running"]:
                await asyncio.sleep(5)
                continue
                
            try:
                # 1. Dapatkan semua koin
                all_symbols = await get_top_futures_by_volume(client, None)
                
                batch_size = 10
                for i in range(0, len(all_symbols), batch_size):
                    batch_symbols = all_symbols[i:i+batch_size]
                    tahap = (i // batch_size) + 1
                    
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
                            
                        current_price = df.iloc[-1]['close']
                        
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
                                del virtual_trades[symbol]
                        # -------------------------------------------------
                        
                        # Filter Anti Koin Receh / Micin
                        if current_price < 0.01:
                            # Agar terminal tidak spam, kita tidak perlu print terus-menerus
                            continue
                            
                        # 3. Hitung Indikator (Bollinger, Support, RSI, & Patterns)
                        df = calculate_bollinger_bands(df)
                        df = calculate_rsi(df)
                        support_zones = detect_support_zones(df)
                        pattern_info = detect_candlestick_patterns(df)
                        
                        last_row = df.iloc[-1]
                        current_price = last_row['close']
                        
                        # 4. Cek Kondisi Teknikal Entry LONG
                        near_lower_bb = last_row.get('is_near_lower_band', False)
                        near_support = is_near_support(current_price, support_zones)
                        rsi_value = last_row.get('RSI', 50)
                        is_oversold = rsi_value < 35
                        
                        # 4. Cek Kondisi Teknikal Entry SHORT
                        upper_band = last_row.get('upper_band')
                        near_upper_bb = current_price >= upper_band * 0.995 if pd.notnull(upper_band) else False
                        resistance_zones = detect_resistance_zones(df)
                        near_resistance = is_near_resistance(current_price, resistance_zones)
                        is_overbought = rsi_value > 75
                        
                        pattern_detected = pattern_info['detected']
                        pattern_name = pattern_info['pattern']
                        pattern_type = pattern_info['type']
                        
                        syarat_teknikal_long = near_lower_bb and near_support and is_oversold
                        syarat_pola_long = near_support and pattern_detected and pattern_type == 'LONG'
                        
                        syarat_teknikal_short = near_upper_bb and near_resistance and is_overbought
                        syarat_pola_short = near_resistance and pattern_detected and pattern_type == 'SHORT'
                        
                        # Anti-Bull Trap (Untuk LONG)
                        bull_trap_detected = is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close'])
                        
                        alasan_long = f"Pola {pattern_name} Terdeteksi!" if syarat_pola_long else f"RSI Oversold ({rsi_value:.2f})"
                        alasan_short = f"Pola {pattern_name} Terdeteksi!" if syarat_pola_short else f"RSI Overbought ({rsi_value:.2f})"
                        
                        # Evaluasi Keandalan dari Learner
                        reliable_long = is_pattern_reliable(alasan_long) if (syarat_teknikal_long or syarat_pola_long) else True
                        reliable_short = is_pattern_reliable(alasan_short) if (syarat_teknikal_short or syarat_pola_short) else True
                        
                        trigger_long = (syarat_teknikal_long or syarat_pola_long) and not bull_trap_detected and reliable_long
                        trigger_short = (syarat_teknikal_short or syarat_pola_short) and reliable_short
                        
                        if trigger_long or trigger_short:
                            trade_type = "LONG" if trigger_long else "SHORT"
                            alasan = alasan_long if trade_type == "LONG" else alasan_short
                            
                            print(f"SETUP TEKNIKAL {trade_type} DITEMUKAN PADA {symbol}! Alasan: {alasan}")
                            
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
                                
                                # Batasan maksimal open posisi
                                if trade_type == "LONG" and active_longs >= 4:
                                    if symbol not in virtual_trades:
                                        if len(virtual_trades) < 15:
                                            print(f"⏩ Lewati {symbol}: Limit 4 posisi LONG tercapai. Memasukkan ke mode Paper Trading.")
                                            
                                            # Kalkulasi pergerakan harga berbasis ROI
                                            pm_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                                            pm_sl = (bot_config.sl_percent / 100) / bot_config.leverage
                                            
                                            virtual_trades[symbol] = {
                                                'tipe': 'LONG',
                                                'entry_price': current_price,
                                                'tp_price': current_price * (1 + pm_tp),
                                                'sl_price': current_price * (1 - pm_sl),
                                                'alasan': alasan,
                                                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                            }
                                        else:
                                            print(f"⏩ Lewati {symbol}: Kapasitas Paper Trading penuh (15 koin).")
                                    continue
                                    
                                if trade_type == "SHORT" and active_shorts >= 4:
                                    if symbol not in virtual_trades:
                                        if len(virtual_trades) < 15:
                                            print(f"⏩ Lewati {symbol}: Limit 4 posisi SHORT tercapai. Memasukkan ke mode Paper Trading.")
                                            
                                            # Kalkulasi pergerakan harga berbasis ROI
                                            pm_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                                            pm_sl = (bot_config.sl_percent / 100) / bot_config.leverage
                                            
                                            virtual_trades[symbol] = {
                                                'tipe': 'SHORT',
                                                'entry_price': current_price,
                                                'tp_price': current_price * (1 - pm_tp),
                                                'sl_price': current_price * (1 + pm_sl),
                                                'alasan': alasan,
                                                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                            except Exception as e_bal:
                                print(f"[ERROR] Gagal mengecek saldo: {e_bal}")
                                continue
                                
                            # 6. Eksekusi Order
                            if trade_type == "LONG":
                                order_res = await place_long_order(
                                    client, symbol, current_price, current_margin, bot_config.leverage
                                )
                            else:
                                order_res = await place_short_order(
                                    client, symbol, current_price, current_margin, bot_config.leverage
                                )
                                
                            if order_res.get('status') == 'success':
                                quantity = order_res['quantity']
                                entry_price = order_res['price']
                                
                                # Hitung pergerakan harga berdasarkan Target ROI dan Leverage
                                pm_tp = (bot_config.tp_percent / 100) / bot_config.leverage
                                pm_sl = (bot_config.sl_percent / 100) / bot_config.leverage
                                
                                if trade_type == "LONG":
                                    tp_price = entry_price * (1 + pm_tp)
                                    sl_price = entry_price * (1 - pm_sl)
                                    tp_sl_side = 'SELL'
                                else:
                                    tp_price = entry_price * (1 - pm_tp)
                                    sl_price = entry_price * (1 + pm_sl)
                                    tp_sl_side = 'BUY'
                                
                                # Pasang TP / SL
                                await place_take_profit_stop_loss(
                                    client, symbol, tp_sl_side, quantity, tp_price, sl_price
                                )
                                
                                # 7. Kirim Notifikasi
                                trade_data = {
                                    'symbol': symbol,
                                    'price': f"{entry_price:.4f}",
                                    'tf': TIMEFRAME,
                                    'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                    'margin': f"{current_margin:.2f} (Modal: {modal:.2f})",
                                    'leverage': bot_config.leverage,
                                    'tp_sl_info': f"TP: {tp_price:.4f} ({bot_config.tp_percent}%), SL: {sl_price:.4f} ({bot_config.sl_percent}%)",
                                    'syarat_1': f"Area {'Support' if trade_type == 'LONG' else 'Resistance'} Divalidasi",
                                    'syarat_2': alasan,
                                    'pola_ml': "-"
                                }
                                await send_trade_notification(bot, TELEGRAM_ADMIN_CHAT_ID, trade_data)
                                bot_state["active_trade_reasons"][symbol] = alasan
                                
                            # Hindari spam trade di koin yang sama, beri jeda
                            await asyncio.sleep(10)
                            
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
    
    bm = BinanceSocketManager(client)
    # Untuk futures testnet atau production
    ts = bm.futures_user_socket()
    
    print("Menghubungkan ke User Data Stream (WebSocket)...")
    try:
        async with ts as stream:
            print("Berhasil terhubung ke WebSocket Binance.")
            while True:
                res = await stream.recv()
                
                # Tangkap event ORDER_TRADE_UPDATE
                if res.get('e') == 'ORDER_TRADE_UPDATE':
                    order_info = res.get('o', {})
                    order_status = order_info.get('X')
                    
                    # Jika order tereksekusi
                    if order_status == 'FILLED':
                        order_type = order_info.get('o', '')
                        
                        # Filter hanya TP dan SL
                        if 'TAKE_PROFIT' in order_type or 'STOP' in order_type:
                            symbol = order_info.get('s')
                            realized_pnl = float(order_info.get('rp', '0.0'))
                            
                            order_data = {
                                'symbol': symbol,
                                'order_type': order_type,
                                'price': order_info.get('ap'), # Average Price eksekusi
                                'quantity': order_info.get('q'),
                                'realized_pnl': f"{realized_pnl:.2f}"
                            }
                            await send_order_filled_notification(bot, TELEGRAM_ADMIN_CHAT_ID, order_data)
                            
                            alasan = bot_state.get("active_trade_reasons", {}).get(symbol)
                            if alasan:
                                is_profit = realized_pnl > 0
                                record_trade_result(alasan, is_profit)
                                del bot_state["active_trade_reasons"][symbol]
                            
                            # Simpan histori riwayat ke CSV
                            import csv
                            waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            if not os.path.exists("real_history_log.csv"):
                                with open("real_history_log.csv", "w", newline="", encoding="utf-8") as f:
                                    writer = csv.writer(f)
                                    writer.writerow(["Waktu", "Symbol", "Tipe", "Harga Eksekusi", "PnL"])
                                    
                            with open("real_history_log.csv", "a", newline="", encoding="utf-8") as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    waktu_sekarang, 
                                    order_data['symbol'], 
                                    order_type, 
                                    order_data['price'], 
                                    order_data['realized_pnl']
                                ])
                                
                elif res.get('e') == 'ACCOUNT_UPDATE':
                    # Pantau PNL posisi untuk memperingatkan jika mendekati SL
                    positions = res.get('a', {}).get('P', [])
                    for pos in positions:
                        pos_amt = float(pos.get('pa', 0))
                        if pos_amt > 0: # Ada posisi LONG
                            symbol = pos.get('s')
                            unrealized_pnl = float(pos.get('up', 0))
                            margin_type = pos.get('mt')
                            entry_price = float(pos.get('ep', 0))
                            
                            if entry_price > 0:
                                # Estimasi persentase SL (kita tau bot_config.sl_percent)
                                loss_percent = (unrealized_pnl / (entry_price * pos_amt / bot_config.leverage)) * 100
                                # Jika kerugian mendekati SL (misal > 80% dari SL)
                                if loss_percent <= -(bot_config.sl_percent * 0.8):
                                    print(f"[WARNING] {symbol} mendekati Stop Loss! (Loss: {loss_percent:.2f}%). Catat dan pelajari pola pantulnya.")
    except Exception as e:
        error_msg = f"Error in User Data Stream: {str(e)}"
        print(error_msg)
        await send_error_log(bot, TELEGRAM_ADMIN_CHAT_ID, error_msg)
    finally:
        await client.close_connection()

async def main():
    # Menjalankan Polling Telegram, Scanner, dan WebSocket secara bersamaan
    await asyncio.gather(
        dp.start_polling(bot),
        scanner_loop(),
        user_data_stream_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot dihentikan oleh user.")
