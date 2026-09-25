import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

STATS_FILE = "data/pattern_stats.json"

def normalize_pattern_category(raw_pattern: str) -> str:
    """
    Menyeragamkan string alasan / sinyal menjadi kategori pola standar agregat
    agar statistik Win Rate AI Learner akurat dan tidak terpecah oleh angka desimal/harga unik.
    """
    if not raw_pattern or raw_pattern == "-":
        return "Setup Standar Indikator"
        
    s = raw_pattern.lower()
    
    if "pump" in s or "pre-pump" in s:
        return "Pola Momentum: Pre-Pump Radar"
    elif "ema21" in s or "pullback" in s:
        return "Pola Trend: EMA21 Dynamic Pullback"
    elif "hammer" in s:
        return "Pola Tier-A: Bullish Hammer"
    elif "morning star" in s:
        return "Pola Tier-A: Morning Star"
    elif "bullish engulfing" in s:
        return "Pola Tier-A: Bullish Engulfing"
    elif "piercing line" in s:
        return "Pola Tier-A: Piercing Line"
    elif "tweezer" in s:
        return "Pola Tier-A: Tweezer Bottom"
    elif "small bodies" in s or "consolidation" in s:
        return "Pola Tier-A: Base Consolidation Breakout"
    elif "shooting star" in s:
        return "Pola Bearish: Shooting Star"
    elif "bearish engulfing" in s:
        return "Pola Bearish: Bearish Engulfing"
    elif "dormant breakout" in s or "breakout" in s:
        return "Setup Breakout: Dormant Squeeze"
    elif "smart buy" in s:
        return "Setup Support: Smart Buy Level"
    elif "rsi oversold" in s:
        return "Setup Reversal: RSI Oversold Lower BB"
    elif "rsi overbought" in s:
        return "Setup Reversal: RSI Overbought Upper BB"
    else:
        return raw_pattern[:45].strip()

def get_stats() -> Dict[str, Any]:
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_stats(stats: Dict[str, Any]) -> None:
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan pattern_stats.json: {e}")

def record_trade_result(
    pattern_name: str,
    is_profit: bool,
    pnl: float = 0.0,
    symbol: str = "",
    timestamp: Optional[str] = None,
) -> None:
    """
    Mencatat hasil dari sebuah pola / setup dengan timestamp riil untuk perhitungan rolling window.
    """
    if not pattern_name or pattern_name == "-":
        return
        
    canonical_name = normalize_pattern_category(pattern_name)
    stats = get_stats()
    
    if canonical_name not in stats:
        stats[canonical_name] = {
            "win": 0,
            "loss": 0,
            "total": 0,
            "total_pnl": 0.0,
            "history": []
        }
        
    cat_data = stats[canonical_name]
    cat_data["total"] = cat_data.get("total", 0) + 1
    cat_data["total_pnl"] = cat_data.get("total_pnl", 0.0) + pnl
    
    if is_profit:
        cat_data["win"] = cat_data.get("win", 0) + 1
    else:
        cat_data["loss"] = cat_data.get("loss", 0) + 1
        
    # Catat ke history untuk perhitungan rolling timeframe
    ts_now = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history: List[Dict[str, Any]] = cat_data.setdefault("history", [])
    history.append({
        "time": ts_now,
        "is_win": bool(is_profit),
        "pnl": float(pnl),
        "symbol": symbol
    })
    
    # Batasi riwayat maksimal 200 per kategori untuk efisiensi
    if len(history) > 200:
        cat_data["history"] = history[-200:]
        
    save_stats(stats)
    wr = (cat_data["win"] / cat_data["total"]) * 100
    print(f"🧠 [LEARNER] Rekam jejak '{canonical_name}' diperbarui: Win: {cat_data['win']}, Loss: {cat_data['loss']} (All-Time WR: {wr:.1f}%)")

def calculate_window_stats(history: List[Dict[str, Any]], window: str = "DAILY") -> Dict[str, Any]:
    """
    Menghitung Win Rate dan PnL berdasarkan jendela waktu (DAILY, RECENT_10, RECENT_20, WEEKLY, MONTHLY, ALL_TIME).
    """
    if not history:
        return {"win": 0, "loss": 0, "total": 0, "win_rate": 0.0, "total_pnl": 0.0}
        
    w = window.upper().strip()
    filtered: List[Dict[str, Any]] = []
    now = datetime.now()
    
    if w in {"RECENT_10", "RECENT10"}:
        filtered = history[-10:]
    elif w in {"RECENT_20", "RECENT20", "RECENT"}:
        filtered = history[-20:]
    elif w in {"DAILY", "1D", "24H"}:
        cutoff = now - timedelta(hours=24)
        for h in history:
            try:
                dt = datetime.strptime(h["time"], "%Y-%m-%d %H:%M:%S")
                if dt >= cutoff:
                    filtered.append(h)
            except Exception:
                filtered.append(h)
    elif w in {"WEEKLY", "7D", "WEEK"}:
        cutoff = now - timedelta(days=7)
        for h in history:
            try:
                dt = datetime.strptime(h["time"], "%Y-%m-%d %H:%M:%S")
                if dt >= cutoff:
                    filtered.append(h)
            except Exception:
                filtered.append(h)
    elif w in {"MONTHLY", "30D", "MONTH"}:
        cutoff = now - timedelta(days=30)
        for h in history:
            try:
                dt = datetime.strptime(h["time"], "%Y-%m-%d %H:%M:%S")
                if dt >= cutoff:
                    filtered.append(h)
            except Exception:
                filtered.append(h)
    else:  # ALL_TIME
        filtered = history

    total = len(filtered)
    wins = sum(1 for x in filtered if x.get("is_win"))
    losses = total - wins
    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    total_pnl = sum(float(x.get("pnl", 0.0)) for x in filtered)
    
    return {
        "win": wins,
        "loss": losses,
        "total": total,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
    }

def is_pattern_reliable(
    pattern_name: str,
    window: Optional[str] = None,
    min_samples: int = 2,
    min_winrate: Optional[float] = None,
    use_probation: bool = True
) -> Tuple[bool, str, float]:
    """
    Mengecek apakah sebuah pola aman untuk dieksekusi berdasarkan jendela waktu yang aktif.
    
    Mengembalikan tuple: (is_allowed: bool, status_reason: str, win_rate: float)
    
    Aturan Anti-Permanent Block (Probation):
    - Jika jendela waktu = DAILY dan hari ini baru ada < min_samples trade untuk pola ini,
      maka pola diberikan 'Probation Pass' (diizinkan 1x uji coba hari ini agar tidak terblokir permanen).
    """
    if not pattern_name or pattern_name == "-":
        return True, "STANDAR", 100.0
        
    canonical_name = normalize_pattern_category(pattern_name)
    stats = get_stats()
    
    if canonical_name not in stats:
        return True, "POLA_BARU_BELUM_ADA_HISTORI", 100.0
        
    cat_data = stats[canonical_name]
    history = cat_data.get("history", [])
    
    # Ambil konfigurasi bot jika parameter None
    try:
        from config.settings import BotSettings
        cfg = BotSettings()
        active_window = window or getattr(cfg, "winrate_eval_window", "DAILY")
        threshold_wr = min_winrate if min_winrate is not None else getattr(cfg, "min_pattern_winrate", 50.0)
        probation_enabled = getattr(cfg, "use_probation_mode", True) if use_probation else False
    except Exception:
        active_window = window or "DAILY"
        threshold_wr = min_winrate if min_winrate is not None else 50.0
        probation_enabled = use_probation

    # Hitung statistik pada window aktif
    w_stats = calculate_window_stats(history, window=active_window)
    total_w = w_stats["total"]
    wr = w_stats["win_rate"]

    # 1. Jika dalam timeframe ini data sampel masih sedikit (< min_samples)
    if total_w < min_samples:
        if probation_enabled and active_window == "DAILY":
            # Beri kesempatan uji coba hari ini
            return True, f"PROBATION_HARIAN ({total_w}/{min_samples} sampel hari ini)", wr
        elif total_w == 0:
            return True, f"BELUM_ADA_TRADE_DI_WINDOW_{active_window}", 100.0
        else:
            return True, f"SAMPEL_SEDIKIT ({total_w}/{min_samples})", wr

    # 2. Jika sampel sudah cukup (>= min_samples), evaluasi Win Rate
    if wr < threshold_wr:
        msg = f"BLACKLIST_{active_window}: WR {wr:.1f}% < {threshold_wr:.0f}% ({w_stats['win']}W/{w_stats['loss']}L)"
        print(f"🚫 [LEARNER GATEKEEPER] Pola '{canonical_name}' DITOLAK! {msg}")
        return False, msg, wr

    return True, f"VALID_{active_window} (WR {wr:.1f}%)", wr

def get_multi_timeframe_summary() -> Dict[str, Any]:
    """
    Menghasilkan ringkasan lengkap win rate multi-timeframe (Daily, Weekly, Monthly, All-time)
    untuk laporan notifikasi Telegram dan dashboard.
    """
    stats = get_stats()
    all_history: List[Dict[str, Any]] = []
    
    category_summaries: List[Dict[str, Any]] = []
    
    for cat_name, data in stats.items():
        hist = data.get("history", [])
        all_history.extend(hist)
        
        daily = calculate_window_stats(hist, "DAILY")
        weekly = calculate_window_stats(hist, "WEEKLY")
        all_time = calculate_window_stats(hist, "ALL_TIME")
        
        category_summaries.append({
            "name": cat_name,
            "daily": daily,
            "weekly": weekly,
            "all_time": all_time,
            "total_all": data.get("total", 0)
        })
        
    # Sort categories by total trades descending
    category_summaries.sort(key=lambda x: x["total_all"], reverse=True)
    
    global_daily = calculate_window_stats(all_history, "DAILY")
    global_weekly = calculate_window_stats(all_history, "WEEKLY")
    global_monthly = calculate_window_stats(all_history, "MONTHLY")
    global_all_time = calculate_window_stats(all_history, "ALL_TIME")
    
    return {
        "global": {
            "daily": global_daily,
            "weekly": global_weekly,
            "monthly": global_monthly,
            "all_time": global_all_time,
        },
        "categories": category_summaries,
    }

def reset_pattern_blacklist() -> int:
    """
    Mereset statistik loss pada history untuk memberikan awal baru segar bagi semua pola.
    """
    stats = get_stats()
    count = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for cat, data in stats.items():
        # Kosongkan history lama atau reset losses
        data["history"] = []
        data["win"] = 0
        data["loss"] = 0
        data["total"] = 0
        data["reset_at"] = now_str
        count += 1
    save_stats(stats)
    return count

