from binance import AsyncClient
from binance.enums import *
from core.logger import log_error
import math
import asyncio

async def set_leverage(client: AsyncClient, symbol: str, leverage: int) -> int:
    """
    Set leverage. Jika leverage terlalu tinggi untuk koin ini,
    otomatis turunkan sampai berhasil. Return nilai leverage aktual.
    """
    for lev in range(leverage, 0, -1):
        try:
            await client.futures_change_leverage(symbol=symbol, leverage=lev)
            if lev < leverage:
                print(f"[INFO] {symbol}: Leverage {leverage}x tidak valid, diturunkan ke {lev}x")
            return lev
        except Exception as e:
            if "-4028" in str(e) or "not valid" in str(e).lower():
                continue  # Coba leverage lebih rendah
            print(f"Error setting leverage for {symbol}: {e}")
            return leverage  # Return nilai asli, biarkan order gagal secara natural
    print(f"[WARNING] {symbol}: Tidak bisa set leverage apapun.")
    return 1

_exchange_info_cache = {}


def _get_average_fill_price(order: dict, fallback_price: float) -> float:
    average_price = order.get("avgPrice") or order.get("averagePrice")
    if average_price and float(average_price) > 0:
        return float(average_price)

    fills = order.get("fills", [])
    total_quantity = sum(float(fill.get("qty", 0)) for fill in fills)
    if total_quantity > 0:
        total_value = sum(
            float(fill.get("price", 0)) * float(fill.get("qty", 0))
            for fill in fills
        )
        if total_value > 0:
            return total_value / total_quantity

    return fallback_price

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

async def place_long_order(client: AsyncClient, symbol: str, current_price: float, margin_usdt: float, leverage: int, use_limit: bool = False, limit_timeout: int = 30) -> dict:
    """
    Membuka posisi Long (Market / Limit Order).
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            actual_leverage = await set_leverage(client, symbol, leverage)
            await set_margin_type(client, symbol, 'ISOLATED')
            
            notional_value = margin_usdt * actual_leverage
            quantity = notional_value / current_price
            
            precision_info = await get_symbol_precision(client, symbol)
            qty_precision = precision_info['qty']
            price_precision = precision_info['price']
            
            multiplier = 10 ** qty_precision
            quantity = math.floor(quantity * multiplier) / multiplier
            
            quantity_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
            price_str = str(int(current_price)) if price_precision == 0 else f"{current_price:.{price_precision}f}"
            
            if use_limit:
                order = await client.futures_create_order(
                    symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_LIMIT,
                    timeInForce=TIME_IN_FORCE_GTC, quantity=quantity_str, price=price_str
                )
                print(f"Limit order BUY {symbol} ditempatkan. Menunggu {limit_timeout} detik...")
                await asyncio.sleep(limit_timeout)
                check_order = await client.futures_get_order(symbol=symbol, orderId=order['orderId'])
                if check_order['status'] != 'FILLED':
                    print(f"Limit order {symbol} tidak ter-fill. Membatalkan...")
                    await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
                    return {"status": "error", "message": "Limit order timeout / Trap avoided"}
                order = check_order
            else:
                order = await client.futures_create_order(
                    symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantity_str
                )
            return {
                "status": "success", "order": order,
                "quantity": quantity, "price": _get_average_fill_price(order, current_price),
                "actual_leverage": actual_leverage,
            }
        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "rate limit" in error_str or "connection" in error_str:
                print(f"[RETRY {attempt+1}/{max_retries}] Long order {symbol}: {e}")
                await asyncio.sleep(2 ** attempt)
            else:
                log_error(f"LONG_ORDER_{symbol}", str(e))
                print(f"Error placing long order for {symbol}: {e}")
                return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "Max retries reached"}

async def place_short_order(client: AsyncClient, symbol: str, current_price: float, margin_usdt: float, leverage: int, use_limit: bool = False, limit_timeout: int = 30) -> dict:
    """
    Membuka posisi Short (Market / Limit Order).
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            actual_leverage = await set_leverage(client, symbol, leverage)
            await set_margin_type(client, symbol, 'ISOLATED')
            
            notional_value = margin_usdt * actual_leverage
            quantity = notional_value / current_price
            
            precision_info = await get_symbol_precision(client, symbol)
            qty_precision = precision_info['qty']
            price_precision = precision_info['price']
            
            multiplier = 10 ** qty_precision
            quantity = math.floor(quantity * multiplier) / multiplier
            
            quantity_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
            price_str = str(int(current_price)) if price_precision == 0 else f"{current_price:.{price_precision}f}"
            
            if use_limit:
                order = await client.futures_create_order(
                    symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_LIMIT,
                    timeInForce=TIME_IN_FORCE_GTC, quantity=quantity_str, price=price_str
                )
                print(f"Limit order SELL {symbol} ditempatkan. Menunggu {limit_timeout} detik...")
                await asyncio.sleep(limit_timeout)
                check_order = await client.futures_get_order(symbol=symbol, orderId=order['orderId'])
                if check_order['status'] != 'FILLED':
                    print(f"Limit order {symbol} tidak ter-fill. Membatalkan...")
                    await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
                    return {"status": "error", "message": "Limit order timeout / Trap avoided"}
                order = check_order
            else:
                order = await client.futures_create_order(
                    symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantity_str
                )
            return {
                "status": "success", "order": order,
                "quantity": quantity, "price": _get_average_fill_price(order, current_price),
                "actual_leverage": actual_leverage,
            }
        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "rate limit" in error_str or "connection" in error_str:
                print(f"[RETRY {attempt+1}/{max_retries}] Short order {symbol}: {e}")
                await asyncio.sleep(2 ** attempt)
            else:
                log_error(f"SHORT_ORDER_{symbol}", str(e))
                print(f"Error placing short order for {symbol}: {e}")
                return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "Max retries reached"}

async def place_take_profit_stop_loss(client: AsyncClient, symbol: str, side: str, quantity: float, tp_price: float, sl_price: float, use_trailing_stop: bool = False, callback_rate: float = 1.0):
    """
    Memasang TP dan SL (Oco / Terpisah di Futures)
    Untuk Long, side untuk menutup adalah SELL.
    """
    try:
        precision_info = await get_symbol_precision(client, symbol)
        price_precision = precision_info['price']
        qty_precision = precision_info['qty']
        
        if qty_precision == 0:
            quantity_str = str(int(quantity))
        else:
            quantity_str = f"{quantity:.{qty_precision}f}"
        
        # Fixed TP keeps TP_PERCENT deterministic; trailing is not a substitute for TP.
        tp_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='TAKE_PROFIT_MARKET',
            stopPrice=round(tp_price, price_precision),
            closePosition=True,
            workingType='MARK_PRICE',
            priceProtect=True,
        )
        
        # Stop loss market
        sl_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            stopPrice=round(sl_price, price_precision),
            closePosition=True,
            workingType='MARK_PRICE',
            priceProtect=True,
        )
        print(
            f"Protection orders active for {symbol}: "
            f"TP={tp_order.get('orderId')} SL={sl_order.get('orderId')}"
        )
        return {
            "status": "success",
            "tp_order": tp_order,
            "sl_order": sl_order,
        }
    except Exception as e:
        log_error(f"TP_SL_{symbol}", str(e))
        print(f"Error placing TP/SL for {symbol}: {e}")
        if "-4130" in str(e):
            return {
                "status": "existing",
                "message": "Binance already has a close-position protective order",
            }
        return {"status": "error", "message": str(e)}


async def emergency_close_position(client: AsyncClient, symbol: str, side: str, quantity: float):
    """Cancel pending orders and close an unprotected position at market."""
    try:
        await client.futures_cancel_all_open_orders(symbol=symbol)
        close_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
            reduceOnly=True,
        )
        return {"status": "success", "order": close_order}
    except Exception as e:
        log_error(f"EMERGENCY_CLOSE_{symbol}", str(e))
        print(f"Error closing unprotected position for {symbol}: {e}")
        return {"status": "error", "message": str(e)}


async def close_profitable_position(client: AsyncClient, symbol: str, position_amount: float):
    """Cancel protection orders and close a profitable position at market."""
    close_side = SIDE_SELL if position_amount > 0 else SIDE_BUY
    quantity = abs(position_amount)
    return await emergency_close_position(client, symbol, close_side, quantity)
