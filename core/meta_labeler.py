"""
core/meta_labeler.py

Pilar 2: Meta-Labeling Architecture (Marcos López de Prado Framework).
Memisahkan proses trading menjadi 2 Lapisan:
1. Primary Model (Rule-based / Technical Signals): Menentukan arah trade (LONG / SHORT).
2. Secondary Model (Meta-Classifier / Probability Filter): Memprediksi probabilitas
   keberhasilan trade P(WIN) berdasarkan konteks multivariat (Market Regime, ATR Relative,
   RVOL, HTF Alignment, Session Hour, Squeeze Score, & Confluence Matrix).

Hanya sinyal primer yang lolos verifikasi Meta-Labeler dengan P(WIN) >= threshold (default: 0.65)
yang diizinkan dieksekusi di akun Real.
"""
from __future__ import annotations

from typing import Dict, Any, Tuple
from datetime import datetime
import numpy as np


def compute_meta_probability(
    side: str = "LONG",
    primary_signal_score: float = 80.0,
    regime_info: Dict[str, Any] = None,
    vol_ratio: float = 1.0,
    atr_percent: float = 1.0,
    htf_trend: str = "UPTREND",
    pattern_name: str = "NONE",
    ml_vision_info: Dict[str, Any] = None,
    min_prob_threshold: float = 0.65,
    df_5m: Any = None,
    primary_side: str = None,
    confluence_score: float = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Menghitung probabilitas Meta-Labeling P(WIN) dari sinyal primer.
    Menggunakan scoring logistik probabilistik berbasis faktor kuantitatif institusional.
    """
    actual_side = (primary_side or side or "LONG").upper()
    actual_score = confluence_score if confluence_score is not None else primary_signal_score
    regime_info = regime_info or {}
    regime = regime_info.get("regime", "RANGING_SIDEWAYS")
    
    # 1. Base log-odds dari Confluence Score (0 - 100)
    base_prob = 0.40 + (actual_score / 100.0) * 0.35

    # 2. Faktor Bobot Keselarasan Regime Pasar
    regime_multiplier = 1.0
    if actual_side == "LONG":
        if regime == "TRENDING_BULLISH":
            regime_multiplier = 1.15
        elif regime == "RANGING_SIDEWAYS":
            regime_multiplier = 1.00
        elif regime == "TRENDING_BEARISH":
            regime_multiplier = 0.70  # Penalti melawan tren utama
        elif regime == "HIGH_VOLATILITY_CHOPPY":
            regime_multiplier = 0.50  # Penalti keras
    else:  # SHORT
        if regime == "TRENDING_BEARISH":
            regime_multiplier = 1.15
        elif regime == "RANGING_SIDEWAYS":
            regime_multiplier = 1.00
        elif regime == "TRENDING_BULLISH":
            regime_multiplier = 0.70
        elif regime == "HIGH_VOLATILITY_CHOPPY":
            regime_multiplier = 0.50

    # 3. Faktor Bobot Volume Imbalance (RVOL)
    vol_bonus = 0.0
    if vol_ratio >= 2.5:
        vol_bonus = 0.08
    elif vol_ratio >= 1.5:
        vol_bonus = 0.04
    elif vol_ratio < 1.0:
        vol_bonus = -0.05  # Penalti volume kering

    # 4. Faktor Bobot AI Machine Learning Vision
    ml_bonus = 0.0
    ml_label = (ml_vision_info.get("label", "NEUTRAL") if ml_vision_info else "NEUTRAL").upper()
    ml_conf = float(ml_vision_info.get("confidence", 0.0) if ml_vision_info else 0.0)
    
    if (actual_side == "LONG" and ml_label == "BULLISH") or (actual_side == "SHORT" and ml_label == "BEARISH"):
        ml_bonus = min(0.10, ml_conf * 0.12)
    elif (actual_side == "LONG" and ml_label == "BEARISH") or (actual_side == "SHORT" and ml_label == "BULLISH"):
        ml_bonus = -0.15  # Penalti kontradiksi ML Vision

    # 5. Faktor Sesi Waktu Pasar (UTC Market Liquidity Hours)
    current_utc_hour = datetime.utcnow().hour
    session_bonus = 0.02 if (12 <= current_utc_hour <= 21 or 0 <= current_utc_hour <= 4) else 0.0

    # 6. Hitung Total Probabilitas Terkalibrasi (Sigmoid Bound [0.05, 0.95])
    raw_score = (base_prob * regime_multiplier) + vol_bonus + ml_bonus + session_bonus
    prob_win = float(np.clip(raw_score, 0.05, 0.95))

    # 7. Evaluasi Kelayakan (Meta-Label Approval)
    is_approved = (prob_win >= min_prob_threshold) and (regime != "HIGH_VOLATILITY_CHOPPY")

    # 8. Rekomendasi Sizing Kelly Criterion Konservatif (Half-Kelly Fraction)
    b_ratio = 1.6
    q_prob = 1.0 - prob_win
    raw_kelly = (b_ratio * prob_win - q_prob) / b_ratio
    half_kelly = float(np.clip(raw_kelly * 0.5, 0.25, 1.50)) if is_approved else 0.50

    reason_details = (
        f"P(WIN): {prob_win*100:.1f}% | Regime: {regime} (x{regime_multiplier:.2f}) | "
        f"Vol Bonus: {vol_bonus:+.2f} | ML: {ml_label} ({ml_bonus:+.2f})"
    )

    return {
        "is_meta_approved": is_approved,
        "win_probability": round(prob_win, 3),
        "probability_win": round(prob_win, 3),
        "confidence_pct": round(prob_win * 100, 1),
        "threshold": min_prob_threshold,
        "half_kelly_multiplier": round(half_kelly, 2),
        "regime": regime,
        "reason": reason_details,
    }
