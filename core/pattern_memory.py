"""
core/pattern_memory.py

Sistem Memori Pola Trading.
Setiap kali terjadi trade (entry), bot menyimpan 'sidik jari' kondisi indikator saat itu.
Ketika trade selesai (WIN/LOSS), hasilnya direkam bersama sidik jari tersebut.
Bot kemudian belajar: kombinasi indikator apa yang paling sering menghasilkan WIN.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional


MEMORY_FILE = "data/pattern_memory.json"
MIN_SAMPLES_TO_LEARN = 3     # Butuh minimal 3 data sebelum pattern dianggap valid
MIN_WIN_RATE_TO_USE  = 0.45  # Pattern hanya dipakai jika win rate >= 45%


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(MEMORY_FILE):
        return {"entries": [], "patterns": {}}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entries": [], "patterns": {}}


def _save(data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _fingerprint(conditions: dict) -> str:
    """
    Buat 'sidik jari' dari kondisi indikator saat entry.
    Format: 'HTF:UPTREND|BB:LOWER|RSI:OVERSOLD|PAT:HAMMER|BRK:YES'
    """
    htf   = conditions.get("htf_trend", "?")
    bb    = conditions.get("bb_zone", "?")          # LOWER / UPPER / MID
    rsi   = conditions.get("rsi_zone", "?")         # OVERSOLD / OVERBOUGHT / NEUTRAL
    pat   = conditions.get("pattern", "NONE")
    brk   = "YES" if conditions.get("is_breakout") else "NO"
    side  = conditions.get("side", "?")
    squeeze = f"SQ{int(conditions.get('squeeze_score', 0) // 20) * 20}"  # Bucketed 0,20,40,60,80,100
    return f"SIDE:{side}|HTF:{htf}|BB:{bb}|RSI:{rsi}|PAT:{pat}|BRK:{brk}|{squeeze}"


# ─── Public API ──────────────────────────────────────────────────────────────

def record_entry(
    symbol: str,
    side: str,
    entry_price: float,
    conditions: dict,
    alasan: str,
) -> str:
    """
    Simpan snapshot kondisi indikator saat entry.
    Mengembalikan entry_id agar bisa di-update nanti.
    """
    data = _load()
    fp = _fingerprint(conditions)
    entry_id = f"{symbol}_{int(datetime.now().timestamp())}"
    data["entries"].append({
        "id": entry_id,
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": fp,
        "alasan": alasan,
        "conditions": conditions,
        "result": None,   # Diisi saat trade selesai
        "pnl": None,
    })
    _save(data)
    return entry_id


def record_result(entry_id: str, is_win: bool, pnl: float) -> None:
    """
    Update hasil trade dan pelajari pola dari hasilnya.
    """
    data = _load()
    for entry in data["entries"]:
        if entry["id"] == entry_id:
            entry["result"] = "WIN" if is_win else "LOSS"
            entry["pnl"] = pnl
            break

    # Rebuild pattern stats dari semua entries yang sudah punya result
    patterns: dict = {}
    for entry in data["entries"]:
        if entry.get("result") is None:
            continue
        fp = entry["fingerprint"]
        if fp not in patterns:
            patterns[fp] = {
                "win": 0, "loss": 0, "total": 0,
                "total_pnl": 0.0,
                "conditions_sample": entry.get("conditions", {}),
                "last_seen": entry["time"],
            }
        patterns[fp]["total"] += 1
        patterns[fp]["total_pnl"] += float(entry.get("pnl") or 0)
        patterns[fp]["last_seen"] = entry["time"]
        if entry["result"] == "WIN":
            patterns[fp]["win"] += 1
        else:
            patterns[fp]["loss"] += 1

    data["patterns"] = patterns
    _save(data)
    print(f"[PATTERN MEMORY] Hasil direkam. Total pola dipelajari: {len(patterns)}")


def score_entry(conditions: dict, min_samples: int = MIN_SAMPLES_TO_LEARN) -> float:
    """
    Hitung skor kecocokan kondisi saat ini dengan pola-pola yang berhasil.
    Return: 0.0 - 100.0 (semakin tinggi, semakin cocok dengan pola WIN historis)
    """
    data = _load()
    patterns = data.get("patterns", {})
    if not patterns:
        return 50.0  # Belum ada data, netral

    fp = _fingerprint(conditions)

    # Exact match
    if fp in patterns:
        p = patterns[fp]
        if p["total"] >= min_samples:
            win_rate = p["win"] / p["total"]
            avg_pnl = p["total_pnl"] / p["total"]
            score = win_rate * 80 + (20 if avg_pnl > 0 else 0)
            return round(min(score, 100.0), 1)

    # Partial match — cari pola mirip (5 dari 6 komponen sama)
    fp_parts = set(fp.split("|"))
    best_score = 50.0
    for stored_fp, p in patterns.items():
        if p["total"] < min_samples:
            continue
        stored_parts = set(stored_fp.split("|"))
        overlap = len(fp_parts & stored_parts)
        if overlap >= 5:   # 5/6 komponen sama = sangat mirip
            win_rate = p["win"] / p["total"]
            sim_score = (overlap / 6) * win_rate * 100
            if sim_score > best_score:
                best_score = sim_score

    return round(best_score, 1)


def get_top_patterns(top_n: int = 10) -> list[dict]:
    """
    Dapatkan daftar pola terbaik berdasarkan win rate (min 3 sampel).
    """
    data = _load()
    patterns = data.get("patterns", {})
    result = []
    for fp, p in patterns.items():
        if p["total"] < MIN_SAMPLES_TO_LEARN:
            continue
        wr = p["win"] / p["total"]
        result.append({
            "fingerprint": fp,
            "win": p["win"],
            "loss": p["loss"],
            "total": p["total"],
            "win_rate": round(wr * 100, 1),
            "avg_pnl": round(p["total_pnl"] / p["total"], 4),
            "last_seen": p["last_seen"],
        })
    result.sort(key=lambda x: (x["win_rate"], x["total"]), reverse=True)
    return result[:top_n]


def is_pattern_blacklisted(conditions: dict) -> bool:
    """
    Cek apakah pola ini masuk daftar hitam (win rate < threshold setelah >= 3 sampel).
    """
    data = _load()
    patterns = data.get("patterns", {})
    fp = _fingerprint(conditions)
    if fp not in patterns:
        return False
    p = patterns[fp]
    if p["total"] >= MIN_SAMPLES_TO_LEARN:
        wr = p["win"] / p["total"]
        if wr < MIN_WIN_RATE_TO_USE:
            return True
    return False
