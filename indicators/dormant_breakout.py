import pandas as pd


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def calculate_dormant_breakout_score(
    df: pd.DataFrame,
    volume_multiplier: float = 2.0,
    atr_percentile_limit: float = 35.0,
) -> dict:
    """Score a closed-candle squeeze followed by a confirmed breakout."""
    if len(df) < 50:
        return {"score": 0, "ready": False, "reason": "insufficient_data"}

    data = df.copy()
    middle = data["close"].rolling(20).mean()
    deviation = data["close"].rolling(20).std()
    bbw = ((middle + 2 * deviation) - (middle - 2 * deviation)) / middle
    atr = _atr(data)
    atr_percentile = atr.rank(pct=True) * 100
    donchian_high = data["high"].rolling(20).max().shift(1)
    donchian_low = data["low"].rolling(20).min().shift(1)
    volume_average = data["volume"].rolling(20).mean()
    volume_ratio = data["volume"] / volume_average

    obv_direction = ((data["close"].diff().fillna(0).where(data["close"].diff() != 0, 0))
                     .where(data["close"].diff() > 0, -data["volume"])
                     .where(data["close"].diff() < 0, data["volume"]))
    obv = obv_direction.cumsum()
    obv_rising = obv.iloc[-1] > obv.rolling(10).mean().iloc[-1]

    fast_ema = data["close"].ewm(span=12, adjust=False).mean()
    slow_ema = data["close"].ewm(span=26, adjust=False).mean()
    macd = fast_ema - slow_ema
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_flip = macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]

    last = data.iloc[-1]
    is_squeeze = bbw.iloc[-2] <= bbw.iloc[-22:-2].quantile(0.35)
    is_low_atr = atr_percentile.iloc[-2] <= atr_percentile.iloc[-22:-2].quantile(0.35)
    is_breakout = last["close"] > donchian_high.iloc[-1] or last["close"] < donchian_low.iloc[-1]
    has_volume_spike = volume_ratio.iloc[-1] >= volume_multiplier
    score = sum([
        is_squeeze,
        is_low_atr,
        is_breakout,
        has_volume_spike,
        bool(obv_rising),
        bool(macd_flip),
    ]) * 100 / 6

    return {
        "score": round(float(score), 1),
        "ready": bool(is_breakout and has_volume_spike and (is_squeeze or is_low_atr)),
        "squeeze": bool(is_squeeze),
        "low_atr": bool(is_low_atr),
        "breakout": bool(is_breakout),
        "volume_spike": round(float(volume_ratio.iloc[-1]), 2),
        "obv_rising": bool(obv_rising),
        "macd_flip": bool(macd_flip),
        "atr_percentile": round(float(atr_percentile.iloc[-1]), 1),
    }