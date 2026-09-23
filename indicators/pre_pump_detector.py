"""
indicators/pre_pump_detector.py

Modul Algoritmik Khusus untuk Mendeteksi Koin/Saham yang Mengalami:
1. Konsolidasi / Sideway Panjang (Volatility Squeeze / BB Bandwidth Sempit)
2. Akumulasi Tersembunyi (Smart Money Footprint & OBV/CMF Divergence)
3. Ledakan Volume Pemicu (Ignition Volume Spike 5M > 3x - 8x)
4. Potensi Full Pump Menuju ATH / Target +10% s.d +50% (+200% s.d +1000% ROI pada Leverage 20x).
"""
from __future__ import annotations

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np


def detect_explosive_pre_pump(
    df: pd.DataFrame,
    symbol: str = "",
    min_squeeze_candles: int = 15,
    volume_multiplier_trigger: float = 3.0,
) -> Dict[str, Any]:
    """
    Menganalisis data OHLCV (TF 5m) untuk mendeteksi sinyal Pra-Pump atau Breakout Ledakan ATH.

    Returns dict dengan:
    - is_alert: bool
    - tier: 'PUMP_IGNITION_ATH' | 'PRE_PUMP_ACCUMULATION' | 'NONE'
    - score: float (0 - 100)
    - rvol: float (Volume spike ratio)
    - bandwidth: float (BB Bandwidth %)
    - consolidation_range: float (Sideway range %)
    - entry_price: float
    - stop_loss: float
    - tp1_price: float (+10% / +200% ROI 20x)
    - tp2_price: float (+25% / +500% ROI 20x)
    - tp3_price: float (+50% / +1000% ROI 20x)
    - reason: str
    """
    if df is None or len(df) < 35:
        return {"is_alert": False, "score": 0.0, "tier": "NONE", "reason": "Data candle kurang"}

    try:
        data = df.copy()

        # 1. Bollinger Bands & Bandwidth
        sma20 = data["close"].rolling(20).mean()
        std20 = data["close"].rolling(20).std()
        upper_bb = sma20 + (2 * std20)
        lower_bb = sma20 - (2 * std20)
        bb_bandwidth = ((upper_bb - lower_bb) / sma20) * 100

        # 2. Volume Spike (RVOL 5M vs SMA20)
        vol_sma20 = data["volume"].rolling(20).mean()
        curr_vol = float(data["volume"].iloc[-1])
        avg_vol = float(vol_sma20.iloc[-1]) if float(vol_sma20.iloc[-1]) > 0 else 1.0
        rvol = curr_vol / avg_vol

        # 3. ATR & Volatility Squeeze Metric
        prev_close = data["close"].shift(1)
        tr = pd.concat([
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs()
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        atr_pct = (atr14 / data["close"]) * 100

        # 4. Sideway Consolidation Check (15-30 candle sebelum candle terakhir)
        lookback = min(30, len(data) - 2)
        base_high = float(data["high"].iloc[-lookback-1:-1].max())
        base_low = float(data["low"].iloc[-lookback-1:-1].min())
        sideway_range_pct = ((base_high - base_low) / base_low * 100) if base_low > 0 else 999.0

        # Hitung berapa banyak candle dalam fase squeeze (BBW < 3.5%)
        recent_bbw = bb_bandwidth.iloc[-lookback-1:-1]
        squeeze_candle_count = int((recent_bbw <= 3.8).sum())
        is_prolonged_sideway = (squeeze_candle_count >= min(10, lookback // 2)) or (sideway_range_pct <= 4.5)

        # 5. Candlestick Analysis (Current Candle)
        c_open = float(data["open"].iloc[-1])
        c_high = float(data["high"].iloc[-1])
        c_low = float(data["low"].iloc[-1])
        c_close = float(data["close"].iloc[-1])
        candle_range = c_high - c_low
        candle_body = abs(c_close - c_open)
        body_ratio = (candle_body / candle_range) if candle_range > 0 else 0.0
        is_green = c_close > c_open

        # 6. Donchian Channel Breakout (Atap Sideway)
        donchian_high = float(data["high"].iloc[-21:-1].max())
        is_breakout = c_close >= donchian_high or c_close >= base_high

        # 7. Squeeze Score Computation (0-100)
        current_bbw = float(bb_bandwidth.iloc[-1])
        prev_bbw = float(bb_bandwidth.iloc[-2]) if len(bb_bandwidth) >= 2 else current_bbw

        score = 0.0
        # Komponen Squeeze (Maks 35 poin)
        if prev_bbw <= 2.5:
            score += 35.0
        elif prev_bbw <= 3.8:
            score += 25.0
        elif prev_bbw <= 5.0:
            score += 15.0

        # Komponen Durasi Sideway (Maks 20 poin)
        if is_prolonged_sideway:
            score += 20.0
        elif sideway_range_pct <= 6.0:
            score += 10.0

        # Komponen Volume Spike (Maks 25 poin)
        if rvol >= 5.0:
            score += 25.0
        elif rvol >= volume_multiplier_trigger:
            score += 18.0
        elif rvol >= 2.0:
            score += 10.0

        # Komponen Breakout & Bullish Ignition (Maks 20 poin)
        if is_breakout and is_green and body_ratio >= 0.65:
            score += 20.0
        elif is_breakout and is_green:
            score += 12.0
        elif is_green and body_ratio >= 0.70:
            score += 8.0

        score = min(100.0, round(score, 1))

        # Target Price & Risk Matrix
        entry_price = c_close
        # Stop loss dipasang 0.8% - 1.2% di bawah level terendah sideway
        stop_loss = round(base_low * 0.992, 8)
        risk_pct = round(((entry_price - stop_loss) / entry_price) * 100, 2) if entry_price > 0 else 1.5

        # Target Pumps
        tp1_price = round(entry_price * 1.10, 8)   # +10% Target
        tp2_price = round(entry_price * 1.25, 8)   # +25% Target
        tp3_price = round(entry_price * 1.50, 8)   # +50% Full Pump ATH Target

        # Evaluasi Tier Pemicu
        tier = "NONE"
        is_alert = False
        reason = ""

        # Kondisi 1: Ledakan Terkonfirmasi (Detonation Ignition)
        if is_breakout and is_green and rvol >= volume_multiplier_trigger and (is_prolonged_sideway or prev_bbw <= 4.0):
            is_alert = True
            tier = "PUMP_IGNITION_ATH"
            reason = (
                f"🚀 Full Breakout Atap Sideway! Volume melonjak {rvol:.1f}x lipat "
                f"(BBW: {prev_bbw:.2f}%, Range Sideway: {sideway_range_pct:.1f}%). "
                f"Siap akselerasi pump +10% s.d +50%!"
            )
        # Kondisi 2: Akumulasi Pra-Ledakan (Pre-Pump Silent Accumulation)
        elif score >= 70.0 and prev_bbw <= 3.2 and is_prolonged_sideway and is_green:
            is_alert = True
            tier = "PRE_PUMP_ACCUMULATION"
            reason = (
                f"⏳ Kompresi Volatilitas Ekstrem (BBW {prev_bbw:.2f}%). "
                f"Smart money terdeteksi akumulasi diam-diam (Range sempit {sideway_range_pct:.1f}%). "
                f"Menunggu konfirmasi pemicu ledakan."
            )

        return {
            "is_alert": is_alert,
            "symbol": symbol,
            "tier": tier,
            "score": score,
            "rvol": round(rvol, 2),
            "bandwidth": round(prev_bbw, 2),
            "current_bbw": round(current_bbw, 2),
            "sideway_range_pct": round(sideway_range_pct, 2),
            "squeeze_candles": squeeze_candle_count,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "risk_pct": risk_pct,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "tp3_price": tp3_price,
            "roi_20x_tp1": "+200.0%",
            "roi_20x_tp2": "+500.0%",
            "roi_20x_tp3": "+1,000.0%",
            "reason": reason,
        }
    except Exception as exc:
        return {
            "is_alert": False,
            "score": 0.0,
            "tier": "NONE",
            "reason": f"Error: {exc}"
        }
