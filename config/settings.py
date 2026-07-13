import os
from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
TELEGRAM_ERROR_CHAT_ID = os.getenv("TELEGRAM_ERROR_CHAT_ID") or TELEGRAM_ADMIN_CHAT_ID

TRADING_MODE = os.getenv("TRADING_MODE", "TESTNET").upper()
LEVERAGE_ENV = int(os.getenv("LEVERAGE", "20"))
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "50"))
TP_PERCENT_ENV = float(os.getenv("TP_PERCENT", "25.0"))
SL_PERCENT_ENV = float(os.getenv("SL_PERCENT", "35.0"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
API_REQUEST_DELAY = float(os.getenv("API_REQUEST_DELAY", "0.5"))
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
MAX_OPEN_POSITIONS_ENV = int(os.getenv("MAX_OPEN_POSITIONS", "4"))

# Fitur Baru: Trailing Stop & MTFA
USE_TRAILING_STOP = os.getenv("USE_TRAILING_STOP", "True").lower() == "true"
TS_ACTIVATION_PERCENT_ENV = float(os.getenv("TS_ACTIVATION_PERCENT", "15.0"))
TS_CALLBACK_RATE_ENV = float(os.getenv("TS_CALLBACK_RATE", "1.0"))
HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "1h")

if not BINANCE_API_KEY or not BINANCE_API_SECRET:
    raise ValueError("Missing Binance API keys in .env")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
    raise ValueError("Missing Telegram credentials in .env")

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
            cls._instance.use_trailing_stop = USE_TRAILING_STOP
            cls._instance.ts_activation_percent = TS_ACTIVATION_PERCENT_ENV
            cls._instance.ts_callback_rate = TS_CALLBACK_RATE_ENV
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
