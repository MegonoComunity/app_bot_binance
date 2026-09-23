from __future__ import annotations
import math
import asyncio
from typing import Union, Dict, Any, Optional
from binance import AsyncClient
from binance.enums import *

from core.exchanges.base import BaseExchange
from core.logger import log_error

_exchange_info_cache: Dict[str, Dict[str, int]] = {}


def _get_average_fill_price(order: dict, fallback_price: float) -> float:
    if not isinstance(order, dict):
        return fallback_price
    average_price = order.get("avgPrice") or order.get("averagePrice") or order.get("price")
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


async def get_symbol_precision(client: Union[BaseExchange, AsyncClient], symbol: str) -> Dict[str, int]:
    if isinstance(client, BaseExchange):
        return await client.get_symbol_precision(symbol)

    if not _exchange_info_cache:
        try:
            info = await client.futures_exchange_info()
            for s in info['symbols']:
                lot_filter = next((f for f in s['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                price_filter = next((f for f in s['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                
                qty_precision = 3
                if lot_filter:
                    step_str = str(lot_filter['stepSize']).rstrip('0')
                    if step_str.endswith('.'):
                        step_str = step_str[:-1]
                    qty_precision = len(step_str.split('.')[1]) if '.' in step_str else 0
                
                price_precision = 4
                if price_filter:
                    tick_str = str(price_filter['tickSize']).rstrip('0')
                    if tick_str.endswith('.'):
                        tick_str = tick_str[:-1]
                    price_precision = len(tick_str.split('.')[1]) if '.' in tick_str else 0
                    
                _exchange_info_cache[s['symbol']] = {'qty': qty_precision, 'price': price_precision}
        except Exception as e:
            log_error("EXCHANGE_INFO", f"Error fetching exchange info: {e}")
            
    return _exchange_info_cache.get(symbol, {'qty': 3, 'price': 4})


async def set_leverage(client: Union[BaseExchange, AsyncClient], symbol: str, leverage: int) -> int:
    if isinstance(client, BaseExchange):
        return await client.set_leverage(symbol, leverage)

    for lev in range(leverage, 0, -1):
        try:
            await client.futures_change_leverage(symbol=symbol, leverage=lev)
            if lev < leverage:
                print(f"[INFO] {symbol}: Leverage {leverage}x tidak valid, diturunkan ke {lev}x")
            return lev
        except Exception as e:
            if "-4028" in str(e) or "not valid" in str(e).lower():
                continue
            print(f"Error setting leverage for {symbol}: {e}")
            return leverage
    return 1


async def set_margin_type(client: Union[BaseExchange, AsyncClient], symbol: str, margin_type: str = 'ISOLATED') -> None:
    if isinstance(client, BaseExchange):
        await client.set_margin_type(symbol, margin_type)
        return

    try:
        await client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
    except Exception as e:
        if "-4046" not in str(e):
            log_error(f"MARGIN_{symbol}", str(e))


async def place_long_order(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    current_price: float,
    margin_usdt: float,
    leverage: int,
    use_limit: bool = False,
    limit_timeout: int = 30,
) -> dict:
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
            multiplier = 10 ** qty_precision
            quantity = math.floor(quantity * multiplier) / multiplier
            
            order_type = "LIMIT" if use_limit else "MARKET"
            
            if isinstance(client, BaseExchange):
                order = await client.place_order(
                    symbol=symbol,
                    side="BUY",
                    order_type=order_type,
                    quantity=quantity,
                    price=current_price if use_limit else None,
                )
            else:
                qty_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
                if use_limit:
                    price_str = f"{current_price:.{precision_info['price']}f}"
                    order = await client.futures_create_order(
                        symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_LIMIT,
                        timeInForce=TIME_IN_FORCE_GTC, quantity=qty_str, price=price_str
                    )
                    await asyncio.sleep(limit_timeout)
                    check_order = await client.futures_get_order(symbol=symbol, orderId=order['orderId'])
                    if check_order['status'] != 'FILLED':
                        await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
                        return {"status": "error", "message": "Limit order timeout / Trap avoided"}
                    order = check_order
                else:
                    order = await client.futures_create_order(
                        symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=qty_str
                    )

            return {
                "status": "success",
                "order": order,
                "quantity": quantity,
                "price": _get_average_fill_price(order, current_price),
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


async def place_short_order(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    current_price: float,
    margin_usdt: float,
    leverage: int,
    use_limit: bool = False,
    limit_timeout: int = 30,
) -> dict:
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
            multiplier = 10 ** qty_precision
            quantity = math.floor(quantity * multiplier) / multiplier
            
            order_type = "LIMIT" if use_limit else "MARKET"
            
            if isinstance(client, BaseExchange):
                order = await client.place_order(
                    symbol=symbol,
                    side="SELL",
                    order_type=order_type,
                    quantity=quantity,
                    price=current_price if use_limit else None,
                )
            else:
                qty_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
                if use_limit:
                    price_str = f"{current_price:.{precision_info['price']}f}"
                    order = await client.futures_create_order(
                        symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_LIMIT,
                        timeInForce=TIME_IN_FORCE_GTC, quantity=qty_str, price=price_str
                    )
                    await asyncio.sleep(limit_timeout)
                    check_order = await client.futures_get_order(symbol=symbol, orderId=order['orderId'])
                    if check_order['status'] != 'FILLED':
                        await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
                        return {"status": "error", "message": "Limit order timeout / Trap avoided"}
                    order = check_order
                else:
                    order = await client.futures_create_order(
                        symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=qty_str
                    )

            return {
                "status": "success",
                "order": order,
                "quantity": quantity,
                "price": _get_average_fill_price(order, current_price),
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


async def place_take_profit_stop_loss(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    side: str,
    quantity: float,
    tp_price: float,
    sl_price: float,
    use_trailing_stop: bool = False,
    callback_rate: float = 1.0,
) -> dict:
    """
    Memasang TP dan SL.
    Untuk Long, side untuk menutup adalah SELL.
    """
    if isinstance(client, BaseExchange):
        res = await client.place_tp_sl(
            symbol=symbol,
            side=side,
            quantity=quantity,
            tp_price=tp_price,
            sl_price=sl_price,
        )
        return {
            "status": "success" if ("take_profit" in res or "stop_loss" in res) else "error",
            **res,
        }

    try:
        precision_info = await get_symbol_precision(client, symbol)
        price_precision = precision_info['price']
        
        tp_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='TAKE_PROFIT_MARKET',
            stopPrice=round(tp_price, price_precision),
            closePosition=True,
            workingType='MARK_PRICE',
            priceProtect=True,
        )
        
        sl_order = await client.futures_create_order(
            symbol=symbol,
            side=side,
            type='STOP_MARKET',
            stopPrice=round(sl_price, price_precision),
            closePosition=True,
            workingType='MARK_PRICE',
            priceProtect=True,
        )
        return {
            "status": "success",
            "tp_order": tp_order,
            "sl_order": sl_order,
        }
    except Exception as e:
        log_error(f"TP_SL_{symbol}", str(e))
        if "-4130" in str(e):
            return {
                "status": "existing",
                "message": "Exchange already has a close-position protective order",
            }
        return {"status": "error", "message": str(e)}


async def emergency_close_position(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    side: str,
    quantity: float,
) -> dict:
    """Cancel pending orders and close an unprotected position at market."""
    if isinstance(client, BaseExchange):
        return await client.emergency_close_position(symbol=symbol)

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
        return {"status": "error", "message": str(e)}


async def close_profitable_position(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    position_amount: float,
) -> dict:
    """Cancel protection orders and close a profitable position at market."""
    close_side = SIDE_SELL if position_amount > 0 else SIDE_BUY
    quantity = abs(position_amount)
    return await emergency_close_position(client, symbol, close_side, quantity)
