"""
indicators/smc_snr_channel.py

LnSNRCH.v2 - Advanced Smart Money Concepts (SMC), Quasimodo Pattern (QML),
Dynamic S/R Channels, Fair Value Gaps (FVG), Order Blocks (OB), and Premium/Discount Zones.
Dikonversi dari TradingView Pine Script v5 ke Python Asynchronous.

Komponen Utama:
1. Smart Money Structure (Swing & Internal BOS/CHoCH, Strong/Weak High-Low).
2. Order Blocks (OB) & Fair Value Gaps (FVG) dengan Deteksi Mitigasi.
3. Equal Highs / Lows (EQH / EQL - Liquidity Pools).
4. Premium & Discount Zones (Fibonacci 50% Equilibrium & Discount Buy Area).
5. Quasimodo Pattern (QML) Reversal (Left Shoulder Entry).
6. Multi-Touch Dynamic Support & Resistance Channels with Breakout Detection.
7. Linear Regression Trend Channel.
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np


def detect_quasimodo_pattern(
    df: pd.DataFrame,
    zigzag_len: int = 13,
) -> Dict[str, Any]:
    """
    Mendeteksi pola Quasimodo (QML / Over-Under Pattern) sesuai algoritma LnSNRCH.v2.
    
    Logika Quasimodo Bullish (QML Buy):
    - Tren sebelumnya turun (trend == -1).
    - Terbentuk Swing High 2 (h2) > Swing High 1 (h1).
    - Terbentuk Swing Low 1 (l1) > Swing Low 0 (l0) (Lower Low dibuat untuk sweep likuiditas).
    - Terbentuk Swing High 0 (h0) > Swing High 1 (h1) (Higher High baru / Break of Structure).
    - Harga retest di area Left Shoulder (l1) -> Entry BUY.
    
    Logika Quasimodo Bearish (QML Sell):
    - Tren sebelumnya naik (trend == 1).
    - Terbentuk Swing Low 2 (l2) < Swing Low 1 (l1).
    - Terbentuk Swing High 1 (h1) < Swing High 0 (h0) (Higher High dibuat untuk sweep likuiditas).
    - Terbentuk Swing Low 0 (l0) < Swing Low 1 (l1) (Lower Low baru / Break of Structure).
    - Harga retest di area Left Shoulder (h1) -> Entry SELL.
    """
    if df is None or len(df) < (zigzag_len * 3):
        return {
            "is_detected": False,
            "pattern_type": None,
            "entry_level": None,
            "detail": "Data candle tidak cukup untuk Quasimodo.",
        }

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    # Identifikasi titik-titik Zigzag Extremes
    high_points: List[float] = []
    high_indices: List[int] = []
    low_points: List[float] = []
    low_indices: List[int] = []

    trend = 1  # 1: Up, -1: Down

    for i in range(zigzag_len, n):
        window_highs = highs[max(0, i - zigzag_len):i + 1]
        window_lows = lows[max(0, i - zigzag_len):i + 1]

        to_up = highs[i] >= np.max(window_highs)
        to_down = lows[i] <= np.min(window_lows)

        prev_trend = trend
        if trend == 1 and to_down:
            trend = -1
        elif trend == -1 and to_up:
            trend = 1

        if trend != prev_trend:
            if trend == 1:
                # Titik terendah saat swing down
                sub_lows = lows[max(0, i - zigzag_len):i + 1]
                min_idx = max(0, i - zigzag_len) + int(np.argmin(sub_lows))
                low_points.append(float(lows[min_idx]))
                low_indices.append(min_idx)
            else:
                # Titik tertinggi saat swing up
                sub_highs = highs[max(0, i - zigzag_len):i + 1]
                max_idx = max(0, i - zigzag_len) + int(np.argmax(sub_highs))
                high_points.append(float(highs[max_idx]))
                high_indices.append(max_idx)

    if len(high_points) < 3 or len(low_points) < 3:
        return {
            "is_detected": False,
            "pattern_type": None,
            "entry_level": None,
            "detail": "Jumlah pivot zigzag belum mencukupi (minimal 3 high & 3 low).",
        }

    h0 = high_points[-1]
    h1 = high_points[-2]
    h2 = high_points[-3]

    l0 = low_points[-1]
    l1 = low_points[-2]
    l2 = low_points[-3]

    curr_close = float(closes[-1])

    # Bullish QM: trend == -1 and h2 > h1 and l1 > l0 and h0 > h1 and close > l1
    bu_cond = (trend == -1) and (h2 > h1) and (l1 > l0) and (h0 > h1) and (curr_close >= l1 * 0.998)
    
    # Bearish QM: trend == 1 and l2 < l1 and h1 < h0 and l0 < l1 and close < h1
    be_cond = (trend == 1) and (l2 < l1) and (h1 < h0) and (l0 < l1) and (curr_close <= h1 * 1.002)

    if bu_cond:
        return {
            "is_detected": True,
            "pattern_type": "BULLISH_QUASIMODO",
            "entry_level": round(l1, 6),
            "stop_loss_level": round(l0, 6),
            "take_profit_level": round(h0, 6),
            "detail": f"🔥 Bullish Quasimodo (QML Buy Level @ Left Shoulder: {l1:.6f}, Invalidation: {l0:.6f})",
        }
    elif be_cond:
        return {
            "is_detected": True,
            "pattern_type": "BEARISH_QUASIMODO",
            "entry_level": round(h1, 6),
            "stop_loss_level": round(h0, 6),
            "take_profit_level": round(l0, 6),
            "detail": f"🔻 Bearish Quasimodo (QML Sell Level @ Left Shoulder: {h1:.6f}, Invalidation: {h0:.6f})",
        }

    return {
        "is_detected": False,
        "pattern_type": None,
        "entry_level": None,
        "detail": "Tidak ada pola Quasimodo aktif.",
    }


def calculate_fair_value_gaps(
    df: pd.DataFrame,
    threshold_atr_mult: float = 0.5,
) -> Dict[str, Any]:
    """
    Mendeteksi Fair Value Gaps (FVG) / Ketidakseimbangan Harga (Imbalance) 3-Candle.
    - Bullish FVG: Low[0] > High[2] (ada gap harga yang belum terisi).
    - Bearish FVG: High[0] < Low[2] (ada gap harga jatuh yang belum terisi).
    """
    if df is None or len(df) < 3:
        return {
            "has_bullish_fvg": False,
            "has_bearish_fvg": False,
            "active_fvgs": [],
            "in_fvg_zone": False,
        }

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    n = len(df)

    active_fvgs: List[Dict[str, Any]] = []
    curr_price = float(closes[-1])

    # Cari 50 bar terakhir untuk FVG aktif yang belum terisi (mitigated)
    lookback = min(50, n)
    for i in range(max(2, n - lookback), n):
        if i < 2:
            continue
        c_low = lows[i]
        c_high = highs[i]
        l2_high = highs[i - 2]
        l2_low = lows[i - 2]

        # Bullish FVG
        if c_low > l2_high and closes[i - 1] > l2_high:
            gap_top = float(c_low)
            gap_bottom = float(l2_high)
            # Cek apakah sudah termitigasi oleh candle sesudahnya
            sub_lows = lows[i + 1:] if i + 1 < n else np.array([])
            is_mitigated = len(sub_lows) > 0 and np.min(sub_lows) <= gap_bottom
            if not is_mitigated:
                active_fvgs.append({
                    "type": "BULLISH_FVG",
                    "top": gap_top,
                    "bottom": gap_bottom,
                    "mid": (gap_top + gap_bottom) / 2.0,
                    "bar_index": i,
                })

        # Bearish FVG
        elif c_high < l2_low and closes[i - 1] < l2_low:
            gap_top = float(l2_low)
            gap_bottom = float(c_high)
            sub_highs = highs[i + 1:] if i + 1 < n else np.array([])
            is_mitigated = len(sub_highs) > 0 and np.max(sub_highs) >= gap_top
            if not is_mitigated:
                active_fvgs.append({
                    "type": "BEARISH_FVG",
                    "top": gap_top,
                    "bottom": gap_bottom,
                    "mid": (gap_top + gap_bottom) / 2.0,
                    "bar_index": i,
                })

    has_bullish_fvg = any(f["type"] == "BULLISH_FVG" for f in active_fvgs)
    has_bearish_fvg = any(f["type"] == "BEARISH_FVG" for f in active_fvgs)

    # Cek apakah harga saat ini berada di dalam salah satu FVG
    in_bull_fvg = any(f["type"] == "BULLISH_FVG" and f["bottom"] <= curr_price <= f["top"] for f in active_fvgs)
    in_bear_fvg = any(f["type"] == "BEARISH_FVG" and f["bottom"] <= curr_price <= f["top"] for f in active_fvgs)

    return {
        "has_bullish_fvg": has_bullish_fvg,
        "has_bearish_fvg": has_bearish_fvg,
        "in_bullish_fvg": in_bull_fvg,
        "in_bearish_fvg": in_bear_fvg,
        "in_fvg_zone": in_bull_fvg or in_bear_fvg,
        "active_fvgs": active_fvgs[-5:],  # 5 FVG terdekat
    }


def calculate_premium_discount_zones(
    df: pd.DataFrame,
    lookback: int = 50,
) -> Dict[str, Any]:
    """
    Menghitung Premium, Equilibrium, dan Discount Zones berdasarkan Swing Extremes.
    - Premium Zone (>50% Fib): Area mahal, cocok untuk SELL / Take Profit Long.
    - Equilibrium (50% Fair Value): Titik keseimbangan.
    - Discount Zone (<50% Fib): Area murah, cocok untuk BUY / Akumulasi Institusi.
    """
    if df is None or len(df) < 15:
        return {
            "current_zone": "EQUILIBRIUM",
            "is_discount": False,
            "is_premium": False,
            "equilibrium_price": None,
            "swing_high": None,
            "swing_low": None,
        }

    actual_lookback = min(len(df), lookback)
    sub_df = df.iloc[-actual_lookback:]
    hi = float(sub_df["high"].max())
    lo = float(sub_df["low"].max()) if float(sub_df["high"].max()) == float(sub_df["low"].min()) else float(sub_df["low"].min())
    curr_p = float(df["close"].iloc[-1])

    if hi == lo:
        return {
            "current_zone": "EQUILIBRIUM",
            "is_discount": False,
            "is_premium": False,
            "equilibrium_price": curr_p,
            "swing_high": hi,
            "swing_low": lo,
        }

    equilibrium = (hi + lo) / 2.0
    discount_threshold = lo + (hi - lo) * 0.45   # <45% rentang
    premium_threshold = lo + (hi - lo) * 0.55    # >55% rentang

    if curr_p <= discount_threshold:
        zone = "DISCOUNT"
    elif curr_p >= premium_threshold:
        zone = "PREMIUM"
    else:
        zone = "EQUILIBRIUM"

    return {
        "current_zone": zone,
        "is_discount": zone == "DISCOUNT",
        "is_premium": zone == "PREMIUM",
        "is_equilibrium": zone == "EQUILIBRIUM",
        "equilibrium_price": round(equilibrium, 6),
        "swing_high": round(hi, 6),
        "swing_low": round(lo, 6),
        "discount_threshold": round(discount_threshold, 6),
        "premium_threshold": round(premium_threshold, 6),
    }


def calculate_smc_structure_v2(
    df: pd.DataFrame,
    swing_length: int = 50,
    internal_length: int = 5,
) -> Dict[str, Any]:
    """
    Engine SMC Lengkap LnSNRCH.v2:
    - Swing Structure (BOS, CHoCH, Strong/Weak High-Low).
    - Internal Structure.
    - Quasimodo Pattern (QML).
    - Fair Value Gaps (FVG).
    - Premium & Discount Zones.
    """
    if df is None or len(df) < 20:
        return {
            "is_valid": False,
            "trend": "SIDEWAYS",
            "bos_signal": None,
            "choch_signal": None,
            "strong_weak_bias": "NEUTRAL",
            "quasimodo": {},
            "fvg": {},
            "zones": {},
            "score_bonus": 0.0,
            "summary": "Data candle tidak cukup",
        }

    qml_info = detect_quasimodo_pattern(df, zigzag_len=13)
    fvg_info = calculate_fair_value_gaps(df)
    zone_info = calculate_premium_discount_zones(df, lookback=swing_length)

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    curr_close = float(closes[-1])

    # Hitung Swing Pivots
    n = len(df)
    sh_val = float(np.max(highs[-swing_length:])) if n >= swing_length else float(np.max(highs))
    sl_val = float(np.min(lows[-swing_length:])) if n >= swing_length else float(np.min(lows))

    # EMA 50 untuk trend bias
    ema50 = float(df["close"].ewm(span=50, adjust=False).mean().iloc[-1])
    is_uptrend = curr_close > ema50

    # BOS & CHoCH Detection
    bos_signal = None
    choch_signal = None
    
    prev_close = float(closes[-2]) if len(closes) >= 2 else curr_close
    
    if prev_close <= sh_val and curr_close > sh_val:
        if is_uptrend:
            bos_signal = "BULLISH_BOS"
        else:
            choch_signal = "BULLISH_CHOCH"
    elif prev_close >= sl_val and curr_close < sl_val:
        if not is_uptrend:
            bos_signal = "BEARISH_BOS"
        else:
            choch_signal = "BEARISH_CHOCH"

    # Strong / Weak Extremes (LnSNRCH.v2 rule: Dalam Uptrend, Low adalah Strong, High adalah Weak / Liquidity target)
    if is_uptrend:
        strong_level = sl_val
        weak_level = sh_val
        strong_weak_bias = "STRONG_LOW_BULLISH"
    else:
        strong_level = sh_val
        weak_level = sl_val
        strong_weak_bias = "STRONG_HIGH_BEARISH"

    # Hitung Skor Bonus Konfluensi SMC
    score_bonus = 0.0
    reasons = []

    if qml_info.get("is_detected"):
        score_bonus += 20.0
        reasons.append(qml_info["pattern_type"])

    if choch_signal:
        score_bonus += 15.0
        reasons.append(f"CHoCH Reversal ({choch_signal})")
    elif bos_signal:
        score_bonus += 10.0
        reasons.append(f"BOS Continuation ({bos_signal})")

    if zone_info.get("is_discount"):
        score_bonus += 10.0
        reasons.append("Discount Zone (Cheap Value)")
    elif zone_info.get("is_premium"):
        score_bonus += 10.0
        reasons.append("Premium Zone (High Value)")

    if fvg_info.get("in_fvg_zone"):
        score_bonus += 10.0
        reasons.append("Tapped Fair Value Gap (FVG)")

    score_bonus = min(score_bonus, 25.0)

    return {
        "is_valid": True,
        "trend": "UPTREND" if is_uptrend else "DOWNTREND",
        "bos_signal": bos_signal,
        "choch_signal": choch_signal,
        "strong_weak_bias": strong_weak_bias,
        "strong_level": round(strong_level, 6),
        "weak_level": round(weak_level, 6),
        "quasimodo": qml_info,
        "fvg": fvg_info,
        "zones": zone_info,
        "score_bonus": score_bonus,
        "reasons": reasons,
        "summary": " + ".join(reasons) if reasons else "Struktur Netral",
    }
