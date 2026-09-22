from __future__ import annotations
import hashlib
import json
import time
import uuid
import ssl
from typing import List, Dict, Any, Optional
import aiohttp
import pandas as pd

from core.exchanges.base import BaseExchange
from core.logger import log_error


class BitunixAdapter(BaseExchange):
    """
    Adapter koneksi untuk Bitunix Futures API menggunakan Asynchronous REST (aiohttp).
    Mengimplementasikan protokol otentikasi Bitunix OpenAPI Double-SHA256.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.bitunix.com",
        proxy: Optional[str] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._proxy = proxy
        self._session: Optional[aiohttp.ClientSession] = None
        self._exchange_info_cache: Dict[str, Dict[str, int]] = {}

    @property
    def exchange_name(self) -> str:
        return "BITUNIX"

    async def init(self) -> None:
        if self._session is None or self._session.closed:
            ssl_ctx = ssl.create_default_context()
            # Hindari kegagalan SSL pada environment tertentu/proxy
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(connector=connector)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _generate_signature(
        self,
        nonce: str,
        timestamp: str,
        query_params_str: str,
        body_dict: Optional[Dict[str, Any]],
    ) -> str:
        """
        Menghasilkan Double SHA-256 signature sesuai standar Bitunix Open API:
        1. digest = sha256(nonce + timestamp + api_key + queryParams + body)
        2. signature = sha256(digest + secretKey)
        """
        body_str = json.dumps(body_dict, separators=(",", ":")) if body_dict else ""
        digest_input = f"{nonce}{timestamp}{self._api_key}{query_params_str}{body_str}"
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        sign_input = f"{digest}{self._api_secret}"
        return hashlib.sha256(sign_input.encode("utf-8")).hexdigest()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth_required: bool = False,
    ) -> Dict[str, Any]:
        await self.init()
        url = f"{self._base_url}{endpoint}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Urutkan query parameters sesuai standar Bitunix jika ada
        query_params_str = ""
        sorted_params = None
        if params:
            sorted_items = sorted(params.items(), key=lambda x: x[0])
            sorted_params = dict(sorted_items)
            query_params_str = "".join(f"{k}{v}" for k, v in sorted_items)

        if auth_required:
            nonce = uuid.uuid4().hex
            timestamp = str(int(time.time() * 1000))
            signature = self._generate_signature(nonce, timestamp, query_params_str, data)
            headers.update({
                "api-key": self._api_key,
                "nonce": nonce,
                "timestamp": timestamp,
                "sign": signature,
            })

        try:
            async with self._session.request(
                method=method.upper(),
                url=url,
                params=sorted_params,
                json=data if data else None,
                headers=headers,
                proxy=self._proxy,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 403:
                    raise RuntimeError("HTTP 403 Forbidden (Kemungkinan terblokir ISP/Internet Positif. Set BITUNIX_PROXY di .env atau gunakan VPN)")
                try:
                    result = await response.json()
                except Exception:
                    text_resp = await response.text()
                    raise RuntimeError(f"HTTP {response.status}: {text_resp[:120]}")

                if response.status != 200 or (isinstance(result, dict) and result.get("code") not in (0, "0", 200, "200", None)):
                    error_msg = result.get("msg", str(result)) if isinstance(result, dict) else f"HTTP {response.status}"
                    raise RuntimeError(f"Bitunix API Error [{response.status}]: {error_msg}")
                return result
        except Exception as e:
            log_error(f"BITUNIX_REQ_{endpoint}", str(e))
            raise

    async def get_top_futures_by_volume(self, n: Optional[int] = None) -> List[str]:
        """
        Mengambil Top N ticker Futures berpasangan USDT berdasarkan volume 24 jam yang valid & OPEN untuk trading.
        """
        try:
            # 1. Ambil daftar instrumen yang berstatus OPEN dan didukung API
            valid_symbols = set()
            try:
                pairs_res = await self._request("GET", "/api/v1/futures/market/trading_pairs")
                pairs_list = pairs_res.get("data", []) if isinstance(pairs_res, dict) else []
                valid_symbols = {
                    p["symbol"].upper() for p in pairs_list
                    if p.get("symbolStatus") == "OPEN" and p.get("isApiSupported", True)
                }
            except Exception as e_pairs:
                logger.debug(f"[BITUNIX] Gagal fetch trading_pairs status: {e_pairs}")

            # 2. Ambil tickers untuk sorting volume
            res = await self._request("GET", "/api/v1/futures/market/tickers")
            ticker_list = res.get("data", []) if isinstance(res, dict) else res
            if not isinstance(ticker_list, list):
                ticker_list = []

            # Filter hanya pair USDT yang aktif
            usdt_pairs = [
                t for t in ticker_list
                if str(t.get("symbol", "")).upper().endswith("USDT")
                and (not valid_symbols or str(t.get("symbol", "")).upper() in valid_symbols)
            ]

            # Urutkan berdasarkan quote volume (volume dalam USDT)
            usdt_pairs.sort(
                key=lambda x: float(x.get("quoteVol", x.get("quoteVolume", x.get("amount", x.get("volume", 0))))),
                reverse=True,
            )

            symbols = [p["symbol"].upper() for p in usdt_pairs]
            if n is not None:
                return symbols[:n]
            return symbols
        except Exception as e:
            log_error("BITUNIX_TOP_COINS", str(e))
            print(f"[BITUNIX] Error fetching top coins: {e}")
            return []

    async def fetch_ohlcv(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        """
        Mengambil klines dari Bitunix dan menormalkan ke format DataFrame bot.
        """
        try:
            # Standarisasi interval Bitunix (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 1w, 1M)
            iv = interval.lower()
            if iv in ("1hour", "60m", "60min"):
                mapped_interval = "1h"
            elif iv in ("1day",):
                mapped_interval = "1d"
            elif iv in ("1week",):
                mapped_interval = "1w"
            elif iv in ("1min",):
                mapped_interval = "1m"
            elif iv in ("5min",):
                mapped_interval = "5m"
            elif iv in ("15min",):
                mapped_interval = "15m"
            elif iv in ("30min",):
                mapped_interval = "30m"
            elif iv in ("4hour",):
                mapped_interval = "4h"
            else:
                mapped_interval = iv

            params = {
                "symbol": symbol.upper(),
                "interval": mapped_interval,
                "limit": limit,
            }
            res = await self._request("GET", "/api/v1/futures/market/kline", params=params)
            raw_klines = res.get("data", []) if isinstance(res, dict) else []

            if not raw_klines:
                return pd.DataFrame()

            # Hitung durasi interval dalam ms untuk close_time
            interval_ms_map = {
                "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
                "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
                "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
            }
            int_ms = interval_ms_map.get(interval.lower(), 300_000)

            # Normalisasi kline list ke dict
            records = []
            for k in raw_klines:
                if isinstance(k, dict):
                    ts = int(k.get("time", k.get("timestamp", k.get("t", 0))))
                    records.append({
                        "timestamp": ts,
                        "open": float(k.get("open", k.get("o", 0))),
                        "high": float(k.get("high", k.get("h", 0))),
                        "low": float(k.get("low", k.get("l", 0))),
                        "close": float(k.get("close", k.get("c", 0))),
                        "volume": float(k.get("baseVol", k.get("volume", k.get("v", 0)))),
                        "close_time": ts + int_ms - 1,
                    })
                elif isinstance(k, (list, tuple)) and len(k) >= 6:
                    ts = int(k[0])
                    records.append({
                        "timestamp": ts,
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "close_time": ts + int_ms - 1,
                    })

            df = pd.DataFrame(records)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms")
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                # Pastikan terurut dari candle terlama ke terbaru
                df.sort_values(by="timestamp", inplace=True)
                df.reset_index(drop=True, inplace=True)
            return df
        except Exception as e:
            err_msg = str(e).lower()
            if "not allowed to trade" in err_msg or "suspended" in err_msg:
                # Kontrak tidak diizinkan untuk trading, lewati tanpa log spam
                return pd.DataFrame()
            log_error(f"BITUNIX_OHLCV_{symbol}", str(e))
            print(f"[BITUNIX] Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()

    async def get_symbol_price(self, symbol: str) -> float:
        """
        Mengambil harga market terkini untuk koin di Bitunix.
        """
        try:
            df = await self.fetch_ohlcv(symbol, interval="1m", limit=2)
            if not df.empty and "close" in df.columns:
                return float(df["close"].iloc[-1])
            return 0.0
        except Exception as e:
            log_error(f"BITUNIX_PRICE_{symbol}", str(e))
            return 0.0

    async def get_account_balance(self) -> Dict[str, float]:
        """
        Mengambil balance futures Bitunix.
        """
        try:
            res = await self._request("GET", "/api/v1/futures/account", auth_required=True)
            data = res.get("data", {}) if isinstance(res, dict) else {}
            total_wallet = float(data.get("marginBalance", data.get("totalWalletBalance", 0.0)))
            available = float(data.get("availableBalance", data.get("available", 0.0)))
            unrealized = float(data.get("unrealizedProfit", data.get("unrealizedPnl", 0.0)))
            return {
                "total_wallet_balance": total_wallet,
                "available_balance": available,
                "unrealized_pnl": unrealized,
            }
        except Exception as e:
            log_error("BITUNIX_BALANCE", str(e))
            return {"total_wallet_balance": 0.0, "available_balance": 0.0, "unrealized_pnl": 0.0}

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Mengambil posisi aktif futures Bitunix.
        """
        try:
            res = await self._request("GET", "/api/v1/futures/position", auth_required=True)
            pos_list = res.get("data", []) if isinstance(res, dict) else []
            active_positions = []
            for pos in pos_list:
                qty = float(pos.get("qty", pos.get("positionAmt", 0.0)))
                if abs(qty) > 0:
                    side_str = str(pos.get("side", "")).upper()
                    if not side_str:
                        side_str = "LONG" if qty > 0 else "SHORT"

                    active_positions.append({
                        "symbol": pos.get("symbol", "").upper(),
                        "side": side_str,
                        "position_amt": qty,
                        "entry_price": float(pos.get("entryPrice", pos.get("avgPrice", 0.0))),
                        "mark_price": float(pos.get("markPrice", pos.get("lastPrice", 0.0))),
                        "unrealized_pnl": float(pos.get("unrealizedProfit", pos.get("unrealizedPnl", 0.0))),
                        "leverage": int(pos.get("leverage", 1)),
                        "liquidation_price": float(pos.get("liquidationPrice", pos.get("liqPrice", 0.0))),
                    })
            return active_positions
        except Exception as e:
            log_error("BITUNIX_POSITIONS", str(e))
            return []

    async def set_leverage(self, symbol: str, leverage: int) -> int:
        for lev in range(leverage, 0, -1):
            try:
                payload = {
                    "symbol": symbol.upper(),
                    "leverage": lev,
                }
                await self._request("POST", "/api/v1/futures/trade/leverage", data=payload, auth_required=True)
                if lev < leverage:
                    print(f"[BITUNIX] {symbol}: Leverage {leverage}x diturunkan ke {lev}x")
                return lev
            except Exception as e:
                if "invalid" in str(e).lower() or "limit" in str(e).lower():
                    continue
                log_error(f"BITUNIX_LEVERAGE_{symbol}", str(e))
                return leverage
        return 1

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> None:
        try:
            payload = {
                "symbol": symbol.upper(),
                "marginMode": margin_type.upper(),
            }
            await self._request("POST", "/api/v1/futures/trade/margin_mode", data=payload, auth_required=True)
        except Exception as e:
            log_error(f"BITUNIX_MARGIN_{symbol}", str(e))

    async def get_symbol_precision(self, symbol: str) -> Dict[str, int]:
        if not self._exchange_info_cache:
            try:
                res = await self._request("GET", "/api/v1/futures/market/trading_pairs")
                pairs = res.get("data", []) if isinstance(res, dict) else []
                for p in pairs:
                    sym = p.get("symbol", "").upper()
                    qty_prec = int(p.get("qtyPrecision", p.get("amountPrecision", 3)))
                    price_prec = int(p.get("pricePrecision", 4))
                    self._exchange_info_cache[sym] = {"qty": qty_prec, "price": price_prec}
            except Exception as e:
                log_error("BITUNIX_PAIR_INFO", str(e))

        return self._exchange_info_cache.get(symbol.upper(), {"qty": 3, "price": 4})

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
        formatted_qty = round(quantity, precision["qty"])

        payload: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),  # 'BUY' atau 'SELL'
            "orderType": order_type.upper(),  # 'MARKET' atau 'LIMIT'
            "qty": str(formatted_qty),
            "reduceOnly": reduce_only,
        }

        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = f"{price:.{precision['price']}f}"

        return await self._request("POST", "/api/v1/futures/trade/place_order", data=payload, auth_required=True)

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        precision = await self.get_symbol_precision(symbol)
        results: Dict[str, Any] = {}

        if tp_price is not None:
            try:
                tp_payload = {
                    "symbol": symbol.upper(),
                    "side": side.upper(),
                    "orderType": "TAKE_PROFIT_MARKET",
                    "triggerPrice": f"{tp_price:.{precision['price']}f}",
                    "qty": str(round(quantity, precision["qty"])),
                    "reduceOnly": True,
                }
                res = await self._request("POST", "/api/v1/futures/trade/place_order", data=tp_payload, auth_required=True)
                results["take_profit"] = res
            except Exception as e:
                log_error(f"BITUNIX_TP_{symbol}", str(e))
                results["tp_error"] = str(e)

        if sl_price is not None:
            try:
                sl_payload = {
                    "symbol": symbol.upper(),
                    "side": side.upper(),
                    "orderType": "STOP_MARKET",
                    "triggerPrice": f"{sl_price:.{precision['price']}f}",
                    "qty": str(round(quantity, precision["qty"])),
                    "reduceOnly": True,
                }
                res = await self._request("POST", "/api/v1/futures/trade/place_order", data=sl_payload, auth_required=True)
                results["stop_loss"] = res
            except Exception as e:
                log_error(f"BITUNIX_SL_{symbol}", str(e))
                results["sl_error"] = str(e)

        return results

    async def emergency_close_position(self, symbol: str) -> Dict[str, Any]:
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol.upper()), None)
        if not pos:
            return {"status": "NO_POSITION", "symbol": symbol}

        close_side = "SELL" if pos["side"] == "LONG" else "BUY"
        return await self.place_order(
            symbol=symbol.upper(),
            side=close_side,
            order_type="MARKET",
            quantity=abs(pos["position_amt"]),
            reduce_only=True,
        )
