from aiogram import Bot
from core.logger import log_error

async def send_trade_notification(bot: Bot, chat_id: str, trade_data: dict):
    """
    Mengirim notifikasi trade ke Telegram dengan format standar.
    """
    message = f"""
🚨 **AUTO-TRADE EXECUTED** 🚨
**BUY/LONG Coin : {trade_data.get('symbol')}**
Harga Entry : {trade_data.get('price')}
Time Frame  : {trade_data.get('tf')} | Tanggal: {trade_data.get('datetime')}
🔧 **Trade Setup:** Margin: {trade_data.get('margin')}% | Leverage: {trade_data.get('leverage')}x | Target: {trade_data.get('tp_sl_info')}
📊 **Kondisi Terpenuhi:** {trade_data.get('syarat_1', '')}, {trade_data.get('syarat_2', '')}, {trade_data.get('pola_ml', '')}
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

    # Tentukan Icon dan Title berdasarkan tipe order
    if 'TAKE_PROFIT' in order_type:
        title = "🟢 **TAKE PROFIT TERSENTUH!** 🟢"
    elif 'STOP' in order_type:
        title = "🔴 **STOP LOSS TERSENTUH!** 🔴"
    else:
        title = "🔔 **ORDER FILLED** 🔔"

    message = f"""
{title}
**Coin:** {symbol}
**Tipe Order:** {order_type}
**Harga Eksekusi:** {price}
**Jumlah:** {quantity}
**Realized PnL:** {realized_pnl} USDT
    """
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
    except Exception as e:
        log_error("TELEGRAM_NOTIFY_TPSL", f"Gagal kirim notif TP/SL ke {chat_id}: {e}")
        print(f"Failed to send TP/SL notification: {e}")
