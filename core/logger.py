import logging
from logging.handlers import RotatingFileHandler
import os

# Pastikan folder logs ada
os.makedirs("logs", exist_ok=True)

# Konfigurasi Logger Utama
bot_logger = logging.getLogger("BotLogger")
bot_logger.setLevel(logging.ERROR)

# Menggunakan RotatingFileHandler (Max 5MB, simpan 2 file backup)
handler = RotatingFileHandler(
    "logs/error_log.txt", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] %(message)s')
handler.setFormatter(formatter)
bot_logger.addHandler(handler)

def log_error(context: str, message: str):
    """
    Fungsi praktis untuk mencatat error ke file.
    """
    logger = logging.getLogger(context)
    if not logger.handlers:
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
    
    logger.error(message)
