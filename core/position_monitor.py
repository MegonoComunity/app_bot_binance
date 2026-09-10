from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from typing import Literal

from indicators.bollinger import calculate_bollinger_bands
from indicators.rsi import calculate_rsi
from indicators.patterns import detect_candlestick_patterns
from indicators.trend import get_htf_trend


@dataclass
class PositionAnalysisResult:
    decision: Literal['HOLD', 'CLOSE']
    confidence: float
    reasons: list[str]
    alerts: list[str]
    health_score: float


def analyze_position(
    side: Literal['LONG', 'SHORT'],
    entry_price: float,
    current_price: float,
    df: pd.DataFrame,
    df_htf: pd.DataFrame,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 75.0,
    rsi_length: int = 14,
    close_score_threshold: float = 60.0,
) -> PositionAnalysisResult:
    reasons: list[str] = []
    alerts: list[str] = []
    close_score = 0.0

    if df.empty or df_htf.empty or len(df) < 20:
        return PositionAnalysisResult('HOLD', 0.0, [], ['Data tidak cukup'], 100.0)

    df = calculate_bollinger_bands(df)
    df = calculate_rsi(df, length=rsi_length)

    last = df.iloc[-1]
    rsi = float(last.get('RSI', 50))
    upper_band = float(last.get('upper_band', 0) or 0)
    lower_band = float(last.get('lower_band', 0) or 0)
    is_near_upper = current_price >= upper_band * 0.995 if upper_band > 0 else False
    is_near_lower = current_price <= lower_band * 1.005 if lower_band > 0 else False

    htf_trend = get_htf_trend(df_htf)
    pattern_info = detect_candlestick_patterns(df)

    unrealized_pct = (current_price - entry_price) / entry_price * 100

    if side == 'LONG':
        if htf_trend == 'DOWNTREND':
            close_score += 30
            reasons.append(f'HTF berbalik DOWNTREND')
        if rsi > rsi_overbought:
            close_score += 25
            reasons.append(f'RSI Overbought ({rsi:.1f})')
        if is_near_upper:
            close_score += 20
            reasons.append('Harga di Upper Bollinger Band')
        if pattern_info['detected'] and pattern_info['type'] == 'SHORT':
            close_score += 25
            reasons.append(f"Pola Bearish: {pattern_info['pattern']}")
        if rsi > 65:
            alerts.append(f'RSI mulai tinggi ({rsi:.1f})')
        if unrealized_pct > 0 and close_score >= 30:
            alerts.append('Pertimbangkan ambil sebagian profit')
    else:
        if htf_trend == 'UPTREND':
            close_score += 30
            reasons.append(f'HTF berbalik UPTREND')
        if rsi < rsi_oversold:
            close_score += 25
            reasons.append(f'RSI Oversold ({rsi:.1f})')
        if is_near_lower:
            close_score += 20
            reasons.append('Harga di Lower Bollinger Band')
        if pattern_info['detected'] and pattern_info['type'] == 'LONG':
            close_score += 25
            reasons.append(f"Pola Bullish: {pattern_info['pattern']}")
        if rsi < 40:
            alerts.append(f'RSI mulai rendah ({rsi:.1f})')
        if unrealized_pct > 0 and close_score >= 30:
            alerts.append('Pertimbangkan ambil sebagian profit')

    if unrealized_pct < -15:
        close_score += 20
        alerts.append(f'Posisi rugi {unrealized_pct:.2f}%, mendekati SL')

    candle_body = abs(float(last['close']) - float(last['open']))
    candle_range = float(last['high']) - float(last['low'])
    if candle_range > 0 and candle_body / candle_range > 0.7:
        is_bearish_candle = float(last['close']) < float(last['open'])
        if side == 'LONG' and is_bearish_candle:
            close_score += 10
            alerts.append('Candle bearish body besar')
        elif side == 'SHORT' and not is_bearish_candle:
            close_score += 10
            alerts.append('Candle bullish body besar')

    close_score = min(close_score, 100.0)
    health_score = max(0.0, 100.0 - close_score)
    confidence = min(close_score / close_score_threshold * 100, 100.0)

    decision = 'CLOSE' if close_score >= close_score_threshold else 'HOLD'

    return PositionAnalysisResult(
        decision=decision,
        confidence=round(confidence, 1),
        reasons=reasons,
        alerts=alerts,
        health_score=round(health_score, 1),
    )
