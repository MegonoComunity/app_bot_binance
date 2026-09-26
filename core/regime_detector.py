"""
core/regime_detector.py

Pilar 1: Market Regime Detection System.
Mengklasifikasikan kondisi pasar secara dinamis menjadi 4 Regime:
1. TRENDING_BULLISH  : Tren naik kuat (ADX >= 25, DI+ > DI-, EMA Alignment). Cocok untuk Trend-Following / Pre-Pump / Breakout LONG.
2. TRENDING_BEARISH  : Tren turun kuat (ADX >= 25, DI- > DI+, EMA Alignment). Cocok untuk Trend-Following Breakdown SHORT.
3. RANGING_SIDEWAYS  : Pasar mendatar / konsolidasi (ADX < 20, BB Width rendah). Cocok untuk Mean Reversion / S&R Bounce / Bollinger Reversal.
4. HIGH_VOLATILITY_CHOPPY : Volatilitas ekstrem / noise liar (Normalized ATR >> normal, Whipsaw). Hindari entri untuk mencegah stop-out!
"""
from __future__ import annotations

from typing import Dict, Any
import numpy as np
import pandas as pd


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Menghitung ADX (Average Directional Index), +DI, dan -DI."""
    df_res = pd.DataFrame(index=df.index)
    
    high = df['high']
    low = df['low']
    close = df['close']
    
    # 1. True Range (TR)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # 2. Directional Movement (+DM, -DM)
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)
    
    # 3. Smoothed TR, +DM, -DM
    atr = tr.rolling(window=period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=period).mean() / atr.replace(0, np.nan))
    
    # 4. Directional Index (DX) & ADX
    dx_den = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * ((plus_di - minus_di).abs() / dx_den)
    adx = dx.rolling(window=period, min_periods=period).mean()
    
    df_res['ADX'] = adx.fillna(0.0)
    df_res['PLUS_DI'] = plus_di.fillna(0.0)
    df_res['MINUS_DI'] = minus_di.fillna(0.0)
    return df_res


def detect_market_regime(
    df_5m: pd.DataFrame,
    adx_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
) -> Dict[str, Any]:
    """
    Menganalisis dan menentukan Market Regime terkini.
    Mengembalikan label regime, metrik kuantitatif (ADX, BBW, Relative ATR), dan kecocokan strategi.
    """
    if df_5m is None or len(df_5m) < max(adx_period * 2, bb_period + 10):
        return {
            "regime": "RANGING_SIDEWAYS",
            "adx": 15.0,
            "plus_di": 20.0,
            "minus_di": 20.0,
            "bb_width_pct": 2.0,
            "relative_atr": 1.0,
            "is_trade_allowed": True,
            "recommended_strategy": "ANY",
            "reason": "Data candle minimal belum cukup (Fallback Ranging)",
        }

    df = df_5m.copy()
    
    # 1. Hitung ADX & DI
    adx_df = calculate_adx(df, period=adx_period)
    last_adx = float(adx_df['ADX'].iloc[-1])
    last_plus_di = float(adx_df['PLUS_DI'].iloc[-1])
    last_minus_di = float(adx_df['MINUS_DI'].iloc[-1])
    
    # 2. Hitung Bollinger Band Width (BBW)
    mid_bb = df['close'].rolling(window=bb_period).mean()
    std_bb = df['close'].rolling(window=bb_period).std()
    upper_bb = mid_bb + (std_bb * bb_std)
    lower_bb = mid_bb - (std_bb * bb_std)
    bb_width_pct = ((upper_bb - lower_bb) / mid_bb.replace(0, np.nan) * 100).iloc[-1]
    bb_width_pct = float(bb_width_pct) if pd.notnull(bb_width_pct) else 2.0
    
    # 3. Hitung Relative ATR (Volatilitas Relatif 5M vs SMA 30 ATR)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    current_atr = tr.rolling(window=atr_period).mean().iloc[-1]
    baseline_atr = tr.rolling(window=atr_period * 3).mean().iloc[-1]
    
    relative_atr = float(current_atr / baseline_atr) if baseline_atr > 0 else 1.0
    
    # 4. Moving Average Alignment
    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    last_close = df['close'].iloc[-1]
    
    # ─── Logika Klasifikasi Market Regime ───
    # A. Anomaly / High Volatility Choppy
    if relative_atr >= 2.2 or (bb_width_pct >= 6.5 and last_adx < 20):
        regime = "HIGH_VOLATILITY_CHOPPY"
        is_allowed = False
        strategy = "STAND_ASIDE"
        reason = f"Volatilitas liar (Rel ATR: {relative_atr:.2f}x, BBW: {bb_width_pct:.1f}%). Risiko whipsaw tinggi."

    # B. Trending Bullish
    elif last_adx >= 23.0 and last_plus_di > last_minus_di and last_close >= ema20 >= ema50:
        regime = "TRENDING_BULLISH"
        is_allowed = True
        strategy = "TREND_FOLLOWING_LONG"
        reason = f"Tren naik kuat (ADX: {last_adx:.1f}, +DI: {last_plus_di:.1f} > -DI: {last_minus_di:.1f})."

    # C. Trending Bearish
    elif last_adx >= 23.0 and last_minus_di > last_plus_di and last_close <= ema20 <= ema50:
        regime = "TRENDING_BEARISH"
        is_allowed = True
        strategy = "TREND_FOLLOWING_SHORT"
        reason = f"Tren turun kuat (ADX: {last_adx:.1f}, -DI: {last_minus_di:.1f} > +DI: {last_plus_di:.1f})."

    # D. Ranging / Sideways Consolidation
    else:
        regime = "RANGING_SIDEWAYS"
        is_allowed = True
        strategy = "MEAN_REVERSION_S_R"
        reason = f"Konsolidasi / Sideways (ADX: {last_adx:.1f} < 23, BBW: {bb_width_pct:.2f}%)."

    regime_label = regime.replace("_", " ").title()
    return {
        "regime": regime,
        "regime_label": regime_label,
        "adx": round(last_adx, 2),
        "plus_di": round(last_plus_di, 2),
        "minus_di": round(last_minus_di, 2),
        "bb_width_pct": round(bb_width_pct, 2),
        "relative_atr": round(relative_atr, 2),
        "is_trade_allowed": is_allowed,
        "is_suitable_for_trade": is_allowed,
        "recommended_strategy": strategy,
        "reason": reason,
    }


def validate_regime_strategy_match(
    regime_info: Dict[str, Any] | str = None,
    side: str = "LONG",
    strategy_type: str = "TREND",
    is_breakout: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """
    Memvalidasi apakah strategi entri selaras dengan regime pasar saat ini.
    Mencegah entri Trend-Following di pasar Sideways, atau Reversal di pasar Choppy Anomaly.
    """
    if isinstance(regime_info, dict):
        regime = regime_info.get("regime", "RANGING_SIDEWAYS")
        adx_val = regime_info.get("adx", 20.0)
    elif isinstance(regime_info, str):
        regime = regime_info
        adx_val = 20.0
    else:
        regime = "RANGING_SIDEWAYS"
        adx_val = 20.0

    side = side.upper()
    if is_breakout:
        strategy_type = "BREAKOUT"

    # Veto keras jika High Volatility Choppy
    if regime == "HIGH_VOLATILITY_CHOPPY":
        return {
            "is_valid": False,
            "reason": f"Veto Regime: Pasar sedang dalam kondisi {regime} (Noise Liar).",
        }

    # Validasi keselarasan
    if "TREND" in strategy_type or "BREAKOUT" in strategy_type or "PUMP" in strategy_type:
        if side == "LONG" and regime == "TRENDING_BEARISH":
            return {
                "is_valid": False,
                "reason": f"Veto Regime: Mencoba Breakout LONG di tengah {regime}.",
            }
        if side == "SHORT" and regime == "TRENDING_BULLISH":
            return {
                "is_valid": False,
                "reason": f"Veto Regime: Mencoba Breakdown SHORT di tengah {regime}.",
            }

    if "REVERSAL" in strategy_type or "OVERSOLD" in strategy_type:
        if side == "LONG" and regime == "TRENDING_BEARISH" and adx_val >= 35:
            return {
                "is_valid": False,
                "reason": f"Veto Regime: Dilarang Tangkap Pisau Jatuh di Strong {regime} (ADX: {adx_val}).",
            }

    return {
        "is_valid": True,
        "reason": f"Regime {regime} selaras dengan strategi {strategy_type}.",
    }
