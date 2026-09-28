import csv
import json
import os
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

STATS_FILE = "data/trade_stats.json"


def _load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        return {"trades": []}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data.get("trades"), list) else {"trades": []}
    except (OSError, json.JSONDecodeError):
        return {"trades": []}


def _write_to_db_async(trade: dict) -> None:
    """
    Fire-and-forget: tulis trade ke PostgreSQL di background.
    Tidak memblokir caller, tidak raise exception jika DB down.
    """
    try:
        from database.trade_repo import insert_trade
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(insert_trade(trade))
        else:
            loop.run_until_complete(insert_trade(trade))
    except Exception as exc:
        logger.warning(f"[DB] dual-write trade gagal (non-fatal): {exc}")


def record_closed_trade(trade: dict) -> dict:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    from config.settings import bot_config
    if "exchange" not in trade or not trade["exchange"]:
        trade["exchange"] = getattr(bot_config, "active_exchange", "BINANCE")
    stats = _load_stats()
    stats["trades"].append(trade)
    with open(STATS_FILE, "w", encoding="utf-8") as file:
        json.dump(stats, file, indent=2)

    # Dual-write ke PostgreSQL (non-blocking)
    _write_to_db_async(trade)

    return trade_summary()


def import_legacy_history() -> None:
    """Import the old CSV once so existing closed orders count in summaries."""
    stats = _load_stats()
    if stats["trades"] or not os.path.exists("real_history_log.csv"):
        return
    try:
        with open("real_history_log.csv", "r", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                pnl = float(row.get("PnL", 0) or 0)
                trade = {
                    "time": row.get("Waktu", ""),
                    "symbol": row.get("Symbol", ""),
                    "exit_price": row.get("Harga Eksekusi", ""),
                    "realized_pnl": pnl,
                    "commission": 0.0,
                    "mfe": None,
                    "mae": None,
                    "duration_minutes": None,
                }
                stats["trades"].append(trade)
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        with open(STATS_FILE, "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2)
    except (OSError, ValueError):
        return


def trade_summary(exchange: str = None) -> dict:
    import_legacy_history()
    all_trades = _load_stats()["trades"]
    
    if exchange:
        ex_clean = str(exchange).upper().strip()
        trades = [t for t in all_trades if str(t.get("exchange", "")).upper().strip() == ex_clean or str(t.get("exchange", "")).upper().startswith(ex_clean)]
    else:
        trades = all_trades

    wins = [trade for trade in trades if float(trade.get("realized_pnl", 0)) > 0]
    losses = [trade for trade in trades if float(trade.get("realized_pnl", 0)) <= 0]
    today = datetime.now().strftime("%Y-%m-%d")
    daily = [trade for trade in trades if str(trade.get("time", "")).startswith(today)]
    daily_wins = sum(float(trade.get("realized_pnl", 0)) > 0 for trade in daily)
    def net(trade):
        return (
            float(trade.get("realized_pnl", 0) or 0)
            - float(trade.get("commission", 0) or 0)
            + float(trade.get("funding_fee", 0) or 0)
        )

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "net_pnl": sum(net(trade) for trade in trades),
        "daily_net_pnl": sum(net(trade) for trade in daily),
        "daily_total": len(daily),
        "daily_wins": daily_wins,
        "daily_losses": len(daily) - daily_wins,
        "commission": sum(float(trade.get("commission", 0) or 0) for trade in trades),
        "funding_fee": sum(float(trade.get("funding_fee", 0) or 0) for trade in trades),
    }


def reset_trade_stats(exchange: str = None) -> dict:
    """Reset / bersihkan rekap trade stats sesi agar bisa menganalisis dari 0."""
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    if exchange and exchange.upper() not in ("ALL", "*"):
        ex_clean = str(exchange).upper().strip()
        stats = _load_stats()
        stats["trades"] = [
            t for t in stats.get("trades", [])
            if not (str(t.get("exchange", "BINANCE")).upper().startswith(ex_clean))
        ]
    else:
        stats = {"trades": []}

    with open(STATS_FILE, "w", encoding="utf-8") as file:
        json.dump(stats, file, indent=2)

    # Dual-write delete ke PostgreSQL (non-blocking)
    try:
        from database.trade_repo import clear_trade_history
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(clear_trade_history(exchange))
        else:
            loop.run_until_complete(clear_trade_history(exchange))
    except Exception as exc:
        logger.warning(f"[DB] dual-write clear trade history gagal: {exc}")

    return trade_summary(exchange=exchange)


EXPLAINABILITY_FILE = "data/trade_explainability.json"

def clear_trade_explainability() -> None:
    """Bersihkan file trade explainability JSON."""
    try:
        if os.path.exists(EXPLAINABILITY_FILE):
            with open(EXPLAINABILITY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
    except Exception as exc:
        logger.warning(f"[EXPLAINABILITY] Gagal membersihkan explainability: {exc}")


def record_trade_explainability_snapshot(
    symbol: str = "UNKNOWN",
    side: str = "LONG",
    entry_price: float = 0.0,
    features: dict = None,
    meta_intel: dict = None,
    regime_intel: dict = None,
    confluence_breakdown: dict = None,
    alasan: str = "",
    trade_id: str = None,
    meta_info: dict = None,
    regime_info: dict = None,
    ml_vision_info: dict = None,
    risk_info: dict = None,
    notes: str = "",
    **kwargs,
) -> dict:
    """
    Pilar 6: Full Logging & Model Explainability.
    Menyimpan snapshot komprehensif seluruh input fitur, probabilitas meta-labeler,
    kondisi pasar, dan alasan keputusan trading.
    """
    os.makedirs("data", exist_ok=True)
    meta_data = meta_intel or meta_info or {}
    regime_data = regime_intel or regime_info or {}
    exec_reason = alasan or notes or ""
    
    try:
        data = []
        if os.path.exists(EXPLAINABILITY_FILE):
            with open(EXPLAINABILITY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        
        snapshot = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trade_id": trade_id or f"{symbol}_{int(datetime.now().timestamp())}",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "market_regime": regime_data.get("regime", "UNKNOWN"),
            "adx": regime_data.get("adx"),
            "relative_atr": regime_data.get("relative_atr"),
            "meta_probability_win": meta_data.get("win_probability", meta_data.get("probability_win")),
            "half_kelly_multiplier": meta_data.get("half_kelly_multiplier"),
            "meta_reason": meta_data.get("reason"),
            "technical_features": features or {},
            "ml_vision_info": ml_vision_info or {},
            "risk_info": risk_info or {},
            "confluence_breakdown": confluence_breakdown or {},
            "alasan_eksekusi": exec_reason,
        }
        
        data.append(snapshot)
        if len(data) > 300:  # Batasi 300 snapshot terakhir
            data = data[-300:]
            
        with open(EXPLAINABILITY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {"status": "recorded", "trade_id": snapshot["trade_id"]}
            
    except Exception as exc:
        logger.warning(f"[EXPLAINABILITY] Gagal menyimpan explainability snapshot: {exc}")
        return {"status": "error", "reason": str(exc)}
