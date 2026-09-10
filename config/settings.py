import os
from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
TELEGRAM_ERROR_CHAT_ID = os.getenv("TELEGRAM_ERROR_CHAT_ID") or TELEGRAM_ADMIN_CHAT_ID
TELEGRAM_ADMIN_USER_IDS = {
    int(value.strip())
    for value in os.getenv("TELEGRAM_ADMIN_USER_IDS", "").split(",")
    if value.strip().lstrip("-").isdigit()
}

TRADING_MODE = os.getenv("TRADING_MODE", "TESTNET").upper()
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

# Fitur Lanjutan: Risk Management, Liquidity, Funding, & Limit Order
DAILY_LOSS_LIMIT_PERCENT_ENV = float(os.getenv("DAILY_LOSS_LIMIT_PERCENT", "5.0"))
MIN_ORDER_BOOK_DEPTH_USDT_ENV = float(os.getenv("MIN_ORDER_BOOK_DEPTH_USDT", "50000.0"))
MAX_FUNDING_RATE_PERCENT_ENV = float(os.getenv("MAX_FUNDING_RATE_PERCENT", "0.05"))
ATR_MULTIPLIER_SL_ENV = float(os.getenv("ATR_MULTIPLIER_SL", "1.5"))
LIMIT_ORDER_TIMEOUT_SECONDS_ENV = int(os.getenv("LIMIT_ORDER_TIMEOUT_SECONDS", "30"))

# Fitur Baru: Trailing Stop & MTFA
USE_TRAILING_STOP = os.getenv("USE_TRAILING_STOP", "True").lower() == "true"
TS_ACTIVATION_PERCENT_ENV = float(os.getenv("TS_ACTIVATION_PERCENT", "15.0"))
TS_CALLBACK_RATE_ENV = float(os.getenv("TS_CALLBACK_RATE", "1.0"))
HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "1h")

if not BINANCE_API_KEY or not BINANCE_API_SECRET:
    raise ValueError("Missing Binance API keys in .env")

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
            cls._instance.tp_percent = TP_PERCENT_ENV
            cls._instance.sl_percent = SL_PERCENT_ENV
            cls._instance.leverage = LEVERAGE_ENV
            cls._instance.max_open_positions = MAX_OPEN_POSITIONS_ENV
            cls._instance.risk_per_trade_percent = RISK_PER_TRADE_PERCENT_ENV
            cls._instance.rsi_length = RSI_LENGTH_ENV
            cls._instance.rsi_oversold = RSI_OVERSOLD_ENV
            cls._instance.rsi_overbought = RSI_OVERBOUGHT_ENV
            cls._instance.scanner_mode = SCANNER_MODE_ENV if SCANNER_MODE_ENV in {"per_coin", "batch"} else "per_coin"
            cls._instance.analysis_lookback_days = ANALYSIS_LOOKBACK_DAYS_ENV
            cls._instance.use_trailing_stop = USE_TRAILING_STOP
            cls._instance.ts_activation_percent = TS_ACTIVATION_PERCENT_ENV
            cls._instance.ts_callback_rate = TS_CALLBACK_RATE_ENV
            
            # Fitur Lanjutan
            cls._instance.daily_loss_limit_percent = DAILY_LOSS_LIMIT_PERCENT_ENV
            cls._instance.min_order_book_depth_usdt = MIN_ORDER_BOOK_DEPTH_USDT_ENV
            cls._instance.max_funding_rate_percent = MAX_FUNDING_RATE_PERCENT_ENV
            cls._instance.atr_multiplier_sl = ATR_MULTIPLIER_SL_ENV
            cls._instance.limit_order_timeout_seconds = LIMIT_ORDER_TIMEOUT_SECONDS_ENV
        return cls._instance
        
    def update_tp(self, val: float):
        self.tp_percent = val
        self._update_env("TP_PERCENT", str(val))
        
    def update_sl(self, val: float):
        self.sl_percent = val
        self._update_env("SL_PERCENT", str(val))

    def update_leverage(self, val: int):
        self.leverage = val
        self._update_env("LEVERAGE", str(val))
        
    def update_max_positions(self, val: int):
        self.max_open_positions = val
        self._update_env("MAX_OPEN_POSITIONS", str(val))

    def update_risk_per_trade(self, val: float):
        self.risk_per_trade_percent = val
        self._update_env("RISK_PER_TRADE_PERCENT", str(val))

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
        
    def _update_env(self, key: str, value: str):
        try:
            import dotenv
            dotenv.set_key(".env", key, value)
        except Exception as e:
            print(f"Failed to update .env: {e}")

bot_config = BotSettings()
