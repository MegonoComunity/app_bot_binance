import json
import os
from typing import Dict, Any

STATS_FILE = "data/pattern_stats.json"

def normalize_pattern_category(raw_pattern: str) -> str:
    """
    Menyeragamkan string alasan / sinyal menjadi kategori pola standar agregat
    agar statistik Win Rate AI Learner akurat dan tidak terpecah oleh angka desimal/harga unik.
    """
    if not raw_pattern or raw_pattern == "-":
        return "UNKNOWN"
        
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
        return raw_pattern[:40].strip()

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
            json.dump(stats, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan pattern_stats.json: {e}")

def record_trade_result(pattern_name: str, is_profit: bool) -> None:
    """
    Mencatat hasil dari sebuah pola / setup.
    Jika is_profit True, maka win bertambah. Jika False, loss bertambah.
    """
    if not pattern_name or pattern_name == "-":
        return
        
    canonical_name = normalize_pattern_category(pattern_name)
    stats = get_stats()
    
    if canonical_name not in stats:
        stats[canonical_name] = {"win": 0, "loss": 0, "total": 0}
        
    stats[canonical_name]["total"] += 1
    
    if is_profit:
        stats[canonical_name]["win"] += 1
    else:
        stats[canonical_name]["loss"] += 1
        
    save_stats(stats)
    wr = (stats[canonical_name]["win"] / stats[canonical_name]["total"]) * 100
    print(f"🧠 [LEARNER] Rekam jejak '{canonical_name}' diperbarui: Win: {stats[canonical_name]['win']}, Loss: {stats[canonical_name]['loss']} (WR: {wr:.1f}%)")

def is_pattern_reliable(pattern_name: str, min_samples: int = 3, min_winrate: float = 0.45) -> bool:
    """
    Mengecek apakah sebuah pola aman untuk digunakan berdasarkan riwayatnya.
    Jika sudah terjadi >= min_samples dan win rate < min_winrate (45%), maka pola ditolak (False).
    """
    if not pattern_name:
        return True
        
    canonical_name = normalize_pattern_category(pattern_name)
    stats = get_stats()
    if canonical_name not in stats:
        return True
        
    data = stats[canonical_name]
    total = data.get("total", 0)
    
    if total >= min_samples:
        win_rate = data["win"] / total
        if win_rate < min_winrate:
            print(f"🚫 [LEARNER] Pola '{canonical_name}' DITOLAK! Win Rate terlalu rendah: {win_rate*100:.1f}% dari {total} percobaan.")
            return False
            
    return True
