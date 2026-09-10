import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from config.settings import (
    TELEGRAM_ADMIN_CHAT_ID,
    TELEGRAM_ADMIN_USER_IDS,
    TELEGRAM_BOT_TOKEN,
    AUTO_CLOSE_PROFIT_HOURS_ENV,
    POSITION_MONITOR_INTERVAL_ENV,
    RSI_LENGTH_ENV,
    RSI_OVERSOLD_ENV,
    RSI_OVERBOUGHT_ENV,
    SCANNER_MODE_ENV,
    ANALYSIS_LOOKBACK_DAYS_ENV,
    bot_config,
)
from core.order_manager import close_profitable_position
from core.market_analysis import analyze_daily_market
from core.risk_manager import calculate_account_pnl_percent, calculate_position_pnl_percent
from core.trade_stats import trade_summary

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        chat = getattr(event, "chat", None)

        is_authorized = (
            user is not None
            and chat is not None
            and user.id in TELEGRAM_ADMIN_USER_IDS
            and chat.id == TELEGRAM_ADMIN_CHAT_ID
        )
        if not is_authorized:
            return None

        return await handler(event, data)


dp.message.middleware(AdminOnlyMiddleware())

# Setup Folder Dataset
os.makedirs("dataset/BULLISH", exist_ok=True)
os.makedirs("dataset/BEARISH", exist_ok=True)
os.makedirs("dataset/NEUTRAL", exist_ok=True)

class DatasetForm(StatesGroup):
    waiting_for_image = State()
    waiting_for_label = State()

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
import os

# Global state for bot
bot_state = {
    "is_running": False,
    "state": "PAUSED",
    "websocket_connected": False,
}

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📊 Status Bot"), KeyboardButton(text="⚙️ Pengaturan")],
        [KeyboardButton(text="📈 Histori TP"), KeyboardButton(text="📉 Histori SL")],
        [KeyboardButton(text="🔎 Analisa Koin"), KeyboardButton(text="🔻 Close SHORT")],
        [KeyboardButton(text="🔢 Scan Modus"), KeyboardButton(text="⛔ Close ALL")],
        [KeyboardButton(text="⏯️ Pause / Resume"), KeyboardButton(text="📸 Upload Dataset")],
        [KeyboardButton(text="📞 Bantuan")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "🚀 Trading Bot is Online!\nSilakan pilih menu di bawah ini:",
        reply_markup=get_main_keyboard()
    )

import time
from datetime import datetime

@dp.message(Command("status"))
async def status_handler(message: types.Message):
    state = bot_state.get("state", "PAUSED")
    if state not in {"DEGRADED", "KILL_SWITCH", "RECONCILING"}:
        state = "RUNNING" if bot_state.get("is_running", False) else "PAUSED"
        bot_state["state"] = state
    status_emoji = "🟢 RUNNING" if state == "RUNNING" else f"🔴 {state}"
    client = bot_state.get("client")
    
    if not client:
        await message.answer(f"Status Bot: {status_emoji}\n⚠️ Koneksi ke Binance belum siap. Coba lagi dalam beberapa detik.")
        return
        
    wait_msg = await message.answer("🔄 Mengambil data dari Binance...")
    
    try:
        account_info = await client.futures_account()
        total_margin = float(account_info['totalMarginBalance'])
        unrealized_pnl = float(account_info['totalUnrealizedProfit'])
        
        positions = account_info.get('positions', [])
        active_positions = [p for p in positions if float(p['positionAmt']) != 0]
        
        longs = []
        shorts = []
        
        for p in active_positions:
            symbol = p['symbol']
            amt = float(p['positionAmt'])
            pnl = float(p['unrealizedProfit'])
            pnl_percent = calculate_position_pnl_percent(p)
            entry = float(p['entryPrice'])
            mark = float(p.get('markPrice', entry))
            
            # Gunakan updateTime sebagai acuan (waktu transaksi terakhir di posisi ini)
            update_time_ms = int(p.get('updateTime', 0))
            if update_time_ms > 0:
                open_time = datetime.fromtimestamp(update_time_ms / 1000)
                diff = datetime.now() - open_time
                hours, remainder = divmod(diff.total_seconds(), 3600)
                minutes, _ = divmod(remainder, 60)
                hold_time = f"{int(hours)}j {int(minutes)}m"
            else:
                hold_time = "N/A"
                
            # Format harga agar presisi koin micin tidak terpotong (dinamis 4-8 desimal)
            entry_str = f"{entry:.8f}".rstrip('0').rstrip('.')
            mark_str = f"{mark:.8f}".rstrip('0').rstrip('.')

            p_info = (f"🔸 **{symbol}**\n"
                      f"   PNL berjalan: `{pnl:+.2f} USDT ({pnl_percent:+.2f}%)`\n"
                      f"   Hold: {hold_time}\n"
                      f"   Entry: {entry_str} | Mark: {mark_str}\n")
                      
            if amt > 0:
                longs.append(p_info)
            else:
                shorts.append(p_info)
                
        websocket_status = "connected" if bot_state.get("websocket_connected") else "disconnected"
        account_pnl_percent = calculate_account_pnl_percent(unrealized_pnl, total_margin)
        text = (
            f"🤖 **PROFIL & STATUS BOT**\n"
            f"Status: {status_emoji}\n"
            f"WebSocket: `{websocket_status}`\n"
            f"💰 Saldo Total: `{total_margin:.2f} USDT`\n"
            f"📈 Unr. PNL  : `{unrealized_pnl:+.2f} USDT ({account_pnl_percent:+.2f}%)`\n"
            f"──────────────\n"
            f"**🟢 POSISI LONG ({len(longs)}/{bot_config.max_open_positions})**\n"
        )
        if longs:
            text += "".join(longs)
        else:
            text += "   _Tidak ada posisi_\n"
            
        text += f"\n**🔴 POSISI SHORT ({len(shorts)}/{bot_config.max_open_positions})**\n"
        if shorts:
            text += "".join(shorts)
        else:
            text += "   _Tidak ada posisi_\n"
            
        await wait_msg.edit_text(text, parse_mode="Markdown")
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ Gagal mengambil profil: {e}")


@dp.message(Command("trade_stats"))
async def trade_stats_handler(message: types.Message):
    summary = trade_summary()
    await message.answer(
        "📊 **REKAP WIN RATE TRADING**\n\n"
        f"Total trade: `{summary['total']}`\n"
        f"Win/Loss: `{summary['wins']}W / {summary['losses']}L`\n"
        f"Win rate: `{summary['win_rate']:.1f}%`\n"
        f"Total PNL bersih: `{summary['net_pnl']:+.4f} USDT`\n"
        f"Komisi: `{summary['commission']:.4f} USDT`\n"
        f"PNL hari ini: `{summary['daily_net_pnl']:+.4f} USDT`\n"
        f"Win rate hari ini: `{(summary['daily_wins'] / summary['daily_total'] * 100) if summary['daily_total'] else 0:.1f}%`\n",
        parse_mode="Markdown",
    )


@dp.message(Command("analyze"))
async def analyze_handler(message: types.Message, command: CommandObject):
    symbol = (command.args or "").strip().upper()
    if not symbol:
        await message.answer("Format: /analyze BTCUSDT")
        return

    client = bot_state.get("client")
    if not client:
        await message.answer("⚠️ Koneksi Binance belum siap.")
        return

    wait_msg = await message.answer(f"🔎 Menganalisis {symbol} dengan candle Daily...")
    try:
        from core.scanner import fetch_ohlcv

        daily_df = await fetch_ohlcv(
            client,
            symbol,
            interval="1d",
            limit=bot_config.analysis_lookback_days + 2,
        )
        result = analyze_daily_market(
            daily_df,
            bot_config.analysis_lookback_days,
            bot_config.rsi_length,
            bot_config.rsi_oversold,
            bot_config.rsi_overbought,
            0.005,
        )
        smart_level = result["smart_level"]
        smart_level_text = f"{smart_level:.8f}" if smart_level else "N/A"
        text = (
            f"📊 **ANALISIS DAILY {symbol}**\n\n"
            f"Candle closed: `{result['candles']}`\n"
            f"Harga terakhir: `{result['current_price']:.8f}`\n"
            f"RSI({bot_config.rsi_length}): `{result['rsi']:.2f}`\n"
            f"Oversold/Overbought: `{bot_config.rsi_oversold:g}/{bot_config.rsi_overbought:g}`\n"
            f"Tren Daily: `{result['trend']}`\n"
            f"Level open/close paling sering: `{smart_level_text}`\n"
            f"Jumlah sentuhan: `{result['smart_touches']}x`\n"
            f"Dekat level: `{result['near_smart_level']}`\n\n"
            f"**Kesimpulan: {result['decision']}**"
        )
        await wait_msg.edit_text(text, parse_mode="Markdown")
    except Exception as error:
        await wait_msg.edit_text(f"❌ Analisis {symbol} gagal: {error}")


@dp.message(Command("scan_modus"))
async def scan_modus_handler(message: types.Message, command: CommandObject):
    symbol = (command.args or "").strip().upper()
    if not symbol:
        await message.answer("Format: /scan_modus BTCUSDT")
        return

    client = bot_state.get("client")
    if not client:
        await message.answer("⚠️ Koneksi Binance belum siap.")
        return

    wait_msg = await message.answer(f"🔢 Scan modus open/close Daily {symbol}...")
    try:
        from core.scanner import fetch_ohlcv

        daily_df = await fetch_ohlcv(
            client,
            symbol,
            interval="1d",
            limit=bot_config.analysis_lookback_days + 2,
        )
        result = analyze_daily_market(
            daily_df,
            bot_config.analysis_lookback_days,
            bot_config.rsi_length,
            bot_config.rsi_oversold,
            bot_config.rsi_overbought,
            0.005,
        )
        level = result["smart_level"]
        level_text = f"{level:.8f}" if level else "N/A"
        distance = ((result["current_price"] - level) / level * 100) if level else 0
        text = (
            f"🔢 **SCAN MODUS DAILY {symbol}**\n\n"
            f"Periode: `{result['candles']} candle closed`\n"
            f"Harga sekarang: `{result['current_price']:.8f}`\n"
            f"Modus open/close: `{level_text}`\n"
            f"Kemunculan dalam zona: `{result['smart_touches']}x`\n"
            f"Jarak harga dari modus: `{distance:+.2f}%`\n"
            f"Dekat modus: `{result['near_smart_level']}`\n"
            f"RSI: `{result['rsi']:.2f}` | Tren: `{result['trend']}`\n\n"
            f"**Hasil: {result['decision']}**"
        )
        await wait_msg.edit_text(text, parse_mode="Markdown")
    except Exception as error:
        await wait_msg.edit_text(f"❌ Scan modus {symbol} gagal: {error}")


async def close_position_from_telegram(symbol: str, close_all: bool = False) -> list[str]:
    client = bot_state.get("client")
    if not client:
        raise RuntimeError("Koneksi Binance belum siap")

    account_info = await client.futures_account()
    results = []
    for position in account_info.get("positions", []):
        position_symbol = position.get("symbol")
        amount = float(position.get("positionAmt", 0))
        if amount == 0 or (not close_all and position_symbol != symbol):
            continue

        result = await close_profitable_position(client, position_symbol, amount)
        results.append(f"{position_symbol}: {result.get('status')}")

    if not results and not close_all:
        results.append(f"{symbol}: posisi tidak ditemukan")
    if not results:
        results.append("Tidak ada posisi terbuka")
    return results


@dp.message(Command("close_short"))
async def close_short_handler(message: types.Message, command: CommandObject):
    symbol = (command.args or "").strip().upper()
    if not symbol:
        await message.answer("Format: /close_short BTCUSDT")
        return

    try:
        client = bot_state.get("client")
        account_info = await client.futures_account() if client else None
        short_position = next(
            (
                position for position in (account_info or {}).get("positions", [])
                if position.get("symbol") == symbol and float(position.get("positionAmt", 0)) < 0
            ),
            None,
        )
        if not short_position:
            await message.answer(f"ℹ️ Tidak ada posisi SHORT pada {symbol}.")
            return

        result = await close_position_from_telegram(symbol)
        await message.answer(f"🔻 Close paksa SHORT {symbol}: {result[0]}")
    except Exception as error:
        await message.answer(f"❌ Gagal close SHORT {symbol}: {error}")


@dp.message(Command("close"))
async def close_handler(message: types.Message, command: CommandObject):
    symbol = (command.args or "").strip().upper()
    if not symbol:
        await message.answer("Format: /close BTCUSDT")
        return

    try:
        results = await close_position_from_telegram(symbol)
        await message.answer(f"⛔ Close paksa {symbol}: {results[0]}")
    except Exception as error:
        await message.answer(f"❌ Gagal close {symbol}: {error}")


@dp.message(Command("close_all"))
async def close_all_handler(message: types.Message):
    try:
        results = await close_position_from_telegram("", close_all=True)
        await message.answer("⛔ Close all:\n" + "\n".join(results))
    except Exception as error:
        await message.answer(f"❌ Gagal close all: {error}")


@dp.message(F.text == "🔻 Close SHORT")
async def btn_close_short_handler(message: types.Message):
    await message.answer("Gunakan format: `/close_short BTCUSDT`", parse_mode="Markdown")


@dp.message(F.text == "🔢 Scan Modus")
async def btn_scan_modus_handler(message: types.Message):
    await message.answer("Gunakan format: `/scan_modus BTCUSDT`", parse_mode="Markdown")


@dp.message(F.text == "🔎 Analisa Koin")
async def btn_analyze_handler(message: types.Message):
    await message.answer("Gunakan format: `/analyze BTCUSDT`", parse_mode="Markdown")


@dp.message(F.text == "⛔ Close ALL")
async def btn_close_all_handler(message: types.Message):
    await close_all_handler(message)

@dp.message(Command("stop"))
async def stop_handler(message: types.Message):
    bot_state["is_running"] = False
    bot_state["state"] = "PAUSED"
    await message.answer("🛑 Bot Scanner dihentikan sementara.")

@dp.message(Command("resume"))
async def resume_handler(message: types.Message):
    bot_state["is_running"] = True
    bot_state["state"] = "RUNNING"
    await message.answer("▶️ Bot Scanner dijalankan kembali.")

@dp.message(Command("set_tp"))
async def set_tp_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            bot_config.update_tp(val)
            await message.answer(f"✅ Take Profit berhasil diubah menjadi {val}%")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_tp 25.5")
    else:
        await message.answer(f"ℹ️ TP saat ini: {bot_config.tp_percent}% (Gunakan /set_tp <angka> untuk mengubah)")

@dp.message(Command("set_sl"))
async def set_sl_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            bot_config.update_sl(val)
            await message.answer(f"✅ Stop Loss berhasil diubah menjadi {val}%")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_sl 35")
    else:
        await message.answer(f"ℹ️ SL saat ini: {bot_config.sl_percent}% (Gunakan /set_sl <angka> untuk mengubah)")


@dp.message(Command("set_rsi"))
async def set_rsi_handler(message: types.Message, command: CommandObject):
    values = (command.args or "").split()
    if len(values) != 3:
        await message.answer("Format: /set_rsi LENGTH OVERSOLD OVERBOUGHT\nContoh: /set_rsi 14 35 75")
        return
    try:
        bot_config.update_rsi(int(values[0]), float(values[1]), float(values[2]))
        await message.answer(
            f"✅ RSI diubah: length={bot_config.rsi_length}, "
            f"oversold={bot_config.rsi_oversold:g}, overbought={bot_config.rsi_overbought:g}"
        )
    except ValueError as error:
        await message.answer(f"❌ Setting RSI tidak valid: {error}")


@dp.message(Command("set_scanner_mode"))
async def set_scanner_mode_handler(message: types.Message, command: CommandObject):
    mode = (command.args or "").strip().lower()
    if not mode:
        await message.answer("Mode saat ini: " + bot_config.scanner_mode + "\nGunakan: /set_scanner_mode per_coin atau batch")
        return
    try:
        bot_config.update_scanner_mode(mode)
        await message.answer(f"✅ Scanner mode diubah menjadi `{mode}`", parse_mode="Markdown")
    except ValueError as error:
        await message.answer(f"❌ {error}")


@dp.message(Command("set_analysis_days"))
async def set_analysis_days_handler(message: types.Message, command: CommandObject):
    try:
        days = int((command.args or "").strip())
        bot_config.update_analysis_lookback_days(days)
        await message.answer(f"✅ Analisis Daily menggunakan {days} candle closed")
    except ValueError as error:
        await message.answer(f"❌ {error}\nMinimal adalah 20 hari.")

@dp.message(Command("set_leverage"))
async def set_leverage_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = int(command.args)
            if val < 1 or val > 125:
                await message.answer("❌ Leverage harus antara 1 dan 125")
                return
            bot_config.update_leverage(val)
            await message.answer(f"✅ Leverage berhasil diubah menjadi {val}x")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_leverage 20")
    else:
        await message.answer(f"ℹ️ Leverage saat ini: {bot_config.leverage}x (Gunakan /set_leverage <angka> untuk mengubah)")

@dp.message(Command("set_max_positions"))
async def set_max_positions_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = int(command.args)
            if val < 1:
                await message.answer("❌ Maksimal posisi harus lebih besar dari 0")
                return
            bot_config.update_max_positions(val)
            await message.answer(f"✅ Maksimal open posisi berhasil diubah menjadi {val}")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_max_positions 5")
    else:
        await message.answer(f"ℹ️ Maksimal posisi saat ini: {bot_config.max_open_positions} (Gunakan /set_max_positions <angka> untuk mengubah)")

@dp.message(Command("set_ts_use"))
async def set_ts_use_handler(message: types.Message, command: CommandObject):
    if command.args:
        val_str = command.args.lower()
        if val_str in ['true', '1', 'on']:
            bot_config.update_use_trailing_stop(True)
            await message.answer("✅ Trailing Stop diaktifkan (ON)")
        elif val_str in ['false', '0', 'off']:
            bot_config.update_use_trailing_stop(False)
            await message.answer("✅ Trailing Stop dinonaktifkan (OFF)")
        else:
            await message.answer("❌ Format salah. Contoh: /set_ts_use True atau /set_ts_use False")
    else:
        status = "ON" if bot_config.use_trailing_stop else "OFF"
        await message.answer(f"ℹ️ Trailing Stop saat ini: {status} (Gunakan /set_ts_use <True/False> untuk mengubah)")

@dp.message(Command("set_ts_activation"))
async def set_ts_activation_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            bot_config.update_ts_activation(val)
            await message.answer(f"✅ TS Activation berhasil diubah menjadi {val}%")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_ts_activation 15.0")
    else:
        await message.answer(f"ℹ️ TS Activation saat ini: {bot_config.ts_activation_percent}% (Gunakan /set_ts_activation <angka> untuk mengubah)")

@dp.message(Command("set_ts_callback"))
async def set_ts_callback_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            bot_config.update_ts_callback_rate(val)
            await message.answer(f"✅ TS Callback Rate berhasil diubah menjadi {val}%")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: /set_ts_callback 1.0")
    else:
        await message.answer(f"ℹ️ TS Callback saat ini: {bot_config.ts_callback_rate}% (Gunakan /set_ts_callback <angka> untuk mengubah)")

@dp.message(F.text == "📊 Status Bot")
async def btn_status_handler(message: types.Message):
    await status_handler(message)

@dp.message(F.text == "⚙️ Pengaturan")
async def btn_pengaturan_handler(message: types.Message):
    ts_status = "ON" if bot_config.use_trailing_stop else "OFF"
    text = (
        "⚙️ **PENGATURAN SAAT INI** ⚙️\n\n"
        f"🎯 Take Profit : {bot_config.tp_percent}%\n"
        f"🛑 Stop Loss   : {bot_config.sl_percent}%\n"
        f"⚡ Leverage    : {bot_config.leverage}x\n"
        f"📊 Max Posisi  : {bot_config.max_open_positions}\n"
        f"🧭 Scanner Mode: {bot_config.scanner_mode}\n"
        f"📅 Analisis Daily: {bot_config.analysis_lookback_days} candle\n"
        f"📉 RSI: {bot_config.rsi_length} / {bot_config.rsi_oversold:g} / {bot_config.rsi_overbought:g}\n"
        f"──────────────\n"
        f"🚀 **Trailing Stop**: {ts_status}\n"
        f"📈 TS Activation : {bot_config.ts_activation_percent}%\n"
        f"📉 TS Callback   : {bot_config.ts_callback_rate}%\n\n"
        f"⏱️ Auto-close profit: {AUTO_CLOSE_PROFIT_HOURS_ENV:g} jam\n"
        f"🔄 Cek posisi tiap: {POSITION_MONITOR_INTERVAL_ENV} detik\n\n"
        "Gunakan perintah berikut untuk mengubah:\n"
        "`/set_tp <angka>`\n"
        "`/set_sl <angka>`\n"
        "`/set_rsi <length> <oversold> <overbought>`\n"
        "`/set_scanner_mode <per_coin|batch>`\n"
        "`/set_analysis_days <minimal 20>`\n"
        "`/set_leverage <angka>`\n"
        "`/set_max_positions <angka>`\n"
        "`/set_ts_use <True/False>`\n"
        "`/set_ts_activation <angka>`\n"
        "`/set_ts_callback <angka>`"
    )
    await message.answer(text)

@dp.message(Command("pause"))
@dp.message(Command("stop"))
async def cmd_pause_handler(message: types.Message):
    if bot_state["is_running"]:
        bot_state["is_running"] = False
        bot_state["state"] = "PAUSED"
        await message.answer("🛑 Bot Scanner dihentikan sementara.")
    else:
        await message.answer("⚠️ Bot sudah dalam keadaan berhenti. Gunakan /resume untuk menjalankan.")

@dp.message(Command("resume"))
async def cmd_resume_handler(message: types.Message):
    if not bot_state["is_running"]:
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        await message.answer("▶️ Bot Scanner dijalankan kembali.")
    else:
        await message.answer("⚠️ Bot sudah berjalan.")

@dp.message(F.text == "⏯️ Pause / Resume")
async def btn_pause_resume_handler(message: types.Message):
    if bot_state["is_running"]:
        bot_state["is_running"] = False
        bot_state["state"] = "PAUSED"
        await message.answer("🛑 Bot Scanner dihentikan sementara.")
    else:
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        await message.answer("▶️ Bot Scanner dijalankan kembali.")

import csv

@dp.message(F.text == "📈 Histori TP")
async def btn_histori_tp_handler(message: types.Message):
    if not os.path.exists("real_history_log.csv"):
        await message.answer("⚠️ Belum ada histori Take Profit.")
        return
        
    records = []
    with open("real_history_log.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if 'TAKE_PROFIT' in row.get('Tipe', ''):
                records.append(row)
                
    if not records:
        await message.answer("⚠️ Belum ada histori Take Profit.")
        return
        
    records = records[-10:] # Ambil 10 terakhir
    text = "📈 **HISTORI TAKE PROFIT (10 Terakhir)** 📈\n\n"
    for r in records:
        text += f"🔸 **{r['Symbol']}**\n   Waktu: {r['Waktu']}\n   Harga: {r['Harga Eksekusi']} | PnL: `{r['PnL']} USDT`\n\n"
        
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📉 Histori SL")
async def btn_histori_sl_handler(message: types.Message):
    if not os.path.exists("real_history_log.csv"):
        await message.answer("⚠️ Belum ada histori Stop Loss.")
        return
        
    records = []
    with open("real_history_log.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if 'STOP' in row.get('Tipe', ''):
                records.append(row)
                
    if not records:
        await message.answer("⚠️ Belum ada histori Stop Loss.")
        return
        
    records = records[-10:] # Ambil 10 terakhir
    text = "📉 **HISTORI STOP LOSS (10 Terakhir)** 📉\n\n"
    for r in records:
        text += f"🔸 **{r['Symbol']}**\n   Waktu: {r['Waktu']}\n   Harga: {r['Harga Eksekusi']} | PnL: `{r['PnL']} USDT`\n\n"
        
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📸 Upload Dataset")
async def btn_upload_dataset_handler(message: types.Message, state: FSMContext):
    await add_data_start(message, state)

import asyncio
from ml_vision.train import train_model

@dp.message(Command("train_model"))
async def train_model_handler(message: types.Message):
    await message.answer("🔄 Memulai proses belajar... Ini mungkin memakan waktu beberapa menit. Bot (scanner utama) mungkin sedikit lambat selama proses ini berlangsung.")
    loop = asyncio.get_event_loop()
    try:
        # Run training in background so we don't block the telegram bot updates
        result = await loop.run_in_executor(None, train_model)
        await message.answer(result)
    except Exception as e:
        await message.answer(f"❌ Terjadi kesalahan saat training: {e}")

@dp.message(Command("help"))
@dp.message(F.text == "📞 Bantuan")
async def btn_bantuan_handler(message: types.Message):
    text = (
        "📖 **PANDUAN BOT** 📖\n\n"
        "🔹 `/start` - Menampilkan menu utama\n"
        "🔹 `/help` atau `📞 Bantuan` - Menampilkan pesan panduan ini\n"
        "🔹 `/status` - Melihat status bot\n"
        "🔹 `/trade_stats` - Rekap win rate dan PNL trading\n"
        "🔹 `/analyze SYMBOL` - Analisis candle Daily dan Smart Buy\n"
        "🔹 `/scan_modus SYMBOL` - Scan modus open/close Daily\n"
        "🔹 `/stop` atau `/pause` - Menghentikan bot\n"
        "🔹 `/resume` - Menjalankan bot kembali\n"
        "🔹 `/close_short SYMBOL` - Menutup posisi SHORT\n"
        "🔹 `/close SYMBOL` - Menutup posisi pada symbol\n"
        "🔹 `/close_all` - Menutup semua posisi\n"
        "🔹 Auto-close - Posisi profit otomatis ditutup setelah 24 jam\n"
        "🔹 `/add_data` - Upload gambar untuk ML\n"
        "🔹 `/train_model` - Melatih otak ML Vision\n"
        "🔹 `/set_tp` - Mengatur persentase TP\n"
        "🔹 `/set_sl` - Mengatur persentase SL\n"
        "🔹 `/set_rsi` - Mengatur parameter RSI secara dinamis\n"
        "🔹 `/set_scanner_mode` - Mode per_coin atau batch\n"
        "🔹 `/set_analysis_days` - Mengatur candle Daily, minimal 20\n"
        "🔹 `/set_leverage` - Mengatur leverage\n"
        "🔹 `/set_max_positions` - Mengatur batas maksimal open posisi\n"
        "🔹 `/set_ts_use` - Menyalakan/Mematikan Trailing Stop\n"
        "🔹 `/set_ts_activation` - Mengatur aktivasi Trailing Stop\n"
        "🔹 `/set_ts_callback` - Mengatur callback Trailing Stop\n"
        "🔹 `/download_summary` - Mengunduh rekaman sukses paper trading\n"
        "🔹 `/getidgroup` - Mengecek ID Grup (untuk setting error log)\n"
    )
    await message.answer(text)

@dp.message(Command("download_summary"))
async def download_summary_handler(message: types.Message):
    if os.path.exists("virtual_success_log.csv"):
        doc = FSInputFile("virtual_success_log.csv")
        await message.answer_document(doc, caption="📈 Ini adalah rekap koin-koin yang sukses menyentuh TP pada Mode Pembelajaran (Paper Trading).")
    else:
        await message.answer("⚠️ Belum ada catatan sukses (file virtual_success_log.csv tidak ditemukan).")

@dp.message(Command("getidgroup"))
async def get_id_group_handler(message: types.Message):
    """
    Mengambil ID dari grup atau chat pribadi tempat perintah ini dipanggil.
    Berguna untuk mengetahui TELEGRAM_ERROR_CHAT_ID.
    """
    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.first_name or "Chat"
    
    await message.answer(
        f"🆔 **Informasi Chat**\n"
        f"**Nama:** {chat_title}\n"
        f"**Chat ID:** `{chat_id}`\n\n"
        f"_(Silakan salin Chat ID di atas untuk dimasukkan ke file .env)_",
        parse_mode="Markdown"
    )

@dp.message(Command("add_data"))
async def add_data_start(message: types.Message, state: FSMContext):
    await message.answer("📸 Silakan kirim (upload) gambar candlestick yang ingin dijadikan dataset.")
    await state.set_state(DatasetForm.waiting_for_image)

@dp.message(DatasetForm.waiting_for_image, F.photo)
async def process_image(message: types.Message, state: FSMContext):
    photo = message.photo[-1] # Ambil resolusi terbesar
    file_id = photo.file_id
    
    await state.update_data(file_id=file_id)
    
    # Keyboard untuk memilih label
    kb = [
        [types.KeyboardButton(text="BULLISH"), types.KeyboardButton(text="BEARISH")],
        [types.KeyboardButton(text="NEUTRAL"), types.KeyboardButton(text="BATAL")]
    ]
    keyboard = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)
    
    await message.answer("📈 Gambar diterima! Apa pola (label) dari gambar ini?", reply_markup=keyboard)
    await state.set_state(DatasetForm.waiting_for_label)

@dp.message(DatasetForm.waiting_for_label)
async def process_label(message: types.Message, state: FSMContext):
    label = message.text.upper()
    
    if label == "BATAL":
        await message.answer("❌ Dibatalkan.", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
        
    if label not in ["BULLISH", "BEARISH", "NEUTRAL"]:
        await message.answer("⚠️ Label tidak valid. Pilih dari keyboard.")
        return
        
    data = await state.get_data()
    file_id = data['file_id']
    
    # Download file
    file = await bot.get_file(file_id)
    file_path = file.file_path
    
    # Save file
    filename = f"{message.message_id}.jpg"
    destination = f"dataset/{label}/{filename}"
    await bot.download_file(file_path, destination)
    
    await message.answer(f"✅ Gambar berhasil disimpan ke folder dataset/{label}", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()

import cv2
import numpy as np
from ml_vision.model import get_model
from ml_vision.preprocessor import preprocess_chart_image
from ml_vision.inference import predict_candle_pattern
from io import BytesIO

# Variabel global untuk model inferensi agar tidak diload berulang-ulang
handler_ml_model = None

@dp.message(F.photo)
async def predict_photo_handler(message: types.Message):
    global handler_ml_model
    
    # Ambil resolusi terbesar
    photo = message.photo[-1]
    file_id = photo.file_id
    file = await bot.get_file(file_id)
    
    wait_msg = await message.answer("🔍 Menganalisis gambar chart...")
    
    try:
        # Download file ke memori
        image_stream = BytesIO()
        await bot.download_file(file.file_path, image_stream)
        
        # Convert image stream ke opencv
        file_bytes = np.asarray(bytearray(image_stream.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if img is None:
            await wait_msg.edit_text("❌ Gagal membaca gambar.")
            return
            
        # Proses gambar
        processed_img = preprocess_chart_image(img)
        
        # Load model jika belum ada (lazy loading)
        if handler_ml_model is None:
            handler_ml_model = get_model("ml_vision/candle_model.pth")
            
        # Prediksi
        label, confidence = predict_candle_pattern(processed_img, handler_ml_model)
        
        text = (
            f"🧠 **Hasil Analisis ML Vision**\n\n"
            f"📊 Prediksi Pola: **{label}**\n"
            f"📈 Akurasi / Confidence: `{confidence*100:.2f}%`\n\n"
            f"_(Ini adalah hasil analisis otomatis dari model AI yang Anda latih)_"
        )
        await wait_msg.edit_text(text, parse_mode="Markdown")
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ Terjadi kesalahan saat prediksi: {e}")
