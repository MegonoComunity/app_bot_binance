import time

from indicators.rsi import calculate_rsi
from indicators.smart_buy import find_frequent_open_close_level, is_near_frequent_level
from indicators.trend import get_htf_trend


def analyze_daily_market(df, lookback_days, rsi_length, rsi_oversold, rsi_overbought, tolerance):
    df = df[df["close_time"] < int(time.time() * 1000)].tail(lookback_days).copy()
    if len(df) < 20:
        raise ValueError("Data Daily belum cukup; minimal 20 candle yang sudah close")

    df = calculate_rsi(df, length=rsi_length)
    last = df.iloc[-1]
    current_price = float(last["close"])
    rsi_value = float(last["RSI"])
    smart_level = find_frequent_open_close_level(df, lookback=lookback_days, tolerance=tolerance)
    near_level = is_near_frequent_level(current_price, smart_level, tolerance=tolerance)
    trend = get_htf_trend(df, period=min(14, len(df) - 1))

    if near_level and rsi_value <= rsi_oversold and trend in {"UPTREND", "SIDEWAYS"}:
        decision = "BUY WATCH"
    elif rsi_value >= rsi_overbought and trend in {"DOWNTREND", "SIDEWAYS"}:
        decision = "SHORT WATCH"
    else:
        decision = "WAIT"

    return {
        "candles": len(df),
        "current_price": current_price,
        "rsi": rsi_value,
        "trend": trend,
        "smart_level": smart_level.get("level"),
        "smart_touches": smart_level.get("touches", 0),
        "near_smart_level": near_level,
        "decision": decision,
    }