"""
core/confluence_engine.py

Sistem Penilaian Konfluensi Multi-Indikator Profesional (Smart Confluence Scoring Matrix).
Menilai kelayakan setup trade dari skala 0 s.d 100 berdasarkan 5 Pilar Analisis:
1. Higher Timeframe Alignment (1D / 4H / 1H Trend) -> Maks 25 Poin
2. Reversal / Continuation Candlestick Pattern di Support/Resistance -> Maks 25 Poin
3. Volume Anomaly & Orderflow Pressure (RVOL 5M vs SMA20) -> Maks 20 Poin
4. Volatility Squeeze / Pre-Pump Compression (BB Bandwidth & Squeeze) -> Maks 15 Poin
5. Momentum & RSI Divergence Zone -> Maks 15 Poin

Hanya trade dengan Total Skor >= min_confluence_score (Default: 80) yang diizinkan dieksekusi.
"""
from __future__ import annotations

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np


def calculate_confluence_score(
    df_5m: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    df_daily: Optional[pd.DataFrame] = None,
    side: str = "LONG",
    pattern_name: Optional[str] = None,
    pattern_type: Optional[str] = None,
    near_support: bool = False,
    near_resistance: bool = False,
    near_lower_bb: bool = False,
    near_upper_bb: bool = False,
    is_oversold: bool = False,
    is_overbought: bool = False,
    vol_ratio: float = 1.0,
    breakout_info: Optional[Dict[str, Any]] = None,
    htf_trend: str = "SIDEWAYS",
    min_score_threshold: float = 80.0,
) -> Dict[str, Any]:
    """
    Menghitung skor konfluensi teknikal dan memberikan rincian pilar poin.
    """
    breakdown = {}
    total_score = 0.0
    side = side.upper()

    # ─── PILAR 1: Higher Timeframe (HTF) Alignment (Maks 25 Poin) ─────────────
    htf_points = 0.0
    if side == "LONG":
        if htf_trend == "UPTREND":
            htf_points = 25.0
        elif htf_trend == "SIDEWAYS":
            htf_points = 12.5
        else: # DOWNTREND
            htf_points = 0.0
    else: # SHORT
        if htf_trend == "DOWNTREND":
            htf_points = 25.0
        elif htf_trend == "SIDEWAYS":
            htf_points = 12.5
        else: # UPTREND
            htf_points = 0.0

    breakdown["htf_alignment"] = {
        "points": htf_points,
        "max": 25.0,
        "trend": htf_trend,
        "detail": f"HTF Trend {htf_trend} ({htf_points}/25)"
    }
    total_score += htf_points

    # ─── PILAR 2: Candlestick Pattern & S/R Validation (Maks 25 Poin) ─────────
    sr_pattern_points = 0.0
    has_valid_pattern = bool(pattern_name and pattern_name not in ("NONE", "None", ""))
    
    if side == "LONG":
        if near_support and has_valid_pattern and pattern_type == "LONG":
            sr_pattern_points = 25.0  # Konfluensi sempurna: pola reversal persis di support
        elif near_support and near_lower_bb:
            sr_pattern_points = 20.0  # Support ganda: Lower BB + Price Support
        elif near_support:
            sr_pattern_points = 15.0
        elif has_valid_pattern and pattern_type == "LONG":
            sr_pattern_points = 12.0
        elif near_lower_bb:
            sr_pattern_points = 8.0
    else: # SHORT
        if near_resistance and has_valid_pattern and pattern_type == "SHORT":
            sr_pattern_points = 25.0
        elif near_resistance and near_upper_bb:
            sr_pattern_points = 20.0
        elif near_resistance:
            sr_pattern_points = 15.0
        elif has_valid_pattern and pattern_type == "SHORT":
            sr_pattern_points = 12.0
        elif near_upper_bb:
            sr_pattern_points = 8.0

    breakdown["sr_and_pattern"] = {
        "points": sr_pattern_points,
        "max": 25.0,
        "pattern": pattern_name or "NONE",
        "detail": f"Pattern: {pattern_name or 'None'}, S/R Touch: {'YES' if (near_support or near_resistance) else 'NO'}"
    }
    total_score += sr_pattern_points

    # ─── PILAR 3: Volume Spike & Orderflow Pressure (Maks 20 Poin) ───────────
    vol_points = 0.0
    if vol_ratio >= 4.0:
        vol_points = 20.0
    elif vol_ratio >= 2.5:
        vol_points = 16.0
    elif vol_ratio >= 1.8:
        vol_points = 12.0
    elif vol_ratio >= 1.2:
        vol_points = 8.0
    else:
        vol_points = 4.0

    breakdown["volume_pressure"] = {
        "points": vol_points,
        "max": 20.0,
        "vol_ratio": round(vol_ratio, 2),
        "detail": f"RVOL 5M: {vol_ratio:.2f}x SMA20"
    }
    total_score += vol_points

    # ─── PILAR 4: Volatility Squeeze & Pre-Pump Compression (Maks 15 Poin) ───
    squeeze_points = 0.0
    if breakout_info and isinstance(breakout_info, dict):
        b_score = float(breakout_info.get("score", 0.0))
        is_ready = bool(breakout_info.get("ready", False))
        is_sq = bool(breakout_info.get("squeeze", False))
        
        if is_ready or b_score >= 75.0:
            squeeze_points = 15.0
        elif is_sq or b_score >= 50.0:
            squeeze_points = 10.0
        elif b_score >= 30.0:
            squeeze_points = 5.0

    breakdown["volatility_squeeze"] = {
        "points": squeeze_points,
        "max": 15.0,
        "detail": f"Squeeze Breakout Score: {breakout_info.get('score', 0) if breakout_info else 0:.1f}/100"
    }
    total_score += squeeze_points

    # ─── PILAR 5: Momentum & RSI Zone (Maks 15 Poin) ───────────────────────────
    rsi_points = 0.0
    if side == "LONG":
        if is_oversold:
            rsi_points = 15.0  # RSI oversold < 35
        elif near_lower_bb:
            rsi_points = 10.0
        else:
            rsi_points = 5.0
    else: # SHORT
        if is_overbought:
            rsi_points = 15.0  # RSI overbought > 75
        elif near_upper_bb:
            rsi_points = 10.0
        else:
            rsi_points = 5.0

    breakdown["rsi_momentum"] = {
        "points": rsi_points,
        "max": 15.0,
        "detail": f"RSI Zone: {'Oversold' if is_oversold else ('Overbought' if is_overbought else 'Neutral')}"
    }
    total_score += rsi_points

    total_score = min(100.0, round(total_score, 1))
    is_approved = total_score >= min_score_threshold

    # Format ringkasan alasan konfluensi
    summary_reasons = []
    if htf_points >= 20.0:
        summary_reasons.append(f"HTF {htf_trend}")
    if sr_pattern_points >= 15.0:
        summary_reasons.append(f"Pola {pattern_name} @ S/R" if has_valid_pattern else "Valid S/R Zone")
    if vol_points >= 12.0:
        summary_reasons.append(f"Vol {vol_ratio:.1f}x")
    if squeeze_points >= 10.0:
        summary_reasons.append("Squeeze Breakout")
    if rsi_points >= 10.0:
        summary_reasons.append("RSI Optimal")

    confluence_summary = " + ".join(summary_reasons) if summary_reasons else "Indikator Standar"

    return {
        "is_approved": is_approved,
        "total_score": total_score,
        "score": total_score,
        "threshold": min_score_threshold,
        "grade": "TIER-A (PRO CONFLUENCE)" if total_score >= 85 else ("TIER-B (SOLID)" if total_score >= 75 else "TIER-C (WEAK)"),
        "summary": confluence_summary,
        "breakdown": breakdown,
    }
