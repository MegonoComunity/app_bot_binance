from __future__ import annotations
import os
from typing import Optional, List, Dict, Any, Set
from dotenv import load_dotenv

load_dotenv()

ACTIVE_EXCHANGE = os.getenv("ACTIVE_EXCHANGE", "BINANCE").upper().strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY", "")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET", "")
BITUNIX_BASE_URL = os.getenv("BITUNIX_BASE_URL", "https://fapi.bitunix.com")
BITUNIX_PROXY = os.getenv("BITUNIX_PROXY", "") or os.getenv("HTTPS_PROXY", "") or os.getenv("HTTP_PROXY", "") or None

# ─── PostgreSQL ───────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_ENABLED   = bool(DATABASE_URL)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
TELEGRAM_ERROR_CHAT_ID = os.getenv("TELEGRAM_ERROR_CHAT_ID") or TELEGRAM_ADMIN_CHAT_ID
TELEGRAM_ADMIN_USER_IDS = {
    int(value.strip())
    for value in os.getenv("TELEGRAM_ADMIN_USER_IDS", "").split(",")
    if value.strip().lstrip("-").isdigit()
}

TRADING_MODE = os.getenv("TRADING_MODE", "PAPER_TRADING").upper()
LEVERAGE_ENV = int(os.getenv("LEVERAGE", "20"))
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "50"))
TP_PERCENT_ENV = float(os.getenv("TP_PERCENT", "25.0"))
SL_PERCENT_ENV = float(os.getenv("SL_PERCENT", "35.0"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "0.5"))
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
MAX_OPEN_POSITIONS_ENV = int(os.getenv("MAX_OPEN_POSITIONS", "4"))
SCAN_UNIVERSE_SIZE_ENV = int(os.getenv("SCAN_UNIVERSE_SIZE", "200"))
TOP_N_COINS_ENV = max(int(os.getenv("TOP_N_COINS", str(SCAN_UNIVERSE_SIZE_ENV))), 200)
SCAN_BATCH_SIZE_ENV = int(os.getenv("SCAN_BATCH_SIZE", "10"))
SMART_BUY_LOOKBACK_DAYS_ENV = int(os.getenv("SMART_BUY_LOOKBACK_DAYS", "20"))
SMART_BUY_TOLERANCE_ENV = float(os.getenv("SMART_BUY_TOLERANCE", "0.005"))
AUTO_CLOSE_PROFIT_HOURS_ENV = float(os.getenv("AUTO_CLOSE_PROFIT_HOURS", "24"))
POSITION_MONITOR_INTERVAL_ENV = int(os.getenv("POSITION_MONITOR_INTERVAL", "60"))
RISK_PER_TRADE_PERCENT_ENV = float(os.getenv("RISK_PER_TRADE_PERCENT", "1.0"))
RSI_LENGTH_ENV = int(os.getenv("RSI_LENGTH", "14"))
RSI_OVERSOLD_ENV = float(os.getenv("RSI_OVERSOLD", "35"))
RSI_OVERBOUGHT_ENV = float(os.getenv("RSI_OVERBOUGHT", "75"))
SCANNER_MODE_ENV = os.getenv("SCANNER_MODE", "per_coin").lower()
ANALYSIS_LOOKBACK_DAYS_ENV = max(int(os.getenv("ANALYSIS_LOOKBACK_DAYS", "20")), 20)
MAX_DAILY_LOSS_PERCENT_ENV = float(os.getenv("MAX_DAILY_LOSS_PERCENT", "3.0"))
MAX_TOTAL_EXPOSURE_PERCENT_ENV = float(os.getenv("MAX_TOTAL_EXPOSURE_PERCENT", "100.0"))
BREAKOUT_MIN_SCORE_ENV = float(os.getenv("BREAKOUT_MIN_SCORE", "50.0"))
BREAKOUT_VOLUME_MULTIPLIER_ENV = float(os.getenv("BREAKOUT_VOLUME_MULTIPLIER", "2.0"))

# Fitur Lanjutan: Risk Management, Liquidity, Funding, & Limit Order
DAILY_LOSS_LIMIT_PERCENT_ENV = float(os.getenv("DAILY_LOSS_LIMIT_PERCENT", "5.0"))
MIN_ORDER_BOOK_DEPTH_USDT_ENV = float(os.getenv("MIN_ORDER_BOOK_DEPTH_USDT", "50000.0"))
MAX_FUNDING_RATE_PERCENT_ENV = float(os.getenv("MAX_FUNDING_RATE_PERCENT", "0.05"))
ATR_MULTIPLIER_SL_ENV = float(os.getenv("ATR_MULTIPLIER_SL", "1.5"))
LIMIT_ORDER_TIMEOUT_SECONDS_ENV = int(os.getenv("LIMIT_ORDER_TIMEOUT_SECONDS", "30"))
MARGIN_MODE_ENV = os.getenv("MARGIN_MODE", "DYNAMIC").upper()
MAX_POSITION_EQUITY_RATIO_ENV = float(os.getenv("MAX_POSITION_EQUITY_RATIO", "0.20"))

# Fitur Baru: Trailing Stop & MTFA
USE_TRAILING_STOP = os.getenv("USE_TRAILING_STOP", "True").lower() == "true"
TS_ACTIVATION_PERCENT_ENV = float(os.getenv("TS_ACTIVATION_PERCENT", "15.0"))
TS_CALLBACK_RATE_ENV = float(os.getenv("TS_CALLBACK_RATE", "1.0"))
HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "1h")
SIMULATED_MODAL_ENV = float(os.getenv("SIMULATED_MODAL", "0.0"))

# Validasi API key sesuai exchange aktif
if ACTIVE_EXCHANGE == "BINANCE":
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise ValueError("ACTIVE_EXCHANGE=BINANCE tetapi BINANCE_API_KEY / BINANCE_API_SECRET belum diisi di .env")
elif ACTIVE_EXCHANGE == "BITUNIX":
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        raise ValueError("ACTIVE_EXCHANGE=BITUNIX tetapi BITUNIX_API_KEY / BITUNIX_API_SECRET belum diisi di .env")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
    raise ValueError("Missing Telegram credentials in .env")

try:
    TELEGRAM_ADMIN_CHAT_ID = int(TELEGRAM_ADMIN_CHAT_ID)
except ValueError as exc:
    raise ValueError("TELEGRAM_ADMIN_CHAT_ID must be an integer") from exc

if not TELEGRAM_ADMIN_USER_IDS:
    TELEGRAM_ADMIN_USER_IDS = {TELEGRAM_ADMIN_CHAT_ID}

class BotSettings:
    """
    Menyimpan setting trading agar bisa diubah saat bot berjalan (diubah via Telegram)
    tanpa perlu restart.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.simulated_modal = SIMULATED_MODAL_ENV if SIMULATED_MODAL_ENV > 0 else None
            cls._instance.tp_percent = TP_PERCENT_ENV
            cls._instance.sl_percent = SL_PERCENT_ENV
            cls._instance.leverage = LEVERAGE_ENV
            cls._instance.max_open_positions = MAX_OPEN_POSITIONS_ENV
            cls._instance.risk_per_trade_percent = RISK_PER_TRADE_PERCENT_ENV
            cls._instance.margin_mode = MARGIN_MODE_ENV if MARGIN_MODE_ENV in {"DYNAMIC", "FIXED"} else "DYNAMIC"
            cls._instance.margin_usdt = MARGIN_USDT
            cls._instance.max_position_equity_ratio = MAX_POSITION_EQUITY_RATIO_ENV
            cls._instance.rsi_length = RSI_LENGTH_ENV
            cls._instance.rsi_oversold = RSI_OVERSOLD_ENV
            cls._instance.rsi_overbought = RSI_OVERBOUGHT_ENV
            cls._instance.scanner_mode = SCANNER_MODE_ENV if SCANNER_MODE_ENV in {"per_coin", "batch"} else "per_coin"
            cls._instance.analysis_lookback_days = ANALYSIS_LOOKBACK_DAYS_ENV
            cls._instance.max_daily_loss_percent = MAX_DAILY_LOSS_PERCENT_ENV
            cls._instance.max_total_exposure_percent = MAX_TOTAL_EXPOSURE_PERCENT_ENV
            cls._instance.breakout_min_score = BREAKOUT_MIN_SCORE_ENV
            cls._instance.breakout_volume_multiplier = BREAKOUT_VOLUME_MULTIPLIER_ENV
            cls._instance.use_trailing_stop = USE_TRAILING_STOP
            cls._instance.ts_activation_percent = TS_ACTIVATION_PERCENT_ENV
            cls._instance.ts_callback_rate = TS_CALLBACK_RATE_ENV
            
            cls._instance.active_exchange = ACTIVE_EXCHANGE
            cls._instance.trading_mode = TRADING_MODE
            
            # Fitur Lanjutan
            cls._instance.daily_loss_limit_percent = DAILY_LOSS_LIMIT_PERCENT_ENV
            cls._instance.min_order_book_depth_usdt = MIN_ORDER_BOOK_DEPTH_USDT_ENV
            cls._instance.max_funding_rate_percent = MAX_FUNDING_RATE_PERCENT_ENV
            cls._instance.atr_multiplier_sl = ATR_MULTIPLIER_SL_ENV
            cls._instance.limit_order_timeout_seconds = LIMIT_ORDER_TIMEOUT_SECONDS_ENV
        return cls._instance
        
    def update_trading_mode(self, mode: str):
        mode = mode.upper().strip()
        if mode not in {"REAL", "PAPER_TRADING", "TESTNET"}:
            raise ValueError("Mode harus REAL, PAPER_TRADING, atau TESTNET")
        self.trading_mode = mode
        self._update_env("TRADING_MODE", mode)

    def update_active_exchange(self, exchange: str):
        exchange = exchange.upper().strip()
        if exchange not in {"BINANCE", "BITUNIX"}:
            raise ValueError("Exchange yang didukung hanya BINANCE dan BITUNIX")
        self.active_exchange = exchange
        self._update_env("ACTIVE_EXCHANGE", exchange)

    def update_tp(self, val: float):
        self.tp_percent = val
        self._update_env("TP_PERCENT", str(val))
        
    def update_sl(self, val: float):
        self.sl_percent = val
        self._update_env("SL_PERCENT", str(val))

    def update_leverage(self, val: int):
        self.leverage = val
        self._update_env("LEVERAGE", str(val))
        
    def update_max_positions(self, val: int, per_side: Optional[int] = None):
        self.max_open_positions = int(val)
        self.max_positions_per_side = int(per_side) if per_side is not None else int(val)
        self._update_env("MAX_OPEN_POSITIONS", str(val))
        self._update_env("MAX_POSITIONS_PER_SIDE", str(self.max_positions_per_side))

    def update_margin_mode(self, mode: str):
        mode = mode.upper()
        if mode not in {"DYNAMIC", "FIXED"}:
            raise ValueError("Mode margin harus DYNAMIC atau FIXED")
        self.margin_mode = mode
        self._update_env("MARGIN_MODE", mode)

    def update_margin(self, val: float):
        if val <= 0:
            raise ValueError("Margin harus lebih besar dari 0")
        self.margin_usdt = val
        self._update_env("MARGIN_USDT", str(val))

    def update_risk_per_trade(self, val: float):
        if val <= 0 or val > 10:
            raise ValueError("Risk per trade harus antara 0.1% sampai 10.0%")
        self.risk_per_trade_percent = val
        self._update_env("RISK_PER_TRADE_PERCENT", str(val))

    def update_max_position_equity_ratio(self, val: float):
        if val <= 0 or val > 1.0:
            raise ValueError("Max position equity ratio harus antara 0.05 sampai 1.0 (5% - 100%)")
        self.max_position_equity_ratio = val
        self._update_env("MAX_POSITION_EQUITY_RATIO", str(val))

    def update_rsi(self, length: int, oversold: float, overbought: float):
        if length < 2 or not 0 < oversold < overbought < 100:
            raise ValueError("RSI harus: length >= 2 dan 0 < oversold < overbought < 100")
        self.rsi_length = length
        self.rsi_oversold = oversold
        self.rsi_overbought = overbought
        self._update_env("RSI_LENGTH", str(length))
        self._update_env("RSI_OVERSOLD", str(oversold))
        self._update_env("RSI_OVERBOUGHT", str(overbought))

    def update_scanner_mode(self, mode: str):
        mode = mode.lower()
        if mode not in {"per_coin", "batch"}:
            raise ValueError("Mode scanner harus per_coin atau batch")
        self.scanner_mode = mode
        self._update_env("SCANNER_MODE", mode)

    def update_analysis_lookback_days(self, days: int):
        if days < 20:
            raise ValueError("Analisis minimal menggunakan 20 candle Daily")
        self.analysis_lookback_days = days
        self._update_env("ANALYSIS_LOOKBACK_DAYS", str(days))
        
    def update_use_trailing_stop(self, val: bool):
        self.use_trailing_stop = val
        self._update_env("USE_TRAILING_STOP", str(val))

    def update_ts_activation(self, val: float):
        self.ts_activation_percent = val
        self._update_env("TS_ACTIVATION_PERCENT", str(val))
        
    def update_ts_callback_rate(self, val: float):
        self.ts_callback_rate = val
        self._update_env("TS_CALLBACK_RATE", str(val))
        
    def update_simulated_modal(self, val: float | None):
        if val is not None and val <= 0:
            raise ValueError("Modal simulasi harus lebih besar dari 0")
        self.simulated_modal = val
        self._update_env("SIMULATED_MODAL", str(val if val is not None else 0.0))

    def reset_simulated_modal(self, val: float = 100.0):
        self.simulated_modal = val
        self._update_env("SIMULATED_MODAL", str(val))

    def add_simulated_pnl(self, pnl: float) -> float:
        """Akumulasi hasil profit/loss ke saldo simulasi (simulated_modal)."""
        current = self.simulated_modal if self.simulated_modal is not None and self.simulated_modal > 0 else 100.0
        new_balance = max(1.0, current + float(pnl))
        self.simulated_modal = round(new_balance, 4)
        self._update_env("SIMULATED_MODAL", str(self.simulated_modal))
        return self.simulated_modal

    def update_rsi_oversold(self, val: float):
        if not (0 < val < self.rsi_overbought):
            raise ValueError(f"RSI Oversold harus antara 0 dan {self.rsi_overbought}")
        self.rsi_oversold = val
        self._update_env("RSI_OVERSOLD", str(val))

    def update_rsi_overbought(self, val: float):
        if not (self.rsi_oversold < val < 100):
            raise ValueError(f"RSI Overbought harus antara {self.rsi_oversold} dan 100")
        self.rsi_overbought = val
        self._update_env("RSI_OVERBOUGHT", str(val))

    def _update_env(self, key: str, value: str):
        try:
            import dotenv
            dotenv.set_key(".env", key, value)
        except Exception as e:
            print(f"Failed to update .env: {e}")

bot_config = BotSettings()
