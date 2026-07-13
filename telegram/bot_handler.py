import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config.settings import TELEGRAM_BOT_TOKEN, bot_config

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

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
    "is_running": True
}

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📊 Status Bot"), KeyboardButton(text="⚙️ Pengaturan")],
        [KeyboardButton(text="📈 Histori TP"), KeyboardButton(text="📉 Histori SL")],
        [KeyboardButton(text="⏯️ Pause / Resume"), KeyboardButton(text="📸 Upload Dataset")],
        [KeyboardButton(text="📞 Bantuan")]
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
    status_emoji = "🟢 RUNNING" if bot_state.get("is_running", True) else "🔴 STOPPED"
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
                      f"   PNL: `{pnl:+.2f} USDT`\n"
                      f"   Hold: {hold_time}\n"
                      f"   Entry: {entry_str} | Mark: {mark_str}\n")
                      
            if amt > 0:
                longs.append(p_info)
            else:
                shorts.append(p_info)
                
        text = (
            f"🤖 **PROFIL & STATUS BOT**\n"
            f"Status: {status_emoji}\n"
            f"💰 Saldo Total: `{total_margin:.2f} USDT`\n"
            f"📈 Unr. PNL  : `{unrealized_pnl:+.2f} USDT`\n"
            f"──────────────\n"
            f"**🟢 POSISI LONG ({len(longs)}/4)**\n"
        )
        if longs:
            text += "".join(longs)
        else:
            text += "   _Tidak ada posisi_\n"
            
        text += f"\n**🔴 POSISI SHORT ({len(shorts)}/4)**\n"
        if shorts:
            text += "".join(shorts)
        else:
            text += "   _Tidak ada posisi_\n"
            
        await wait_msg.edit_text(text, parse_mode="Markdown")
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ Gagal mengambil profil: {e}")

@dp.message(Command("stop"))
async def stop_handler(message: types.Message):
    bot_state["is_running"] = False
    await message.answer("🛑 Bot Scanner dihentikan sementara.")

@dp.message(Command("resume"))
async def resume_handler(message: types.Message):
    bot_state["is_running"] = True
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

@dp.message(F.text == "📊 Status Bot")
async def btn_status_handler(message: types.Message):
    await status_handler(message)

@dp.message(F.text == "⚙️ Pengaturan")
async def btn_pengaturan_handler(message: types.Message):
    text = (
        "⚙️ **PENGATURAN SAAT INI** ⚙️\n\n"
        f"🎯 Take Profit : {bot_config.tp_percent}%\n"
        f"🛑 Stop Loss   : {bot_config.sl_percent}%\n"
        f"⚡ Leverage    : {bot_config.leverage}x\n\n"
        "Gunakan perintah berikut untuk mengubah:\n"
        "`/set_tp <angka>`\n"
        "`/set_sl <angka>`\n"
        "`/set_leverage <angka>`"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⏯️ Pause / Resume")
async def btn_pause_resume_handler(message: types.Message):
    if bot_state["is_running"]:
        bot_state["is_running"] = False
        await message.answer("🛑 Bot Scanner dihentikan sementara.")
    else:
        bot_state["is_running"] = True
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

@dp.message(Command("help"))
@dp.message(F.text == "📞 Bantuan")
async def btn_bantuan_handler(message: types.Message):
    text = (
        "📖 **PANDUAN BOT** 📖\n\n"
        "🔹 `/start` - Menampilkan menu utama\n"
        "🔹 `/help` atau `📞 Bantuan` - Menampilkan pesan panduan ini\n"
        "🔹 `/status` - Melihat status bot\n"
        "🔹 `/stop` atau `/pause` - Menghentikan bot\n"
        "🔹 `/resume` - Menjalankan bot kembali\n"
        "🔹 `/add_data` - Upload gambar untuk ML\n"
        "🔹 `/set_tp` - Mengatur persentase TP\n"
        "🔹 `/set_sl` - Mengatur persentase SL\n"
        "🔹 `/set_leverage` - Mengatur leverage\n"
        "🔹 `/download_summary` - Mengunduh rekaman sukses paper trading\n"
        "🔹 `/getidgroup` - Mengecek ID Grup (untuk setting error log)\n"
    )
    await message.answer(text, parse_mode="Markdown")

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
