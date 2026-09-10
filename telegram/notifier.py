from aiogram import Bot
from core.logger import log_error
from core.trade_stats import trade_summary

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
━━━━━━━━━━━━━━━━
🪙 Koin: **{trade_data.get('symbol')}**
{direction_icon} Arah: **{direction}**
💰 Margin Target: **{float(trade_data.get('margin_usdt', 0) or 0):.6f} USDT**
⚙️ Leverage Efektif: **{trade_data.get('leverage')}x**
💼 Notional Target: **{float(trade_data.get('notional_usdt', 0) or 0):.2f} USDT**
📦 Kuantitas: **{quantity:.8f}**
💵 Harga Masuk: **{entry_price:.8f}**
━━━━━━━━━━━━━━━━
🎯 Score Sinyal: **{score}**
🧠 Confidence Rule: **{confidence}**
━━━━━━━━━━━━━━━━
🎯 Target TP: **{tp_price:.8f}** *(+{tp_value:.2f} USDT)*
🛡️ Target SL: **{sl_price:.8f}** *(-{sl_value:.2f} USDT)*
━━━━━━━━━━━━━━━━
📊 **Analisa:** {trade_data.get('syarat_1', '')}
📝 **Alasan:** {trade_data.get('syarat_2', '')}
🧠 **ML:** {trade_data.get('pola_ml', 'N/A')}
⏱️ Timeframe: `{trade_data.get('tf')}` | `{trade_data.get('datetime')}`
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
    Mengirim notifikasi ketika Take Profit atau Stop Loss tereksekusi.
    """
    order_type = order_data.get('order_type', '')
    symbol = order_data.get('symbol', '')
    price = order_data.get('price', '')
    quantity = order_data.get('quantity', '')
    realized_pnl = order_data.get('realized_pnl', '0.0')
    commission = float(order_data.get('commission', 0) or 0)
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
        title = "🔔 **ORDER FILLED** 🔔"

    result_icon = "🟢" if float(realized_pnl) > 0 else "🔴"
    message = f"""
📊 **BOT CLOSED ORDER**
━━━━━━━━━━━━━━
🪙 Koin: **{symbol}**
🏆 Hasil Akhir: {result_icon} **{'PROFIT' if float(realized_pnl) > 0 else 'LOSS'}**
━━━━━━━━━━━━━━
💰 Realisasi PNL: **{float(realized_pnl):+.4f} USDT**
💸 Total Komisi: **{commission:.4f} USDT**
💵 Harga Keluar: `{price}`
📈 MFE Teramati: **{mfe} USDT**
📉 MAE Teramati: **{mae} USDT**
⏱️ Waktu Pegang: **{duration}**
━━━━━━━━━━━━━━
📊 **REKAP TRADING**
💰 Total PNL Bersih: **{summary['net_pnl']:+.4f} USDT**
🎯 Win Rate: **{summary['win_rate']:.1f}% ({summary['wins']}W/{summary['losses']}L)**
📅 PNL Hari Ini: **{summary['daily_net_pnl']:+.4f} USDT**
📊 Win Rate Hari Ini: **{(summary['daily_wins'] / summary['daily_total'] * 100) if summary['daily_total'] else 0:.1f}% ({summary['daily_wins']}W/{summary['daily_losses']}L)**
━━━━━━━━━━━━━━
"""
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        log_error("TELEGRAM_NOTIFY_TPSL", f"Gagal kirim notif TP/SL ke {chat_id}: {e}")
        print(f"Failed to send TP/SL notification: {e}")
