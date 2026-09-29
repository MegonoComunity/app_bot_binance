import os
from typing import Optional, List, Dict, Any, Union
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from config.settings import (
    TELEGRAM_ADMIN_CHAT_ID,
    TELEGRAM_ERROR_CHAT_ID,
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
from core.trade_stats import trade_summary, reset_trade_stats, clear_trade_explainability
from database.trade_repo import get_trade_summary
from core.trade_sync import sync_real_exchange_account
from core.learner import get_multi_timeframe_summary, get_stats as get_learner_stats, reset_pattern_blacklist
from core.pattern_memory import _load as load_pattern_memory, clear_pattern_memory
from core.auto_updater import check_for_git_updates, smart_git_pull_and_heal

# Initialize bot and dispatcher
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        chat = data.get("event_chat") or getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)

        if not user:
            return await handler(event, data)

        admin_chat_str = str(TELEGRAM_ADMIN_CHAT_ID or "").strip()
        admin_err_str = str(TELEGRAM_ERROR_CHAT_ID or "").strip()
        user_id_str = str(getattr(user, "id", ""))
        chat_id_str = str(getattr(chat, "id", "")) if chat else ""

        is_authorized = (
            chat_id_str == admin_chat_str
            or chat_id_str == admin_err_str
            or user_id_str == admin_chat_str
            or (user.id in TELEGRAM_ADMIN_USER_IDS if TELEGRAM_ADMIN_USER_IDS else False)
            or not admin_chat_str  # Jika belum diatur di env
        )
        if not is_authorized:
            return None

        return await handler(event, data)


dp.message.middleware(AdminOnlyMiddleware())
dp.callback_query.middleware(AdminOnlyMiddleware())


# Setup Folder Dataset
os.makedirs("dataset/BULLISH", exist_ok=True)
os.makedirs("dataset/BEARISH", exist_ok=True)
os.makedirs("dataset/NEUTRAL", exist_ok=True)

class DatasetForm(StatesGroup):
    waiting_for_image = State()
    waiting_for_label = State()

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    FSInputFile,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
import os

from core.exchanges.base import BaseExchange
from core.exchanges.factory import get_exchange_adapter

# Global state for bot
bot_state = {
    "is_running": False,
    "state": "PAUSED",
    "websocket_connected": False,
    "pre_pump_alerts": [],
}

async def setup_bot_commands(bot_instance: Bot) -> None:
    """Reset dan daftarkan seluruh perintah resmi bot ke Telegram di semua scope (Private & Group)."""
    commands = [
        # --- Navigasi & Utama ---
        BotCommand(command="start", description="🚀 Buka Menu Utama & Keyboard Interaktif"),
        BotCommand(command="help", description="📖 Panduan Lengkap & Manual Fitur Bot"),
        BotCommand(command="status", description="📊 Cek Saldo Akun & Posisi Terbuka"),
        BotCommand(command="mode", description="🎯 Cek & Ganti Mode (REAL / TESTNET / PAPER)"),
        BotCommand(command="set_exchange", description="🏛️ Pilih Exchange (Binance / Bitunix)"),

        # --- Scanning & Eksekusi ---
        BotCommand(command="scan_order_real", description="🟢 Mulai Scan & Auto-Trade Akun REAL"),
        BotCommand(command="scan_order_paper", description="🔵 Mulai Scan Mode Paper Trade"),
        BotCommand(command="scan_binance_testnet", description="🌐 Mulai Scan Mode Binance Testnet"),
        BotCommand(command="analisa", description="🔎 Analisis Teknikal & ML Koin Tertentu"),
        BotCommand(command="pause", description="⏸️ Jeda Aktivitas Scanning Bot"),
        BotCommand(command="resume", description="▶️ Lanjutkan Scanning Otomatis"),
        BotCommand(command="close_all", description="⛔ Tutup Paksa Semua Posisi Terbuka"),

        # --- Konfigurasi & Manajemen Risiko ---
        BotCommand(command="pengaturan", description="⚙️ Tampilkan Seluruh Pengaturan Aktif"),
        BotCommand(command="hitung_margin", description="🧮 Kalkulator Margin & Lot Aman"),
        BotCommand(command="set_modal", description="💰 Atur Modal Sizing Simulasi ($)"),
        BotCommand(command="reset_modal", description="🔄 Kembalikan Modal Sizing ke $100"),
        BotCommand(command="set_margin", description="💵 Mode Margin (DYNAMIC / FIXED)"),
        BotCommand(command="set_risk", description="🛡️ Risk Per Trade (% Modal)"),
        BotCommand(command="set_leverage", description="⚡ Ubah Leverage (1x - 125x)"),
        BotCommand(command="set_tp", description="🎯 Target Take Profit ROI (%)"),
        BotCommand(command="set_sl", description="🛑 Target Stop Loss ROI (%)"),
        BotCommand(command="set_max_ratio", description="🔒 Max Margin per Posisi (% Modal)"),
        BotCommand(command="set_max_positions", description="🔢 Batas Maksimal Posisi Terbuka"),
        BotCommand(command="set_confluence", description="🎚️ Skor Konfluensi Min (50 - 100)"),
        BotCommand(command="set_breakeven", description="🛡️ Auto Break-Even (ON / OFF)"),

        # --- Eksekusi Presisi & Limit Pullback ---
        BotCommand(command="set_exec_mode", description="⚡ Mode Eksekusi (SMART_LIMIT / HYBRID / MARKET)"),
        BotCommand(command="set_limit_retrace", description="🎯 Diskon Limit Pullback (%)"),
        BotCommand(command="set_limit_timeout", description="⏱️ Timeout Batal Limit Order (Detik)"),
        BotCommand(command="set_atr_sl", description="📏 Multiplier Buffer ATR Stop Loss"),

        # --- Filter Target Scanner ---
        BotCommand(command="set_scan", description="🌐 Target Scan & Urutan (ALL / Vol / Change)"),
        BotCommand(command="set_scan_target", description="🎯 Jumlah Koin Scan (all / 50 / 100 / 200 / 500)"),
        BotCommand(command="set_scan_sort", description="📶 Urutan Sort (volume / change / gainers / losers)"),

        # --- AI Learning, Winrate & Reset ---
        BotCommand(command="ai_stats", description="🧠 Monitoring Berkala AI & Win Rate Pola"),
        BotCommand(command="winrate", description="📈 Rekap Win Rate Multi-Timeframe"),
        BotCommand(command="explain", description="🔍 Penjelasan Log Keputusan Trade Terakhir"),
        BotCommand(command="reset_demo", description="🔄 Reset Total History & Memori Pola AI"),
        BotCommand(command="reset_binance_history", description="🔄 Reset History Khusus Binance"),
        BotCommand(command="reset_pola", description="🧠 Bersihkan Blacklist & Refresh Pola AI"),

        # --- Akun, Database & GitHub ---
        BotCommand(command="deposit", description="💵 Cek Saldo & Info Deposit Exchange"),
        BotCommand(command="upload_dataset", description="📸 Upload Gambar Chart Dataset Vision"),
        BotCommand(command="backup_db", description="💾 Backup Database PostgreSQL ke Telegram"),
        BotCommand(command="git_sync", description="🔄 Cek & Sinkronkan Update GitHub"),
        BotCommand(command="update_bot", description="🚀 Auto-Pull & Self-Healing dari GitHub"),
    ]
    try:
        # 1. Scope Default (Global)
        await bot_instance.delete_my_commands(scope=BotCommandScopeDefault())
        await bot_instance.set_my_commands(commands, scope=BotCommandScopeDefault())

        # 2. Scope Private Chats (Khusus DM/Chat Pribadi Pengguna)
        await bot_instance.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await bot_instance.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())

        # 3. Scope Group Chats
        await bot_instance.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await bot_instance.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())

        # 4. Scope Spesifik Admin Chat
        if TELEGRAM_ADMIN_CHAT_ID:
            try:
                chat_id_int = int(str(TELEGRAM_ADMIN_CHAT_ID).strip())
                await bot_instance.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat_id_int))
                await bot_instance.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id_int))
            except Exception:
                pass

        print(f"[TELEGRAM] ✅ {len(commands)} perintah resmi berhasil didaftarkan ke semua scope Telegram (Default, Private & Admin)!")
    except Exception as exc:
        print(f"[TELEGRAM] Gagal update commands: {exc}")



def get_main_keyboard(active_exchange: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Membuat Reply Keyboard dinamis sesuai exchange aktif (Binance Testnet vs Bitunix Paper)."""
    ex = (active_exchange or getattr(bot_config, "active_exchange", "BINANCE")).upper().strip()
    if ex == "BINANCE":
        demo_btn = KeyboardButton(text="🌐 Scan Binance Testnet")
    else:
        demo_btn = KeyboardButton(text="🔵 Scan Bitunix Paper")

    kb = [
        [demo_btn, KeyboardButton(text="🟢 Scan Order Real")],
        [KeyboardButton(text="🎯 Ganti Mode"), KeyboardButton(text="🏛️ Ganti Exchange")],
        [KeyboardButton(text="📊 Status Bot"), KeyboardButton(text="🧠 Monitoring AI")],
        [KeyboardButton(text="⚙️ Pengaturan"), KeyboardButton(text="🧮 Hitung Margin")],
        [KeyboardButton(text="📈 Histori TP"), KeyboardButton(text="📉 Histori SL")],
        [KeyboardButton(text="🔎 Analisa Koin"), KeyboardButton(text="🔄 Reset Demo")],
        [KeyboardButton(text="💵 Cek Deposit"), KeyboardButton(text="📸 Upload Dataset")],
        [KeyboardButton(text="⏯️ Pause / Resume"), KeyboardButton(text="⛔ Close ALL")],
        [KeyboardButton(text="📞 Bantuan")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    curr_mode = getattr(bot_config, "trading_mode", "TESTNET" if active_ex == "BINANCE" else "PAPER_TRADING")
    demo_guide = "**🌐 Scan Binance Testnet** (demo.binance.com)" if active_ex == "BINANCE" else "**🔵 Scan Bitunix Paper** (Simulasi Virtual)"
    
    await message.answer(
        f"🚀 **Trading Bot is Online!**\n"
        f"🏛️ **Exchange:** `{active_ex}` | 🎯 **Mode:** `{curr_mode}`\n\n"
        f"• Gunakan tombol {demo_guide} untuk uji coba trading tanpa risiko modal.\n"
        f"• Gunakan tombol **🟢 Scan Order Real** untuk eksekusi order dengan akun nyata.\n"
        f"• Ketik `/help` atau klik **📞 Bantuan** untuk melihat panduan lengkap.\n\n"
        f"Silakan pilih menu di bawah ini:",
        reply_markup=get_main_keyboard(active_ex),
        parse_mode="Markdown",
    )

@dp.message(Command("sync_menu", "reload_commands"))
async def sync_menu_handler(message: types.Message):
    """Memperbarui dan mendaftarkan ulang seluruh perintah menu bot ke Telegram."""
    try:
        await setup_bot_commands(bot)
        await message.answer(
            "✅ **Daftar Perintah Menu Telegram Berhasil Disinkronkan ke Semua Scope!**\n\n"
            "Silakan ketik `/` atau buka ikon Menu di samping kolom ketik untuk melihat daftar seluruh perintah resmi yang bisa di-scroll.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        await message.answer(f"❌ Gagal memperbarui menu Telegram: {exc}")


@dp.message(Command("help"))
@dp.message(Command("bantuan"))
@dp.message(F.text == "📞 Bantuan")
async def help_handler(message: types.Message):

    active_ex = getattr(bot_config, "active_exchange", "BINANCE")
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    help_text = (
        f"📖 **PANDUAN & DAFTAR PERINTAH BOT** 🤖\n"
        f"────────────────────────\n"
        f"🏛️ **Exchange Aktif:** `{active_ex}`\n"
        f"🎯 **Mode Trading:** `{curr_mode}`\n"
        f"⚡ **Status Scan:** `{bot_state.get('state', 'PAUSED')}`\n\n"
        f"🔹 **KONTROL SCANNER & TRADING:**\n"
        f"• `/scan_order_paper` — Mulai Scan + Paper Trading (Simulasi Aman)\n"
        f"• `/scan_order_real` — Mulai Scan + Eksekusi Order Nyata (Akun Real)\n"
        f"• `/pause` — Jeda sementara scanning koin\n"
        f"• `/resume` — Lanjutkan kembali scanning\n"
        f"• `/close_all` — Tutup darurat semua posisi aktif di exchange\n\n"
        f"🔹 **WIN RATE & GATEKEEPER AI:**\n"
        f"• `/winrate` — Rekap Win Rate Multi-Timeframe (Daily, Weekly, Monthly)\n"
        f"• `/set_wr_window <daily|recent20|weekly|all>` — Atur jendela waktu hitung win rate\n"
        f"• `/set_min_wr <persen>` — Atur batas minimal Win Rate (contoh: `/set_min_wr 50`)\n"
        f"• `/reset_blacklist` — Reset status blokir pola secara instan\n\n"
        f"🔹 **MONITORING & KEUANGAN:**\n"
        f"• `/status` — Cek Saldo Wallet, Floating PnL, & Posisi Terbuka\n"
        f"• `/hitung_margin` — Kalkulator perhitungan margin aman sesuai modal\n"
        f"• `/reset_demo` — Reset saldo simulasi ($100) & riwayat winrate\n"
        f"• `/analisa <koin>` — Analisa teknikal & AI instan (contoh: `/analisa BTCUSDT`)\n"
        f"• `/backup_db` — Export dan kirim backup database ke Telegram\n"
        f"• `/restore_db` — Panduan & eksekusi restore database\n\n"
        f"🔹 **PENGATURAN PARAMETER:**\n"
        f"• `/set_exchange <binance|bitunix>` — Ganti exchange aktif (Binance / Bitunix)\n"
        f"• `/set_modal <nominal>` — Set modal awal simulasi (contoh: `/set_modal 500`)\n"
        f"• `/set_margin <auto|nominal>` — Set mode margin (contoh: `/set_margin 50`)\n"
        f"• `/set_risk <persen>` — Risk per trade dalam % saldo (contoh: `/set_risk 2.0`)\n"
        f"• `/set_leverage <angka>` — Ubah leverage (contoh: `/set_leverage 20`)\n"
        f"• `/set_tp <persen>` — Ubah target Take Profit ROI % (contoh: `/set_tp 40`)\n"
        f"• `/set_sl <persen>` — Ubah batasan Stop Loss ROI % (contoh: `/set_sl 25`)\n"
        f"• `/pengaturan` — Buka dasbor tombol interaktif pengaturan\n"
        f"────────────────────────\n"
        f"💡 *Tip:* Gunakan menu keyboard di bawah untuk akses cepat sekali klik."
    )
    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(Command("set_exchange"))
async def set_exchange_handler(message: types.Message, command: CommandObject):
    """
    Mengubah exchange aktif secara dinamis: /set_exchange binance atau /set_exchange bitunix
    """
    args = (command.args or "").strip().upper()
    if args not in {"BINANCE", "BITUNIX"}:
        current_ex = getattr(bot_config, "active_exchange", "BINANCE")
        text = (
            f"🏛️ **PILIH EXCHANGE AKTIF**\n"
            f"────────────────────────\n"
            f"Exchange saat ini: **{current_ex}**\n\n"
            f"Ketik perintah dengan nama exchange:\n"
            f"• `/set_exchange binance` — Aktifkan Binance Futures\n"
            f"• `/set_exchange bitunix` — Aktifkan Bitunix Futures\n"
        )
        await message.answer(text, parse_mode="Markdown")
        return

    try:
        old_ex = getattr(bot_config, "active_exchange", "BINANCE")
        bot_config.update_active_exchange(args)
        new_adapter = get_exchange_adapter(args)
        await new_adapter.init()
        bot_state["client"] = new_adapter
        
        await message.answer(
            f"✅ **Exchange Berhasil Diubah!**\n"
            f"Sebelumnya: `{old_ex}` ➔ Sekarang: **`{args}`**\n\n"
            f"Scanner dan eksekutor order sekarang terhubung ke **{args}**.",
            reply_markup=get_main_keyboard(args),
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Gagal switch exchange: {e}")


import time
from datetime import datetime


async def fetch_account_balance_info(client_inst, exchange_name: str, mode: str) -> dict:
    """
    Mengambil saldo dan posisi realtime secara adaptif:
    - BINANCE REAL: Cek saldo real dari Binance Production Futures API.
    - BINANCE TESTNET/DEMO: Cek saldo demo dari Binance Futures Testnet API, fallback ke Paper Sim jika testnet API tidak terhubung.
    - BITUNIX REAL: Cek saldo real dari Bitunix Futures API.
    - BITUNIX SIMULASI: Cek saldo virtual dari database & memori (bot_config.simulated_modal).
    """
    is_real = mode.upper() in ("REAL", "LIVE")
    target_ex = exchange_name.upper().strip()

    if is_real:
        try:
            target_client = get_exchange_adapter(target_ex, is_testnet=False)
            await target_client.init()
            bot_state["client"] = target_client

            sync_res = await sync_real_exchange_account(
                client=target_client,
                sync_history=True,
                history_limit=50,
                bot_state_ref=bot_state,
            )

            if sync_res.get("success"):
                return {
                    "success": True,
                    "is_real": True,
                    "source": f"{target_ex} Real Account API",
                    "exchange": target_ex,
                    "total_balance": sync_res["total_wallet_balance"],
                    "available_balance": sync_res["available_balance"],
                    "unrealized_pnl": sync_res["unrealized_pnl"],
                    "margin_locked": sync_res["margin_locked"],
                    "positions_count": sync_res["open_positions_count"],
                    "positions": sync_res["open_positions"],
                    "synced_history_count": sync_res["synced_history_count"],
                }
            else:
                raise RuntimeError(sync_res.get("error") or "Gagal membaca saldo real dari API.")
        except Exception as err:
            err_str = str(err)
            if "whitelist" in err_str.lower() or "10004" in err_str:
                err_clean = "IP saat ini belum terdaftar di IP Whitelist Bitunix API Key Anda (code: 10004). Mohon buka dashboard Bitunix > API Management, lalu tambahkan IP publik Anda ke whitelist atau pilih opsi 'No IP Restriction'."
            elif "403" in err_str:
                err_clean = "Akses terblokir HTTP 403 (Kemungkinan ISP/Internet Positif, silakan pasang Proxy atau VPN)"
            elif "100007" in err_str or "signature" in err_str.lower():
                err_clean = "API Key / Secret Key ditolak oleh Bitunix (Sign error 100007 / IP Restriction). Pastikan API Key memiliki permission Futures Trading dan IP diizinkan."
            else:
                err_clean = err_str

            return {
                "success": False,
                "is_real": True,
                "source": f"{target_ex} Real API",
                "exchange": target_ex,
                "error": err_clean,
                "total_balance": 0.0,
                "available_balance": 0.0,
                "unrealized_pnl": 0.0,
                "positions_count": 0,
                "positions": [],
            }
    else:
        # Mode TESTNET (Khusus Binance Futures Testnet API di demo.binance.com)
        if target_ex == "BINANCE" and mode.upper() in ("TESTNET", "DEMO", "BINANCE_DEMO"):
            try:
                testnet_client = get_exchange_adapter("BINANCE", is_testnet=True, force_recreate=True)
                await testnet_client.init()
                bal_info = await testnet_client.get_account_balance()
                total_tn = float(bal_info.get("total_wallet_balance", 0.0))
                avail_tn = float(bal_info.get("available_balance", total_tn))
                unreal_tn = float(bal_info.get("unrealized_pnl", 0.0))
                positions_tn = await testnet_client.get_open_positions()
                
                bot_state["client"] = testnet_client
                return {
                    "success": True,
                    "is_real": False,
                    "source": "Binance Futures Testnet API (demo.binance.com)",
                    "exchange": "BINANCE",
                    "total_balance": total_tn,
                    "available_balance": avail_tn,
                    "unrealized_pnl": unreal_tn,
                    "positions_count": len(positions_tn),
                    "positions": positions_tn,
                    "is_api_demo": True,
                }
            except Exception as e_tn:
                print(f"[TESTNET BINANCE] Gagal ambil saldo testnet API: {e_tn}")

        # Mode VIRTUAL SIMULASI / PAPER TRADING
        if bot_config.simulated_modal is not None and bot_config.simulated_modal > 0:
            sim_modal = float(bot_config.simulated_modal)
            active_meta = bot_state.get("active_trade_meta", {})
            active_sim_pos = [m for m in active_meta.values() if m.get("is_paper")]
            
            margin_locked = sum(float(m.get("margin_usdt", 0.0) or 0.0) for m in active_sim_pos)
            unreal_pnl = sum(float(m.get("unrealized_pnl", 0.0) or 0.0) for m in active_sim_pos)
            avail_bal = max(0.0, sim_modal - margin_locked + unreal_pnl)

            db_stats = {}
            try:
                db_stats = await get_trade_summary(exchange=f"{target_ex}_SIM")
            except Exception:
                pass

            return {
                "success": True,
                "is_real": False,
                "source": f"{target_ex} Virtual Demo Wallet (Akumulasi PnL)",
                "exchange": target_ex,
                "total_balance": sim_modal,
                "available_balance": avail_bal,
                "margin_locked": margin_locked,
                "unrealized_pnl": unreal_pnl,
                "positions_count": len(active_sim_pos),
                "positions": active_sim_pos,
                "db_stats": db_stats,
                "is_api_demo": False,
            }

        # 3. Default Fallback ke 100 USDT Simulated Modal
        sim_modal = 100.0
        bot_config.simulated_modal = sim_modal
        
        db_stats = {}
        try:
            db_stats = await get_trade_summary(exchange=f"{target_ex}_SIM")
        except Exception:
            pass

        active_meta = bot_state.get("active_trade_meta", {})
        active_sim_pos = [m for m in active_meta.values() if m.get("is_paper")]

        return {
            "success": True,
            "is_real": False,
            "source": f"{target_ex} Virtual Demo Wallet",
            "exchange": target_ex,
            "total_balance": sim_modal,
            "available_balance": sim_modal,
            "unrealized_pnl": 0.0,
            "positions_count": len(active_sim_pos),
            "positions": active_sim_pos,
            "db_stats": db_stats,
            "is_api_demo": False,
        }


@dp.message(Command("scan_order_real"))
@dp.message(F.text == "🟢 Scan Order Real")
async def scan_order_real_handler(message: types.Message):
    """
    Mengaktifkan mode REAL Trading: bot akan men-scan market dan mengeksekusi order nyata di exchange,
    serta otomatis mendeteksi akun real via API (Saldo, Open Positions, dan Closed History).
    """
    try:
        bot_config.update_trading_mode("REAL")
        bot_config.simulated_modal = None
        bot_config._update_env("SIMULATED_MODAL", "0.0")
        active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
        
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        
        # Eksekusi sinkronisasi menyeluruh akun real
        client_inst = bot_state.get("client")
        sync_res = await sync_real_exchange_account(client_inst, sync_history=True, history_limit=50, bot_state_ref=bot_state)
        
        if sync_res.get("success"):
            total_bal = sync_res.get("total_wallet_balance", 0.0)
            avail_bal = sync_res.get("available_balance", 0.0)
            unr_pnl = sync_res.get("unrealized_pnl", 0.0)
            pos_cnt = sync_res.get("open_positions_count", 0)
            hist_cnt = sync_res.get("synced_history_count", 0)
            src_lbl = f"{active_ex} Real Account API"
            
            pos_details = ""
            if pos_cnt > 0:
                pos_list = sync_res.get("open_positions", [])
                pos_str_list = [f"• `{p.get('symbol')}` ({p.get('side')}) Entry: `{p.get('entry_price')}` | PnL: `{p.get('unrealized_pnl', 0.0):+.2f}`" for p in pos_list[:5]]
                pos_details = "\n" + "\n".join(pos_str_list) + "\n"

            saldo_text = (
                f"💰 **Saldo Real Wallet:** `${total_bal:.2f} USDT`\n"
                f"💵 **Available Margin:** `${avail_bal:.2f} USDT`\n"
                f"📈 **Floating PnL:** `{unr_pnl:+.2f} USDT`\n"
                f"📊 **Posisi Real Terbuka:** `{pos_cnt}` posisi{pos_details}"
                f"🔄 **Riwayat Closed Synced:** `{hist_cnt}` trade baru tersimpan ke DB\n"
                f"🔌 **Sumber Data:** `{src_lbl}`\n"
            )
        else:
            saldo_text = f"⚠️ **Saldo Real:** Gagal terhubung ke API `{active_ex}` ({sync_res.get('error')})\n"

        text = (
            f"🚀 **MODE ORDER REAL DIAKTIFKAN!** 🟢\n"
            f"────────────────────────\n"
            f"🏛️ **Exchange Aktif:** `{active_ex}`\n"
            f"⚡ **Status Scanning:** `AKTIF (Running)`\n"
            f"🎯 **Mode Eksekusi:** `REAL ACCOUNT ORDERS`\n"
            f"────────────────────────\n"
            f"{saldo_text}"
            f"────────────────────────\n"
            f"⚠️ **Perhatian:** Sinyal valid akan langsung dieksekusi sebagai real order di akun **{active_ex}** Anda.\n"
            f"🔧 **Setup:** Lev `{bot_config.leverage}x` | TP `{bot_config.tp_percent}%` | SL `{bot_config.sl_percent}%` | Mode `{bot_config.margin_mode}`\n\n"
            f"Gunakan `/scan_order_paper` kapan saja untuk kembali ke mode simulasi aman."
        )
        await message.answer(text, reply_markup=get_main_keyboard(active_ex), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal mengaktifkan mode Real: {e}")


@dp.message(Command("scan_order_paper"))
@dp.message(Command("scan_binance_testnet"))
@dp.message(Command("simulasi"))
@dp.message(Command("demo"))
@dp.message(Command("paper"))
@dp.message(F.text.in_({"🔵 Scan Order Paper", "🌐 Scan Binance Testnet", "🔵 Scan Bitunix Paper", "🔵 Scan Order Demo"}))
async def scan_order_paper_handler(message: types.Message):
    """
    Mengaktifkan mode Demo / Testnet secara otomatis:
    - Jika exchange BINANCE: Mengaktifkan mode TESTNET (terhubung langsung ke API demo.binance.com).
    - Jika exchange BITUNIX: Mengaktifkan mode PAPER_TRADING (simulasi virtual internal dengan harga real Bitunix).
    """
    try:
        active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
        target_mode = "TESTNET" if active_ex == "BINANCE" else "PAPER_TRADING"
        
        bot_config.update_trading_mode(target_mode)
        is_tn = (target_mode == "TESTNET")
        
        # Inisialisasi adapter exchange sesuai mode
        new_adapter = get_exchange_adapter(active_ex, is_testnet=is_tn, force_recreate=True)
        await new_adapter.init()
        bot_state["client"] = new_adapter
        
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        
        # Ambil saldo realtime
        bal_res = await fetch_account_balance_info(new_adapter, active_ex, target_mode)
        sim_bal = bal_res.get("total_balance", 0.0)
        avail_bal = bal_res.get("available_balance", sim_bal)
        pos_cnt = bal_res.get("positions_count", 0)
        db_stats = bal_res.get("db_stats", {})
        src_lbl = bal_res.get("source", "Testnet / Paper Simulation")
        
        stats_text = ""
        if db_stats and db_stats.get("total", 0) > 0:
            stats_text = f"📊 **Riwayat Sesi DB:** `{db_stats.get('wins', 0)}W / {db_stats.get('losses', 0)}L` (WR: `{db_stats.get('win_rate', 0)}%` | Net: `{db_stats.get('net_pnl', 0):+.2f} USDT`)\n"

        if active_ex == "BINANCE":
            header_title = "🌐 **MODE BINANCE FUTURES TESTNET DIAKTIFKAN!** 🔵"
            saldo_label = "Saldo Testnet Demo"
            mode_desc = "Order dieksekusi langsung ke server **Binance Futures Testnet API** (sinkron dengan web `demo.binance.com`)."
        else:
            header_title = "📝 **MODE BITUNIX PAPER TRADING DIAKTIFKAN!** 🔵"
            saldo_label = "Saldo Virtual Simulasi"
            mode_desc = "Simulasi internal presisi 1:1 tanpa resiko modal dengan feed harga live **Bitunix**."

        text = (
            f"{header_title}\n"
            f"────────────────────────\n"
            f"🏛️ **Exchange Data:** `{active_ex}` (Live Market Feed)\n"
            f"⚡ **Status Scanning:** `AKTIF (Running 🟢)`\n"
            f"🎯 **Mode Eksekusi:** `{target_mode}`\n"
            f"────────────────────────\n"
            f"💰 **{saldo_label}:** `${sim_bal:.2f} USDT` (Tersedia: `${avail_bal:.2f}`)\n"
            f"🔌 **Sumber:** `{src_lbl}`\n"
            f"📊 **Posisi Terbuka:** `{pos_cnt}` posisi\n"
            f"{stats_text}"
            f"────────────────────────\n"
            f"ℹ️ *{mode_desc}*\n"
            f"🔧 **Setup:** Lev `{bot_config.leverage}x` | TP `{bot_config.tp_percent}%` | SL `{bot_config.sl_percent}%` | Mode `{bot_config.execution_mode}`\n\n"
            f"Gunakan `/scan_order_real` untuk mulai order dengan modal real."
        )
        await message.answer(text, reply_markup=get_main_keyboard(active_ex), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal mengaktifkan mode demo/testnet: {e}")


def get_mode_gui_keyboard(current_mode: str, active_exchange: str = "BINANCE") -> InlineKeyboardMarkup:
    """Membuat Inline Keyboard untuk Menu GUI Mode Trading (Khusus Binance Testnet vs Bitunix Paper)."""
    curr_m = current_mode.upper()
    is_real = curr_m in ("REAL", "LIVE")
    is_testnet = curr_m in ("TESTNET", "DEMO_TESTNET", "BINANCE_DEMO")
    is_paper = curr_m in ("PAPER_TRADING", "SIMULATION", "VIRTUAL")

    btn_real = InlineKeyboardButton(
        text="🟢 REAL (Aktif ✅)" if is_real else "🟢 Aktifkan REAL",
        callback_data="gui_mode_real"
    )

    if active_exchange.upper() == "BINANCE":
        btn_demo = InlineKeyboardButton(
            text="🌐 TESTNET API (Aktif ✅)" if is_testnet else "🌐 Aktifkan TESTNET (demo.binance.com)",
            callback_data="gui_mode_testnet"
        )
    else:
        btn_demo = InlineKeyboardButton(
            text="📝 PAPER Mode (Aktif ✅)" if is_paper or is_testnet else "📝 Aktifkan PAPER (Virtual)",
            callback_data="gui_mode_paper"
        )

    btn_refresh = InlineKeyboardButton(text="🔄 Refresh Status", callback_data="gui_mode_refresh")
    btn_exchange = InlineKeyboardButton(text="🏛️ Ganti Exchange", callback_data="gui_goto_exchange")

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn_real, btn_demo],
        [btn_refresh, btn_exchange],
    ])


def get_exchange_gui_keyboard(current_exchange: str) -> InlineKeyboardMarkup:
    """Membuat Inline Keyboard untuk Menu GUI Exchange Switcher."""
    is_bitunix = current_exchange.upper() == "BITUNIX"
    is_binance = current_exchange.upper() == "BINANCE"

    btn_bitunix = InlineKeyboardButton(
        text="🔷 BITUNIX (Aktif ✅)" if is_bitunix else "🔷 Pilih BITUNIX",
        callback_data="gui_ex_bitunix"
    )
    btn_binance = InlineKeyboardButton(
        text="🔶 BINANCE (Aktif ✅)" if is_binance else "🔶 Pilih BINANCE",
        callback_data="gui_ex_binance"
    )
    btn_refresh = InlineKeyboardButton(text="🔄 Refresh Status", callback_data="gui_ex_refresh")
    btn_mode = InlineKeyboardButton(text="🎯 Ganti Mode", callback_data="gui_goto_mode")

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn_bitunix, btn_binance],
        [btn_refresh, btn_mode],
    ])


async def build_mode_gui_content(client_inst, active_ex: str, curr_mode: str) -> tuple:
    """Membangun teks pesan dan keyboard untuk GUI Mode."""
    bal_res = await fetch_account_balance_info(client_inst, active_ex, curr_mode)
    curr_m = curr_mode.upper()
    is_real = curr_m in ("REAL", "LIVE")
    is_testnet = curr_m in ("TESTNET", "DEMO_TESTNET", "BINANCE_DEMO")
    bal_val = bal_res.get("total_balance", 0.0)
    avail_val = bal_res.get("available_balance", bal_val)
    pos_cnt = bal_res.get("positions_count", 0)

    if is_real:
        mode_title = "🟢 REAL TRADING (UANG ASLI)"
        exec_desc = f"Order dieksekusi secara nyata di exchange **{active_ex}** dengan modal riil."
        bal_label = "Saldo Akun Real"
    elif active_ex == "BINANCE" and is_testnet:
        mode_title = "🌐 BINANCE FUTURES TESTNET (DEMO.BINANCE.COM)"
        exec_desc = "Terhubung langsung ke **Binance Futures Testnet API**. Order & posisi sinkron ke website `demo.binance.com`."
        bal_label = "Saldo Testnet Demo"
    else:
        mode_title = "📝 VIRTUAL SIMULATION (PAPER TRADING)"
        exec_desc = f"Simulasi internal presisi 1:1 tanpa resiko dengan live market price feed dari **{active_ex}**."
        bal_label = "Saldo Virtual Simulasi"

    text = (
        f"🎛️ **MENU KONTROL MODE TRADING**\n"
        f"────────────────────────\n"
        f"🎯 **Mode Saat Ini:** `{mode_title}`\n"
        f"🏛️ **Exchange Aktif:** `{active_ex}`\n"
        f"💰 **{bal_label}:** `${bal_val:.2f} USDT` (Tersedia: `${avail_val:.2f}`)\n"
        f"📊 **Posisi Terbuka:** `{pos_cnt}` posisi\n"
        f"────────────────────────\n"
        f"ℹ️ *{exec_desc}*\n\n"
        f"Klik tombol di bawah untuk beralih mode secara instan:"
    )
    return text, get_mode_gui_keyboard(curr_mode, active_ex)


async def build_exchange_gui_content(client_inst, active_ex: str, curr_mode: str) -> tuple:
    """Membangun teks pesan dan keyboard untuk GUI Exchange."""
    bal_res = await fetch_account_balance_info(client_inst, active_ex, curr_mode)
    bal_val = bal_res.get("total_balance", 0.0)
    is_real = curr_mode.upper() in ("REAL", "LIVE")
    bal_label = "Saldo Akun Real" if is_real else "Saldo Demo/Simulasi"

    text = (
        f"🏛️ **MENU PILIH EXCHANGE AKTIF**\n"
        f"────────────────────────\n"
        f"🏛️ **Exchange Aktif:** `{active_ex}`\n"
        f"🎯 **Mode Trading:** `{'🟢 REAL' if is_real else '🔵 DEMO/SIMULASI'}`\n"
        f"💰 **{bal_label}:** `${bal_val:.2f} USDT`\n"
        f"────────────────────────\n"
        f"• **BITUNIX**: Altcoin Focus, Low Slippage & Smart Execution\n"
        f"• **BINANCE**: High Liquidity & Official Testnet Demo API\n\n"
        f"Klik tombol di bawah untuk beralih exchange:"
    )
    return text, get_exchange_gui_keyboard(active_ex)


@dp.message(Command("mode"))
@dp.message(Command("set_mode"))
@dp.message(F.text == "🎯 Ganti Mode")
async def mode_switch_handler(message: types.Message, command: CommandObject = None):
    """Membuka GUI kontrol mode trading interaktif."""
    arg = (command.args or "").strip().upper() if command and command.args else ""
    if arg in ("REAL", "LIVE", "ASLI"):
        await scan_order_real_handler(message)
    elif arg in ("PAPER", "SIMULASI", "DEMO", "VIRTUAL", "PAPER_TRADING"):
        await scan_order_paper_handler(message)
    else:
        active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
        client_inst = bot_state.get("client")
        text, kb = await build_mode_gui_content(client_inst, active_ex, curr_mode)
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@dp.message(Command("set_exchange"))
@dp.message(Command("exchange"))
@dp.message(F.text == "🏛️ Ganti Exchange")
async def set_exchange_handler(message: types.Message, command: CommandObject = None):
    """Membuka GUI switch exchange interaktif."""
    target = (command.args or "").strip().upper() if command and command.args else ""
    if target in {"BINANCE", "BITUNIX"}:
        try:
            bot_config.update_active_exchange(target)
            new_adapter = get_exchange_adapter(target)
            await new_adapter.init()
            bot_state["client"] = new_adapter
            
            curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
            text, kb = await build_exchange_gui_content(new_adapter, target, curr_mode)
            await message.answer(
                f"✅ **EXCHANGE BERHASIL DIUBAH KE {target}!**\n\n" + text,
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.answer(f"❌ Gagal mengubah exchange ke {target}: {e}")
    else:
        active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
        client_inst = bot_state.get("client")
        text, kb = await build_exchange_gui_content(client_inst, active_ex, curr_mode)
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")


# ─── CALLBACK QUERY HANDLERS UNTUK INLINE GUI ─────────────────────────────────

@dp.callback_query(F.data.startswith("gui_mode_"))
async def callback_mode_handler(callback: types.CallbackQuery):
    action = callback.data.replace("gui_mode_", "")
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()

    try:
        if action == "real":
            bot_config.update_trading_mode("REAL")
            new_adapter = get_exchange_adapter(active_ex, is_testnet=False, force_recreate=True)
            await new_adapter.init()
            bot_state["client"] = new_adapter
            await callback.answer("🟢 Beralih ke Mode REAL!", show_alert=False)
        elif action == "testnet":
            bot_config.update_trading_mode("TESTNET")
            new_adapter = get_exchange_adapter("BINANCE", is_testnet=True, force_recreate=True)
            await new_adapter.init()
            bot_state["client"] = new_adapter
            await callback.answer("🌐 Beralih ke Mode TESTNET (demo.binance.com)!", show_alert=False)
        elif action in ("demo", "paper"):
            target_mode = "TESTNET" if active_ex == "BINANCE" else "PAPER_TRADING"
            bot_config.update_trading_mode(target_mode)
            is_tn = (target_mode == "TESTNET")
            new_adapter = get_exchange_adapter(active_ex, is_testnet=is_tn, force_recreate=True)
            await new_adapter.init()
            bot_state["client"] = new_adapter
            await callback.answer(f"🔵 Beralih ke Mode {target_mode}!", show_alert=False)
        elif action == "refresh":
            await callback.answer("🔄 Status Mode Diperbarui!", show_alert=False)
    except Exception as e:
        await callback.answer(f"❌ Gagal ganti mode: {e}", show_alert=True)
        return

    curr_mode = getattr(bot_config, "trading_mode", "TESTNET" if active_ex == "BINANCE" else "PAPER_TRADING")
    client_inst = bot_state.get("client")
    text, kb = await build_mode_gui_content(client_inst, active_ex, curr_mode)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("gui_ex_"))
async def callback_exchange_handler(callback: types.CallbackQuery):
    action = callback.data.replace("gui_ex_", "")
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")

    if action in ("bitunix", "binance"):
        target = action.upper()
        try:
            bot_config.update_active_exchange(target)
            new_adapter = get_exchange_adapter(target)
            await new_adapter.init()
            bot_state["client"] = new_adapter
            await callback.answer(f"🏛️ Exchange aktif: {target}!", show_alert=False)
        except Exception as e:
            await callback.answer(f"❌ Gagal beralih exchange: {e}", show_alert=True)
            return
    elif action == "refresh":
        await callback.answer("🔄 Status Exchange Diperbarui!", show_alert=False)

    active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
    client_inst = bot_state.get("client")
    text, kb = await build_exchange_gui_content(client_inst, active_ex, curr_mode)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


@dp.callback_query(F.data == "gui_goto_mode")
async def callback_goto_mode_handler(callback: types.CallbackQuery):
    active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    client_inst = bot_state.get("client")
    text, kb = await build_mode_gui_content(client_inst, active_ex, curr_mode)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@dp.callback_query(F.data == "gui_goto_exchange")
async def callback_goto_exchange_handler(callback: types.CallbackQuery):
    active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    client_inst = bot_state.get("client")
    text, kb = await build_exchange_gui_content(client_inst, active_ex, curr_mode)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()

@dp.message(Command("status"))
@dp.message(Command("saldo"))
@dp.message(Command("balance"))
@dp.message(F.text == "📊 Status Bot")
@dp.message(F.text == "💰 Saldo")
@dp.message(Command("status"))
@dp.message(Command("saldo"))
async def status_handler(message: types.Message):
    state = bot_state.get("state", "PAUSED")
    if state == "DEGRADED" and bot_state.get("websocket_connected"):
        state = "RUNNING" if bot_state.get("is_running", False) else "PAUSED"
        bot_state["state"] = state
    elif state not in {"DEGRADED", "KILL_SWITCH", "RECONCILING"}:
        state = "RUNNING" if bot_state.get("is_running", False) else "PAUSED"
        bot_state["state"] = state
    status_emoji = "🟢 RUNNING" if state == "RUNNING" else f"🔴 {state}"
    client = bot_state.get("client")
    active_ex = getattr(client, "exchange_name", getattr(bot_config, "active_exchange", "EXCHANGE"))
    
    if not client:
        await message.answer(f"Status Bot: {status_emoji}\n⚠️ Koneksi ke {active_ex} belum siap. Coba lagi dalam beberapa detik.")
        return
        
    wait_msg = await message.answer(f"🔄 Mengambil data realtime dari {active_ex}...")
    
    try:
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
        bal_res = await fetch_account_balance_info(client, active_ex, curr_mode)
        is_paper_mode = curr_mode.upper() in ("PAPER_TRADING", "SIMULATION", "VIRTUAL")
        
        if is_paper_mode and not bal_res.get("is_api_demo"):
            total_margin = bal_res.get("total_balance", bot_config.simulated_modal or 100.0)
            unrealized_pnl = bal_res.get("unrealized_pnl", 0.0)
            active_positions = []
        else:
            total_margin = bal_res.get("total_balance", 0.0)
            unrealized_pnl = bal_res.get("unrealized_pnl", 0.0)
            active_positions = bal_res.get("positions", [])
            
            # Fallback jika bal_res belum mengisi positions
            if not active_positions:
                if isinstance(client, BaseExchange):
                    active_positions = await client.get_open_positions()
                elif hasattr(client, "futures_position_information"):
                    pos_info_raw = await client.futures_position_information()
                    active_positions = []
                    for pos in pos_info_raw:
                        amt = float(pos.get("positionAmt", 0.0))
                        if abs(amt) > 0:
                            entry_p = float(pos.get("entryPrice", 0.0))
                            lev = int(pos.get("leverage", 1) or 1)
                            raw_margin = float(pos.get("isolatedMargin", 0.0) or pos.get("positionInitialMargin", 0.0) or 0.0)
                            if raw_margin <= 0 and lev > 0 and entry_p > 0:
                                raw_margin = abs(amt) * entry_p / lev
                            active_positions.append({
                                "symbol": pos["symbol"],
                                "side": "LONG" if amt > 0 else "SHORT",
                                "position_amt": amt,
                                "entry_price": entry_p,
                                "mark_price": float(pos.get("markPrice", 0.0)),
                                "unrealized_pnl": float(pos.get("unRealizedProfit", 0.0)),
                                "leverage": lev,
                                "margin": raw_margin,
                                "update_time": int(pos.get("updateTime", 0)),
                            })
                elif hasattr(client, "futures_account"):
                    account_info = await client.futures_account()
                    total_margin = float(account_info.get("totalMarginBalance", total_margin))
                    unrealized_pnl = float(account_info.get("totalUnrealizedProfit", unrealized_pnl))
                    positions = account_info.get("positions", [])
                    active_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        
        longs = []
        shorts = []
        
        for p in active_positions:
            symbol = p['symbol']
            amt = float(p.get('position_amt', p.get('positionAmt', 0)))
            pnl = float(p.get('unrealized_pnl', p.get('unRealizedProfit', p.get('unrealizedProfit', 0))))
            entry = float(p.get('entry_price', p.get('entryPrice', 0)))
            mark = float(p.get('mark_price', p.get('markPrice', p.get('last_price', 0.0))) or 0.0)
            if mark <= 0:
                mark = entry
            
            meta = bot_state.setdefault("active_trade_meta", {}).get(symbol, {})
            if meta:
                meta["mfe"] = max(float(meta.get("mfe", 0.0)), pnl)
                meta["mae"] = min(float(meta.get("mae", 0.0)), pnl)
                mfe_val = float(meta.get("mfe", 0.0))
            else:
                mfe_val = max(0.0, pnl)

            # 1. Ambil leverage yang benar dari API posisi exchange
            raw_pos_lev = p.get('leverage')
            pos_lev = int(raw_pos_lev) if raw_pos_lev and int(raw_pos_lev) >= 1 else None
            leverage = int(pos_lev or meta.get("leverage") or bot_config.leverage or 20)
            if leverage <= 0:
                leverage = int(bot_config.leverage or 20)

            # 2. Ambil margin modal yang sebenarnya digunakan (bukan notional)
            raw_pos_margin = float(p.get('margin', 0.0) or p.get('isolatedMargin', 0.0) or p.get('positionInitialMargin', 0.0) or p.get('initialMargin', 0.0) or 0.0)
            if raw_pos_margin > 0:
                margin_target = raw_pos_margin
            elif meta.get("margin_usdt") and float(meta["margin_usdt"]) > 0:
                margin_target = float(meta["margin_usdt"])
            else:
                margin_target = (abs(amt) * entry / leverage) if leverage > 0 else (abs(amt) * entry)

            pnl_percent = (pnl / margin_target * 100) if margin_target > 0 else calculate_position_pnl_percent(p)

            # 3. Ambil target TP & SL yang persis sesuai trade setup order
            if meta.get("tp_price") and meta.get("sl_price"):
                tp_price = float(meta["tp_price"])
                sl_price = float(meta["sl_price"])
            else:
                price_tp_move = (bot_config.tp_percent / 100) / leverage if leverage > 0 else 0
                price_sl_move = (bot_config.sl_percent / 100) / leverage if leverage > 0 else 0
                if amt > 0:
                    tp_price = entry * (1 + price_tp_move)
                    sl_price = entry * (1 - price_sl_move)
                else:
                    tp_price = entry * (1 - price_tp_move)
                    sl_price = entry * (1 + price_sl_move)
            
            update_time_ms = int(p.get('update_time') or p.get('updateTime') or 0)
            if update_time_ms > 0:
                open_time = datetime.fromtimestamp(update_time_ms / 1000)
                diff = abs(datetime.now() - open_time)
                total_sec = diff.total_seconds()
                if total_sec < 60:
                    hold_time = f"{int(total_sec)} detik"
                else:
                    hours, remainder = divmod(total_sec, 3600)
                    minutes, _ = divmod(remainder, 60)
                    hold_time = f"{int(hours)}j {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
            else:
                entry_time = meta.get("entry_time") if meta else None
                if entry_time:
                    diff = abs(datetime.now() - entry_time)
                    total_sec = diff.total_seconds()
                    if total_sec < 60:
                        hold_time = f"{int(total_sec)} detik"
                    else:
                        hours, remainder = divmod(total_sec, 3600)
                        minutes, _ = divmod(remainder, 60)
                        hold_time = f"{int(hours)}j {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
                else:
                    hold_time = "N/A"
                
            entry_str = f"{entry:.8f}".rstrip('0').rstrip('.')
            mark_str = f"{mark:.8f}".rstrip('0').rstrip('.')
            tp_str = f"{tp_price:.8f}".rstrip('0').rstrip('.')
            sl_str = f"{sl_price:.8f}".rstrip('0').rstrip('.')
            mfe_str = f"+{mfe_val:.2f} USDT" if mfe_val > 0 else "0.00 USDT"

            is_bot = meta is not None and meta.get("is_bot_trade", False)
            tag_manual = "" if is_bot else " (Manual Trade)"

            p_info = (
                f"🔸 **`{symbol}`**{tag_manual}\n"
                f"   Margin: `{margin_target:.2f} USDT` | Lev: `{leverage}x`\n"
                f"   PNL berjalan: `{pnl:+.2f} USDT ({pnl_percent:+.2f}%)` | MFE: `{mfe_str}`\n"
                f"   Harga entry: `{entry_str}`\n"
                f"   Harga sekarang: `{mark_str}`\n"
                f"   Harga target TP: `{tp_str}`\n"
                f"   Harga target SL: `{sl_str}`\n"
                f"   Hold: `{hold_time}`\n"
            )

                      
            pos_side = str(p.get("side", "")).upper()
            if pos_side in ("LONG", "BUY") or amt > 0:
                longs.append(p_info)
            else:
                shorts.append(p_info)
                
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING").upper()
        is_paper = curr_mode in ("PAPER_TRADING", "SIMULATION", "VIRTUAL")

        # Masukkan posisi virtual / paper trading yang sedang aktif
        active_meta = bot_state.get("active_trade_meta", {})
        for sym_meta, meta in active_meta.items():
            if meta.get("is_paper"):
                side = meta.get("side", "LONG").upper()
                entry = float(meta.get("entry_price", 0.0))
                mfe_val = float(meta.get("mfe", 0.0) or 0.0)
                mae_val = float(meta.get("mae", 0.0) or 0.0)
                lev = int(meta.get("leverage", 10))
                m_usdt = float(meta.get("margin_usdt", 0.0))
                tp_val = float(meta.get("tp_price", 0.0))
                sl_val = float(meta.get("sl_price", 0.0))
                entry_time = meta.get("entry_time")
                duration_min = round((datetime.now() - entry_time).total_seconds() / 60, 1) if entry_time else 0
                hold_time = f"{duration_min:.0f}m"
                
                pnl = mfe_val if mfe_val != 0 else mae_val
                pnl_percent = (pnl / m_usdt * 100) if m_usdt > 0 else 0.0
                unrealized_pnl += pnl

                mfe_str = f"+{mfe_val:.2f} USDT" if mfe_val > 0 else "0.00 USDT"
                p_info = (
                    f"🔸 **🧪 SIMULASI: `{sym_meta}`**\n"
                    f"   Margin: `{m_usdt:.2f} USDT` | Lev: `{lev}x`\n"
                    f"   Floating PNL: `{pnl:+.2f} USDT ({pnl_percent:+.2f}%)` | MFE: `{mfe_str}`\n"
                    f"   Entry: `{entry:.6f}`\n"
                    f"   Target TP: `{tp_val:.6f}` | SL: `{sl_val:.6f}`\n"
                    f"   Hold: `{hold_time}`\n"
                )
                if side in ("LONG", "BUY"):
                    longs.append(p_info)
                else:
                    shorts.append(p_info)

        if active_ex == "BITUNIX" or is_paper:
            websocket_status = "REST Engine 🟢 (Live Monitor)"
        else:
            websocket_status = "Connected 🟢" if bot_state.get("websocket_connected") else "Disconnected ⚪"

        display_modal_val = total_margin
        modal_label = "Saldo Total"
        if is_paper and total_margin <= 0:
            display_modal_val = bot_config.simulated_modal or 100.0
            modal_label = "Modal Simulasi"

        account_pnl_percent = calculate_account_pnl_percent(unrealized_pnl, display_modal_val) if display_modal_val > 0 else 0.0
        mode_badge = f"🧪 `{curr_mode}`" if is_paper else f"🟢 `{curr_mode}`"
        text = (
            f"🤖 **STATUS BOT TRADING**\n"
            f"🏛️ Exchange : `{active_ex}`\n"
            f"🎯 Mode     : {mode_badge}\n"
            f"Status: {status_emoji}\n"
            f"WebSocket: `{websocket_status}`\n"
            f"💰 {modal_label}: `{display_modal_val:.2f} USDT`\n"
            f"📈 Unr. PNL  : `{unrealized_pnl:+.2f} USDT ({account_pnl_percent:+.2f}%)`\n"
            f"──────────────\n"
            f"**🟢 POSISI LONG ({len(longs)}/{bot_config.max_open_positions})**\n"
        )
        if longs:
            text += "".join(longs)
        else:
            text += "   — Tidak ada posisi —\n"
            
        text += f"\n**🔴 POSISI SHORT ({len(shorts)}/{bot_config.max_open_positions})**\n"
        if shorts:
            text += "".join(shorts)
        else:
            text += "   — Tidak ada posisi —\n"
            
        try:
            await wait_msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await wait_msg.edit_text(text)
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ Gagal mengambil profil: {e}")


@dp.message(F.text == "📈 Histori TP")
async def histori_tp_handler(message: types.Message):
    await _send_histori_by_type(message, "TAKE_PROFIT")

@dp.message(F.text == "📉 Histori SL")
async def histori_sl_handler(message: types.Message):
    await _send_histori_by_type(message, "STOP_LOSS")

async def _send_histori_by_type(message: types.Message, order_type_filter: str):
    from database.trade_repo import get_recent_trades
    ex_tag = getattr(bot_config, "active_exchange", "BITUNIX")
    is_real = getattr(bot_config, "trading_mode", "PAPER_TRADING") == "REAL"
    tag = f"{ex_tag}_REAL" if is_real else f"{ex_tag}_SIM"
    
    trades = await get_recent_trades(limit=100, exchange=tag)
    filtered = [t for t in trades if order_type_filter in t.get("order_type", "").upper()]
    
    if not filtered:
        await message.answer(f"Belum ada histori {order_type_filter} untuk {tag}.")
        return
        
    text = f"📊 **HISTORI {order_type_filter} (10 Terakhir)**\n"
    text += f"Mode: `{tag}`\n──────────────\n"
    for t in filtered[:10]:
        sym = t.get("symbol", "")
        side = t.get("side", "")
        pnl = float(t.get("net_pnl", 0))
        dur = t.get("duration_minutes", 0)
        dt_str = t.get("time", "")
        icon = "🟢" if pnl > 0 else "🔴"
        text += f"{icon} `{dt_str}` | **{sym}** ({side})\n"
        text += f"   PNL: {pnl:+.4f} USDT | Hold: {dur}m\n\n"
        
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "💵 Cek Deposit")
@dp.message(Command("deposit"))
async def cek_deposit_handler(message: types.Message):
    await message.answer("🛠️ **Fitur Deposit Terakhir**\nRiwayat transfer (deposit) sedang disiapkan karena memerlukan dukungan khusus dari endpoint API (terutama Bitunix).\n\n💡 _Saat ini, Anda bisa melihat total balance realtime di menu 📊 Status Bot._", parse_mode="Markdown")

@dp.message(Command("mode"))
async def mode_toggle_handler(message: types.Message):
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    if curr_mode == "PAPER_TRADING":
        await scan_order_real_handler(message)
    else:
        await scan_order_paper_handler(message)

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
@dp.message(Command("analisa"))
@dp.message(Command("cek"))
async def analyze_handler(message: types.Message, command: CommandObject):
    symbol = (command.args or "").strip().upper()
    if not symbol:
        await message.answer("Format: `/analisa BTCUSDT` atau `/analyze BTCUSDT`", parse_mode="Markdown")
        return

    client = bot_state.get("client")
    if not client:
        await message.answer("⚠️ Koneksi exchange belum siap.")
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


@dp.message(Command("explain"))
async def explain_handler(message: types.Message, command: CommandObject = None):
    """Menampilkan snapshot penjelasan keputusan trading terakhir (AI Model Explainability)."""
    import json
    import os
    from core.trade_stats import EXPLAINABILITY_FILE

    if not os.path.exists(EXPLAINABILITY_FILE):
        await message.answer("ℹ️ Belum ada rekaman snapshot explainability trade.", parse_mode="Markdown")
        return

    try:
        with open(EXPLAINABILITY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list) or not data:
                await message.answer("ℹ️ Belum ada rekaman snapshot explainability trade.", parse_mode="Markdown")
                return

        target_symbol = (command.args or "").strip().upper() if command else ""
        if target_symbol:
            filtered = [d for d in data if d.get("symbol", "").upper() == target_symbol]
            item = filtered[-1] if filtered else None
            if not item:
                await message.answer(f"ℹ️ Belum ada snapshot trade untuk simbol `{target_symbol}`.", parse_mode="Markdown")
                return
        else:
            item = data[-1]

        symbol = item.get("symbol", "UNKNOWN")
        side = item.get("side", "LONG")
        price = item.get("entry_price", 0.0)
        time_str = item.get("timestamp", "-")
        regime = item.get("market_regime", "UNKNOWN")
        adx = item.get("adx")
        atr_rel = item.get("relative_atr")
        prob = item.get("meta_probability_win")
        prob_str = f"{prob * 100:.1f}%" if prob is not None else "-"
        kelly = item.get("half_kelly_multiplier")
        kelly_str = f"{kelly:.2f}x" if kelly is not None else "-"
        alasan = item.get("alasan_eksekusi", "-")
        confluence = item.get("confluence_breakdown", {})
        conf_score = confluence.get("total_score", "-")

        side_emoji = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
        msg = (
            f"🧠 **AI TRADE EXPLAINABILITY SNAPSHOT**\n"
            f"──────────────\n"
            f"🪙 **Simbol**: `{symbol}` ({side_emoji})\n"
            f"💵 **Harga Entry**: `{price}`\n"
            f"⏱️ **Waktu Analisis**: `{time_str}`\n"
            f"──────────────\n"
            f"🌐 **Market Regime**: `{regime}`\n"
            f"📊 **ADX Strength**: `{adx if adx is not None else '-'}` | **Relative ATR**: `{atr_rel if atr_rel is not None else '-'}`\n"
            f"🎯 **Meta-Labeler Win Prob**: `{prob_str}`\n"
            f"⚖️ **Half-Kelly Multiplier**: `{kelly_str}`\n"
            f"🎚️ **Confluence Score**: `{conf_score}/100`\n"
            f"──────────────\n"
            f"📝 **Alasan Sinyal**: {alasan}\n"
        )
        await message.answer(msg, parse_mode="Markdown")
    except Exception as exc:
        await message.answer(f"❌ Gagal memuat explainability snapshot: {exc}")



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
        raise RuntimeError("Koneksi Exchange belum siap")

    results = []
    if hasattr(client, "get_open_positions"):
        open_positions = await client.get_open_positions()
        for pos in open_positions:
            position_symbol = pos.get("symbol")
            amount = float(pos.get("position_amt", 0))
            if amount == 0 or (not close_all and position_symbol != symbol):
                continue
            result = await close_profitable_position(client, position_symbol, amount)
            results.append(f"{position_symbol}: {result.get('status')}")
    elif hasattr(client, "futures_account"):
        account_info = await client.futures_account()
        for position in account_info.get("positions", []):
            position_symbol = position.get("symbol")
            amount = float(position.get("positionAmt", 0))
            if amount == 0 or (not close_all and position_symbol != symbol):
                continue
            result = await close_profitable_position(client, position_symbol, amount)
            results.append(f"{position_symbol}: {result.get('status')}")

    # Bersihkan juga dari antrean paper trading jika ada
    active_meta = bot_state.get("active_trade_meta", {})
    for sym_meta in list(active_meta.keys()):
        if close_all or sym_meta == symbol:
            active_meta.pop(sym_meta, None)
            bot_state.get("active_trade_reasons", {}).pop(sym_meta, None)
            results.append(f"{sym_meta} (Paper Trade): CLOSED")

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
        result = await close_position_from_telegram(symbol)
        await message.answer(f"🔻 Close posisi {symbol}: {', '.join(result)}")
    except Exception as error:
        await message.answer(f"❌ Gagal close {symbol}: {error}")


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
@dp.message(Command("pause"))
async def stop_handler(message: types.Message):
    bot_state["is_running"] = False
    bot_state["state"] = "PAUSED"
    await message.answer("🛑 Bot Scanner dihentikan sementara.")

@dp.message(Command("resume"))
async def resume_handler(message: types.Message):
    bot_state["is_running"] = True
    bot_state["state"] = "RUNNING"
    bot_state["circuit_breaker_acknowledged"] = True
    await message.answer("▶️ **Bot Scanner Dijalankan Kembali!**\nSistem circuit breaker di-override oleh Admin.", parse_mode="Markdown")

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


def get_scan_settings_keyboard() -> InlineKeyboardMarkup:
    curr_target = str(getattr(bot_config, "scan_target", "ALL")).upper()
    curr_sort = str(getattr(bot_config, "scan_sort", "VOLUME_DESC")).upper()

    t_all = f"{'✅ ' if curr_target == 'ALL' else ''}🌐 ALL Altcoins"
    t_500 = f"{'✅ ' if curr_target == '500' else ''}Top 500"
    t_200 = f"{'✅ ' if curr_target == '200' else ''}Top 200"
    t_100 = f"{'✅ ' if curr_target == '100' else ''}Top 100"
    t_50 = f"{'✅ ' if curr_target == '50' else ''}Top 50"

    s_vol = f"{'✅ ' if curr_sort == 'VOLUME_DESC' else ''}📊 Volume Terbesar"
    s_chg = f"{'✅ ' if curr_sort == 'CHANGE_DESC' else ''}🔥 Change Terbanyak"
    s_gain = f"{'✅ ' if curr_sort == 'GAINERS' else ''}🚀 Top Gainers"
    s_lose = f"{'✅ ' if curr_sort == 'LOSERS' else ''}🔻 Top Losers"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t_all, callback_data="scan_target:ALL"),
            InlineKeyboardButton(text=t_500, callback_data="scan_target:500"),
            InlineKeyboardButton(text=t_200, callback_data="scan_target:200"),
        ],
        [
            InlineKeyboardButton(text=t_100, callback_data="scan_target:100"),
            InlineKeyboardButton(text=t_50, callback_data="scan_target:50"),
        ],
        [
            InlineKeyboardButton(text=s_vol, callback_data="scan_sort:VOLUME_DESC"),
            InlineKeyboardButton(text=s_chg, callback_data="scan_sort:CHANGE_DESC"),
        ],
        [
            InlineKeyboardButton(text=s_gain, callback_data="scan_sort:GAINERS"),
            InlineKeyboardButton(text=s_lose, callback_data="scan_sort:LOSERS"),
        ]
    ])
    return kb


def get_scan_settings_text() -> str:
    curr_target = str(getattr(bot_config, "scan_target", "ALL")).upper()
    curr_sort = str(getattr(bot_config, "scan_sort", "VOLUME_DESC")).upper()
    active_ex = getattr(bot_config, "active_exchange", "BINANCE")
    
    target_desc = "Semua Altcoin Futures (ALL)" if curr_target == "ALL" else f"Top {curr_target} Koin"
    if curr_sort == "VOLUME_DESC":
        sort_desc = "Volume Terbesar 24 Jam (Quote Volume USDT 📊)"
    elif curr_sort == "CHANGE_DESC":
        sort_desc = "Change Terbanyak / Volatilitas Tertinggi (% Perubahan Harga 🔥)"
    elif curr_sort == "GAINERS":
        sort_desc = "Top Gainers (% Kenaikan Tertinggi 🚀)"
    else:
        sort_desc = "Top Losers (% Penurunan Terdalam 🔻)"

    text = (
        f"⚙️ **PENGATURAN SCANNER ALTCOIN**\n\n"
        f"🏛️ **Exchange:** `{active_ex}`\n"
        f"🎯 **Target Koin:** `{target_desc}`\n"
        f"🔄 **Urutan Sortir:** `{sort_desc}`\n\n"
        f"Klik tombol di bawah ini untuk langsung mengubah target dan urutan scanning:\n"
        f"• **Target Koin:** Pilih `ALL Altcoins` atau batasi (Top 500, 200, 100, 50).\n"
        f"• **Urutan Koin:** Pilih `Volume Terbesar`, `Change Terbanyak`, `Gainers`, atau `Losers`.\n\n"
        f"_Bisa juga via teks: `/set_scan_target all` atau `/set_scan_sort change`_"
    )
    return text


@dp.message(Command("set_scan"))
@dp.message(Command("scan_settings"))
async def set_scan_menu_handler(message: types.Message):
    await message.answer(get_scan_settings_text(), reply_markup=get_scan_settings_keyboard(), parse_mode="Markdown")


@dp.message(Command("set_scan_target"))
async def set_scan_target_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            f"ℹ️ Target scan saat ini: `{bot_config.scan_target}`\n"
            f"Gunakan: `/set_scan_target all` atau `/set_scan_target 200`\n"
            f"Atau buka panel tombol interaktif: `/set_scan`",
            parse_mode="Markdown"
        )
        return
    try:
        bot_config.update_scan_target(arg)
        target_info = "Semua Altcoin Futures (ALL)" if bot_config.scan_target == "ALL" else f"Top {bot_config.scan_target} koin"
        await message.answer(
            f"✅ **Target scan diperbarui:** `{bot_config.scan_target}` ({target_info})",
            parse_mode="Markdown"
        )
    except ValueError as e:
        await message.answer(f"❌ {e}")


@dp.message(Command("set_scan_sort"))
async def set_scan_sort_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    if not arg:
        await message.answer(
            f"ℹ️ Urutan scan saat ini: `{bot_config.scan_sort}`\n"
            f"Pilihan yang tersedia:\n"
            f"• `/set_scan_sort volume` (Urutan dari volume USDT terbesar)\n"
            f"• `/set_scan_sort change` (Urutan dari % change terbanyak/volatil)\n"
            f"• `/set_scan_sort gainers` (Urutan koin naik tertinggi)\n"
            f"• `/set_scan_sort losers` (Urutan koin turun terdalam)\n"
            f"Atau buka panel tombol interaktif: `/set_scan`",
            parse_mode="Markdown"
        )
        return
    try:
        bot_config.update_scan_sort(arg)
        await message.answer(
            f"✅ **Urutan scan diperbarui:** `{bot_config.scan_sort}`",
            parse_mode="Markdown"
        )
    except ValueError as e:
        await message.answer(f"❌ {e}")


@dp.callback_query(F.data.startswith("scan_target:"))
async def callback_scan_target(query: types.CallbackQuery):
    target = query.data.split(":")[1]
    try:
        bot_config.update_scan_target(target)
        await query.answer(f"Target Scan: {bot_config.scan_target}")
        await query.message.edit_text(
            get_scan_settings_text(),
            reply_markup=get_scan_settings_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.answer(f"Error: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("scan_sort:"))
async def callback_scan_sort(query: types.CallbackQuery):
    sort_mode = query.data.split(":")[1]
    try:
        bot_config.update_scan_sort(sort_mode)
        await query.answer(f"Urutan Scan: {bot_config.scan_sort}")
        await query.message.edit_text(
            get_scan_settings_text(),
            reply_markup=get_scan_settings_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.answer(f"Error: {e}", show_alert=True)


@dp.message(Command("set_analysis_days"))
async def set_analysis_days_handler(message: types.Message, command: CommandObject):
    try:
        days = int((command.args or "").strip())
        bot_config.update_analysis_lookback_days(days)
        await message.answer(f"✅ Analisis Daily menggunakan {days} candle closed")
    except ValueError as error:
        await message.answer(f"❌ {error}\nMinimal adalah 20 hari.")

@dp.message(Command("set_tp"))
async def set_tp_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            if val <= 0:
                await message.answer("❌ Target Take Profit harus lebih besar dari 0%.")
                return
            bot_config.update_tp(val)
            await message.answer(f"✅ Target Take Profit berhasil diubah menjadi `{val:.1f}%` ROI", parse_mode="Markdown")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: `/set_tp 30`", parse_mode="Markdown")
    else:
        await message.answer(f"ℹ️ Take Profit saat ini: `{bot_config.tp_percent}%` ROI (Gunakan `/set_tp <angka>` untuk mengubah)", parse_mode="Markdown")

@dp.message(Command("set_sl"))
async def set_sl_handler(message: types.Message, command: CommandObject):
    if command.args:
        try:
            val = float(command.args)
            if val <= 0:
                await message.answer("❌ Batas Stop Loss harus lebih besar dari 0%.")
                return
            bot_config.update_sl(val)
            await message.answer(f"✅ Batas Stop Loss berhasil diubah menjadi `{val:.1f}%` ROI", parse_mode="Markdown")
        except ValueError:
            await message.answer("❌ Format salah. Contoh: `/set_sl 25`", parse_mode="Markdown")
    else:
        await message.answer(f"ℹ️ Stop Loss saat ini: `{bot_config.sl_percent}%` ROI (Gunakan `/set_sl <angka>` untuk mengubah)", parse_mode="Markdown")

@dp.message(Command("set_confluence"))
async def set_confluence_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        curr_score = getattr(bot_config, "min_confluence_score", 80.0)
        text = (
            f"🎯 **SMART CONFLUENCE SCORING MATRIX**\n"
            f"────────────────────────\n"
            f"Skor Minimum Saat Ini: **`{curr_score:.0f}/100`** Poin\n\n"
            f"5 Pilar Analisis Institusional:\n"
            f"1. HTF Trend (1D/1H): 25 Poin\n"
            f"2. Pola Candlestick @ Support: 25 Poin\n"
            f"3. Volume Surge (RVOL 5M): 20 Poin\n"
            f"4. Volatility Squeeze Breakout: 15 Poin\n"
            f"5. Momentum & RSI Zone: 15 Poin\n\n"
            f"Gunakan `/set_confluence <50-100>` (Rekomendasi: `80` untuk High-Probability Pro Trades)"
        )
        await message.answer(text, parse_mode="Markdown")
        return
    try:
        val = float(arg)
        bot_config.update_confluence_score(val)
        await message.answer(
            f"✅ **Batas Skor Konfluensi Diperbarui!**\n"
            f"Skor Minimum Baru: **`{val:.1f}/100`**\n"
            f"Bot hanya akan mengeksekusi sinyal jika total skor $\\ge {val:.1f}$.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ {e}")

@dp.message(Command("set_breakeven", "set_be"))
async def set_breakeven_handler(message: types.Message, command: CommandObject):
    args = (command.args or "").strip().split()
    if not args:
        is_on = getattr(bot_config, "use_auto_breakeven", True)
        roi_target = getattr(bot_config, "auto_breakeven_roi_percent", 8.0)
        status_str = "AKTIF (ON) 🟢" if is_on else "NONAKTIF (OFF) 🔴"
        text = (
            f"🛡️ **PENGATURAN AUTO BREAK-EVEN (RISK-FREE)**\n"
            f"────────────────────────\n"
            f"Status Fitur : **{status_str}**\n"
            f"Trigger ROI  : **`+{roi_target:.1f}%`**\n\n"
            f"Penjelasan:\n"
            f"Ketika posisi floating profit mencapai $\\ge +{roi_target:.1f}\\%$, Stop Loss otomatis digeser ke harga Entry (+ fee buffer). Anda bebas risiko dari kerugian!\n\n"
            f"Perintah:\n"
            f"• `/set_breakeven on` (Aktifkan Auto BE)\n"
            f"• `/set_breakeven off` (Matikan Auto BE)\n"
            f"• `/set_breakeven 8.0` (Set trigger aktivasi ke ROI +8.0%)\n"
            f"• `/set_breakeven on 10.0` (Aktifkan dengan trigger +10.0%)"
        )
        await message.answer(text, parse_mode="Markdown")
        return
    
    first = args[0].lower()
    roi_val = None
    if len(args) > 1:
        try:
            roi_val = float(args[1])
        except ValueError:
            pass

    if first in ("on", "true", "1", "aktif", "enable"):
        bot_config.update_auto_breakeven(True, roi_val)
        roi_info = f" pada ROI `+{bot_config.auto_breakeven_roi_percent:.1f}%`" if roi_val is not None else ""
        await message.answer(f"✅ **Auto Break-Even DIAKTIFKAN**{roi_info}! 🛡️", parse_mode="Markdown")
    elif first in ("off", "false", "0", "nonaktif", "disable"):
        bot_config.update_auto_breakeven(False)
        await message.answer("🛑 **Auto Break-Even DINONAKTIFKAN**.", parse_mode="Markdown")
    else:
        try:
            val = float(first)
            bot_config.update_auto_breakeven(True, val)
            await message.answer(f"✅ **Auto Break-Even diset ke ROI `+{val:.1f}%`** (Status: ON) 🛡️", parse_mode="Markdown")
        except ValueError:
            await message.answer("❌ Format salah. Gunakan `/set_breakeven on`, `/set_breakeven off`, atau `/set_breakeven 8.0`", parse_mode="Markdown")

@dp.message(Command("set_exec_mode", "set_execution"))
async def set_exec_mode_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().upper()
    if not arg:
        curr_mode = getattr(bot_config, "execution_mode", "SMART_LIMIT")
        retrace = getattr(bot_config, "limit_retracement_percent", 0.4)
        timeout = getattr(bot_config, "limit_order_timeout_seconds", 180)
        text = (
            f"🎯 **PENGATURAN MODE EKSEKUSI ORDER**\n"
            f"────────────────────────\n"
            f"• Mode Saat Ini : **`{curr_mode}`**\n"
            f"• Limit Retrace : **`{retrace:.2f}%`** (Diskon Pullback)\n"
            f"• Order Timeout : **`{timeout}s`** (Batal Otomatis jika Trap)\n\n"
            f"Pilihan Mode:\n"
            f"1️⃣ `/set_exec_mode SMART_LIMIT` — Pasang Limit di harga diskon pullback. Batalkan jika tidak tersentuh (Anti-Pucuk/Anti-Slippage).\n"
            f"2️⃣ `/set_exec_mode HYBRID` — Pasang Limit diskon dengan toleransi adaptif.\n"
            f"3️⃣ `/set_exec_mode MARKET` — Eksekusi Instan Market Order (Taker)."
        )
        await message.answer(text, parse_mode="Markdown")
        return
    try:
        bot_config.update_execution_mode(arg)
        await message.answer(
            f"✅ **Mode Eksekusi Diubah ke `{bot_config.execution_mode}`!**\n"
            f"• Use Limit Orders: `{bot_config.use_limit_orders}`\n"
            f"• Pullback Retracement: `{bot_config.limit_retracement_percent:.2f}%`\n"
            f"• Timeout Guard: `{bot_config.limit_order_timeout_seconds} detik`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ {e}")

@dp.message(Command("set_limit_retrace", "set_retrace"))
async def set_limit_retrace_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        curr_val = getattr(bot_config, "limit_retracement_percent", 0.4)
        await message.answer(
            f"ℹ️ **Limit Retracement Saat Ini:** `{curr_val:.2f}%`\n\n"
            f"Penjelasan: Bot akan memasang Limit Order dengan harga diskon `{curr_val:.2f}%` di bawah harga sinyal (untuk LONG) atau di atas sinyal (untuk SHORT) agar tidak mengejar harga di pucuk.\n\n"
            f"Gunakan `/set_limit_retrace <0.1-5.0>` (Contoh: `/set_limit_retrace 0.4`)",
            parse_mode="Markdown"
        )
        return
    try:
        val = float(arg)
        bot_config.update_limit_retracement(val)
        await message.answer(f"✅ **Diskon Limit Retracement diubah ke `{val:.2f}%`**! 🎯", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ {e}")

@dp.message(Command("set_limit_timeout", "set_timeout"))
async def set_limit_timeout_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        curr_val = getattr(bot_config, "limit_order_timeout_seconds", 180)
        await message.answer(
            f"ℹ️ **Limit Order Timeout Saat Ini:** `{curr_val}` detik\n\n"
            f"Penjelasan: Jika Limit Order tidak tersentuh (pullback tidak terjadi) dalam `{curr_val}` detik, bot otomatis membatalkan order di exchange untuk menghindari fakeout/trap.\n\n"
            f"Gunakan `/set_limit_timeout <10-1800>` (Contoh: `/set_limit_timeout 180`)",
            parse_mode="Markdown"
        )
        return
    try:
        val = int(arg)
        bot_config.update_limit_timeout(val)
        await message.answer(f"✅ **Timeout Pembatalan Limit Order diubah ke `{val}` detik**! ⏳", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ {e}")

@dp.message(Command("set_atr_sl"))
async def set_atr_sl_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        curr_val = getattr(bot_config, "atr_multiplier_sl", 1.5)
        await message.answer(
            f"ℹ️ **Dynamic ATR SL Multiplier Saat Ini:** `{curr_val:.2f}x ATR`\n\n"
            f"Penjelasan: Stop Loss disesuaikan secara dinamis dengan volatilitas koin ($SL = ATR \\times {curr_val:.2f}$) agar tidak terkena wick hunting pada koin dengan fluktuasi tinggi.\n\n"
            f"Gunakan `/set_atr_sl <0.5-5.0>` (Contoh: `/set_atr_sl 1.8`)",
            parse_mode="Markdown"
        )
        return
    try:
        val = float(arg)
        bot_config.update_atr_multiplier_sl(val)
        await message.answer(f"✅ **Multiplier ATR Stop Loss diubah ke `{val:.2f}x`**! 🛡️", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ {e}")


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

@dp.message(Command("set_max_positions", "set_pos"))
async def set_max_positions_handler(message: types.Message, command: CommandObject):
    args = (command.args or "").strip().split()
    if args:
        try:
            val = int(args[0])
            if val < 1:
                await message.answer("❌ Maksimal posisi harus lebih besar dari 0.")
                return
            per_side = int(args[1]) if len(args) > 1 else val
            bot_config.update_max_positions(val, per_side)
            await message.answer(
                f"✅ **Maksimal Posisi Berhasil Diubah!**\n"
                f"• Max Open Posisi Total: `{val}`\n"
                f"• Max per Sisi (LONG/SHORT): `{per_side}` posisi",
                parse_mode="Markdown"
            )
        except ValueError:
            await message.answer("❌ Format salah. Contoh:\n• `/set_max_positions 2` (Max 2 posisi)\n• `/set_pos 4 2` (Max 4 total, 2 per sisi)", parse_mode="Markdown")
    else:
        max_tot = getattr(bot_config, "max_open_positions", 2)
        max_side = getattr(bot_config, "max_positions_per_side", max_tot)
        await message.answer(
            f"ℹ️ **Pengaturan Maksimal Posisi Saat Ini:**\n"
            f"• Max Open Posisi Total: `{max_tot}`\n"
            f"• Max per Sisi (LONG / SHORT): `{max_side}`\n\n"
            f"**Cara Mengubah:**\n"
            f"• `/set_max_positions 2` (Mengubah menjadi maksimal 2 posisi)\n"
            f"• `/set_pos 4` (Mengubah menjadi maksimal 4 posisi)",
            parse_mode="Markdown"
        )

@dp.message(Command("set_margin"))
async def set_margin_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    if not arg:
        mode_desc = "DYNAMIC (Computed Auto)" if bot_config.margin_mode == "DYNAMIC" else f"FIXED ({bot_config.margin_usdt:.2f} USDT)"
        await message.answer(
            f"ℹ️ **Pengaturan Margin Saat Ini:** `{mode_desc}`\n"
            f"• Risk Per Trade: `{bot_config.risk_per_trade_percent}%` dari saldo\n"
            f"• Max Alokasi per Posisi: `{bot_config.max_position_equity_ratio * 100:.0f}%` dari saldo\n\n"
            "**Cara Mengubah:**\n"
            "• `/set_margin auto` (Mengaktifkan Dynamic Computed Sizing)\n"
            "• `/set_margin 10` (Mengatur Fixed Margin 10 USDT)\n"
            "• `/set_risk 1.5` (Mengatur risiko per trade 1.5% modal)\n"
            "• `/hitung_margin` (Lihat tabel simulasi margin paling aman)",
            parse_mode="Markdown",
        )
        return

    if arg in ["auto", "dynamic", "otomatis"]:
        bot_config.update_margin_mode("DYNAMIC")
        await message.answer("✅ **Margin Mode diubah ke DYNAMIC (Computed Sizing)**!\nMargin akan dihitung otomatis proporsional terhadap saldo & jarak Stop Loss.", parse_mode="Markdown")
    else:
        try:
            val = float(arg)
            if val <= 0:
                await message.answer("❌ Margin harus lebih besar dari 0.")
                return
            bot_config.update_margin_mode("FIXED")
            bot_config.update_margin(val)
            await message.answer(f"✅ **Margin Mode diubah ke FIXED ({val:.2f} USDT)**!", parse_mode="Markdown")
        except ValueError:
            await message.answer("❌ Format salah. Gunakan `/set_margin auto` atau `/set_margin 10`", parse_mode="Markdown")

@dp.message(Command("set_risk"))
async def set_risk_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(f"ℹ️ Risk per trade saat ini: `{bot_config.risk_per_trade_percent}%`\nGunakan `/set_risk <angka>` (Contoh: `/set_risk 1.5`)", parse_mode="Markdown")
        return
    try:
        val = float(arg)
        bot_config.update_risk_per_trade(val)
        await message.answer(f"✅ Risk per trade berhasil diubah menjadi `{val:.2f}%` dari total modal.", parse_mode="Markdown")
    except ValueError as err:
        await message.answer(f"❌ {err}")

@dp.message(Command("set_margin_mode"))
async def set_margin_mode_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().upper()
    if arg not in ["DYNAMIC", "FIXED"]:
        await message.answer("Format: `/set_margin_mode DYNAMIC` atau `/set_margin_mode FIXED`", parse_mode="Markdown")
        return
    try:
        bot_config.update_margin_mode(arg)
        await message.answer(f"✅ Margin Mode berhasil diubah ke `{arg}`.", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal update: {e}")

@dp.message(Command("hitung_margin"))
@dp.message(Command("kalkulasi_margin"))
async def hitung_margin_handler(message: types.Message, command: CommandObject):
    client = bot_state.get("client")
    current_balance = 50.0
    if client:
        try:
            if hasattr(client, "get_account_balance"):
                bal = await client.get_account_balance()
                current_balance = float(bal.get("total_wallet_balance", 50.0))
            elif hasattr(client, "futures_account"):
                acc = await client.futures_account()
                current_balance = float(acc.get("totalMarginBalance", 50.0))
        except Exception:
            pass

    arg = (command.args or "").strip()
    if arg:
        try:
            current_balance = float(arg)
        except ValueError:
            pass

    risk_pct = bot_config.risk_per_trade_percent
    risk_amount = current_balance * (risk_pct / 100.0)
    lev = bot_config.leverage
    max_margin_cap = current_balance * bot_config.max_position_equity_ratio

    safe_margin_sl2 = min((risk_amount / 0.02) / lev, max_margin_cap)
    safe_margin_sl3 = min((risk_amount / 0.03) / lev, max_margin_cap)

    text = (
        f"🧮 **KALKULASI MARGIN PALING AMAN**\n\n"
        f"💰 **Saldo Akun**: `{current_balance:.2f} USDT`\n"
        f"🛡️ **Risk Budget ({risk_pct}%)**: `{risk_amount:.2f} USDT` (Maks rugi jika SL)\n"
        f"⚡ **Leverage Bot**: `{lev}x`\n"
        f"🔒 **Batas Maks Margin/Posisi (20%)**: `{max_margin_cap:.2f} USDT`\n"
        f"──────────────\n"
        f"📊 **Rekomendasi Margin Open Paling Aman:**\n"
        f"• Jarak SL 2.0% : **`{max(1.0, safe_margin_sl2):.2f} USDT`**\n"
        f"• Jarak SL 3.0% : **`{max(1.0, safe_margin_sl3):.2f} USDT`**\n\n"
        f"💡 **Panduan Ukuran Modal Kecil:**\n"
        f"1. Modal $10 - $30 : Margin $1.00 - $2.00 USDT (Lev 5x-10x)\n"
        f"2. Modal $50 - $100: Margin $2.50 - $6.50 USDT (Lev 10x-15x)\n"
        f"3. Modal > $200     : Margin $10.00 - $20.00 USDT (Risk 1.5%)\n\n"
        f"Ketik `/set_margin auto` untuk mengaktifkan kalkulasi dinamis otomatis."
    )
    await message.answer(text, parse_mode="Markdown")

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

@dp.message(Command("set_modal"))
async def set_modal_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    trading_mode = getattr(bot_config, "trading_mode", "TESTNET").upper()

    # Ambil info saldo aktual dari client jika ada
    client = bot_state.get("client")
    actual_balance = 0.0
    if client:
        try:
            if hasattr(client, "get_account_balance"):
                bal = await client.get_account_balance()
                actual_balance = float(bal.get("total_wallet_balance", bal.get("totalMarginBalance", 0.0)))
            elif hasattr(client, "futures_account"):
                acc = await client.futures_account()
                actual_balance = float(acc.get("totalMarginBalance", 0.0))
        except Exception:
            pass

    if not arg:
        if bot_config.simulated_modal and bot_config.simulated_modal > 0:
            current_modal_desc = f"{bot_config.simulated_modal:.2f} USDT (Simulasi Custom)"
        else:
            if active_ex == "BINANCE" and trading_mode == "TESTNET":
                current_modal_desc = f"AUTO (${actual_balance:.2f} USDT Saldo Asli Binance Testnet)" if actual_balance > 0 else "AUTO (Saldo Asli Binance Testnet demo.binance.com)"
            else:
                current_modal_desc = f"AUTO (${actual_balance:.2f} USDT Saldo Real Exchange)" if actual_balance > 0 else f"AUTO (Saldo Asli Exchange {active_ex})"

        await message.answer(
            f"ℹ️ **Pengaturan Modal Sizing Saat Ini:** `{current_modal_desc}`\n"
            f"🏛️ **Exchange:** `{active_ex}` | 🎯 **Mode:** `{trading_mode}`\n\n"
            "**Cara Penggunaan:**\n"
            "• `/set_modal auto` (Otomatis gunakan saldo asli Exchange/Testnet Binance)\n"
            "• `/set_modal 50` (Simulasi sizing dengan modal $50 USDT)\n"
            "• `/set_modal 100` (Simulasi sizing dengan modal $100 USDT)\n"
            "• `/reset_modal` (Reset modal simulasi custom ke default $100)",
            parse_mode="Markdown",
        )
        return

    if arg in ["auto", "real", "tested", "testnet", "reset_auto"]:
        bot_config.update_simulated_modal(None)
        if active_ex == "BINANCE" and trading_mode == "TESTNET":
            bal_str = f"sebesar `${actual_balance:.2f} USDT`" if actual_balance > 0 else "dari API Testnet"
            await message.answer(
                f"✅ **Modal Sizing Diubah ke AUTO (Binance Testnet)!** 🌐\n\n"
                f"Bot sekarang otomatis menghitung margin & lot sizing langsung dari **Saldo Asli Binance Testnet** {bal_str} (`demo.binance.com`).",
                parse_mode="Markdown"
            )
        else:
            bal_str = f"sebesar `${actual_balance:.2f} USDT`" if actual_balance > 0 else "dari Exchange"
            await message.answer(
                f"✅ **Modal Sizing Diubah ke AUTO!** 🏛️\n\n"
                f"Bot sekarang otomatis menghitung margin & lot sizing langsung dari **Saldo Real Exchange** `{active_ex}` {bal_str}.",
                parse_mode="Markdown"
            )
    else:
        try:
            val = float(arg)
            if val <= 0:
                await message.answer("❌ Modal harus lebih besar dari 0.")
                return
            bot_config.update_simulated_modal(val)
            await message.answer(
                f"✅ **Modal Sizing Diset ke `{val:.2f} USDT` (Custom)!** 💰\n\n"
                f"Bot akan menghitung kalkulasi margin & risiko seolah-olah modal Anda adalah `{val:.2f} USDT`.\n"
                f"💡 Ketik `/set_modal auto` kapan saja untuk kembali memakai saldo asli Exchange/Testnet.",
                parse_mode="Markdown",
            )
        except ValueError:
            await message.answer("❌ Format salah. Contoh: `/set_modal 100` atau `/set_modal auto`", parse_mode="Markdown")


@dp.message(Command("reset_modal"))
async def reset_modal_handler(message: types.Message):
    bot_config.reset_simulated_modal(100.0)
    await message.answer("🔄 **Modal Simulasi berhasil di-reset ke `100.00 USDT`**!\nKalkulasi sizing sekarang berbasis modal $100.", parse_mode="Markdown")

@dp.message(Command("reset_stats"))
@dp.message(Command("reset_demo"))
@dp.message(Command("reset_binance_history"))
@dp.message(Command("reset_pola"))
@dp.message(F.text == "🔄 Reset Demo")
async def reset_stats_handler(message: types.Message):
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    trading_mode = getattr(bot_config, "trading_mode", "TESTNET").upper()
    
    bot_config.reset_simulated_modal(100.0)
    bot_config.update_margin_mode("DYNAMIC")
    bot_config.update_leverage(15)
    bot_config.update_risk_per_trade(1.0)
    bot_config.update_max_position_equity_ratio(0.15)
    bot_config.update_tp(30.0)
    bot_config.update_sl(25.0)

    # 1. Reset trade stats session & PostgreSQL trade_history
    reset_trade_stats(exchange=active_ex)
    
    # 2. Reset AI Learner category stats & blacklist
    reset_pattern_blacklist()
    
    # 3. Reset Pattern Memory fingerprints & PostgreSQL pattern tables
    clear_pattern_memory(exchange=active_ex)
    
    # 4. Clear explainability snapshots
    clear_trade_explainability()
    
    # 5. Clear in-memory active trade & daily circuit breaker state
    if isinstance(bot_state.get("active_trade_reasons"), dict):
        bot_state["active_trade_reasons"].clear()
    if isinstance(bot_state.get("active_trade_meta"), dict):
        bot_state["active_trade_meta"].clear()
    bot_state["daily_trades_count"] = 0
    bot_state["consecutive_losses"] = 0
    bot_state["circuit_breaker_active"] = False

    await message.answer(
        f"🔄 **RESET TOTAL HISTORY & POLA AI BERHASIL!** 🔄\n"
        f"Exchange: `{active_ex}` | Mode: `{trading_mode}`\n\n"
        "Seluruh rekaman history lama dan memori pola telah **DIBERSIHKAN TOTAL**:\n"
        "• 🧹 **Trade Stats & DB History** : Reset ke `0W / 0L`\n"
        "• 🧠 **Pattern Learner & Blacklist**: Dikosongkan (Semua pola kembali fresh)\n"
        "• 🧬 **Pattern Memory Fingerprints**: Dihapus (AI siap belajar data baru)\n"
        "• 📑 **Trade Explainability Log**  : Dikosongkan\n"
        "• 🛡️ **Circuit Breaker Status**    : Normal (0 Consecutive Loss)\n"
        "• 💰 **Preset Parameter Aman**    : Modal $100 | Risk 1% | Lev 15x | Dynamic SL/TP\n\n"
        "Bot sekarang akan merekam dan mempelajari setiap setup baru dari nol secara akurat! 🚀",
        parse_mode="Markdown",
    )


@dp.message(Command("set_max_ratio"))
async def set_max_ratio_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(
            f"ℹ️ Max Alokasi per Posisi saat ini: `{bot_config.max_position_equity_ratio * 100:.0f}%` dari total modal.\n"
            "Gunakan `/set_max_ratio <persen>` (Contoh: `/set_max_ratio 15` untuk 15% modal)",
            parse_mode="Markdown",
        )
        return
    try:
        val = float(arg)
        if val > 1.0:
            val = val / 100.0
        bot_config.update_max_position_equity_ratio(val)
        await message.answer(f"✅ Max Alokasi per Posisi berhasil diubah ke `{val * 100:.1f}%` dari total modal.", parse_mode="Markdown")
    except ValueError as err:
        await message.answer(f"❌ {err}")

@dp.message(Command("set_rsi_oversold"))
async def set_rsi_oversold_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(f"ℹ️ RSI Oversold saat ini: `{bot_config.rsi_oversold}`\nGunakan `/set_rsi_oversold 30`", parse_mode="Markdown")
        return
    try:
        val = float(arg)
        bot_config.update_rsi_oversold(val)
        await message.answer(f"✅ RSI Oversold diubah ke `{val}`", parse_mode="Markdown")
    except ValueError as err:
        await message.answer(f"❌ {err}")

@dp.message(Command("set_rsi_overbought"))
async def set_rsi_overbought_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.answer(f"ℹ️ RSI Overbought saat ini: `{bot_config.rsi_overbought}`\nGunakan `/set_rsi_overbought 70`", parse_mode="Markdown")
        return
    try:
        val = float(arg)
        bot_config.update_rsi_overbought(val)
        await message.answer(f"✅ RSI Overbought diubah ke `{val}`", parse_mode="Markdown")
    except ValueError as err:
        await message.answer(f"❌ {err}")

@dp.message(Command("backup_db"))
async def backup_db_handler(message: types.Message):
    wait_msg = await message.answer("🔄 Sedang membuat dump database & mengompres ke ZIP...")
    try:
        from database.send_backup_to_telegram import execute_database_backup
        success = await execute_database_backup(bot=bot, chat_id=str(message.chat.id))
        if success:
            await wait_msg.delete()
        else:
            await wait_msg.edit_text("❌ Gagal membuat backup database. Cek log server.")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Error saat backup: {e}")


@dp.message(Command("restore_latest"))
async def restore_latest_handler(message: types.Message):
    wait_msg = await message.answer("🔄 Sedang mendeteksi koneksi PostgreSQL & me-restore file backup terbaru...")
    try:
        from database.restore_service import restore_from_latest_backup
        result = await restore_from_latest_backup()
        await wait_msg.edit_text(result["message"], parse_mode="Markdown")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Error saat restore: {e}")


@dp.message(Command("restore_db"))
async def restore_db_handler(message: types.Message):
    text = (
        "📥 **PANDUAN RESTORE DATABASE POSTGRESQL**\n\n"
        "Anda dapat me-restore database dengan 2 cara praktis:\n\n"
        "1️⃣ **Upload File Backup Langsung:**\n"
        "   Kirimkan file `.sql` atau `.zip` langsung ke bot Telegram ini (sebagai Document).\n"
        "   Bot akan mengecek koneksi database & otomatis menggabungkan data (*Smart Upsert*).\n\n"
        "2️⃣ **Restore File Terbaru di Server:**\n"
        "   Ketik perintah `/restore_latest` untuk langsung me-restore backup terakhir yang ada di server.\n\n"
        "3️⃣ **Via Web Dashboard:**\n"
        "   Buka `http://localhost:8000` untuk upload via panel web."
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.document)
async def handle_document_upload(message: types.Message):
    doc = message.document
    if not doc or not doc.file_name:
        return

    fn = doc.file_name.lower()
    if not (fn.endswith(".sql") or fn.endswith(".zip")):
        return

    wait_msg = await message.answer(f"📥 Mengunduh file `{doc.file_name}` dan memverifikasi koneksi database...")
    temp_dir = os.path.join("database", "temp_telegram_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    temp_file = os.path.join(temp_dir, doc.file_name)

    try:
        await bot.download(doc, destination=temp_file)
        await wait_msg.edit_text("⚙️ File terunduh. Menjalankan *Smart Upsert* ke database...")

        from database.restore_service import execute_database_restore
        result = await execute_database_restore(temp_file)
        await wait_msg.edit_text(result["message"], parse_mode="Markdown")
    except Exception as exc:
        await wait_msg.edit_text(f"❌ Gagal memproses restore file `{doc.file_name}`: {exc}")
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

@dp.message(Command("winrate", "wr", "wr_stats"))
async def winrate_command_handler(message: types.Message):
    """Laporan performa Win Rate multi-timeframe & status gatekeeper."""
    try:
        from core.learner import get_multi_timeframe_summary
        summary = get_multi_timeframe_summary()
        
        g = summary.get("global", {})
        d = g.get("daily", {})
        w = g.get("weekly", {})
        m = g.get("monthly", {})
        a = g.get("all_time", {})
        
        active_window = getattr(bot_config, "winrate_eval_window", "DAILY")
        min_wr = getattr(bot_config, "min_pattern_winrate", 50.0)
        probation = "AKTIF 🛡️ (1x Uji Coba Harian)" if getattr(bot_config, "use_probation_mode", True) else "OFF"
        
        text = (
            "📊 **REKAP PERFORMA WIN RATE & GATEKEEPER AI** 📊\n\n"
            "⚙️ **Konfigurasi Gatekeeper Saat Ini:**\n"
            f"• 🪟 Jendela Evaluasi : `{active_window}`\n"
            f"• 🎯 Target Min WR    : `{min_wr:.0f}%` (Pola di bawah ini ditolak dari Real)\n"
            f"• 🛡️ Probation Mode  : `{probation}`\n\n"
            "📈 **Statistik Win Rate Multi-Timeframe:**\n"
            f"• 📅 **Hari Ini (Daily 24H) :** `{d.get('win_rate', 0):.1f}%` ({d.get('win', 0)}W / {d.get('loss', 0)}L) | PnL: `{d.get('total_pnl', 0):+.2f}%`\n"
            f"• 🗓️ **Mingguan (Weekly 7D) :** `{w.get('win_rate', 0):.1f}%` ({w.get('win', 0)}W / {w.get('loss', 0)}L) | PnL: `{w.get('total_pnl', 0):+.2f}%`\n"
            f"• 📆 **Bulanan (Monthly 30D):** `{m.get('win_rate', 0):.1f}%` ({m.get('win', 0)}W / {m.get('loss', 0)}L) | PnL: `{m.get('total_pnl', 0):+.2f}%`\n"
            f"• 🌐 **All-Time Akumulasi   :** `{a.get('win_rate', 0):.1f}%` ({a.get('win', 0)}W / {a.get('loss', 0)}L) | PnL: `{a.get('total_pnl', 0):+.2f}%`\n\n"
            "🧠 **Performa Pola & Setup Teratas:**\n"
        )
        
        cats = summary.get("categories", [])[:6]
        if not cats:
            text += "• _Belum ada transaksi terekam di AI Learner._\n"
        else:
            for c in cats:
                w_info = c.get("daily", {}) if active_window == "DAILY" else (c.get("weekly", {}) if active_window == "WEEKLY" else c.get("all_time", {}))
                c_wr = w_info.get("win_rate", 0)
                status_icon = "🟢" if c_wr >= min_wr or w_info.get("total", 0) < 2 else "🔴"
                text += (
                    f"• {status_icon} **{c['name']}**\n"
                    f"  └ `{active_window}`: `{c_wr:.1f}%` ({w_info.get('win', 0)}W/{w_info.get('loss', 0)}L) | Total: {c['total_all']}x\n"
                )
                
        text += (
            "\n──────────────\n"
            "💡 **Perintah Cepat Telegram:**\n"
            "• `/set_wr_window daily` (Hanya nilai 24 jam terakhir)\n"
            "• `/set_wr_window recent20` (Hanya nilai 20 trade terakhir)\n"
            "• `/set_wr_window weekly` (Nilai 7 hari terakhir)\n"
            "• `/set_min_wr 50` (Atur batas minimal Win Rate %)\n"
            "• `/reset_blacklist` (Reset blokir semua pola secara instan)"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal memuat statistik winrate: {e}")

@dp.message(Command("set_wr_window", "set_window"))
async def set_wr_window_command_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    if not arg:
        cur_w = getattr(bot_config, "winrate_eval_window", "DAILY")
        await message.answer(
            f"ℹ️ **Jendela Waktu Evaluasi Win Rate Saat Ini:** `{cur_w}`\n\n"
            "**Pilihan Jendela Waktu:**\n"
            "• `/set_wr_window daily` (Hanya hitung 24 jam terakhir - Fresh harian)\n"
            "• `/set_wr_window recent10` (Hanya 10 trade terakhir)\n"
            "• `/set_wr_window recent20` (Hanya 20 trade terakhir)\n"
            "• `/set_wr_window weekly` (Hitung 7 hari terakhir)\n"
            "• `/set_wr_window all` (Hitung seluruh histori All-Time)",
            parse_mode="Markdown"
        )
        return
        
    try:
        bot_config.update_winrate_window(arg)
        await message.answer(
            f"✅ **Jendela Waktu Win Rate Berhasil Diubah!**\n"
            f"• Mode Aktif: `{bot_config.winrate_eval_window}`\n"
            f"• Pola dengan Win Rate < `{bot_config.min_pattern_winrate}%` pada jendela ini akan otomatis dialihkan ke Latihan Simulasi.",
            parse_mode="Markdown"
        )
    except ValueError as ve:
        await message.answer(f"❌ {ve}")

@dp.message(Command("set_min_wr", "set_min_winrate"))
async def set_min_wr_command_handler(message: types.Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        cur_min = getattr(bot_config, "min_pattern_winrate", 50.0)
        await message.answer(
            f"ℹ️ **Batas Minimal Win Rate Saat Ini:** `{cur_min:.1f}%`\n\n"
            "**Contoh Penggunaan:**\n"
            "• `/set_min_wr 50` (Pola butuh minimal 50% WR)\n"
            "• `/set_min_wr 55` (Pola butuh minimal 55% WR)",
            parse_mode="Markdown"
        )
        return
        
    try:
        val = float(arg)
        bot_config.update_min_pattern_winrate(val)
        await message.answer(
            f"✅ **Batas Minimal Win Rate Berhasil Diubah!**\n"
            f"• Target Min Win Rate: `{val:.1f}%`\n"
            f"• Evaluasi Jendela: `{bot_config.winrate_eval_window}`",
            parse_mode="Markdown"
        )
    except ValueError:
        await message.answer("❌ Format salah. Masukkan angka persentase (misal: `/set_min_wr 50`).", parse_mode="Markdown")

@dp.message(Command("reset_blacklist", "unblock_patterns"))
async def reset_blacklist_command_handler(message: types.Message):
    try:
        from core.learner import reset_pattern_blacklist
        count = reset_pattern_blacklist()
        await message.answer(
            f"✅ **Status Blokir Pola Berhasil Direset!**\n"
            f"• Total `{count}` kategori pola telah di-refresh.\n"
            f"• Semua pola kini memiliki kesempatan segar untuk dievaluasi pada market hari ini.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Gagal reset blacklist: {e}")

@dp.message(F.text == "📊 Status Bot")
async def btn_status_handler(message: types.Message):
    await status_handler(message)

@dp.message(F.text == "🧮 Hitung Margin")
async def btn_hitung_margin_handler(message: types.Message):
    await hitung_margin_handler(message, CommandObject(prefix="/", command="hitung_margin", args=""))

def check_config_conflicts() -> list[str]:
    """Mendeteksi potensi konflik antar parameter konfigurasi bot."""
    warnings = []
    lev = float(getattr(bot_config, "leverage", 15) or 15)
    sl_roi = float(getattr(bot_config, "sl_percent", 25) or 25)
    min_conf = float(getattr(bot_config, "min_confluence_score", 65) or 65)
    exec_m = getattr(bot_config, "execution_mode", "SMART_LIMIT").upper()
    act_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    tr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING").upper()

    # 1. Jarak Harga SL vs Leverage
    price_sl_dist = (sl_roi / lev) if lev > 0 else 0.0
    if price_sl_dist < 0.8:
        warnings.append(
            f"⚠️ **Konflik Leverage vs SL:** Leverage `{lev:.0f}x` & SL `{sl_roi:.0f}% ROI` membuat jarak SL hanya `{price_sl_dist:.2f}%`. "
            f"Rentan terkena wick/sick 5m! Disarankan Leverage 10x-20x."
        )

    # 2. Confluence Score terlalu rendah
    if min_conf < 60:
        warnings.append(
            f"⚠️ **Konflik Kualitas Sinyal:** Min Confluence `{min_conf:.0f}` terlalu rendah. Sinyal noise akan mudah lolos. Disarankan `>= 65.0`."
        )

    # 3. Mode Eksekusi Market
    if exec_m == "MARKET":
        warnings.append(
            "⚠️ **Peringatan Eksekusi:** Mode `MARKET` aktif (rentan beli di pucuk candle & slippage). Disarankan gunakan `SMART_LIMIT`."
        )

    # 4. Penjelasan Spesifik Demo Binance vs Bitunix
    if tr_mode in ("PAPER_TRADING", "TESTNET", "DEMO", "SIMULATION"):
        if act_ex == "BINANCE":
            if bot_config.simulated_modal and bot_config.simulated_modal > 0:
                warnings.append(
                    f"💡 **Demo Binance:** Menggunakan *Virtual Wallet Custom* (${bot_config.simulated_modal:.2f}). PnL terakumulasi realtime ke saldo ini."
                )
            else:
                warnings.append(
                    "💡 **Demo Binance:** Menggunakan *Binance Futures Testnet API ($15.000)* faucet resmi."
                )
        elif act_ex == "BITUNIX":
            warnings.append(
                f"💡 **Demo Bitunix:** Berjalan via *Virtual Paper Engine 1:1* (Live Price Bitunix + Akumulasi PnL Realtime) karena Bitunix tidak memiliki public faucet."
            )

    return warnings


@dp.message(Command("pengaturan", "settings"))
@dp.message(F.text == "⚙️ Pengaturan")
async def btn_pengaturan_handler(message: types.Message):
    ts_status = "ON" if bot_config.use_trailing_stop else "OFF"
    be_status = f"ON (+{getattr(bot_config, 'auto_breakeven_roi_percent', 8.0):.1f}% ROI)" if getattr(bot_config, "use_auto_breakeven", True) else "OFF"
    margin_desc = "DYNAMIC (Auto Computed)" if bot_config.margin_mode == "DYNAMIC" else f"FIXED ({bot_config.margin_usdt:.2f} USDT)"
    modal_desc = f"{bot_config.simulated_modal:.2f} USDT (Custom Demo)" if bot_config.simulated_modal else "AUTO (Saldo Real Exchange)"
    
    scan_target_desc = "Semua Altcoin Futures (ALL)" if bot_config.scan_target == "ALL" else f"Top {bot_config.scan_target}"
    scan_sort_desc = "Volume Terbesar 📊" if bot_config.scan_sort == "VOLUME_DESC" else ("Change % 🔥" if bot_config.scan_sort == "CHANGE_DESC" else ("Gainers 🚀" if bot_config.scan_sort == "GAINERS" else "Losers 🔻"))
    min_conf = getattr(bot_config, "min_confluence_score", 80.0)
    eval_win = getattr(bot_config, "winrate_eval_window", "DAILY")
    min_wr = getattr(bot_config, "min_pattern_winrate", 50.0)

    exec_mode = getattr(bot_config, "execution_mode", "SMART_LIMIT")
    retrace_pct = getattr(bot_config, "limit_retracement_percent", 0.4)
    limit_to = getattr(bot_config, "limit_order_timeout_seconds", 180)
    atr_sl_mult = getattr(bot_config, "atr_multiplier_sl", 1.8)

    # Evaluasi Konflik & Peringatan
    conflicts = check_config_conflicts()
    conflict_text = ""
    if conflicts:
        conflict_text = "────────────────────────\n📋 **STATUS & CATATAN KONFIGURASI:**\n" + "\n".join(conflicts) + "\n"

    text = (
        "⚙️ **PENGATURAN BOT LENGKAP & STATUS KONFIGURASI** ⚙️\n\n"
        "🎯 **1. MODE EKSEKUSI & FIDELITY**\n"
        f"• Mode Eksekusi : **`{exec_mode}`** 🛡️\n"
        f"• Limit Retrace : **`{retrace_pct:.2f}%`** (Diskon Entry Pullback)\n"
        f"• Timeout Guard : **`{limit_to}s`** (Auto-Cancel jika Trap)\n"
        f"• Dynamic ATR SL: **`{atr_sl_mult:.2f}x ATR`** (Anti Wick Hunting)\n\n"
        "💰 **2. MODAL & MARGIN SIZING**\n"
        f"• Basis Modal   : `{modal_desc}`\n"
        f"• Mode Margin   : `{margin_desc}`\n"
        f"• Risk / Trade  : `{bot_config.risk_per_trade_percent}%` dari saldo\n"
        f"• Max Alokasi   : `{bot_config.max_position_equity_ratio * 100:.0f}%` saldo / posisi\n\n"
        "🛡️ **3. TARGET & PROTEKSI (TP / SL / BE / TS)**\n"
        f"• Take Profit   : `{bot_config.tp_percent}%` ROI\n"
        f"• Stop Loss     : `{bot_config.sl_percent}%` ROI\n"
        f"• Auto Break-Even : `{be_status}` (Risk-Free Mode 🛡️)\n"
        f"• Trailing Stop : `{ts_status}` (Act: `{bot_config.ts_activation_percent}%`, Call: `{bot_config.ts_callback_rate}%`)\n\n"
        "🧠 **4. ADAPTIVE WIN RATE GATEKEEPER**\n"
        f"• Jendela Waktu : `{eval_win}` (Rolling Window)\n"
        f"• Min Win Rate  : `{min_wr:.0f}%` (Ambang batas Real vs Latihan)\n"
        f"• Probation Mode: `{'AKTIF (1x Test Harian)' if bot_config.use_probation_mode else 'OFF'}`\n\n"
        "⚡ **5. SCANNER & FILTER PASAR**\n"
        f"• Exchange      : `{bot_config.active_exchange}` | Mode: `{bot_config.trading_mode}`\n"
        f"• Confluence    : Min `{min_conf:.0f}/100` Poin (5-Pillar Pro Setup)\n"
        f"• Target Scan   : `{scan_target_desc}`\n"
        f"• Urutan Scan   : `{scan_sort_desc}`\n"
        f"• Leverage      : `{bot_config.leverage}x` | Max Posisi: `{bot_config.max_open_positions}`\n\n"
        f"{conflict_text}"
        "────────────────────────\n"
        "📝 **PERINTAH PENGATURAN CEPAT:**\n"
        "• `/mode` (GUI Mode) | `/exchange` (GUI Exchange)\n"
        "• `/set_exec_mode smart` | `/set_limit_retrace 0.4` | `/set_atr_sl 1.8`\n"
        "• `/set_modal 100` | `/set_leverage 15` | `/set_tp 40` | `/set_sl 25`"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎯 SMART LIMIT", callback_data="change_exec_SMART_LIMIT"),
            InlineKeyboardButton(text="⚡ HYBRID", callback_data="change_exec_HYBRID_LIMIT"),
            InlineKeyboardButton(text="🚀 MARKET", callback_data="change_exec_MARKET")
        ],
        [
            InlineKeyboardButton(text="🎯 Retrace 0.3%", callback_data="change_retrace_0.3"),
            InlineKeyboardButton(text="🎯 Retrace 0.4%", callback_data="change_retrace_0.4"),
            InlineKeyboardButton(text="🎯 Retrace 0.6%", callback_data="change_retrace_0.6"),
        ],
        [
            InlineKeyboardButton(text="🎯 Ganti Mode", callback_data="gui_goto_mode"),
            InlineKeyboardButton(text="🏛️ Ganti Exchange", callback_data="gui_goto_exchange"),
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh Pengaturan", callback_data="gui_refresh_settings")
        ]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("change_exec_"))
async def callback_change_exec_mode(callback: types.CallbackQuery):
    target = callback.data.replace("change_exec_", "").upper()
    try:
        bot_config.update_execution_mode(target)
        await callback.answer(f"✅ Mode Eksekusi diubah ke {bot_config.execution_mode}!", show_alert=True)
        try:
            await callback.message.delete()
        except:
            pass
        await btn_pengaturan_handler(callback.message)
    except Exception as e:
        await callback.answer(f"Gagal ubah mode eksekusi: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("change_retrace_"))
async def callback_change_retrace(callback: types.CallbackQuery):
    try:
        val_str = callback.data.replace("change_retrace_", "")
        val = float(val_str)
        bot_config.update_limit_retracement(val)
        await callback.answer(f"✅ Diskon Limit Retracement diubah ke {val:.2f}%!", show_alert=True)
        try:
            await callback.message.delete()
        except:
            pass
        await btn_pengaturan_handler(callback.message)
    except Exception as e:
        await callback.answer(f"Gagal ubah limit retrace: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("change_exchange_"))
async def callback_change_exchange(callback: types.CallbackQuery):
    target = callback.data.split("_")[-1].upper()
    current = getattr(bot_config, "active_exchange", "BINANCE").upper()
    
    if target == current:
        await callback.answer(f"Exchange sudah diatur ke {target}!", show_alert=True)
        return

    try:
        await callback.answer("Memproses ganti exchange...")
        bot_config.update_active_exchange(target)
        new_adapter = get_exchange_adapter(target)
        await new_adapter.init()
        bot_state["client"] = new_adapter
        
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
        bal_res = await fetch_account_balance_info(new_adapter, target, curr_mode)
        is_real = curr_mode.upper() in ("REAL", "LIVE")
        
        if is_real:
            total_bal = bal_res.get("total_balance", 0.0)
            bal_str = f"💰 **Saldo Akun Real {target}:** `${total_bal:.2f} USDT`\n"
        else:
            sim_bal = bal_res.get("total_balance", 100.0)
            bal_str = f"💰 **Saldo Simulasi:** `${sim_bal:.2f} USDT` (Live Market Feed dari {target})\n"
            
        text_resp = (
            f"✅ **EXCHANGE BERHASIL DIUBAH KE {target}!**\n"
            f"────────────────────────\n"
            f"🏛️ **Platform Aktif:** `{target}`\n"
            f"🎯 **Mode Saat Ini:** `{curr_mode}`\n"
            f"{bal_str}"
            f"⚡ Scanner sekarang membaca orderbook & data kline langsung dari **{target}**."
        )
        await callback.message.answer(text_resp, parse_mode="Markdown")
        
        try:
            await callback.message.delete()
        except:
            pass
        await btn_pengaturan_handler(callback.message)
        
    except Exception as e:
        await callback.answer(f"Gagal ganti exchange: {e}", show_alert=True)

@dp.callback_query(F.data == "gui_refresh_settings")
async def callback_refresh_settings(callback: types.CallbackQuery):
    await callback.answer("🔄 Memperbarui pengaturan...")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await btn_pengaturan_handler(callback.message)

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
    bot_state["is_running"] = True
    bot_state["state"] = "RUNNING"
    bot_state["circuit_breaker_acknowledged"] = True
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    await message.answer(f"▶️ **Bot Scanner dijalankan kembali!** (Exchange: `{active_ex}`)", reply_markup=get_main_keyboard(active_ex), parse_mode="Markdown")

@dp.message(F.text == "⏯️ Pause / Resume")
async def btn_pause_resume_handler(message: types.Message):
    active_ex = getattr(bot_config, "active_exchange", "BINANCE").upper()
    if bot_state["is_running"]:
        bot_state["is_running"] = False
        bot_state["state"] = "PAUSED"
        await message.answer("🛑 Bot Scanner dihentikan sementara.", reply_markup=get_main_keyboard(active_ex))
    else:
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        bot_state["circuit_breaker_acknowledged"] = True
        await message.answer(f"▶️ **Bot Scanner dijalankan kembali!** (Exchange: `{active_ex}`)", reply_markup=get_main_keyboard(active_ex), parse_mode="Markdown")

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


# ─── GUI MONITORING BERKALA AI & WIN RATE POLA ──────────────────────────────

def get_ai_monitor_keyboard(active_view: str = "overview") -> InlineKeyboardMarkup:
    """Membuat tombol navigasi GUI interaktif untuk Dashboard Monitoring AI."""
    btn_overview = InlineKeyboardButton(
        text="📊 Overview" if active_view != "overview" else "▶️ 📊 Overview",
        callback_data="aimon_overview"
    )
    btn_top = InlineKeyboardButton(
        text="🏆 Top Pola" if active_view != "top_patterns" else "▶️ 🏆 Top Pola",
        callback_data="aimon_top_patterns"
    )
    btn_memory = InlineKeyboardButton(
        text="🧬 Fingerprint" if active_view != "pattern_memory" else "▶️ 🧬 Fingerprint",
        callback_data="aimon_pattern_memory"
    )
    btn_sim = InlineKeyboardButton(
        text="📚 Simulasi Sukses" if active_view != "virtual_wins" else "▶️ 📚 Simulasi",
        callback_data="aimon_virtual_wins"
    )
    btn_refresh = InlineKeyboardButton(
        text="🔄 Refresh Data",
        callback_data=f"aimon_refresh_{active_view}"
    )
    btn_train = InlineKeyboardButton(
        text="🚀 Latih Vision ML",
        callback_data="aimon_train"
    )
    btn_sync = InlineKeyboardButton(
        text="🔄 Auto-Pull GitHub & Heal",
        callback_data="aimon_git_sync"
    )
    btn_reset = InlineKeyboardButton(
        text="🗑️ Reset History AI",
        callback_data="aimon_confirm_reset"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn_overview, btn_top],
            [btn_memory, btn_sim],
            [btn_refresh, btn_train],
            [btn_sync],
            [btn_reset],
        ]
    )

def build_ai_monitor_text(view: str = "overview") -> str:
    """Menyusun laporan data monitoring AI dalam format markdown menarik."""
    summary_data = get_multi_timeframe_summary()
    g_stats = summary_data.get("global", {})
    categories = summary_data.get("categories", [])
    mem_data = load_pattern_memory()
    patterns_map = mem_data.get("patterns", {})
    entries_list = mem_data.get("entries", [])
    
    cfg_window = getattr(bot_config, "winrate_eval_window", "DAILY")
    cfg_min_wr = getattr(bot_config, "min_pattern_winrate", 50.0)
    
    if view == "top_patterns":
        text = "🏆 **AI PATTERN PERFORMANCE & WIN RATE** 🏆\n"
        text += f"⚙️ Gatekeeper Window: `{cfg_window}` | Min WR: `{cfg_min_wr:.0f}%`\n"
        text += "──────────────────────────\n\n"
        if not categories:
            text += "ℹ️ _Belum ada histori pola yang tercatat._\n"
        else:
            for i, cat in enumerate(categories[:8], 1):
                name = cat["name"]
                at = cat["all_time"]
                d = cat["daily"]
                tot = at["total"]
                wr = at["win_rate"]
                pnl = at["total_pnl"]
                wr_icon = "🟢" if wr >= 60 else ("🟡" if wr >= 50 else "🔴")
                status_gate = "✅ ALLOWED" if wr >= cfg_min_wr or tot < 2 else "🚫 BLOCKED"
                text += (
                    f"**{i}. {name}**\n"
                    f"• All-Time: {wr_icon} `{wr:.1f}%` ({at['win']}W/{at['loss']}L | Tot: {tot})\n"
                    f"• Hari Ini: `{d['win_rate']:.1f}%` ({d['win']}W/{d['loss']}L) | PnL: `{pnl:+.2f} USDT`\n"
                    f"• Status: `{status_gate}`\n\n"
                )
        text += "💡 _Klik tombol di bawah untuk navigasi modul lainnya._"
        return text

    elif view == "pattern_memory":
        total_patterns = len(patterns_map)
        valid_patterns = sum(1 for p in patterns_map.values() if (p.get("win", 0) / max(p.get("total", 1), 1)) >= (cfg_min_wr / 100.0))
        blocked_patterns = total_patterns - valid_patterns
        total_entries = len(entries_list)
        
        text = "🧬 **AI PATTERN FINGERPRINT MEMORY** 🧬\n"
        text += "Sistem memori sidik jari kondisi pasar (S/R, BB, RSI, RVOL, Squeeze, ML Vision).\n"
        text += "──────────────────────────\n\n"
        text += f"📊 **Total Sidik Jari Unik:** `{total_patterns}` Pola\n"
        text += f"✅ **Pola Terverifikasi Winrate >= {cfg_min_wr:.0f}%:** `{valid_patterns}` Pola\n"
        text += f"🚫 **Pola Blacklist / Winrate Rendah:** `{blocked_patterns}` Pola\n"
        text += f"📝 **Total Trade Snapshot:** `{total_entries}` Entries\n\n"
        
        sorted_pat = sorted(patterns_map.items(), key=lambda x: (x[1].get("win", 0) / max(x[1].get("total", 1), 1), x[1].get("total", 0)), reverse=True)
        if sorted_pat:
            text += "🌟 **Top 3 Fingerprint Sukses:**\n"
            for k, (fp, p_info) in enumerate(sorted_pat[:3], 1):
                p_tot = p_info.get("total", 0)
                p_win = p_info.get("win", 0)
                p_wr = (p_win / max(p_tot, 1)) * 100
                text += f"**{k}.** `{fp[:42]}...`\n   └ Win Rate: `{p_wr:.1f}%` ({p_win}/{p_tot} Trade) | PnL: `{p_info.get('total_pnl', 0.0):+.2f}%`\n"
        text += "\n💡 _Sidik jari indikator membantu bot memprioritaskan setup terbaik._"
        return text

    elif view == "virtual_wins":
        text = "📚 **HISTORI SIMULASI SUKSES (PAPER WINS)** 📚\n"
        text += "Trade simulasi yang sukses menyentuh TP untuk pembelajaran AI.\n"
        text += "──────────────────────────\n\n"
        if not os.path.exists("virtual_success_log.csv"):
            text += "ℹ️ _Belum ada log simulasi sukses (virtual_success_log.csv)._\n"
        else:
            try:
                wins = []
                with open("virtual_success_log.csv", "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 6 and ("SUCCESS" in str(row) or "TP" in str(row)):
                            wins.append(row)
                if not wins:
                    text += "ℹ️ _Belum ada trade simulasi yang berstatus SUCCESS/TP._\n"
                else:
                    for row in wins[-6:]:
                        t_time = row[0] if len(row) > 0 else "-"
                        t_sym = row[1] if len(row) > 1 else "-"
                        t_side = row[2] if len(row) > 2 else "LONG"
                        t_reason = row[5] if len(row) > 5 else "-"
                        text += f"🎯 **{t_sym}** ({t_side}) - `{t_time}`\n   Setup: _{t_reason[:48]}_\n\n"
            except Exception as e_csv:
                text += f"⚠️ Gagal membaca virtual log: {e_csv}\n"
        text += "💡 _Hasil simulasi otomatis dipelajari oleh Pattern Memory AI._"
        return text

    else:  # OVERVIEW
        d = g_stats.get("daily", {})
        w = g_stats.get("weekly", {})
        m = g_stats.get("monthly", {})
        at = g_stats.get("all_time", {})
        
        d_wr = d.get("win_rate", 0.0)
        w_wr = w.get("win_rate", 0.0)
        at_wr = at.get("win_rate", 0.0)
        
        text = (
            "🧠 **DASHBOARD MONITORING BERKALA AI** 🤖\n"
            "──────────────────────────\n"
            f"🎯 **Status Gatekeeper:** `AKTIF 🛡️`\n"
            f"⏱️ **Jendela Evaluasi:** `{cfg_window}` (Min WR: `{cfg_min_wr:.0f}%`)\n"
            f"🧬 **Total Pola di Memori:** `{len(patterns_map)}` Sidik Jari\n\n"
            "📈 **REKAPITULASI WIN RATE GLOBAL:**\n"
            f"• **Hari Ini (24H):** `{d_wr:.1f}%` ({d.get('win', 0)}W / {d.get('loss', 0)}L | Tot: {d.get('total', 0)})\n"
            f"• **7 Hari (Weekly):** `{w_wr:.1f}%` ({w.get('win', 0)}W / {w.get('loss', 0)}L | Tot: {w.get('total', 0)})\n"
            f"• **All-Time:** `{at_wr:.1f}%` ({at.get('win', 0)}W / {at.get('loss', 0)}L | Tot: {at.get('total', 0)})\n"
            f"• **Estimasi PnL Akumulasi:** `{at.get('total_pnl', 0.0):+.2f} USDT`\n\n"
            "🤖 **Status Machine Learning Vision:**\n"
            f"• Model CNN: `Aktif & Terintegrasi`\n"
            f"• Filter Sinyal: `Strict Confluence + ATR Protective`\n\n"
            "👇 _Pilih tombol di bawah untuk melihat rincian performa:_"
        )
        return text


@dp.message(Command("ai_stats"))
@dp.message(Command("ai_monitor"))
@dp.message(Command("winrate"))
@dp.message(F.text == "🧠 Monitoring AI")
@dp.message(F.text == "🧠 AI Stats")
async def ai_monitor_message_handler(message: types.Message):
    """Membuka dashboard GUI monitoring AI berkala."""
    text = build_ai_monitor_text(view="overview")
    kb = get_ai_monitor_keyboard(active_view="overview")
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("aimon_"))
async def ai_monitor_callback_handler(callback: types.CallbackQuery):
    """Menangani interaksi tombol inline pada dashboard monitoring AI."""
    data = callback.data
    
    if data == "aimon_overview":
        text = build_ai_monitor_text(view="overview")
        kb = get_ai_monitor_keyboard(active_view="overview")
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        await callback.answer("📊 Ringkasan AI ditampilkan.")

    elif data == "aimon_top_patterns":
        text = build_ai_monitor_text(view="top_patterns")
        kb = get_ai_monitor_keyboard(active_view="top_patterns")
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        await callback.answer("🏆 Top Pola ditampilkan.")

    elif data == "aimon_pattern_memory":
        text = build_ai_monitor_text(view="pattern_memory")
        kb = get_ai_monitor_keyboard(active_view="pattern_memory")
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        await callback.answer("🧬 Pattern Memory ditampilkan.")

    elif data == "aimon_virtual_wins":
        text = build_ai_monitor_text(view="virtual_wins")
        kb = get_ai_monitor_keyboard(active_view="virtual_wins")
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        await callback.answer("📚 Simulasi Sukses ditampilkan.")

    elif data.startswith("aimon_refresh_"):
        active_view = data.replace("aimon_refresh_", "")
        text = build_ai_monitor_text(view=active_view)
        kb = get_ai_monitor_keyboard(active_view=active_view)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
            await callback.answer("🔄 Data berhasil diperbarui!")
        except Exception:
            await callback.answer("✅ Data sudah yang terbaru.")

    elif data == "aimon_train":
        await callback.answer("🚀 Memulai proses pelatihan model AI...")
        await callback.message.answer("🔄 Memulai proses pelatihan ulang model CNN Vision... Mohon tunggu.")
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, train_model)
            await callback.message.answer(f"🧠 **Hasil Pelatihan AI:**\n{result}", parse_mode="Markdown")
        except Exception as e_tr:
            await callback.message.answer(f"❌ Terjadi kesalahan saat training: {e_tr}")

    elif data == "aimon_confirm_reset":
        confirm_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="⚠️ Ya, Reset Semua", callback_data="aimon_do_reset"),
                    InlineKeyboardButton(text="❌ Batal", callback_data="aimon_overview")
                ]
            ]
        )
        await callback.message.edit_text(
            "⚠️ **Konfirmasi Reset Memory AI**\n\n"
            "Apakah Anda yakin ingin mereset seluruh histori pembelajaran win rate dan blacklist pola?\n"
            "Tindakan ini akan memberikan awal baru bagi semua pola teknikal.",
            reply_markup=confirm_kb,
            parse_mode="Markdown"
        )
        await callback.answer()

    elif data == "aimon_do_reset":
        reset_count = reset_pattern_blacklist()
        await callback.answer(f"✅ Reset selesai ({reset_count} kategori di-refresh).")
        text = build_ai_monitor_text(view="overview")
        kb = get_ai_monitor_keyboard(active_view="overview")
        await callback.message.edit_text(
            f"✅ **Histori AI Telah Direset!** ({reset_count} kategori)\n\n" + text,
            reply_markup=kb,
            parse_mode="Markdown"
        )

    elif data == "aimon_git_sync":
        await callback.answer("🔄 Memeriksa & menyinkronkan pembaruan GitHub...")
        wait_m = await callback.message.answer("🔍 **Memeriksa pembaruan di GitHub remote...**")
        
        upd = await check_for_git_updates(branch="main")
        if not upd.get("has_update"):
            await wait_m.edit_text(
                "✅ **Kode Bot Sudah Versi Terbaru!**\n"
                f"• Local Commit: `{upd.get('local_hash', 'N/A')}`\n"
                f"• Remote Commit: `{upd.get('remote_hash', 'N/A')}`\n"
                "Tidak ada pembaruan baru di GitHub repository.",
                parse_mode="Markdown"
            )
            return

        behind_n = upd.get("behind_count", 1)
        commit_lines = "\n".join([f"• `{c}`" for c in upd.get("commits", [])])
        await wait_m.edit_text(
            f"📦 Ditemukan `{behind_n}` commit baru!\n{commit_lines}\n\n"
            f"⬇️ Memulai proses Smart Git Pull & Self-Healing...",
            parse_mode="Markdown"
        )
        
        heal_res = await smart_git_pull_and_heal(branch="main")
        log_text = "\n".join([f"• {l}" for l in heal_res.get("logs", [])])
        
        res_msg = (
            f"🎉 **PROSES SINKRONISASI & SELF-HEALING SELESAI!** 🔄\n\n"
            f"🛠️ **Aktivitas:**\n{log_text}\n\n"
            f"🤖 *Bot telah diperbarui dan berjalan dengan kode & dataset terbaru.*"
        )
        await wait_m.edit_text(res_msg, parse_mode="Markdown")


@dp.message(Command("update_bot"))
@dp.message(Command("git_sync"))
@dp.message(Command("git_pull"))
async def update_bot_command_handler(message: types.Message):
    """Command untuk memicu update manual dari GitHub remote."""
    wait_m = await message.answer("🔍 **Memeriksa pembaruan di GitHub...**")
    
    upd = await check_for_git_updates(branch="main")
    if not upd.get("has_update"):
        await wait_m.edit_text(
            "✅ **Kode Bot Sudah Versi Terbaru!**\n"
            f"• Local Commit: `{upd.get('local_hash', 'N/A')}`\n"
            f"• Remote Commit: `{upd.get('remote_hash', 'N/A')}`\n"
            "Tidak ada pembaruan baru di GitHub repository.",
            parse_mode="Markdown"
        )
        return

    behind_n = upd.get("behind_count", 1)
    commit_lines = "\n".join([f"• `{c}`" for c in upd.get("commits", [])])
    await wait_m.edit_text(
        f"📦 Ditemukan `{behind_n}` commit baru!\n{commit_lines}\n\n"
        f"⬇️ Menjalankan Smart Auto-Pull & Self-Healing...",
        parse_mode="Markdown"
    )
    
    heal_res = await smart_git_pull_and_heal(branch="main")
    log_text = "\n".join([f"• {l}" for l in heal_res.get("logs", [])])
    
    res_msg = (
        f"🎉 **SINKRONISASI GITHUB & SELF-HEALING BERHASIL!** 🔄\n\n"
        f"🛠️ **Log Eksekusi:**\n{log_text}\n\n"
        f"🤖 *Bot telah diperbarui secara otomatis.*"
    )
    await wait_m.edit_text(res_msg, parse_mode="Markdown")


