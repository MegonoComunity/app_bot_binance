import csv
import json
import os
from datetime import datetime


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


def record_closed_trade(trade: dict) -> dict:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    stats = _load_stats()
    stats["trades"].append(trade)
    with open(STATS_FILE, "w", encoding="utf-8") as file:
        json.dump(stats, file, indent=2)
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
                stats["trades"].append({
                    "time": row.get("Waktu", ""),
                    "symbol": row.get("Symbol", ""),
                    "exit_price": row.get("Harga Eksekusi", ""),
                    "realized_pnl": pnl,
                    "commission": 0.0,
                    "mfe": None,
                    "mae": None,
                    "duration_minutes": None,
                })
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        with open(STATS_FILE, "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2)
    except (OSError, ValueError):
        return


def trade_summary() -> dict:
    import_legacy_history()
    trades = _load_stats()["trades"]
    wins = [trade for trade in trades if float(trade.get("realized_pnl", 0)) > 0]
    losses = [trade for trade in trades if float(trade.get("realized_pnl", 0)) <= 0]
    today = datetime.now().strftime("%Y-%m-%d")
    daily = [trade for trade in trades if str(trade.get("time", "")).startswith(today)]
    daily_wins = sum(float(trade.get("realized_pnl", 0)) > 0 for trade in daily)
    def net(trade):
        return float(trade.get("realized_pnl", 0)) - float(trade.get("commission", 0))

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
        "commission": sum(float(trade.get("commission", 0)) for trade in trades),
    }