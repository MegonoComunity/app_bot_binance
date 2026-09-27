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

from indicators.market_structure import (
    analyze_market_structure,
    calculate_dynamic_swing_avwap,
    detect_ema21_pullback,
)
from indicators.sniper_volume import calculate_smc_sniper_volume
from indicators.smc_snr_channel import calculate_smc_structure_v2


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
    min_score_threshold: float = 60.0,
    pump_info: Optional[Dict[str, Any]] = None,
    near_smart_buy: bool = False,
    two_consecutive_candles: bool = False,
    compression_reversal: bool = False,
    ml_vision_info: Optional[Dict[str, Any]] = None,
    regime_info: Optional[Dict[str, Any]] = None,
    sniper_info: Optional[Dict[str, Any]] = None,
    smc_v2_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Menghitung skor konfluensi teknikal dan memberikan rincian pilar poin.
    Mendukung:
    - Smart Money Concepts Market Structure (HH, HL, LH, LL)
    - Market Regime Detection (Bullish Trend / Bearish Trend / Sideways / Choppy)
    - Dynamic Swing Anchored VWAP (AVWAP Fair Value)
    - EMA 21 Dynamic Pullback Reversal
    - PUMP RADAR momentum wave
    - Smart Buy Level & 2x Consecutive Candle Reversal / Base Compression Breakout
    - AI Machine Learning Vision (Candlestick CNN Inference)
    """
    breakdown = {}
    total_score = 0.0
    side = side.upper()

    is_pump_alert = bool(pump_info and (pump_info.get("is_alert") or pump_info.get("score", 0) >= 60) and side == "LONG")
    pump_score = float(pump_info.get("score", 0.0)) if pump_info else 0.0

    # Evaluasi Struktur Pasar SMC (HH, HL, LH, LL) & EMA 21 Pullback
    struct_info = analyze_market_structure(df_5m, window=2)
    avwap_info = calculate_dynamic_swing_avwap(df_5m, window=2)
    ema21_info = detect_ema21_pullback(df_5m)

    # Evaluasi SMC Sniper Elite V17 - Volume Profile & Institutional Accumulation
    if sniper_info is None and df_5m is not None and len(df_5m) >= 15:
        sniper_info = calculate_smc_sniper_volume(df_5m)
    elif sniper_info is None:
        sniper_info = {}

    # Evaluasi LnSNRCH.v2 - Quasimodo (QML), FVG, & Premium/Discount Zones
    if smc_v2_info is None and df_5m is not None and len(df_5m) >= 20:
        smc_v2_info = calculate_smc_structure_v2(df_5m)
    elif smc_v2_info is None:
        smc_v2_info = {}

    is_sniper_buy = bool(sniper_info.get("is_in_sniper_buy_zone", False))
    is_sniper_sell = bool(sniper_info.get("is_in_sniper_sell_zone", False))
    market_state = sniper_info.get("market_state", "MONITORING")
    buy_power = float(sniper_info.get("buy_power_pct", 50.0))
    sell_power = float(sniper_info.get("sell_power_pct", 50.0))

    qml_data = smc_v2_info.get("quasimodo", {})
    is_qml_buy = bool(qml_data.get("is_detected") and qml_data.get("pattern_type") == "BULLISH_QUASIMODO")
    is_qml_sell = bool(qml_data.get("is_detected") and qml_data.get("pattern_type") == "BEARISH_QUASIMODO")
    zone_data = smc_v2_info.get("zones", {})
    is_discount = bool(zone_data.get("is_discount", False))
    is_premium = bool(zone_data.get("is_premium", False))
    fvg_data = smc_v2_info.get("fvg", {})
    in_fvg = bool(fvg_data.get("in_fvg_zone", False))

    market_regime = struct_info.get("regime", "SIDEWAYS")
    regime_label = regime_info.get("regime", "RANGING_SIDEWAYS") if regime_info else "RANGING_SIDEWAYS"
    is_ema21_pullback = ema21_info.get("is_pullback", False)
    ema21_type = ema21_info.get("type")

    # Evaluasi ML Vision Candlestick Recognition
    ml_label = (ml_vision_info.get("label", "NEUTRAL") if ml_vision_info else "NEUTRAL").upper()
    ml_confidence = float(ml_vision_info.get("confidence", 0.0) if ml_vision_info else 0.0)
    is_ml_bullish = (ml_label == "BULLISH" and ml_confidence >= 0.55)
    is_ml_bearish = (ml_label == "BEARISH" and ml_confidence >= 0.55)
    is_ml_opposing = (side == "LONG" and is_ml_bearish and ml_confidence >= 0.70) or (side == "SHORT" and is_ml_bullish and ml_confidence >= 0.70)

    # ─── PILAR 1: Higher Timeframe (HTF) Alignment & Market Regime (Maks 25 Poin) ─────────────
    htf_points = 0.0
    if side == "LONG":
        if htf_trend == "UPTREND" or regime_label == "TRENDING_BULLISH" or market_regime == "UPTREND_HEALTHY":
            htf_points = 25.0
        elif htf_trend == "SIDEWAYS" or regime_label == "RANGING_SIDEWAYS" or struct_info.get("is_compression") or is_discount:
            htf_points = 20.0 if (is_pump_alert or is_qml_buy) else 15.0
        else: # DOWNTREND
            htf_points = 15.0 if (is_pump_alert and pump_score >= 65) or is_qml_buy else 5.0
    else: # SHORT
        if htf_trend == "DOWNTREND" or regime_label == "TRENDING_BEARISH" or market_regime == "DOWNTREND_HEALTHY":
            htf_points = 25.0
        elif htf_trend == "SIDEWAYS" or regime_label == "RANGING_SIDEWAYS" or struct_info.get("is_compression") or is_premium:
            htf_points = 15.0
        else: # UPTREND
            htf_points = 15.0 if is_qml_sell else 5.0

    breakdown["htf_alignment"] = {
        "points": htf_points,
        "max": 25.0,
        "trend": htf_trend,
        "regime": regime_label,
        "market_structure": market_regime,
        "structure_seq": struct_info.get("structure_sequence", []),
        "detail": f"HTF: {htf_trend} | Regime: {regime_label} | SMC: {market_regime} ({htf_points}/25)"
    }
    total_score += htf_points

    # ─── PILAR 2: Candlestick Pattern, ML Vision, S/R, AVWAP & Sniper Zone (Maks 25 Poin) ──
    sr_pattern_points = 0.0
    has_valid_pattern = bool(pattern_name and pattern_name not in ("NONE", "None", ""))
    
    if side == "LONG":
        if is_qml_buy:
            sr_pattern_points = 25.0  # LnSNRCH.v2 Bullish Quasimodo (QML Left Shoulder Reversal)
        elif is_sniper_buy:
            sr_pattern_points = 25.0  # SMC Sniper Buy Volume Zone (POC Reversal)
        elif near_smart_buy:
            sr_pattern_points = 25.0  # Level Smart Buy institusional 20D
        elif two_consecutive_candles and (near_support or near_lower_bb):
            sr_pattern_points = 25.0  # Reversal 2x candle hijau di area support / Lower BB
        elif compression_reversal and (near_support or near_lower_bb):
            sr_pattern_points = 25.0  # 3-5 candle kompresi body kecil + breakout hijau solid
        elif is_ml_bullish and (near_support or near_lower_bb):
            sr_pattern_points = 25.0  # Konfirmasi ML Vision Candlestick Bullish di Support
        elif is_ema21_pullback and ema21_type == "BULLISH_PULLBACK":
            sr_pattern_points = 25.0  # Golden Pullback EMA21 pada tren impulsif
        elif avwap_info.get("position_to_avwap") == "BULLISH_ABOVE_AVWAP" and near_support:
            sr_pattern_points = 25.0  # Fair Value AVWAP Support
        elif near_support and has_valid_pattern and pattern_type == "LONG":
            sr_pattern_points = 25.0  # Konfluensi sempurna: pola reversal persis di support
        elif is_pump_alert and (near_support or in_fvg or is_discount):
            sr_pattern_points = 25.0  # Pump breakout valid jika dari support/fvg
        elif near_support and near_lower_bb:
            sr_pattern_points = 20.0  # Support ganda: Lower BB + Price Support
        elif two_consecutive_candles or compression_reversal or is_pump_alert:
            sr_pattern_points = 18.0
        elif near_support or avwap_info.get("position_to_avwap") == "BULLISH_ABOVE_AVWAP" or in_fvg:
            sr_pattern_points = 16.0
        elif has_valid_pattern and pattern_type == "LONG":
            sr_pattern_points = 15.0
        elif near_lower_bb:
            sr_pattern_points = 12.0
        else:
            sr_pattern_points = 8.0
    else: # SHORT
        if is_qml_sell:
            sr_pattern_points = 25.0  # LnSNRCH.v2 Bearish Quasimodo (QML Left Shoulder Resistance)
        elif is_sniper_sell:
            sr_pattern_points = 25.0  # SMC Sniper Sell Volume Zone (POC Resistance)
        elif two_consecutive_candles and (near_resistance or near_upper_bb):
            sr_pattern_points = 25.0  # Reversal 2x candle merah di area resistance
        elif is_ml_bearish and (near_resistance or near_upper_bb):
            sr_pattern_points = 25.0  # Konfirmasi ML Vision Candlestick Bearish di Resistance
        elif is_ema21_pullback and ema21_type == "BEARISH_PULLBACK":
            sr_pattern_points = 25.0  # Golden Pullback EMA21 pada tren impulsif turun
        elif avwap_info.get("position_to_avwap") == "BEARISH_BELOW_AVWAP" and near_resistance:
            sr_pattern_points = 25.0  # Fair Value AVWAP Resistance
        elif near_resistance and has_valid_pattern and pattern_type == "SHORT":
            sr_pattern_points = 25.0
        elif near_resistance and near_upper_bb:
            sr_pattern_points = 20.0
        elif two_consecutive_candles:
            sr_pattern_points = 18.0
        elif near_resistance or avwap_info.get("position_to_avwap") == "BEARISH_BELOW_AVWAP" or in_fvg:
            sr_pattern_points = 16.0
        elif has_valid_pattern and pattern_type == "SHORT":
            sr_pattern_points = 15.0
        elif near_upper_bb:
            sr_pattern_points = 12.0
        else:
            sr_pattern_points = 8.0

    # Penalti jika ML Vision mendeteksi arah yang sangat bertentangan
    if is_ml_opposing:
        sr_pattern_points = max(0.0, sr_pattern_points - 10.0)

    pattern_desc = pattern_name or ("PUMP_BREAKOUT" if is_pump_alert else ("QUASIMODO_BUY" if (side == "LONG" and is_qml_buy) else ("QUASIMODO_SELL" if (side == "SHORT" and is_qml_sell) else ("SNIPER_BUY_VOL" if (side == "LONG" and is_sniper_buy) else ("SNIPER_SELL_VOL" if (side == "SHORT" and is_sniper_sell) else ("SMART_BUY" if near_smart_buy else ("2X_CANDLE_REVERSAL" if two_consecutive_candles else ("BASE_COMPRESSION_BREAKOUT" if compression_reversal else ("ML_VISION_" + ml_label if ml_confidence >= 0.6 else ("EMA21_PULLBACK" if is_ema21_pullback else "NONE"))))))))))
    breakdown["sr_and_pattern"] = {
        "points": sr_pattern_points,
        "max": 25.0,
        "pattern": pattern_desc,
        "ml_vision": {"label": ml_label, "confidence": round(ml_confidence, 2)},
        "avwap_bias": avwap_info.get("position_to_avwap"),
        "detail": f"Pattern: {pattern_desc} (ML: {ml_label} {ml_confidence:.0%}), S/R/QML/Sniper: {'YES' if (near_support or near_resistance or is_qml_buy or is_qml_sell or is_sniper_buy or is_sniper_sell or is_pump_alert or is_ema21_pullback or near_smart_buy or two_consecutive_candles or compression_reversal) else 'NO'}"
    }
    total_score += sr_pattern_points

    # ─── PILAR 3: Volume Spike & Orderflow / Institutional Delta (Maks 20 Poin) ──
    vol_points = 0.0
    if is_pump_alert or vol_ratio >= 3.0:
        vol_points = 20.0
    elif (side == "LONG" and market_state == "ACCUMULATION_READY") or (side == "SHORT" and market_state == "DISTRIBUTION"):
        vol_points = 20.0  # Institutional Smart Money Imbalance terdeteksi
    elif vol_ratio >= 2.0 or (side == "LONG" and buy_power >= 60.0) or (side == "SHORT" and sell_power >= 60.0):
        vol_points = 16.0
    elif vol_ratio >= 1.5 or (side == "LONG" and buy_power >= 53.0) or (side == "SHORT" and sell_power >= 53.0):
        vol_points = 12.0
    elif vol_ratio >= 1.2:
        vol_points = 8.0
    else:
        vol_points = 4.0

    breakdown["volume_pressure"] = {
        "points": vol_points,
        "max": 20.0,
        "vol_ratio": round(vol_ratio, 2),
        "orderflow_delta": f"Buy: {buy_power}% | Sell: {sell_power}%",
        "market_state": market_state,
        "detail": f"RVOL 5M: {vol_ratio:.2f}x | Flow: {sniper_info.get('market_state_label', market_state)}"
    }
    total_score += vol_points

    # ─── PILAR 4: Volatility Squeeze & Discount/Premium Zones (Maks 15 Poin) ───
    squeeze_points = 0.0
    if (side == "LONG" and is_discount) or (side == "SHORT" and is_premium):
        squeeze_points = 15.0  # Zona Diskon/Premium SMC Optimal
    elif breakout_info and isinstance(breakout_info, dict):
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
        "detail": f"Zone: {zone_data.get('current_zone', 'EQUILIBRIUM')} | Squeeze Score: {100.0 if is_pump_alert else (breakout_info.get('score', 0) if breakout_info else 0):.1f}/100"
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
        "detail": f"RSI Zone: {'Pump Momentum' if is_pump_alert else ('Oversold' if is_oversold else ('Overbought' if is_overbought else 'Neutral'))}"
    }
    total_score += rsi_points

    # SMC Sniper Volume Dedicated Breakdown
    breakdown["smc_sniper_volume"] = {
        "poc_price": sniper_info.get("poc_price"),
        "sniper_buy_price": sniper_info.get("sniper_buy_price"),
        "sniper_sell_price": sniper_info.get("sniper_sell_price"),
        "is_in_sniper_buy_zone": is_sniper_buy,
        "is_in_sniper_sell_zone": is_sniper_sell,
        "market_state": market_state,
        "market_state_label": sniper_info.get("market_state_label", "MONITORING"),
        "buy_power_pct": buy_power,
        "sell_power_pct": sell_power,
        "score_bonus": sniper_info.get("score_bonus", 0.0),
    }

    # SMC Structure LnSNRCH.v2 Dedicated Breakdown
    breakdown["smc_structure_v2"] = {
        "quasimodo": qml_data,
        "fvg": fvg_data,
        "zones": zone_data,
        "bos_signal": smc_v2_info.get("bos_signal"),
        "choch_signal": smc_v2_info.get("choch_signal"),
    }

    total_score = min(100.0, round(total_score, 1))
    is_approved = total_score >= min_score_threshold

    # Format ringkasan alasan konfluensi
    summary_reasons = []
    if htf_points >= 20.0:
        summary_reasons.append(f"HTF {htf_trend}")
    if is_qml_buy and side == "LONG":
        summary_reasons.append("👑 Quasimodo QML Buy")
    elif is_qml_sell and side == "SHORT":
        summary_reasons.append("👑 Quasimodo QML Sell")
    elif is_sniper_buy and side == "LONG":
        summary_reasons.append("🎯 SMC Sniper Buy Vol")
    elif is_sniper_sell and side == "SHORT":
        summary_reasons.append("🎯 SMC Sniper Sell Vol")
    elif sr_pattern_points >= 15.0:
        summary_reasons.append(f"Pola {pattern_name} @ S/R" if has_valid_pattern else "Valid S/R Zone")
    
    if market_state == "ACCUMULATION_READY" and side == "LONG":
        summary_reasons.append("🔥 Akumulasi Smart Money")
    elif vol_points >= 12.0:
        summary_reasons.append(f"Vol {vol_ratio:.1f}x")

    if is_discount and side == "LONG":
        summary_reasons.append("Discount Zone")
    elif is_premium and side == "SHORT":
        summary_reasons.append("Premium Zone")
    elif squeeze_points >= 10.0:
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
        "sniper_info": sniper_info,
        "smc_v2_info": smc_v2_info,
    }
