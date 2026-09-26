from __future__ import annotations
import asyncio
import math
import hashlib
import json
import time
import uuid
import ssl
import socket
import logging
import datetime
from datetime import datetime
from typing import List, Dict, Any, Optional
import aiohttp
import pandas as pd

from core.exchanges.base import BaseExchange
from core.logger import log_error
from config.settings import bot_config

logger = logging.getLogger(__name__)


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
            connector = aiohttp.TCPConnector(
                ssl=ssl_ctx,
                family=socket.AF_INET,
                happy_eyeballs_delay=None,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                trust_env=bool(self._proxy),
            )

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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        # Urutkan query parameters sesuai standar Bitunix jika ada
        query_params_str = ""
        sorted_params = None
        if params:
            sorted_items = sorted(params.items(), key=lambda x: x[0])
            sorted_params = dict(sorted_items)
            query_params_str = "".join(f"{k}{v}" for k, v in sorted_items)

        body_str = json.dumps(data, separators=(",", ":")) if data else ""
        if auth_required:
            nonce = uuid.uuid4().hex
            timestamp = str(int(time.time() * 1000))
            signature = self._generate_signature(nonce, timestamp, query_params_str, data)
            headers.update({
                "api-key": self._api_key,
                "nonce": nonce,
                "timestamp": timestamp,
                "sign": signature,
                "language": "en-US",
            })

        try:
            async with self._session.request(
                method=method.upper(),
                url=url,
                params=sorted_params,
                data=body_str if body_str else None,
                headers=headers,
                proxy=self._proxy,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 403:
                    raise RuntimeError("HTTP 403 Forbidden (Kemungkinan terblokir ISP/Internet Positif. Aktifkan VPN / Cloudflare WARP 1.1.1.1 atau isi BITUNIX_PROXY di .env)")
                try:
                    result = await response.json()
                except Exception:
                    text_resp = await response.text()
                    if "<html" in text_resp.lower() or "<!doctype" in text_resp.lower():
                        raise RuntimeError("Respon terblokir ISP/Internet Positif (Menerima HTML bukan JSON API). Silakan aktifkan VPN / Cloudflare WARP 1.1.1.1 / Private DNS)")
                    raise RuntimeError(f"HTTP {response.status}: {text_resp[:120]}")

                if response.status != 200 or (isinstance(result, dict) and result.get("code") not in (0, "0", 200, "200", None)):
                    code_val = result.get("code") if isinstance(result, dict) else response.status
                    error_msg = result.get("msg", str(result)) if isinstance(result, dict) else f"HTTP {response.status}"
                    if code_val in (100007, "100007"):
                        error_msg = f"{error_msg} (Kode 100007: API Key/Secret tidak valid atau IP terblokir IP Restriction Bitunix. Ubah ke 'No IP restriction' di API Management Bitunix)."
                    raise RuntimeError(f"Bitunix API Error [{code_val}]: {error_msg}")
                return result
        except Exception as e:
            log_error(f"BITUNIX_REQ_{endpoint}", str(e))
            raise

    async def get_top_futures_by_volume(self, n: Optional[int] = None, sort_by: str = "VOLUME_DESC") -> List[str]:
        """
        Mengambil Top N / Seluruh ticker Futures berpasangan USDT berdasarkan volume atau change 24 jam yang valid & OPEN.
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

            # 2. Ambil tickers untuk sorting
            res = await self._request("GET", "/api/v1/futures/market/tickers")
            ticker_list = res.get("data", []) if isinstance(res, dict) else res
            if not isinstance(ticker_list, list):
                ticker_list = []

            # Filter hanya pair USDT yang aktif dan BUKAN koin yang di-exclude (Fokus Altcoins)
            usdt_pairs = []
            for t in ticker_list:
                sym = str(t.get("symbol", "")).upper()
                if not sym.endswith("USDT"):
                    continue
                if valid_symbols and sym not in valid_symbols:
                    continue
                if hasattr(bot_config, "is_coin_excluded") and bot_config.is_coin_excluded(sym):
                    continue

                quote_vol = float(t.get("quoteVol", t.get("quoteVolume", t.get("amount", t.get("volume", 0)))) or 0)
                open_p = float(t.get("open", 0) or 0)
                last_p = float(t.get("lastPrice", t.get("last", 0)) or 0)
                change_pct = ((last_p - open_p) / open_p * 100) if open_p > 0 else 0.0

                usdt_pairs.append({
                    "symbol": sym,
                    "quote_vol": quote_vol,
                    "change_pct": change_pct,
                    "abs_change": abs(change_pct),
                })

            sort_mode = str(sort_by).upper().strip()
            if sort_mode in ("CHANGE_DESC", "CHANGE", "VOLATILITY"):
                # Urutkan berdasarkan persentase perubahan harga terbesar (volatilitas)
                usdt_pairs.sort(key=lambda x: (x["abs_change"], x["quote_vol"]), reverse=True)
            elif sort_mode in ("GAINERS", "TOP_GAINERS"):
                # Urutkan dari koin naik terbanyak
                usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=True)
            elif sort_mode in ("LOSERS", "TOP_LOSERS"):
                # Urutkan dari koin turun terdalam
                usdt_pairs.sort(key=lambda x: x["change_pct"], reverse=False)
            else:
                # Default: Urutkan berdasarkan volume USDT 24h tertinggi
                usdt_pairs.sort(key=lambda x: x["quote_vol"], reverse=True)

            symbols = [p["symbol"] for p in usdt_pairs]
            if n is not None and isinstance(n, int) and n > 0:
                return symbols[:n]
            return symbols
        except Exception as e:
            log_error("BITUNIX_TOP_COINS", str(e))
            print(f"[BITUNIX] Warning saat fetch scan coins: {e}")
            fallback_coins = [
                "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT",
                "AVAXUSDT", "LINKUSDT", "SUIUSDT", "PEPEUSDT", "NEARUSDT",
                "APTUSDT", "OPUSDT", "ARBUSDT", "FTMUSDT", "TIAUSDT"
            ]
            return fallback_coins[:n] if n else fallback_coins

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
            if "not allowed to trade" in err_msg or "suspended" in err_msg or "terblokir isp" in err_msg or "html" in err_msg:
                # Kontrak tidak diizinkan untuk trading atau terblokir ISP, lewati tanpa log spam
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

    async def get_account_balance(self, margin_coin: str = "USDT") -> Dict[str, float]:
        """
        Mengambil balance futures Bitunix untuk margin coin (default USDT).
        Endpoint resmi: GET /api/v1/futures/account?marginCoin=USDT
        """
        try:
            res = await self._request(
                "GET",
                "/api/v1/futures/account",
                params={"marginCoin": margin_coin.upper()},
                auth_required=True,
            )
            raw_data = res.get("data", []) if isinstance(res, dict) else []
            if isinstance(raw_data, list) and len(raw_data) > 0:
                data = raw_data[0]
            elif isinstance(raw_data, dict):
                data = raw_data
            else:
                data = {}

            available = float(data.get("available", data.get("availableBalance", 0.0)))
            margin_locked = float(data.get("margin", 0.0))
            frozen = float(data.get("frozen", 0.0))
            cross_unreal = float(data.get("crossUnrealizedPNL", 0.0))
            iso_unreal = float(data.get("isolationUnrealizedPNL", 0.0))
            unrealized = cross_unreal + iso_unreal

            # Total wallet balance = available + margin terkunci + frozen
            total_wallet = available + margin_locked + frozen
            if total_wallet == 0.0:
                total_wallet = float(data.get("transfer", data.get("marginBalance", available)))

            return {
                "total_wallet_balance": total_wallet,
                "available_balance": available,
                "unrealized_pnl": unrealized,
                "margin_locked": margin_locked,
                "frozen": frozen,
            }
        except Exception as e:
            log_error("BITUNIX_BALANCE", str(e))
            return {
                "total_wallet_balance": 0.0,
                "available_balance": 0.0,
                "unrealized_pnl": 0.0,
                "margin_locked": 0.0,
                "frozen": 0.0,
            }

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Mengambil posisi aktif futures Bitunix.
        Endpoint resmi: GET /api/v1/futures/position/get_pending_positions
        """
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol.upper()
            res = await self._request(
                "GET",
                "/api/v1/futures/position/get_pending_positions",
                params=params if params else None,
                auth_required=True,
            )
            raw_pos_data = res.get("data", []) if isinstance(res, dict) else []
            if isinstance(raw_pos_data, dict):
                pos_list = raw_pos_data.get("positionList", raw_pos_data.get("positions", raw_pos_data.get("list", [])))
            elif isinstance(raw_pos_data, list):
                pos_list = raw_pos_data
            else:
                pos_list = []

            active_positions = []
            for pos in pos_list:
                qty = float(pos.get("qty", pos.get("positionAmt", 0.0)))
                if abs(qty) > 0:
                    raw_side = str(pos.get("side", pos.get("positionSide", ""))).upper().strip()
                    if raw_side in ("BUY", "LONG", "OPEN_LONG", "1"):
                        normalized_side = "LONG"
                        pos_amt = abs(qty)
                    elif raw_side in ("SELL", "SHORT", "OPEN_SHORT", "2"):
                        normalized_side = "SHORT"
                        pos_amt = -abs(qty)
                    else:
                        normalized_side = "LONG" if qty >= 0 else "SHORT"
                        pos_amt = qty

                    entry_p = float(pos.get("avgOpenPrice", pos.get("entryPrice", pos.get("avgPrice", 0.0))))
                    unreal_pnl = float(pos.get("unrealizedPNL", pos.get("unrealizedProfit", pos.get("unrealizedPnl", 0.0))))
                    margin_val = float(pos.get("margin", 0.0))
                    entry_val = float(pos.get("entryValue", 0.0))
                    lev = int(pos.get("leverage", 1))

                    active_positions.append({
                        "position_id": str(pos.get("positionId", "")),
                        "symbol": str(pos.get("symbol", "")).upper(),
                        "side": normalized_side,
                        "position_amt": pos_amt,
                        "qty": abs(qty),
                        "entry_price": entry_p,
                        "entry_value": entry_val,
                        "margin": margin_val if margin_val > 0 else (entry_val / lev if lev > 0 else entry_val),
                        "mark_price": float(pos.get("markPrice", pos.get("lastPrice", entry_p))),
                        "unrealized_pnl": unreal_pnl,
                        "leverage": lev,
                        "margin_mode": pos.get("marginMode", "ISOLATION"),
                        "position_mode": pos.get("positionMode", "HEDGE"),
                        "liquidation_price": float(pos.get("liqPrice", pos.get("liquidationPrice", 0.0))),
                        "fee": float(pos.get("fee", 0.0)),
                        "funding": float(pos.get("funding", 0.0)),
                        "ctime": int(pos.get("ctime", 0)),
                    })
            return active_positions
        except Exception as e:
            log_error("BITUNIX_POSITIONS", str(e))
            return []

    async def get_history_positions(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Mengambil riwayat posisi yang sudah selesai (closed positions) dari Bitunix OpenAPI.
        Endpoint resmi: GET /api/v1/futures/position/get_history_positions
        """
        try:
            params = {"limit": limit}
            if symbol:
                params["symbol"] = symbol.upper()
            res = await self._request("GET", "/api/v1/futures/position/get_history_positions", params=params, auth_required=True)
            raw_data = res.get("data", {}) if isinstance(res, dict) else {}
            if isinstance(raw_data, dict):
                raw_list = raw_data.get("positionList", raw_data.get("positions", raw_data.get("list", [])))
            elif isinstance(raw_data, list):
                raw_list = raw_data
            else:
                raw_list = []

            history = []
            for p in raw_list:
                raw_side = str(p.get("side", "")).upper().strip()
                side = "LONG" if raw_side in ("BUY", "LONG", "OPEN_LONG", "1") else "SHORT"
                entry_p = float(p.get("entryPrice", 0.0) or 0.0)
                close_p = float(p.get("closePrice", 0.0) or 0.0)
                pnl = float(p.get("realizedPNL", 0.0) or 0.0)
                fee = float(p.get("fee", 0.0) or 0.0)
                funding = float(p.get("funding", 0.0) or 0.0)
                net_pnl = pnl - fee + funding
                qty = float(p.get("qty", p.get("maxQty", 0.0)) or 0.0)
                lev = int(p.get("leverage", 1) or 1)
                margin = (qty * entry_p / lev) if lev > 0 else (qty * entry_p)
                ctime = int(p.get("ctime", 0) or 0)
                mtime = int(p.get("mtime", 0) or 0)
                duration_m = round((mtime - ctime) / 60000.0, 1) if (mtime > ctime and ctime > 0) else 0.0
                closed_at_dt = datetime.fromtimestamp(mtime / 1000.0) if mtime > 0 else datetime.now()

                history.append({
                    "position_id": str(p.get("positionId", "")),
                    "symbol": str(p.get("symbol", "")).upper(),
                    "side": side,
                    "entry_price": entry_p,
                    "exit_price": close_p,
                    "qty": qty,
                    "realized_pnl": pnl,
                    "fee": fee,
                    "commission": fee,
                    "funding_fee": funding,
                    "net_pnl": net_pnl,
                    "leverage": lev,
                    "margin_usdt": margin,
                    "duration_minutes": duration_m,
                    "closed_at": closed_at_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_type": "EXCHANGE_TP_SL",
                    "result": "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "BREAKEVEN"),
                    "exchange": "BITUNIX_REAL",
                })
            return history
        except Exception as e:
            log_error("BITUNIX_HISTORY_POSITIONS", str(e))
            return []


    async def get_position_tiers(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Mengambil tingkatan posisi (Position Tiers), batas leverage, dan maintenance margin rate untuk koin tertentu.
        Endpoint resmi: GET /api/v1/futures/position/get_position_tiers?symbol=BTCUSDT
        """
        try:
            res = await self._request("GET", "/api/v1/futures/position/get_position_tiers", params={"symbol": symbol.upper()})
            tiers_list = res.get("data", []) if isinstance(res, dict) else []
            if not isinstance(tiers_list, list):
                return []
            return [
                {
                    "symbol": str(t.get("symbol", symbol)).upper(),
                    "level": int(t.get("level", 1)),
                    "start_value": float(t.get("startValue", 0.0)),
                    "end_value": float(t.get("endValue", 0.0)),
                    "max_leverage": int(t.get("leverage", 1)),
                    "maintenance_margin_rate": float(t.get("maintenanceMarginRate", 0.0)),
                }
                for t in tiers_list
            ]
        except Exception as e:
            log_error(f"BITUNIX_TIERS_{symbol}", str(e))
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

    async def get_symbol_precision(self, symbol: str) -> Dict[str, Any]:
        if not self._exchange_info_cache:
            try:
                res = await self._request("GET", "/api/v1/futures/market/trading_pairs")
                pairs = res.get("data", []) if isinstance(res, dict) else []
                for p in pairs:
                    sym = p.get("symbol", "").upper()
                    qty_prec = int(p.get("basePrecision", p.get("qtyPrecision", p.get("amountPrecision", 3))))
                    price_prec = int(p.get("quotePrecision", p.get("pricePrecision", 4)))
                    min_qty = float(p.get("minTradeVolume", p.get("minTradeAmount", p.get("minQty", 0.0))) or 0.0)
                    self._exchange_info_cache[sym] = {
                        "qty": qty_prec,
                        "price": price_prec,
                        "min_qty": min_qty,
                    }
            except Exception as e:
                log_error("BITUNIX_PAIR_INFO", str(e))

        return self._exchange_info_cache.get(symbol.upper(), {"qty": 3, "price": 4, "min_qty": 0.0})

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        trade_side: Optional[str] = None,
        position_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        precision = await self.get_symbol_precision(symbol)
        qty_prec = precision.get("qty", 3)
        price_prec = precision.get("price", 4)
        min_qty = float(precision.get("min_qty", 0.0) or 0.0)

        order_qty = quantity
        if min_qty > 0 and order_qty < min_qty:
            order_qty = min_qty

        if qty_prec == 0:
            formatted_qty_str = str(int(math.floor(order_qty)))
        else:
            multiplier = 10 ** qty_prec
            qty_floored = math.floor(order_qty * multiplier) / multiplier
            formatted_qty_str = f"{qty_floored:.{qty_prec}f}"

        if float(formatted_qty_str) <= 0:
            raise ValueError(f"Quantity order untuk {symbol} terlalu kecil: {order_qty} (Formatted: {formatted_qty_str})")

        side_clean = side.upper().strip()
        if not trade_side:
            trade_side = "CLOSE" if reduce_only else "OPEN"

        payload: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": formatted_qty_str,
            "side": side_clean,
            "tradeSide": trade_side.upper(),
            "orderType": order_type.upper(),
        }

        if position_id:
            payload["positionId"] = str(position_id)

        if reduce_only:
            payload["reduceOnly"] = True

        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = f"{price:.{price_prec}f}"
            payload["effect"] = "GTC"

        return await self._request("POST", "/api/v1/futures/trade/place_order", data=payload, auth_required=True)

    async def place_tp_sl(
        self,
        symbol: str,
        side: str,
        quantity: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Memasang Take Profit dan/atau Stop Loss untuk posisi aktif menggunakan endpoint resmi Bitunix:
        POST /api/v1/futures/tpsl/position/place_order
        """
        precision = await self.get_symbol_precision(symbol)
        results: Dict[str, Any] = {}

        pos = None
        for attempt in range(4):
            positions = await self.get_open_positions(symbol=symbol)
            pos = next((p for p in positions if p.get("symbol") == symbol.upper()), None)
            if not pos:
                all_positions = await self.get_open_positions()
                pos = next((p for p in all_positions if p.get("symbol") == symbol.upper()), None)
            if pos and pos.get("position_id"):
                break
            await asyncio.sleep(0.35)

        pos_id = pos.get("position_id") if pos else None

        if pos_id:
            tpsl_payload: Dict[str, Any] = {
                "symbol": symbol.upper(),
                "positionId": str(pos_id),
            }
            if tp_price is not None and tp_price > 0:
                tpsl_payload["tpPrice"] = f"{tp_price:.{precision['price']}f}"
                tpsl_payload["tpStopType"] = "MARK_PRICE"
            if sl_price is not None and sl_price > 0:
                tpsl_payload["slPrice"] = f"{sl_price:.{precision['price']}f}"
                tpsl_payload["slStopType"] = "MARK_PRICE"

            try:
                res = await self._request("POST", "/api/v1/futures/tpsl/position/place_order", data=tpsl_payload, auth_required=True)
                results["position_tpsl"] = res
                results["status"] = "success"
                return results
            except Exception as e:
                log_error(f"BITUNIX_POS_TPSL_{symbol}", str(e))
                results["tpsl_error"] = str(e)
                results["status"] = "error"
                return results

        # Fallback jika positionId belum tersedia
        return {"status": "NO_POSITION_FOR_TPSL", "symbol": symbol}

    async def emergency_close_position(self, symbol: str) -> Dict[str, Any]:
        """
        Menutup posisi secara instan di market menggunakan flash close atau market order close.
        """
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol.upper()), None)
        if not pos:
            return {"status": "NO_POSITION", "symbol": symbol}

        pos_id = pos.get("position_id")
        # Coba flash close position jika ada positionId
        if pos_id:
            try:
                res_fc = await self._request("POST", "/api/v1/futures/trade/flash_close_position", data={"positionId": str(pos_id)}, auth_required=True)
                return {"status": "success", "flash_close": res_fc}
            except Exception as e_fc:
                log_error(f"BITUNIX_FLASH_CLOSE_{symbol}", str(e_fc))

        close_side = "SELL" if pos["side"] == "LONG" else "BUY"
        return await self.place_order(
            symbol=symbol.upper(),
            side=close_side,
            order_type="MARKET",
            quantity=abs(pos["position_amt"]),
            reduce_only=True,
            trade_side="CLOSE",
            position_id=pos_id,
        )
