"""
core/scanner_logger.py

Modul penyimpan dan pengelola stream log scanner secara real-time di memori
untuk dikonsumsi oleh Web Dashboard Console dan API kontrol VPS.
"""
from __future__ import annotations

import collections
from datetime import datetime
from typing import Dict, Any, List, Optional
from telegram.bot_handler import bot_state

# Ring buffer untuk menyimpan 300 log scanner terbaru
MAX_SCANNER_LOGS = 300

if "scanner_logs" not in bot_state:
    bot_state["scanner_logs"] = []

if "scanner_status" not in bot_state:
    bot_state["scanner_status"] = {
        "is_scanning": False,
        "current_cycle": 1,
        "current_batch": 1,
        "total_batches": 1,
        "current_symbol": "-",
        "scanned_count": 0,
        "total_coins": 80,
        "progress_percent": 0.0,
        "signals_found_cycle": 0,
        "filtered_count_cycle": 0,
        "active_radar_count": 0,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status_message": "Bot Scanner Siap.",
    }


def add_scanner_log(
    level: str,
    symbol: str,
    message: str,
    score: Optional[float] = None,
    tag: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Menambahkan satu baris log scanner ke memory buffer web dashboard.
    Levels: 'INFO' | 'SCAN' | 'PUMP' | 'CONFLUENCE' | 'ORDER' | 'FILTERED' | 'CYCLE' | 'WARN' | 'ERROR'
    """
    now_str = datetime.now().strftime("%H:%M:%S")
    timestamp_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_entry = {
        "id": len(bot_state.get("scanner_logs", [])) + 1,
        "time": now_str,
        "timestamp": timestamp_full,
        "level": level.upper(),
        "symbol": symbol.upper() if symbol else "",
        "message": message,
        "score": score,
        "tag": tag or level.upper(),
        "details": details or {},
    }
    
    logs: List[Dict[str, Any]] = bot_state.setdefault("scanner_logs", [])
    logs.append(log_entry)
    
    # Jaga ukuran memory buffer agar tetap ringan
    if len(logs) > MAX_SCANNER_LOGS:
        bot_state["scanner_logs"] = logs[-MAX_SCANNER_LOGS:]
        
    return log_entry


def update_scanner_progress(
    current_symbol: str = "",
    scanned_count: int = 0,
    total_coins: int = 80,
    current_batch: int = 1,
    total_batches: int = 1,
    cycle_index: int = 1,
    is_scanning: bool = True,
    status_message: str = "",
) -> None:
    """Memperbarui metrik progress siklus scanner untuk visualisasi progress bar web dashboard."""
    pct = round((scanned_count / total_coins * 100), 1) if total_coins > 0 else 0.0
    status = bot_state.setdefault("scanner_status", {})
    status.update({
        "is_scanning": is_scanning,
        "current_cycle": cycle_index,
        "current_batch": current_batch,
        "total_batches": total_batches,
        "current_symbol": current_symbol,
        "scanned_count": scanned_count,
        "total_coins": total_coins,
        "progress_percent": pct,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    if status_message:
        status["status_message"] = status_message


def get_scanner_snapshot() -> Dict[str, Any]:
    """Mengambil snapshot lengkap log dan status scanner untuk REST API."""
    return {
        "status": bot_state.get("scanner_status", {}),
        "bot_state": bot_state.get("state", "RUNNING"),
        "is_running": bot_state.get("is_running", False),
        "total_logs": len(bot_state.get("scanner_logs", [])),
        "logs": list(reversed(bot_state.get("scanner_logs", []))),
    }


def clear_scanner_logs() -> None:
    """Membersihkan antrean log scanner di memory."""
    bot_state["scanner_logs"] = []
    add_scanner_log("INFO", "SYSTEM", "Log scanner telah dibersihkan oleh pengguna.")
