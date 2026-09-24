"""
indicators/market_structure.py

Modul Analisis Struktur Pasar Institusional & Smart Money Concepts (SMC):
1. Healthy Trend Structure:
   - Uptrend Sehat: HL -> HH -> HL -> HH (Higher Lows & Higher Highs berturut-turut)
   - Downtrend Sehat: LH -> LL -> LH -> LL (Lower Highs & Lower Lows berturut-turut)
   - Major Consolidation / Large Balance Area / Structural Compression:
     Pola zigzag campuran (HH -> LH -> HL -> LL -> HL -> LH) di mana harga sedang bertarung di Fair Value.
2. Dynamic Swing Anchored VWAP (AVWAP):
   - Menghitung Anchored VWAP dari titik Swing High / Low mayor terakhir untuk menemukan garis keseimbangan Fair Value institusi.
3. EMA 21 Dynamic Pullback:
   - Mendeteksi konsolidasi kecil saat harga impulsif sedang 'istirahat' / retest menyentuh EMA 21 sebelum melanjutkan rally.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np


def detect_swing_points(df: pd.DataFrame, window: int = 3) -> List[Dict[str, Any]]:
    """
    Mendeteksi titik-titik Swing High (SH) dan Swing Low (SL) dari data OHLCV.
    """
    if df.empty or len(df) < (window * 2 + 1):
        return []

    highs = df['high'].values
    lows = df['low'].values
    timestamps = df['timestamp'].values if 'timestamp' in df.columns else np.arange(len(df))

    swing_points = []
    n = len(df)

    for i in range(window, n - window):
        # Cek Swing High
        is_sh = True
        for w in range(1, window + 1):
            if highs[i] < highs[i - w] or highs[i] < highs[i + w]:
                is_sh = False
                break
        if is_sh:
            swing_points.append({
                "index": i,
                "type": "HIGH",
                "price": float(highs[i]),
                "timestamp": timestamps[i],
            })

        # Cek Swing Low
        is_sl = True
        for w in range(1, window + 1):
            if lows[i] > lows[i - w] or lows[i] > lows[i + w]:
                is_sl = False
                break
        if is_sl:
            swing_points.append({
                "index": i,
                "type": "LOW",
                "price": float(lows[i]),
                "timestamp": timestamps[i],
            })

    swing_points.sort(key=lambda x: x["index"])
    return swing_points


def analyze_market_structure(df: pd.DataFrame, window: int = 3) -> Dict[str, Any]:
    """
    Menganalisis sekuens struktur High dan Low (HH, HL, LH, LL) untuk menentukan:
    - UPTREND_HEALTHY (HL -> HH -> HL -> HH)
    - DOWNTREND_HEALTHY (LH -> LL -> LH -> LL)
    - MAJOR_CONSOLIDATION / COMPRESSION (Balance Area / Fair Value pertarungan harga)
    """
    if df.empty or len(df) < 15:
        return {
            "regime": "UNKNOWN",
            "structure_sequence": [],
            "last_hl": None,
            "last_hh": None,
            "last_lh": None,
            "last_ll": None,
            "is_compression": False,
            "description": "Data tidak cukup untuk analisis struktur pasar.",
        }

    swings = detect_swing_points(df, window=window)
    if len(swings) < 4:
        return {
            "regime": "SIDEWAYS",
            "structure_sequence": [],
            "last_hl": None,
            "last_hh": None,
            "last_lh": None,
            "last_ll": None,
            "is_compression": False,
            "description": "Swing point belum cukup (minimal 4 swing points).",
        }

    high_swings = [s for s in swings if s["type"] == "HIGH"]
    low_swings = [s for s in swings if s["type"] == "LOW"]

    # Klasifikasikan Highs
    for i in range(1, len(high_swings)):
        prev_h = high_swings[i - 1]["price"]
        curr_h = high_swings[i]["price"]
        tag = "HH" if curr_h > prev_h else "LH"
        high_swings[i]["tag"] = tag

    # Klasifikasikan Lows
    for i in range(1, len(low_swings)):
        prev_l = low_swings[i - 1]["price"]
        curr_l = low_swings[i]["price"]
        tag = "HL" if curr_l > prev_l else "LL"
        low_swings[i]["tag"] = tag

    # Satukan kembali sekuens waktu
    tagged_swings = sorted(
        [s for s in (high_swings + low_swings) if "tag" in s],
        key=lambda x: x["index"]
    )
    sequence = [s["tag"] for s in tagged_swings]

    recent_seq = sequence[-6:] if len(sequence) >= 6 else sequence

    last_hh = next((s["price"] for s in reversed(high_swings) if s.get("tag") == "HH"), None)
    last_lh = next((s["price"] for s in reversed(high_swings) if s.get("tag") == "LH"), None)
    last_hl = next((s["price"] for s in reversed(low_swings) if s.get("tag") == "HL"), None)
    last_ll = next((s["price"] for s in reversed(low_swings) if s.get("tag") == "LL"), None)

    hh_count = recent_seq.count("HH")
    hl_count = recent_seq.count("HL")
    lh_count = recent_seq.count("LH")
    ll_count = recent_seq.count("LL")

    # Klasifikasi Rezim Pasar
    if hh_count >= 2 and hl_count >= 1 and lh_count == 0:
        regime = "UPTREND_HEALTHY"
        desc = f"Uptrend Sehat: Struktur {recent_seq} (Higher Highs & Higher Lows konsisten)."
        is_compression = False
    elif ll_count >= 2 and lh_count >= 1 and hl_count == 0:
        regime = "DOWNTREND_HEALTHY"
        desc = f"Downtrend Sehat: Struktur {recent_seq} (Lower Lows & Lower Highs konsisten)."
        is_compression = False
    elif ("HH" in recent_seq and "LH" in recent_seq) or ("HL" in recent_seq and "LL" in recent_seq):
        regime = "MAJOR_CONSOLIDATION"
        desc = f"Major Balance Area: Struktur kompresi {recent_seq} (Harga sedang bertarung di Fair Value)."
        is_compression = True
    else:
        regime = "SIDEWAYS"
        desc = f"Struktur Transisi: {recent_seq}"
        is_compression = False

    return {
        "regime": regime,
        "structure_sequence": recent_seq,
        "full_sequence": sequence,
        "last_hl": last_hl,
        "last_hh": last_hh,
        "last_lh": last_lh,
        "last_ll": last_ll,
        "is_compression": is_compression,
        "description": desc,
    }


def calculate_anchored_vwap(df: pd.DataFrame, anchor_index: int) -> pd.Series:
    """
    Menghitung Anchored VWAP (AVWAP) mulai dari candle anchor_index hingga candle terakhir.
    Rumus: sum(Typical_Price * Volume) / sum(Volume) sejak anchor.
    """
    if df.empty or anchor_index < 0 or anchor_index >= len(df):
        return pd.Series(index=df.index, dtype=float)

    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    vol = df['volume'].replace(0, 1e-8)

    pv = typical_price * vol
    
    pv_anchored = pv.copy()
    vol_anchored = vol.copy()
    
    pv_anchored.iloc[:anchor_index] = 0.0
    vol_anchored.iloc[:anchor_index] = 0.0

    cum_pv = pv_anchored.cumsum()
    cum_vol = vol_anchored.cumsum()

    avwap = cum_pv / cum_vol
    avwap.iloc[:anchor_index] = np.nan
    return avwap


def calculate_dynamic_swing_avwap(df: pd.DataFrame, window: int = 3) -> Dict[str, Any]:
    """
    Menghitung Dynamic Swing Anchored VWAP dari Swing High dan Swing Low signifikan terakhir.
    """
    if df.empty or len(df) < 15:
        return {"avwap_high": None, "avwap_low": None, "current_price": 0.0, "position_to_avwap": "NEUTRAL"}

    swings = detect_swing_points(df, window=window)
    last_high_swing = next((s for s in reversed(swings) if s["type"] == "HIGH"), None)
    last_low_swing = next((s for s in reversed(swings) if s["type"] == "LOW"), None)

    current_price = float(df['close'].iloc[-1])

    avwap_high_val = None
    if last_high_swing:
        avwap_h_series = calculate_anchored_vwap(df, last_high_swing["index"])
        avwap_high_val = float(avwap_h_series.iloc[-1]) if pd.notnull(avwap_h_series.iloc[-1]) else None

    avwap_low_val = None
    if last_low_swing:
        avwap_l_series = calculate_anchored_vwap(df, last_low_swing["index"])
        avwap_low_val = float(avwap_l_series.iloc[-1]) if pd.notnull(avwap_l_series.iloc[-1]) else None

    # Tentukan bias posisi terhadap Anchored VWAP
    if avwap_low_val and current_price >= avwap_low_val:
        pos_bias = "BULLISH_ABOVE_AVWAP"
    elif avwap_high_val and current_price <= avwap_high_val:
        pos_bias = "BEARISH_BELOW_AVWAP"
    else:
        pos_bias = "FAIR_VALUE_BALANCE"

    return {
        "avwap_high": avwap_high_val,
        "avwap_low": avwap_low_val,
        "current_price": current_price,
        "position_to_avwap": pos_bias,
        "anchor_high_index": last_high_swing["index"] if last_high_swing else None,
        "anchor_low_index": last_low_swing["index"] if last_low_swing else None,
    }


def detect_ema21_pullback(df: pd.DataFrame, tolerance: float = 0.003) -> Dict[str, Any]:
    """
    Mendeteksi Pullback Konsolidasi Kecil ke Dinamis EMA 21:
    - Saat tren sedang impulsif, harga 'istirahat' sejenak mendekati EMA 21.
    - Candle menyentuh / retest EMA 21 dan membentuk rejection candle (EMA 21 Bounce).
    """
    if df.empty or len(df) < 25:
        return {
            "is_pullback": False,
            "type": None,
            "ema21": 0.0,
            "dist_pct": 0.0,
            "detail": "Data candle kurang untuk EMA21."
        }

    ema21_series = df['close'].ewm(span=21, adjust=False).mean()
    ema21_val = float(ema21_series.iloc[-1])
    
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    
    current_close = float(last_row['close'])
    current_low = float(last_row['low'])
    current_high = float(last_row['high'])
    current_open = float(last_row['open'])

    dist_pct = (current_close - ema21_val) / ema21_val

    # 1. EMA 21 Bullish Pullback (Impulsive Up, istirahat turun menyentuh EMA 21 lalu mantul)
    is_touch_ema21_low = (current_low <= ema21_val * (1 + tolerance)) and (current_close >= ema21_val * (1 - tolerance))
    is_bullish_bounce = is_touch_ema21_low and (current_close > current_open or current_close > float(prev_row['close']))
    
    # 2. EMA 21 Bearish Pullback (Impulsive Down, istirahat naik mendekat EMA 21 lalu reject ke bawah)
    is_touch_ema21_high = (current_high >= ema21_val * (1 - tolerance)) and (current_close <= ema21_val * (1 + tolerance))
    is_bearish_reject = is_touch_ema21_high and (current_close < current_open or current_close < float(prev_row['close']))

    if is_bullish_bounce:
        return {
            "is_pullback": True,
            "type": "BULLISH_PULLBACK",
            "ema21": ema21_val,
            "dist_pct": round(dist_pct * 100, 2),
            "detail": f"Golden Pullback EMA21: Harga retest & mantul di EMA21 ({ema21_val:.6f})",
        }
    elif is_bearish_reject:
        return {
            "is_pullback": True,
            "type": "BEARISH_PULLBACK",
            "ema21": ema21_val,
            "dist_pct": round(dist_pct * 100, 2),
            "detail": f"Golden Pullback EMA21: Harga retest & reject di EMA21 ({ema21_val:.6f})",
        }

    return {
        "is_pullback": False,
        "type": None,
        "ema21": ema21_val,
        "dist_pct": round(dist_pct * 100, 2),
        "detail": f"Jarak ke EMA21: {dist_pct * 100:+.2f}%",
    }
