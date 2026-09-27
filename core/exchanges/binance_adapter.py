from __future__ import annotations
import math
import asyncio
from typing import List, Dict, Any, Optional
import pandas as pd
from binance import AsyncClient
from binance.enums import *

from core.exchanges.base import BaseExchange
from core.logger import log_error
from config.settings import bot_config


class BinanceAdapter(BaseExchange):
    """
    Adapter koneksi untuk Binance Futures menggunakan library python-binance (AsyncClient).
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._client: Optional[AsyncClient] = None
        self._exchange_info_cache: Dict[str, Dict[str, int]] = {}

    @property
    def exchange_name(self) -> str:
        return "BINANCE"

    @property
    def client(self) -> AsyncClient:
        if self._client is None:
            raise RuntimeError("BinanceAdapter belum diinisialisasi. Panggil await adapter.init() terlebih dahulu.")
        return self._client

    async def init(self) -> None:
        if self._client is None:
            self._client = await AsyncClient.create(
                api_key=self._api_key,
                api_secret=self._api_secret,
                testnet=self._testnet,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close_connection()
            self._client = None

    async def get_top_futures_by_volume(self, n: Optional[int] = None, sort_by: str = "VOLUME_DESC") -> List[str]:
        try:
            exchange_info = await self.client.futures_exchange_info()
            valid_symbols = {
                s['symbol'] for s in exchange_info['symbols']
                if s.get('status') == 'TRADING' and s['symbol'].endswith('USDT')
            }

            tickers = await self.client.futures_ticker()
            usdt_pairs = []
            for t in tickers:
                sym = t.get('symbol', '')
                if sym not in valid_symbols or not sym.isascii():
                    continue
                if hasattr(bot_config, "is_coin_excluded") and bot_config.is_coin_excluded(sym):
                    continue
                quote_vol = float(t.get('quoteVolume', 0) or 0)
                change_pct = float(t.get('priceChangePercent', 0) or 0)
                usdt_pairs.append({
                    "symbol": sym,
                    "quote_vol": quote_vol,
                    "change_pct": change_pct,
                    "abs_change": abs(change_pct),
                })

            sort_mode = str(sort_by).upper().strip()
            if sort_mode in ("CHANGE_DESC", "CHANGE", "VOLATILITY"):
                usdt_pairs.sort(key=lambda x: (x["abs_change"], x["quote_vol"]), reverse=True)
            elif sort_mode in ("GAINERS", "TOP_GAINERS"):
                usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=True)
            elif sort_mode in ("LOSERS", "TOP_LOSERS"):
                usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=False)
            else:
                usdt_pairs.sort(key=lambda x: x["quote_vol"], reverse=True)

            symbols = [pair['symbol'] for pair in usdt_pairs]
            if n is not None and isinstance(n, int) and n > 0:
                return symbols[:n]
            return symbols
        except Exception as e:
            log_error("BINANCE_TOP_COINS", str(e))
            print(f"[BINANCE] Error fetching scan coins: {e}")
            return []

    async def fetch_ohlcv(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        try:
            klines = await self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            log_error(f"BINANCE_OHLCV_{symbol}", str(e))
            print(f"[BINANCE] Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()

    async def get_symbol_price(self, symbol: str) -> float:
        try:
            ticker = await self.client.futures_symbol_ticker(symbol=symbol.upper())
            return float(ticker.get("price", 0.0))
        except Exception as e:
            log_error(f"BINANCE_PRICE_{symbol}", str(e))
            return 0.0

    async def get_orderbook_spread(self, symbol: str) -> Dict[str, float]:
        try:
            depth = await self.client.futures_order_book(symbol=symbol.upper(), limit=5)
            bids = depth.get("bids", [])
            asks = depth.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                if best_bid > 0 and best_ask > 0:
                    mid = (best_bid + best_ask) / 2.0
                    spread_pct = ((best_ask - best_bid) / mid) * 100.0
                    return {"bid": best_bid, "ask": best_ask, "mid": mid, "spread_pct": spread_pct}
        except Exception as e:
            log_error(f"BINANCE_DEPTH_{symbol}", str(e))
        price = await self.get_symbol_price(symbol)
        return {"bid": price, "ask": price, "mid": price, "spread_pct": 0.0}

    async def get_account_balance(self) -> Dict[str, float]:
        try:
            account_info = await self.client.futures_account()
            total_wallet = float(account_info.get('totalWalletBalance', 0.0))
            available = float(account_info.get('availableBalance', 0.0))
            unrealized_pnl = float(account_info.get('totalUnrealizedProfit', 0.0))
            return {
                'total_wallet_balance': total_wallet,
                'available_balance': available,
                'unrealized_pnl': unrealized_pnl,
            }
        except Exception as e:
            log_error("BINANCE_BALANCE", str(e))
            return {'total_wallet_balance': 0.0, 'available_balance': 0.0, 'unrealized_pnl': 0.0}

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        try:
            positions = await self.client.futures_position_information()
            active_positions = []
            for pos in positions:
                amt = float(pos.get('positionAmt', 0.0))
                if abs(amt) > 0:
                    active_positions.append({
                        'symbol': pos['symbol'],
                        'side': 'LONG' if amt > 0 else 'SHORT',
                        'position_amt': amt,
                        'entry_price': float(pos.get('entryPrice', 0.0)),
                        'mark_price': float(pos.get('markPrice', 0.0)),
                        'unrealized_pnl': float(pos.get('unRealizedProfit', 0.0)),
                        'leverage': int(pos.get('leverage', 1)),
                        'liquidation_price': float(pos.get('liquidationPrice', 0.0)),
                        'update_time': int(pos.get('updateTime', 0)),
                    })
            return active_positions
        except Exception as e:
            log_error("BINANCE_POSITIONS", str(e))
            return []

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        for lev in range(leverage, 0, -1):
            try:
                await self.client.futures_change_leverage(symbol=symbol, leverage=lev)
                if lev < leverage:
                    print(f"[BINANCE] {symbol}: Leverage {leverage}x tidak valid, diturunkan ke {lev}x")
                return lev
            except Exception as e:
                if "-4028" in str(e) or "not valid" in str(e).lower():
                    continue
                log_error(f"BINANCE_LEVERAGE_{symbol}", str(e))
                return leverage
        return 1

    async def set_margin_type(self, symbol: str, margin_type: str = 'ISOLATED') -> None:
        try:
            await self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except Exception as e:
            if "-4046" not in str(e):
                log_error(f"BINANCE_MARGIN_{symbol}", str(e))

    async def get_symbol_precision(self, symbol: str) -> Dict[str, int]:
        if not self._exchange_info_cache:
            try:
                info = await self.client.futures_exchange_info()
                for s in info.get('symbols', []):
                    lot_filter = next((f for f in s.get('filters', []) if f.get('filterType') == 'LOT_SIZE'), None)
                    price_filter = next((f for f in s.get('filters', []) if f.get('filterType') == 'PRICE_FILTER'), None)

                    qty_precision = 3
                    if lot_filter:
                        step_str = str(lot_filter.get('stepSize', '0.001')).rstrip('0')
                        if step_str.endswith('.'):
                            step_str = step_str[:-1]
                        qty_precision = len(step_str.split('.')[1]) if '.' in step_str else 0

                    price_precision = 4
                    if price_filter:
                        tick_str = str(price_filter.get('tickSize', '0.0001')).rstrip('0')
                        if tick_str.endswith('.'):
                            tick_str = tick_str[:-1]
                        price_precision = len(tick_str.split('.')[1]) if '.' in tick_str else 0

                    self._exchange_info_cache[s['symbol']] = {'qty': qty_precision, 'price': price_precision}
            except Exception as e:
                log_error("BINANCE_EXCHANGE_INFO", str(e))

        return self._exchange_info_cache.get(symbol, {'qty': 3, 'price': 4})

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        precision = await self.get_symbol_precision(symbol)
        formatted_qty = round(quantity, precision['qty'])

        params: Dict[str, Any] = {
            'symbol': symbol,
            'side': SIDE_BUY if side.upper() == 'BUY' else SIDE_SELL,
            'type': ORDER_TYPE_MARKET if order_type.upper() == 'MARKET' else ORDER_TYPE_LIMIT,
            'quantity': formatted_qty,
        }

        if reduce_only:
            params['reduceOnly'] = 'true'

        if order_type.upper() == 'LIMIT' and price is not None:
            params['price'] = f"{price:.{precision['price']}f}"
            params['timeInForce'] = TIME_IN_FORCE_GTC

        return await self.client.futures_create_order(**params)

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        precision = await self.get_symbol_precision(symbol)
        formatted_qty = round(quantity, precision['qty'])
        order_side = SIDE_BUY if side.upper() == 'BUY' else SIDE_SELL

        results: Dict[str, Any] = {}

        if tp_price is not None:
            formatted_tp = f"{tp_price:.{precision['price']}f}"
            try:
                tp_res = await self.client.futures_create_order(
                    symbol=symbol,
                    side=order_side,
                    type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    stopPrice=formatted_tp,
                    closePosition='true',
                )
                results['take_profit'] = tp_res
            except Exception as e:
                log_error(f"BINANCE_TP_{symbol}", str(e))
                results['tp_error'] = str(e)

        if sl_price is not None:
            formatted_sl = f"{sl_price:.{precision['price']}f}"
            try:
                sl_res = await self.client.futures_create_order(
                    symbol=symbol,
                    side=order_side,
                    type=FUTURE_ORDER_TYPE_STOP_MARKET,
                    stopPrice=formatted_sl,
                    closePosition='true',
                )
                results['stop_loss'] = sl_res
            except Exception as e:
                log_error(f"BINANCE_SL_{symbol}", str(e))
                results['sl_error'] = str(e)

        return results

    async def emergency_close_position(self, symbol: str) -> Dict[str, Any]:
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p['symbol'] == symbol), None)
        if not pos:
            return {'status': 'NO_POSITION', 'symbol': symbol}

        close_side = 'SELL' if pos['side'] == 'LONG' else 'BUY'
        return await self.place_order(
            symbol=symbol,
            side=close_side,
            order_type='MARKET',
            quantity=abs(pos['position_amt']),
            reduce_only=True,
        )
