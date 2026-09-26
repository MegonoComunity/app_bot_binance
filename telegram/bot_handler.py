import os
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
from core.trade_stats import trade_summary, reset_trade_stats
from database.trade_repo import get_trade_summary
from core.trade_sync import sync_real_exchange_account
from core.learner import get_multi_timeframe_summary, get_stats as get_learner_stats, reset_pattern_blacklist
from core.pattern_memory import _load as load_pattern_memory
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
    """Reset dan daftarkan perintah resmi bot ke Telegram."""
    commands = [
        BotCommand(command="start", description="Buka Menu Utama & Keyboard"),
        BotCommand(command="help", description="Panduan & Daftar Perintah Lengkap"),
        BotCommand(command="ai_stats", description="🧠 Monitoring Berkala AI & Win Rate Pola"),
        BotCommand(command="winrate", description="📊 Rekap Win Rate Multi-Timeframe"),
        BotCommand(command="update_bot", description="🔄 Auto-Pull & Self-Healing dari GitHub"),
        BotCommand(command="git_sync", description="🔄 Cek & Tarik Update GitHub"),
        BotCommand(command="scan_order_paper", description="🔵 Mulai Scan & Simulasi Paper Trade"),
        BotCommand(command="scan_order_real", description="🟢 Mulai Scan & Order REAL Account"),
        BotCommand(command="mode", description="Cek / Ganti Mode Trading (REAL/SIMULASI)"),
        BotCommand(command="status", description="Cek Saldo & Posisi Terbuka"),
        BotCommand(command="set_exchange", description="Pilih Exchange (binance/bitunix)"),
        BotCommand(command="set_scan", description="🌐 Atur Target Scan & Urutan (ALL/Volume/Change)"),
        BotCommand(command="set_scan_target", description="Target Scan (all/50/100/200/500)"),
        BotCommand(command="set_scan_sort", description="Urutan Scan (volume/change/gainers/losers)"),
        BotCommand(command="pengaturan", description="Menu Pengaturan Lengkap"),
        BotCommand(command="reset_demo", description="Reset Modal ($100) & Statistik"),
        BotCommand(command="hitung_margin", description="Kalkulator Margin Aman"),
        BotCommand(command="set_modal", description="Atur Nominal Modal Simulasi"),
        BotCommand(command="set_margin", description="Atur Mode Margin (auto/nominal)"),
        BotCommand(command="set_risk", description="Atur Risk Per Trade (% Saldo)"),
        BotCommand(command="set_leverage", description="Ubah Leverage"),
        BotCommand(command="set_tp", description="Target Take Profit (%)"),
        BotCommand(command="set_sl", description="Target Stop Loss (%)"),
        BotCommand(command="set_confluence", description="Skor Konfluensi Minimum (50-100, Rekomendasi: 80)"),
        BotCommand(command="set_breakeven", description="Auto Break-Even (on/off, target ROI %)"),
        BotCommand(command="backup_db", description="Backup Database ke Telegram"),
        BotCommand(command="close_all", description="Tutup Semua Posisi Terbuka"),
        BotCommand(command="pause", description="Jeda Scanning"),
        BotCommand(command="resume", description="Lanjutkan Scanning"),
    ]
    try:
        await bot_instance.delete_my_commands()
        await bot_instance.set_my_commands(commands)
    except Exception as exc:
        print(f"[TELEGRAM] Gagal update commands: {exc}")

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="🔵 Scan Order Paper"), KeyboardButton(text="🟢 Scan Order Real")],
        [KeyboardButton(text="📊 Status Bot"), KeyboardButton(text="🧠 Monitoring AI")],
        [KeyboardButton(text="⚙️ Pengaturan"), KeyboardButton(text="🧮 Hitung Margin")],
        [KeyboardButton(text="📈 Histori TP"), KeyboardButton(text="📉 Histori SL")],
        [KeyboardButton(text="🔎 Analisa Koin"), KeyboardButton(text="🔄 Reset Demo")],
        [KeyboardButton(text="⏯️ Pause / Resume"), KeyboardButton(text="📸 Upload Dataset")],
        [KeyboardButton(text="⛔ Close ALL"), KeyboardButton(text="📞 Bantuan")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    active_ex = getattr(bot_config, "active_exchange", "BINANCE")
    curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
    await message.answer(
        f"🚀 **Trading Bot is Online!**\n"
        f"🏛️ **Exchange:** `{active_ex}` | 🎯 **Mode:** `{curr_mode}`\n\n"
        f"• Gunakan tombol **🔵 Scan Order Paper** untuk simulasi tanpa resiko.\n"
        f"• Gunakan tombol **🟢 Scan Order Real** untuk eksekusi order dengan akun nyata.\n"
        f"• Ketik `/help` atau klik **📞 Bantuan** untuk melihat panduan lengkap.\n\n"
        f"Silakan pilih menu di bawah ini:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )

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
            reply_markup=get_main_keyboard(),
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
        # Mode SIMULASI / PAPER / TESTNET
        if target_ex == "BINANCE":
            # Coba koneksi ke Binance Futures Testnet API terlebih dahulu
            try:
                testnet_client = get_exchange_adapter("BINANCE", is_testnet=True)
                await testnet_client.init()
                bal_info = await testnet_client.get_account_balance()
                total_tn = float(bal_info.get("total_wallet_balance", 0.0))
                avail_tn = float(bal_info.get("available_balance", total_tn))
                unreal_tn = float(bal_info.get("unrealized_pnl", 0.0))
                positions_tn = await testnet_client.get_open_positions()
                
                if total_tn > 0:
                    bot_state["client"] = testnet_client
                    return {
                        "success": True,
                        "is_real": False,
                        "source": "Binance Futures Testnet Demo API",
                        "exchange": "BINANCE",
                        "total_balance": total_tn,
                        "available_balance": avail_tn,
                        "unrealized_pnl": unreal_tn,
                        "positions_count": len(positions_tn),
                        "positions": positions_tn,
                        "is_api_demo": True,
                    }
            except Exception as e_tn:
                pass

        # Fallback / Default Paper Simulation (Memori & Database)
        sim_modal = bot_config.simulated_modal if bot_config.simulated_modal is not None and bot_config.simulated_modal > 0 else 100.0
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
            "source": f"{target_ex} Virtual Paper Simulation (DB / Memori)",
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
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal mengaktifkan mode Real: {e}")


@dp.message(Command("scan_order_paper"))
@dp.message(Command("simulasi"))
@dp.message(F.text == "🔵 Scan Order Paper")
async def scan_order_paper_handler(message: types.Message):
    """
    Mengaktifkan mode Paper Trading / Simulasi: bot men-scan market live dan mengeksekusi order virtual,
    serta otomatis mengecek saldo akun demo (Binance Testnet API) atau memori/database (Bitunix).
    """
    try:
        bot_config.update_trading_mode("PAPER_TRADING")
        active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
        
        bot_state["is_running"] = True
        bot_state["state"] = "RUNNING"
        
        # Cek saldo simulasi / demo secara otomatis
        client_inst = bot_state.get("client")
        bal_res = await fetch_account_balance_info(client_inst, active_ex, "PAPER_TRADING")
        
        sim_bal = bal_res.get("total_balance", 100.0)
        pos_cnt = bal_res.get("positions_count", 0)
        db_stats = bal_res.get("db_stats", {})
        src_lbl = bal_res.get("source", "Virtual Paper Simulation")
        is_api_demo = bal_res.get("is_api_demo", False)
        
        stats_text = ""
        if db_stats and db_stats.get("total", 0) > 0:
            stats_text = f"📊 **Riwayat Simulasi DB:** `{db_stats.get('wins', 0)}W / {db_stats.get('losses', 0)}L` (WR: `{db_stats.get('win_rate', 0)}%` | Net: `{db_stats.get('net_pnl', 0):+.2f} USDT`)\n"

        saldo_label = "Saldo Demo Testnet API" if is_api_demo else "Saldo Virtual Simulasi"

        text = (
            f"🧪 **MODE SIMULASI / DEMO DIAKTIFKAN!** 🔵\n"
            f"────────────────────────\n"
            f"🏛️ **Exchange Data:** `{active_ex}` (Live Market Feed)\n"
            f"⚡ **Status Scanning:** `AKTIF (Running)`\n"
            f"🎯 **Mode Eksekusi:** `{'DEMO TESTNET API' if is_api_demo else 'VIRTUAL SIMULATION'}`\n"
            f"────────────────────────\n"
            f"💰 **{saldo_label}:** `${sim_bal:.2f} USDT`\n"
            f"🔌 **Sumber:** `{src_lbl}`\n"
            f"📊 **Posisi Aktif:** `{pos_cnt}` posisi\n"
            f"{stats_text}"
            f"────────────────────────\n"
            f"💡 **Catatan:** Sinyal market real {active_ex} akan diproses dan dipantau 24/7 hingga target TP/SL tercapai.\n"
            f"🔧 **Setup:** Lev `{bot_config.leverage}x` | TP `{bot_config.tp_percent}%` | SL `{bot_config.sl_percent}%`\n\n"
            f"Gunakan `/scan_order_real` untuk mulai order dengan modal real."
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Gagal mengaktifkan mode Paper Trading: {e}")


@dp.message(Command("mode"))
@dp.message(Command("set_mode"))
async def mode_switch_handler(message: types.Message, command: CommandObject):
    """
    Perintah fleksibel untuk melihat status atau mengubah mode trading:
    • /mode real -> Ganti ke mode Real & cek saldo real
    • /mode paper / /mode simulasi -> Ganti ke mode Simulasi & cek saldo simulasi
    • /mode -> Tampilkan status mode & saldo saat ini
    """
    arg = (command.args or "").strip().upper()
    if arg in ("REAL", "LIVE", "ASLI"):
        await scan_order_real_handler(message)
    elif arg in ("PAPER", "SIMULASI", "DEMO", "VIRTUAL", "PAPER_TRADING"):
        await scan_order_paper_handler(message)
    else:
        active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
        curr_mode = getattr(bot_config, "trading_mode", "PAPER_TRADING")
        client_inst = bot_state.get("client")
        bal_res = await fetch_account_balance_info(client_inst, active_ex, curr_mode)
        
        is_real = curr_mode.upper() in ("REAL", "LIVE")
        bal_val = bal_res.get("total_balance", 0.0)
        bal_label = "Saldo Real Wallet" if is_real else "Saldo Virtual Simulasi"
        
        await message.answer(
            f"ℹ️ **STATUS MODE TRADING SAAT INI**\n"
            f"────────────────────────\n"
            f"🏛️ **Exchange:** `{active_ex}`\n"
            f"🎯 **Mode Aktif:** `{'🟢 REAL' if is_real else '🔵 PAPER TRADING (SIMULASI)'}`\n"
            f"💰 **{bal_label}:** `${bal_val:.2f} USDT`\n"
            f"────────────────────────\n"
            f"**Perintah Ganti Mode:**\n"
            f"• `/mode real` atau tombol `🟢 Scan Order Real`\n"
            f"• `/mode paper` atau tombol `🔵 Scan Order Paper`",
            parse_mode="Markdown"
        )


@dp.message(Command("set_exchange"))
async def set_exchange_handler(message: types.Message, command: CommandObject):
    target = (command.args or "").strip().upper()
    if target not in {"BINANCE", "BITUNIX"}:
        current = getattr(bot_config, "active_exchange", "BINANCE")
        await message.answer(
            f"🏛️ **Exchange Saat Ini:** `{current}`\n\n"
            "Format ganti exchange:\n"
            "• `/set_exchange bitunix`\n"
            "• `/set_exchange binance`",
            parse_mode="Markdown"
        )
        return
    try:
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
        
        await message.answer(
            f"✅ **EXCHANGE BERHASIL DIUBAH KE {target}!**\n"
            f"────────────────────────\n"
            f"🏛️ **Platform Aktif:** `{target}`\n"
            f"🎯 **Mode Saat Ini:** `{curr_mode}`\n"
            f"{bal_str}"
            f"⚡ Scanner sekarang membaca orderbook & data kline langsung dari **{target}**.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Gagal mengubah exchange ke {target}: {e}")

@dp.message(Command("status"))
@dp.message(Command("saldo"))
@dp.message(Command("balance"))
@dp.message(F.text == "📊 Status Bot")
@dp.message(F.text == "💰 Saldo")
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
        
    wait_msg = await message.answer(f"🔄 Mengambil data dari {active_ex}...")
    
    try:
        if isinstance(client, BaseExchange):
            balance_info = await client.get_account_balance()
            total_margin = balance_info.get('total_wallet_balance', 0.0)
            unrealized_pnl = balance_info.get('unrealized_pnl', 0.0)
            active_positions = await client.get_open_positions()
        else:
            account_info = await client.futures_account()
            total_margin = float(account_info['totalMarginBalance'])
            unrealized_pnl = float(account_info['totalUnrealizedProfit'])
            positions = account_info.get('positions', [])
            active_positions = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        
        longs = []
        shorts = []
        
        for p in active_positions:
            symbol = p['symbol']
            amt = float(p.get('position_amt', p.get('positionAmt', 0)))
            pnl = float(p.get('unrealized_pnl', p.get('unrealizedProfit', 0)))
            entry = float(p.get('entry_price', p.get('entryPrice', 0)))
            leverage = float(p.get('leverage', 0) or bot_config.leverage)
            margin_target = float(p.get('margin', 0) or 0)
            if margin_target <= 0:
                margin_target = abs(amt) * entry / leverage if leverage > 0 else 0
            
            pnl_percent = (pnl / margin_target * 100) if margin_target > 0 else calculate_position_pnl_percent(p)
            
            mark = float(p.get('mark_price', p.get('markPrice', 0)) or 0)
            if mark <= 0 and amt != 0 and entry > 0:
                mark = entry + (pnl / amt)
            elif mark <= 0:
                mark = entry
                
            meta = bot_state.setdefault("active_trade_meta", {}).get(symbol, {})
            if meta:
                meta["mfe"] = max(float(meta.get("mfe", 0.0)), pnl)
                meta["mae"] = min(float(meta.get("mae", 0.0)), pnl)
                mfe_val = float(meta.get("mfe", 0.0))
            else:
                mfe_val = max(0.0, pnl)

            # 1. Ambil leverage yang benar (prioritas: meta -> position -> config)
            raw_pos_lev = p.get('leverage')
            pos_lev = int(raw_pos_lev) if raw_pos_lev and int(raw_pos_lev) > 1 else None
            leverage = int(meta.get("leverage") or pos_lev or bot_config.leverage or 20)
            if leverage <= 0:
                leverage = int(bot_config.leverage or 20)

            # 2. Ambil margin modal yang sebenarnya digunakan (bukan notional)
            if meta.get("margin_usdt") and float(meta["margin_usdt"]) > 0:
                margin_target = float(meta["margin_usdt"])
            elif p.get("positionInitialMargin") and float(p.get("positionInitialMargin")) > 0:
                margin_target = float(p.get("positionInitialMargin"))
            elif p.get("initialMargin") and float(p.get("initialMargin")) > 0:
                margin_target = float(p.get("initialMargin"))
            else:
                margin_target = (abs(amt) * entry / leverage) if leverage > 0 else (abs(amt) * entry)

            pnl_percent = (pnl / margin_target * 100) if margin_target > 0 else 0.0

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
    if not arg:
        current_modal_desc = f"{bot_config.simulated_modal:.2f} USDT (Simulasi Custom)" if bot_config.simulated_modal else "AUTO (Saldo Real Exchange)"
        await message.answer(
            f"ℹ️ **Pengaturan Modal Sizing Saat Ini:** `{current_modal_desc}`\n\n"
            "**Cara Penggunaan:**\n"
            "• `/set_modal 50` (Simulasi sizing dengan modal $50 USDT)\n"
            "• `/set_modal 100` (Simulasi sizing dengan modal $100 USDT)\n"
            "• `/set_modal auto` (Kembali menggunakan saldo asli Exchange/Testnet)\n"
            "• `/reset_modal` (Reset modal simulasi ke default $100)",
            parse_mode="Markdown",
        )
        return

    if arg in ["auto", "real", "reset"]:
        bot_config.update_simulated_modal(None)
        await message.answer("✅ **Modal Sizing diubah ke AUTO** (Menggunakan saldo riil akun Binance).", parse_mode="Markdown")
    else:
        try:
            val = float(arg)
            if val <= 0:
                await message.answer("❌ Modal harus lebih besar dari 0.")
                return
            bot_config.update_simulated_modal(val)
            await message.answer(
                f"✅ **Modal Sizing diset ke `{val:.2f} USDT`**!\n"
                f"Bot akan menghitung margin dan risiko trading seolah-olah saldo Anda adalah `{val:.2f} USDT`.",
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
@dp.message(F.text == "🔄 Reset Demo")
async def reset_stats_handler(message: types.Message):
    bot_config.reset_simulated_modal(100.0)
    bot_config.update_margin_mode("DYNAMIC")
    bot_config.update_leverage(10)
    bot_config.update_risk_per_trade(1.0)
    bot_config.update_max_position_equity_ratio(0.15)
    bot_config.update_tp(30.0)
    bot_config.update_sl(25.0)
    reset_trade_stats()
    
    await message.answer(
        "🔄 **RESET TOTAL PENGUJIAN DEMO BERHASIL!** 🔄\n\n"
        "Seluruh parameter dan rekap performa telah dikembalikan ke **Preset Uji Coba Paling Aman**:\n"
        "• 💰 Modal Simulasi : `100.00 USDT`\n"
        "• 💵 Margin Sizing  : `DYNAMIC (Auto Safe Computed)`\n"
        "• 🛡️ Risk / Trade   : `1.0%` ($1.00 per SL hit)\n"
        "• 🔒 Max Margin/Pos : `15%` ($15.00 max)\n"
        "• ⚡ Leverage       : `10x` (Jarak SL lega 2.5%)\n"
        "• 🎯 Take Profit    : `30.0%` ROI\n"
        "• 🛑 Stop Loss      : `25.0%` ROI\n"
        "• 📊 Rekap Win Rate : `0W / 0L (Reset ke 0)`\n\n"
        "Bot siap melakukan scanning dan pengujian ulang dari awal dengan teknik optimal! 🚀",
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

    text = (
        "⚙️ **PENGATURAN BOT LENGKAP** ⚙️\n\n"
        "💰 **1. MODAL & MARGIN SIZING**\n"
        f"• Basis Modal : `{modal_desc}`\n"
        f"• Mode Margin : `{margin_desc}`\n"
        f"• Risk / Trade: `{bot_config.risk_per_trade_percent}%` dari saldo\n"
        f"• Max Alokasi : `{bot_config.max_position_equity_ratio * 100:.0f}%` saldo / posisi\n\n"
        "🛡️ **2. TARGET & PROTEKSI (TP / SL / BE / TS)**\n"
        f"• Take Profit : `{bot_config.tp_percent}%` ROI\n"
        f"• Stop Loss   : `{bot_config.sl_percent}%` ROI\n"
        f"• Auto Break-Even : `{be_status}` (Risk-Free Mode 🛡️)\n"
        f"• Trailing Stop   : `{ts_status}` (Act: `{bot_config.ts_activation_percent}%`, Call: `{bot_config.ts_callback_rate}%`)\n\n"
        "🧠 **3. ADAPTIVE WIN RATE GATEKEEPER**\n"
        f"• Jendela Waktu : `{eval_win}` (Rolling Window)\n"
        f"• Min Win Rate  : `{min_wr:.0f}%` (Ambang batas Real vs Latihan)\n"
        f"• Probation Mode: `{'AKTIF (1x Test Harian)' if bot_config.use_probation_mode else 'OFF'}`\n\n"
        "⚡ **4. EKSEKUSI & SCANNER PRO**\n"
        f"• Exchange    : `{bot_config.active_exchange}` | Mode: `{bot_config.trading_mode}`\n"
        f"• Confluence Matrix : Min `{min_conf:.0f}/100` Poin (5-Pillar Pro Setup)\n"
        f"• Target Scan : `{scan_target_desc}`\n"
        f"• Urutan Scan : `{scan_sort_desc}`\n"
        f"• Leverage    : `{bot_config.leverage}x`\n"
        f"• Max Posisi  : `{bot_config.max_open_positions} koin bersamaan`\n"
        f"• RSI Filter  : `{bot_config.rsi_length}` (Oversold: `{bot_config.rsi_oversold:g}` / Overbought: `{bot_config.rsi_overbought:g}`)\n\n"
        "──────────────\n"
        "📝 **DAFTAR PERINTAH PENGATURAN:**\n"
        "📊 **Win Rate & Evaluasi AI:**\n"
        "• `/winrate` (Cek rekap Win Rate Daily / Weekly / Monthly)\n"
        "• `/set_wr_window daily` (Hanya nilai 24 jam terakhir)\n"
        "• `/set_wr_window recent20` (Hanya nilai 20 trade terakhir)\n"
        "• `/set_min_wr 50` (Atur minimal 50% Win Rate)\n"
        "• `/reset_blacklist` (Reset blokir pola)\n\n"
        "🎯 **Pro Matrix & Proteksi:**\n"
        "• `/set_confluence 80` (Min skor konfluensi 80/100)\n"
        "• `/set_breakeven on 8.0` (Auto geser SL ke Entry)\n"
        "• `/set_tp 30` (Take profit 30%)\n"
        "• `/set_sl 25` (Stop loss 25%)\n\n"
        "🌐 **Scanner & Target Koin:**\n"
        "• `/set_scan` (Buka Panel Tombol Target & Urutan Scan)\n"
        "• `/set_scan_target all` (Scan seluruh altcoin futures)\n"
        "• `/set_scan_sort volume` | `/set_scan_sort change`\n\n"
        "💵 **Modal & Sizing:**\n"
        "• `/set_modal 100` | `/set_modal auto` | `/reset_modal`\n"
        "• `/set_margin auto` atau `/set_margin 25`\n"
        "• `/set_risk 1.0` | `/set_max_ratio 15`\n\n"
        "⚡ **Eksekusi & Filter:**\n"
        "• `/set_leverage 10` | `/set_max_positions 3`\n"
        "• `/set_rsi_oversold 30` | `/set_rsi_overbought 70`"
    )
    await message.answer(text, parse_mode="Markdown")

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


