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


_symbol_order_locks: Dict[str, asyncio.Lock] = {}


def _get_symbol_lock(symbol: str) -> asyncio.Lock:
    """Mengembalikan asyncio.Lock untuk simbol koin tertentu guna mencegah race condition / double order."""
    sym = symbol.upper().strip()
    if sym not in _symbol_order_locks:
        _symbol_order_locks[sym] = asyncio.Lock()
    return _symbol_order_locks[sym]


async def check_exchange_active_position(client: Union[BaseExchange, AsyncClient], symbol: str) -> bool:
    """
    Memeriksa langsung ke API Exchange apakah koin ini sudah memiliki posisi aktif terbuka.
    Mengembalikan True jika sudah ada posisi, False jika bersih.
    """
    try:
        sym_clean = symbol.upper().strip()
        if hasattr(client, "get_open_positions"):
            open_positions = await client.get_open_positions()
            for p in open_positions:
                p_sym = str(p.get("symbol", "")).upper().strip()
                amt = float(p.get("position_amt", p.get("positionAmt", p.get("qty", 0))) or 0)
                if p_sym == sym_clean and abs(amt) > 0:
                    return True
        elif hasattr(client, "futures_position_information"):
            open_positions = await client.futures_position_information(symbol=sym_clean)
            for p in open_positions:
                amt = float(p.get("positionAmt", 0) or 0)
                if abs(amt) > 0:
                    return True
    except Exception as e_chk:
        log_error(f"POS_CHECK_{symbol}", str(e_chk))
    return False


async def get_orderbook_mid_or_spread(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
) -> Dict[str, float]:
    """
    Mengambil harga best bid, best ask, mid price, dan spread percentage.
    """
    bid = 0.0
    ask = 0.0
    try:
        if isinstance(client, BaseExchange):
            if hasattr(client, "get_orderbook_spread"):
                return await client.get_orderbook_spread(symbol)
            price = await client.get_symbol_price(symbol)
            if price > 0:
                return {"bid": price, "ask": price, "mid": price, "spread_pct": 0.0}
        else:
            depth = await client.futures_order_book(symbol=symbol.upper(), limit=5)
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            if bids and asks:
                bid = float(bids[0][0])
                ask = float(asks[0][0])
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2.0
                    spread_pct = ((ask - bid) / mid) * 100.0
                    return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct}
    except Exception as e:
        log_error(f"SPREAD_CHECK_{symbol}", str(e))

    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0 if (bid and ask) else 0.0, "spread_pct": 0.0}


async def place_long_order(
    client: Union[BaseExchange, AsyncClient],
    symbol: str,
    current_price: float,
    margin_usdt: float,
    leverage: int,
    use_limit: bool = False,
    limit_timeout: int = 30,
    max_slippage_pct: float = 0.003,
) -> dict:
    """
    Membuka posisi Long (Market / Limit Order) dengan Slippage Guard & perlindungan Anti-Double Entry.
    """
    async with _get_symbol_lock(symbol):
        # 1. Verifikasi langsung ke Exchange sebelum menembak order
        if await check_exchange_active_position(client, symbol):
            print(f"🛑 [EXCHANGE LOCK] {symbol} sudah memiliki posisi aktif di exchange. Order LONG dibatalkan (Anti-Double Entry).")
            return {"status": "error", "message": f"Position already active on exchange for {symbol}"}

        # 2. Slippage Guard: Re-fetch harga terkini sesaat sebelum order ditembak
        fresh_price = current_price
        try:
            if isinstance(client, BaseExchange):
                p = await client.get_symbol_price(symbol)
                if p > 0:
                    fresh_price = p
            else:
                ticker = await client.futures_symbol_ticker(symbol=symbol.upper())
                p = float(ticker.get("price", 0.0))
                if p > 0:
                    fresh_price = p
        except Exception as e_p:
            log_error(f"PRICE_CHECK_{symbol}", f"Gagal re-fetch harga pasar terkini: {e_p}")

        if current_price > 0 and fresh_price > 0:
            price_deviation = (fresh_price - current_price) / current_price
            if price_deviation > max_slippage_pct:
                print(
                    f"⚠️ [SLIPPAGE GUARD] Order LONG {symbol} DIBATALKAN (SIGNAL_STALE): "
                    f"Harga pasar ({fresh_price:.6f}) telah melonjak {price_deviation * 100:+.2f}% di atas harga sinyal ({current_price:.6f}) "
                    f"> batas toleransi ({max_slippage_pct * 100:.2f}%)."
                )
                return {
                    "status": "error",
                    "reason": "SIGNAL_STALE",
                    "message": f"Market price jumped {price_deviation * 100:+.2f}% above signal price (exceeds max slippage {max_slippage_pct * 100:.2f}%)",
                    "signal_price": current_price,
                    "market_price": fresh_price,
                }

        exec_ref_price = fresh_price if fresh_price > 0 else current_price
        max_retries = 3
        for attempt in range(max_retries):
            try:
                actual_leverage = await set_leverage(client, symbol, leverage)
                await set_margin_type(client, symbol, 'ISOLATED')
                
                notional_value = margin_usdt * actual_leverage
                quantity = notional_value / exec_ref_price
                
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
                        price=exec_ref_price if use_limit else None,
                    )
                else:
                    qty_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
                    if use_limit:
                        price_str = f"{exec_ref_price:.{precision_info['price']}f}"
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

                fill_price = _get_average_fill_price(order, exec_ref_price)
                slippage_pct = (fill_price - current_price) / current_price if current_price > 0 else 0.0
                
                print(
                    f"🎯 [EXECUTION FIDELITY] LONG {symbol} Filled | "
                    f"Sinyal: {current_price:.6f} | Fill: {fill_price:.6f} | "
                    f"Slippage: {slippage_pct * 100:+.3f}% ({order_type})"
                )

                return {
                    "status": "success",
                    "order": order,
                    "quantity": quantity,
                    "price": fill_price,
                    "signal_price": current_price,
                    "slippage_pct": slippage_pct,
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
    max_slippage_pct: float = 0.003,
) -> dict:
    """
    Membuka posisi Short (Market / Limit Order) dengan Slippage Guard & perlindungan Anti-Double Entry.
    """
    async with _get_symbol_lock(symbol):
        # 1. Verifikasi langsung ke Exchange sebelum menembak order
        if await check_exchange_active_position(client, symbol):
            print(f"🛑 [EXCHANGE LOCK] {symbol} sudah memiliki posisi aktif di exchange. Order SHORT dibatalkan (Anti-Double Entry).")
            return {"status": "error", "message": f"Position already active on exchange for {symbol}"}

        # 2. Slippage Guard: Re-fetch harga terkini sesaat sebelum order ditembak
        fresh_price = current_price
        try:
            if isinstance(client, BaseExchange):
                p = await client.get_symbol_price(symbol)
                if p > 0:
                    fresh_price = p
            else:
                ticker = await client.futures_symbol_ticker(symbol=symbol.upper())
                p = float(ticker.get("price", 0.0))
                if p > 0:
                    fresh_price = p
        except Exception as e_p:
            log_error(f"PRICE_CHECK_{symbol}", f"Gagal re-fetch harga pasar terkini: {e_p}")

        if current_price > 0 and fresh_price > 0:
            price_deviation = (current_price - fresh_price) / current_price
            if price_deviation > max_slippage_pct:
                print(
                    f"⚠️ [SLIPPAGE GUARD] Order SHORT {symbol} DIBATALKAN (SIGNAL_STALE): "
                    f"Harga pasar ({fresh_price:.6f}) telah anjlok {price_deviation * 100:+.2f}% di bawah harga sinyal ({current_price:.6f}) "
                    f"> batas toleransi ({max_slippage_pct * 100:.2f}%)."
                )
                return {
                    "status": "error",
                    "reason": "SIGNAL_STALE",
                    "message": f"Market price dropped {price_deviation * 100:+.2f}% below signal price (exceeds max slippage {max_slippage_pct * 100:.2f}%)",
                    "signal_price": current_price,
                    "market_price": fresh_price,
                }

        exec_ref_price = fresh_price if fresh_price > 0 else current_price
        max_retries = 3
        for attempt in range(max_retries):
            try:
                actual_leverage = await set_leverage(client, symbol, leverage)
                await set_margin_type(client, symbol, 'ISOLATED')
                
                notional_value = margin_usdt * actual_leverage
                quantity = notional_value / exec_ref_price
                
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
                        price=exec_ref_price if use_limit else None,
                    )
                else:
                    qty_str = str(int(quantity)) if qty_precision == 0 else f"{quantity:.{qty_precision}f}"
                    if use_limit:
                        price_str = f"{exec_ref_price:.{precision_info['price']}f}"
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

                fill_price = _get_average_fill_price(order, exec_ref_price)
                slippage_pct = (current_price - fill_price) / current_price if current_price > 0 else 0.0
                
                print(
                    f"🎯 [EXECUTION FIDELITY] SHORT {symbol} Filled | "
                    f"Sinyal: {current_price:.6f} | Fill: {fill_price:.6f} | "
                    f"Slippage: {slippage_pct * 100:+.3f}% ({order_type})"
                )

                return {
                    "status": "success",
                    "order": order,
                    "quantity": quantity,
                    "price": fill_price,
                    "signal_price": current_price,
                    "slippage_pct": slippage_pct,
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
