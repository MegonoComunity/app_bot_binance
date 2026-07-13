import json
import os
from telegram.bot_handler import bot_state

STATS_FILE = "data/pattern_stats.json"

def get_stats():
    if not os.path.exists("data"):
        os.makedirs("data")
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_stats(stats):
    if not os.path.exists("data"):
        os.makedirs("data")
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=4)

def record_trade_result(pattern_name: str, is_profit: bool):
    """
    Mencatat hasil dari sebuah pattern.
    Jika is_profit True, maka win bertambah. Jika False, loss bertambah.
    """
    if not pattern_name or pattern_name == "-":
        return
        
    stats = get_stats()
    
    if pattern_name not in stats:
        stats[pattern_name] = {"win": 0, "loss": 0, "total": 0}
        
    stats[pattern_name]["total"] += 1
    
    if is_profit:
        stats[pattern_name]["win"] += 1
    else:
        stats[pattern_name]["loss"] += 1
        
    save_stats(stats)
    print(f"🧠 [LEARNER] Rekam jejak '{pattern_name}' diperbarui: Win: {stats[pattern_name]['win']}, Loss: {stats[pattern_name]['loss']}")

def is_pattern_reliable(pattern_name: str) -> bool:
    """
    Mengecek apakah sebuah pattern aman untuk digunakan.
    Minimal sudah terjadi 3x. Jika win rate < 40%, maka pattern dianggap buruk (False).
    """
    if not pattern_name:
        return True
        
    stats = get_stats()
    if pattern_name not in stats:
        return True
        
    data = stats[pattern_name]
    total = data["total"]
    
    if total >= 3:
        win_rate = data["win"] / total
        if win_rate < 0.4:
            print(f"🚫 [LEARNER] Pola '{pattern_name}' DITOLAK! Win Rate terlalu rendah: {win_rate*100:.1f}% dari {total} percobaan.")
            return False
            
    return True
