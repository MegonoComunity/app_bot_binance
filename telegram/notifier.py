from __future__ import annotations
import asyncio
import time
from typing import Dict, Optional, Any
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from core.logger import log_error
from core.trade_stats import trade_summary
from datetime import datetime

# Rate limiting & deduplication state
_last_sent_time: float = 0.0
_telegram_lock = asyncio.Lock()
_recent_errors: Dict[str, float] = {}

async def safe_send_message(
    bot: Bot,
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
    max_retries: int = 3
) -> bool:
    """
    Kirim pesan Telegram dengan proteksi Rate Limit & Anti-Flood:
    1. Jeda minimum 0.5s antar pesan untuk menghindari Telegram 429 Flood Control.
    2. Menangani TelegramRetryAfter secara otomatis dengan auto-retry.
    """
    if not bot or not chat_id:
        return False

    global _last_sent_time
    async with _telegram_lock:
        now = time.time()
        elapsed = now - _last_sent_time
        if elapsed < 0.5:
            await asyncio.sleep(0.5 - elapsed)

        for attempt in range(max_retries):
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                _last_sent_time = time.time()
                return True
            except TelegramRetryAfter as e_flood:
                wait_time = max(1, int(getattr(e_flood, 'retry_after', 3)))
                print(f"[TELEGRAM FLOOD] Kena rate limit Telegram. Cooldown {wait_time} detik...")
                await asyncio.sleep(wait_time + 1)
            except TelegramAPIError as e_api:
                print(f"[TELEGRAM API ERROR] Attempt {attempt+1}/{max_retries}: {e_api}")
                await asyncio.sleep(1)
            except Exception as e_gen:
                log_error("TELEGRAM_SEND_ERR", str(e_gen))
                break
        return False


async def send_position_analysis_alert(bot: Bot, chat_id: str, data: dict):
    """
    Kirim peringatan analisa posisi aktif (HOLD dengan alert).
    data keys: symbol, side, health_score, alerts, entry_price, current_price, pnl, pnl_pct
    """
    side = data.get("side", "LONG")
    side_icon = "🟢" if side == "LONG" else "🔴"
    health = data.get("health_score", 100)
    health_bar = "🟩" * int(health / 10) + "⬜" * (10 - int(health / 10))
    alerts = data.get("alerts", [])
    entry = data.get("entry_price", 0)
    current = data.get("current_price", 0)
    pnl = data.get("pnl", 0)
    pnl_pct = data.get("pnl_pct", 0)
    pnl_icon = "📈" if pnl >= 0 else "📉"

    def fmt(v): return f"{v:.8f}".rstrip('0').rstrip('.')

    alert_lines = "\n".join(f"   ⚠️ {a}" for a in alerts) if alerts else "   _Tidak ada_"
    message = (
        f"🔍 **ANALISA POSISI AKTIF**\n"
        f"──────────────\n"
        f"{side_icon} **{data.get('symbol')}** ({side})\n"
        f"📥 Entry  : `{fmt(entry)}`\n"
        f"📊 Harga  : `{fmt(current)}`\n"
        f"{pnl_icon} PNL      : `{pnl:+.4f} USDT ({pnl_pct:+.2f}%)`\n"
        f"──────────────\n"
        f"💚 Health Score: **{health:.0f}/100**\n"
        f"{health_bar}\n"
        f"──────────────\n"
        f"⚠️ **Peringatan:**\n{alert_lines}\n"
        f"──────────────\n"
        f"📌 Keputusan: **HOLD** — Kondisi masih aman\n"
    )
    await safe_send_message(bot, chat_id=chat_id, text=message, parse_mode="Markdown")


async def send_early_close_notification(bot: Bot, chat_id: str, data: dict):
    """
    Kirim notifikasi ketika bot menutup posisi lebih awal berdasarkan analisa teknikal.
    data keys: symbol, side, health_score, confidence, reasons, entry_price, close_price, pnl, pnl_pct
    """
    side = data.get("side", "LONG")
    side_icon = "🟢" if side == "LONG" else "🔴"
    pnl = data.get("pnl", 0)
    pnl_icon = "📈 PROFIT" if pnl >= 0 else "📉 LOSS"
    reasons = data.get("reasons", [])
    confidence = data.get("confidence", 0)
    health = data.get("health_score", 0)
    entry = data.get("entry_price", 0)
    close_p = data.get("close_price", 0)
    pnl_pct = data.get("pnl_pct", 0)
    waktu = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    summary = trade_summary()
    net_pnl_hari_ini = summary['daily_net_pnl']
    net_pnl_icon = "🟢" if net_pnl_hari_ini >= 0 else "🔴"
    win_rate_hari_ini = (summary['daily_wins'] / summary['daily_total'] * 100) if summary['daily_total'] else 0

    def fmt(v): return f"{v:.8f}".rstrip('0').rstrip('.')

    reason_lines = "\n".join(f"   🔺 {r}" for r in reasons) if reasons else "   —"

    message = (
        f"🤖 **SMART EXIT — POSISI DITUTUP OTOMATIS**\n"
        f"──────────────\n"
        f"{side_icon} **{data.get('symbol')}** ({side})\n"
        f"🏆 Hasil  : **{pnl_icon}**\n"
        f"──────────────\n"
        f"📥 Entry     : `{fmt(entry)}`\n"
        f"📤 Close     : `{fmt(close_p)}`\n"
        f"💰 PNL       : `{pnl:+.4f} USDT ({pnl_pct:+.2f}%)`\n"
        f"⏱️ Waktu     : {waktu} WIB\n"
        f"──────────────\n"
        f"🧠 **Alasan Smart Exit (Confidence {confidence:.0f}%):**\n"
        f"{reason_lines}\n"
        f"💚 Health Score terakhir: **{health:.0f}/100**\n"
        f"──────────────\n"
        f"📊 **Rekap PNL Hari Ini**\n"
        f"💰 Total PNL Bersih: {net_pnl_icon} {net_pnl_hari_ini:+.4f} USDT\n"
        f"🎯 Win Rate Hari Ini: {win_rate_hari_ini:.1f}% ({summary['daily_wins']}W / {summary['daily_losses']}L)\n"
    )
    await safe_send_message(bot, chat_id=chat_id, text=message, parse_mode="Markdown")


async def send_trade_notification(bot: Bot, chat_id: str, trade_data: dict):
    """
    Mengirim notifikasi trade ke Telegram dengan detail Evaluasi Brain AI lengkap saat ORDER.
    """
    symbol = trade_data.get("symbol", "")
    direction = trade_data.get("direction", "LONG")
    entry_price = float(trade_data.get("entry_price", 0) or 0)
    tp_price = float(trade_data.get("tp_price", 0) or 0)
    sl_price = float(trade_data.get("sl_price", 0) or 0)
    quantity = float(trade_data.get("quantity", 0) or 0)
    margin_usdt = float(trade_data.get("margin_usdt", 0) or 0)
    leverage = trade_data.get("leverage", 10)
    notional_usdt = float(trade_data.get("notional_usdt", 0) or 0)
    score = trade_data.get("score", "N/A")
    confidence = trade_data.get("confidence", "N/A")
    tf = trade_data.get("tf", "15m")
    dt = trade_data.get("datetime", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tp_sl_info = trade_data.get("tp_sl_info", "")
    direction_icon = "🟢" if direction in {"LONG", "BUY"} else "🔴"
    
    tp_value = abs(tp_price - entry_price) * quantity
    sl_value = abs(sl_price - entry_price) * quantity
    
    ai_eval = trade_data.get("ai_evaluation", trade_data.get("syarat_2", "Sinyal Multi-Indikator"))
    method = trade_data.get("method", trade_data.get("syarat_2", "Multi-Indicator Confluence"))

    action_text = "BUY/LONG" if direction in {"LONG", "BUY"} else "SELL/SHORT"

    message = (
        f"🚨 **AUTO-TRADE EXECUTED** 🚨\n"
        f"**{direction_icon} {action_text} Coin : `{symbol}`**\n"
        f"Harga Entry : `{entry_price}`\n"
        f"Time Frame  : `{tf}` | Tanggal: `{dt} WIB`\n"
        f"🔧 **Trade Setup:** Margin: `{margin_usdt:.2f} USDT` | Leverage: `{leverage}x` | Target: `{tp_sl_info}`\n"
        f"📦 **Kuantitas:** `{quantity}` (Notional: `{notional_usdt:.2f} USDT`)\n"
        f"──────────────\n"
        f"🎯 **Target TP:** `{tp_price}` (+${tp_value:.2f})\n"
        f"🛡️ **Target SL:** `{sl_price}` (-${sl_value:.2f})\n"
        f"──────────────\n"
        f"🧠 **EVALUASI BRAIN AI & ALASAN ENTRY:**\n"
        f"{ai_eval}\n"
        f"──────────────\n"
        f"📊 **Metode / Setup:** `{method}`\n"
        f"🎯 **Signal Score:** `{score}` | 🧠 **Confidence:** `{confidence}`\n"
    )
    await safe_send_message(bot, chat_id=chat_id, text=message, parse_mode='Markdown')


async def send_error_log(bot: Bot, chat_id: str, error: str):
    """
    Mengirim log error ke admin dengan deduplikasi (mencegah spam saat rate limit berulang).
    """
    global _recent_errors
    now = time.time()
    
    # Bersihkan error lama > 300 detik
    _recent_errors = {k: v for k, v in _recent_errors.items() if now - v < 300}
    
    # Simple error key (40 chars pertama)
    err_key = error[:40]
    if err_key in _recent_errors and (now - _recent_errors[err_key]) < 60:
        # Skip pengiriman error yang sama jika baru dikirim dalam 60 detik terakhir
        return
        
    _recent_errors[err_key] = now
    message = f"⚠️ **BOT ERROR:**\n`{error}`"
    await safe_send_message(bot, chat_id=chat_id, text=message, parse_mode='Markdown')


async def send_order_filled_notification(bot: Bot, chat_id: str, order_data: dict):
    """
    Mengirim notifikasi ketika Take Profit, Stop Loss, atau Market Close tereksekusi.
    Format mencakup Koin, Hasil Akhir, Net PnL, Harga Keluar, Durasi, MFE/MAE, Evaluasi Brain AI, dan Rekap PNL.
    """
    order_type = order_data.get('order_type', '')
    symbol = order_data.get('symbol', '')
    price = order_data.get('price', '')
    entry_price = order_data.get('entry_price', 'N/A')
    realized_pnl = float(order_data.get('realized_pnl', 0) or 0)
    commission = float(order_data.get('commission', 0) or 0)
    funding_fee = float(order_data.get('funding_fee', 0) or 0)
    net_pnl = float(order_data.get('net_pnl', realized_pnl - commission + funding_fee) or 0)

    mfe = order_data.get('mfe', '+0.00%')
    mae = order_data.get('mae', '-0.00%')
    duration = order_data.get('duration', 'N/A')

    fingerprint = order_data.get('fingerprint', 'Kombinasi Standar')
    win_rate = float(order_data.get('win_rate', 0.0) or 0.0)
    total_trades = int(order_data.get('total_trades', 0) or 0)
    wins = int(order_data.get('wins', 0) or 0)
    losses = int(order_data.get('losses', 0) or 0)

    alasan_masuk = order_data.get('alasan_masuk', order_data.get('alasan', 'Sinyal Multi-Indikator AI'))
    ai_eval_summary = order_data.get('ai_eval_summary', '')

    summary = trade_summary()

    if 'TAKE_PROFIT' in order_type:
        header_title = "🏁 **BOT CLOSED ORDER (TAKE PROFIT)** 🏁"
        trigger_reason = "🎯 Target Take Profit Tercapai (Profit Terkunci)"
    elif 'STOP' in order_type:
        header_title = "🏁 **BOT CLOSED ORDER (STOP LOSS)** 🏁"
        trigger_reason = "🛡️ Proteksi Stop Loss Tertrigger (Risiko Dibatasi)"
    else:
        header_title = f"🏁 **BOT CLOSED ORDER ({order_type})** 🏁"
        trigger_reason = f"🤖 Market Exit ({order_type})"

    result_icon = "🟢" if net_pnl > 0 else "🔴"
    result_text = "PROFIT" if net_pnl > 0 else "LOSS"
    net_pnl_hari_ini = summary['daily_net_pnl']
    net_pnl_icon = "🟢" if net_pnl_hari_ini >= 0 else "🔴"
    win_rate_hari_ini = (summary['daily_wins'] / summary['daily_total'] * 100) if summary['daily_total'] else 0

    eval_section = ""
    if ai_eval_summary:
        eval_section = f"──────────────\n🧠 **EVALUASI INDIKATOR SAAT ENTRY:**\n{ai_eval_summary}\n"

    message = (
        f"{header_title}\n"
        f"──────────────\n"
        f"🪙 **Koin:** `{symbol}`\n"
        f"🏆 **Hasil Akhir:** {result_icon} **{result_text}**\n"
        f"──────────────\n"
        f"💰 **Net PnL Bersih:** `{result_icon} {net_pnl:+.4f} USDT`\n"
        f"💵 **Harga Entry:** `{entry_price}` ➔ **Harga Keluar:** `{price}`\n"
        f"⏱️ **Durasi Posisi:** `{duration}`\n"
        f"📈 **MFE (Max Profit):** `{mfe}` | 📉 **MAE (Max Drawdown):** `{mae}`\n"
        f"{eval_section}"
        f"──────────────\n"
        f"🧠 **EVALUASI BRAIN AI (POST-TRADE LEARNING):**\n"
        f"• **Alasan Masuk Awal:** {alasan_masuk}\n"
        f"• **Trigger Exit:** {trigger_reason}\n"
        f"• **Sidik Jari Pola:** `{fingerprint}`\n"
        f"• **Statistik Pola AI:** Win Rate: **{win_rate:.1f}%** ({wins}W / {losses}L dari {total_trades}x trade)\n"
        f"──────────────\n"
        f"📊 **Rekap PNL Hari Ini**\n"
        f"💰 **Total PNL Bersih:** {net_pnl_icon} `{net_pnl_hari_ini:+.4f} USDT`\n"
        f"🎯 **Win Rate Hari Ini:** {win_rate_hari_ini:.1f}% ({summary['daily_wins']}W / {summary['daily_losses']}L)\n"
    )
    await safe_send_message(bot, chat_id=chat_id, text=message, parse_mode='Markdown')
