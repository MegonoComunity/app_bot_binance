from binance import AsyncClient
from binance.enums import *
from core.logger import log_error
import math

async def set_leverage(client: AsyncClient, symbol: str, leverage: int):
    try:
        await client.futures_change_leverage(symbol=symbol, leverage=leverage)
        print(f"Leverage for {symbol} set to {leverage}x")
    except Exception as e:
        print(f"Error setting leverage for {symbol}: {e}")

_exchange_info_cache = {}

async def get_symbol_precision(client: AsyncClient, symbol: str):
    if not _exchange_info_cache:
        try:
            info = await client.futures_exchange_info()
            for s in info['symbols']:
                lot_filter = next((f for f in s['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                price_filter = next((f for f in s['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                
                if lot_filter:
                    step_str = str(lot_filter['stepSize']).rstrip('0')
                    if step_str.endswith('.'):
                        step_str = step_str[:-1]
                        
                    if '.' in step_str:
                        qty_precision = len(step_str.split('.')[1])
                    else:
                        qty_precision = 0
                
                price_precision = 4
                if price_filter:
                    tick_str = str(price_filter['tickSize']).rstrip('0')
                    if tick_str.endswith('.'):
                        tick_str = tick_str[:-1]
                        
                    if '.' in tick_str:
                        price_precision = len(tick_str.split('.')[1])
                        
                _exchange_info_cache[s['symbol']] = {'qty': qty_precision, 'price': price_precision}
        except Exception as e:
            log_error("EXCHANGE_INFO", f"Error fetching exchange info: {e}")
            print(f"Error fetching exchange info: {e}")
            
    return _exchange_info_cache.get(symbol, {'qty': 3, 'price': 4})

async def set_margin_type(client: AsyncClient, symbol: str, margin_type: str = 'ISOLATED'):
    try:
        await client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        print(f"Margin type for {symbol} set to {margin_type}")
    except Exception as e:
        # Ignore error if already set
        if "-4046" not in str(e):
            log_error(f"MARGIN_{symbol}", str(e))
            print(f"Error setting margin type for {symbol}: {e}")

async def place_long_order(client: AsyncClient, symbol: str, current_price: float, margin_usdt: float, leverage: int) -> dict:
    """
    Membuka posisi Long (Market Order).
    """
    try:
        await set_leverage(client, symbol, leverage)
        await set_margin_type(client, symbol, 'ISOLATED')
        
        # Kalkulasi kuantitas (Position Size = (Margin * Leverage) / Price)
        notional_value = margin_usdt * leverage
        quantity = notional_value / current_price
        
        # Ambil precision aktual untuk symbol ini
        precision_info = await get_symbol_precision(client, symbol)
        qty_precision = precision_info['qty']
        
        # Potong (floor) desimal agar tidak melebihi precision dan tidak ter-round up yang bisa menyebabkan insificient margin
        multiplier = 10 ** qty_precision
        quantity = math.floor(quantity * multiplier) / multiplier
        
        # Format ke string agar Binance tidak menerima precision palsu bawaan float (misal: 12.3000000001)
        if qty_precision == 0:
            quantity_str = str(int(quantity))
            quantity = int(quantity)
        else:
            quantity_str = f"{quantity:.{qty_precision}f}"
        
        order = await client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=quantity_str
        )
        return {"status": "success", "order": order, "quantity": quantity, "price": current_price}
    except Exception as e:
        log_error(f"LONG_ORDER_{symbol}", str(e))
        print(f"Error placing long order for {symbol}: {e}")
        return {"status": "error", "message": str(e)}

async def place_short_order(client: AsyncClient, symbol: str, current_price: float, margin_usdt: float, leverage: int) -> dict:
    """
    Membuka posisi Short (Market Order).
    """
    try:
        await set_leverage(client, symbol, leverage)
        await set_margin_type(client, symbol, 'ISOLATED')
        
        # Kalkulasi kuantitas
        notional_value = margin_usdt * leverage
        quantity = notional_value / current_price
        
        precision_info = await get_symbol_precision(client, symbol)
        qty_precision = precision_info['qty']
        
        # Potong (floor) desimal
        multiplier = 10 ** qty_precision
        quantity = math.floor(quantity * multiplier) / multiplier
        
        if qty_precision == 0:
            quantity_str = str(int(quantity))
            quantity = int(quantity)
        else:
            quantity_str = f"{quantity:.{qty_precision}f}"
        
        order = await client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=quantity_str
        )
        return {"status": "success", "order": order, "quantity": quantity, "price": current_price}
    except Exception as e:
        log_error(f"SHORT_ORDER_{symbol}", str(e))
        print(f"Error placing short order for {symbol}: {e}")
        return {"status": "error", "message": str(e)}

async def place_take_profit_stop_loss(client: AsyncClient, symbol: str, side: str, quantity: float, tp_price: float, sl_price: float):
    """
    Memasang TP dan SL (Oco / Terpisah di Futures)
    Untuk Long, side untuk menutup adalah SELL.
    """
    try:
        precision_info = await get_symbol_precision(client, symbol)
        price_precision = precision_info['price']
        
        # Take profit market
        tp_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='TAKE_PROFIT_MARKET',
            stopPrice=round(tp_price, price_precision),
            closePosition=True,
            timeInForce='GTC'
        )
        
        # Stop loss market
        sl_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            stopPrice=round(sl_price, price_precision),
            closePosition=True,
            timeInForce='GTC'
        )
        return True
    except Exception as e:
        log_error(f"TP_SL_{symbol}", str(e))
        print(f"Error placing TP/SL for {symbol}: {e}")
        return False
