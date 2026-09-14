from aiogram import Bot
from core.logger import log_error
from core.trade_stats import trade_summary
from datetime import datetime

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
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    except Exception as e:
        log_error("TELEGRAM_POSITION_ALERT", str(e))

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
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    except Exception as e:
        log_error("TELEGRAM_EARLY_CLOSE", str(e))


async def send_trade_notification(bot: Bot, chat_id: str, trade_data: dict):
    """
    Mengirim notifikasi trade ke Telegram dengan format standar.
    """
    direction = trade_data.get("direction", "LONG")
    entry_price = float(trade_data.get("entry_price", 0) or 0)
    tp_price = float(trade_data.get("tp_price", 0) or 0)
    sl_price = float(trade_data.get("sl_price", 0) or 0)
    quantity = float(trade_data.get("quantity", 0) or 0)
    tp_value = abs(tp_price - entry_price) * quantity
    sl_value = abs(sl_price - entry_price) * quantity
    score = trade_data.get("score", "N/A")
    confidence = trade_data.get("confidence", "N/A")
    direction_icon = "🟢" if direction in {"LONG", "BUY"} else "🔴"

    message = f"""
🚨 **BOT ORDER**
──────────────
🪙 **Koin:** {trade_data.get('symbol')}
{direction_icon} **Arah:** {direction}
💰 **Margin Target:** {float(trade_data.get('margin_usdt', 0) or 0):.6f} USDT
⚙️ **Leverage Efektif:** {trade_data.get('leverage')}x
💼 **Notional Target:** {float(trade_data.get('notional_usdt', 0) or 0):.2f} USDT
📦 **Kuantitas:** {quantity}
💵 **Harga Masuk:** {entry_price}
──────────────
🎯 **Score:** {score}
🧠 **Confidence:** {confidence}
──────────────
🎯 **Target TP:**
{tp_price} (+${tp_value:.2f})
──────────────
🛡️ **Target SL:**
{sl_price} (-${sl_value:.2f})
"""
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        log_error("TELEGRAM_NOTIFY_TRADE", f"Gagal kirim notif trade ke {chat_id}: {e}")
        print(f"Failed to send Telegram notification: {e}")

async def send_error_log(bot: Bot, chat_id: str, error: str):
    """
    Mengirim log error ke admin.
    """
    message = f"⚠️ **BOT ERROR:**\n`{error}`"
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        log_error("TELEGRAM_NOTIFY_ERROR", f"Gagal kirim notif error ke {chat_id}: {e}")
        print(f"Failed to send error log: {e}")

async def send_order_filled_notification(bot: Bot, chat_id: str, order_data: dict):
    """
    Mengirim notifikasi ketika Take Profit atau Stop Loss tereksekusi beserta rincian Funding Fee.
    """
    order_type = order_data.get('order_type', '')
    symbol = order_data.get('symbol', '')
    price = order_data.get('price', '')
    quantity = order_data.get('quantity', '')
    realized_pnl = float(order_data.get('realized_pnl', 0) or 0)
    commission = float(order_data.get('commission', 0) or 0)
    funding_fee = float(order_data.get('funding_fee', 0) or 0)
    net_pnl = float(order_data.get('net_pnl', realized_pnl - commission + funding_fee) or 0)
    mfe = order_data.get('mfe', 'N/A')
    mae = order_data.get('mae', 'N/A')
    duration = order_data.get('duration', 'N/A')
    summary = trade_summary()

    # Tentukan Icon dan Title berdasarkan tipe order
    if 'TAKE_PROFIT' in order_type:
        title = "🟢 **TAKE PROFIT TERSENTUH!** 🟢"
    elif 'STOP' in order_type:
        title = "🔴 **STOP LOSS TERSENTUH!** 🔴"
    else:
        title = "🏁 **BOT CLOSED ORDER** 🏁"

    result_icon = "🟢" if net_pnl > 0 else "🔴"
    result_text = "PROFIT" if net_pnl > 0 else "LOSS"
    net_pnl_hari_ini = summary['daily_net_pnl']
    net_pnl_icon = "🟢" if net_pnl_hari_ini > 0 else "🔴"
    win_rate_hari_ini = (summary['daily_wins'] / summary['daily_total'] * 100) if summary['daily_total'] else 0
    waktu_tutup = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    funding_icon = "🟢" if funding_fee >= 0 else "🔴"

    message = f"""
{title}
──────────────
🪙 **Koin:** {symbol}
🏆 **Hasil Akhir:** {result_icon} **{result_text}**
──────────────
💰 **Gross Realized PNL:** `{realized_pnl:+.4f} USDT`
💸 **Biaya Komisi Trading:** `-{commission:.4f} USDT`
🔄 **Funding Fee (Pendanaan):** `{funding_icon} {funding_fee:+.4f} USDT`
──────────────────────────────
💵 **NET PNL BERSIH:** `{result_icon} {net_pnl:+.4f} USDT`
──────────────
💵 **Harga Keluar Akhir:** `{price}`
⏱️ **Durasi Posisi:** `{duration}`
📈 **MFE Teramati:** `{mfe}` | 📉 **MAE:** `{mae}`
⏱️ **Waktu Tutup:** {waktu_tutup} WIB
──────────────
📊 **Rekap PNL Hari Ini**
💰 **Total PNL Bersih:** {net_pnl_icon} `{net_pnl_hari_ini:+.4f} USDT`
🎯 **Win Rate Hari Ini:** {win_rate_hari_ini:.1f}% ({summary['daily_wins']}W / {summary['daily_losses']}L)
"""
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        log_error("TELEGRAM_NOTIFY_TPSL", f"Gagal kirim notif TP/SL ke {chat_id}: {e}")
        print(f"Failed to send TP/SL notification: {e}")
