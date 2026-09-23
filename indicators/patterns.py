import pandas as pd
import numpy as np


# ─── POLA BULLISH (UNTUK POSISI BUY / LONG) ───────────────────────────────────

def check_hammer(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Syarat High-Probability Bullish Hammer:
    - Body kecil di bagian atas (<= 30% dari total range)
    - Shadow bawah panjang (>= 58% dari total range, min 2x body)
    - Shadow atas sangat pendek (<= 12% dari total range)
    - Harga ditutup di separuh atas range candle
    """
    range_total = high_p - low_p
    if range_total <= 0:
        return False
        
    body = abs(close_p - open_p)
    lower_shadow = min(open_p, close_p) - low_p
    upper_shadow = high_p - max(open_p, close_p)
    
    is_body_small = body <= (0.30 * range_total)
    is_lower_long = lower_shadow >= (0.58 * range_total) and (lower_shadow >= 2.0 * max(body, 1e-9))
    is_upper_short = upper_shadow <= (0.12 * range_total)
    is_closing_high = close_p >= (low_p + 0.5 * range_total)
    
    return is_body_small and is_lower_long and is_upper_short and is_closing_high


def check_bullish_engulfing(
    prev_open: float, prev_close: float, open_p: float, close_p: float,
    prev_vol: float = 0.0, curr_vol: float = 0.0
) -> bool:
    """
    Syarat High-Probability Bullish Engulfing:
    - Candle 1: Merah (prev_close < prev_open)
    - Candle 2: Hijau kuat (close_p > open_p)
    - Body Candle 2 menelan (engulf) body Candle 1 secara signifikan.
    - Volume candle 2 lebih besar dari candle 1 (jika data volume tersedia).
    """
    prev_is_red = prev_close < prev_open
    curr_is_green = close_p > open_p
    
    if not (prev_is_red and curr_is_green):
        return False
        
    prev_body = prev_open - prev_close
    curr_body = close_p - open_p
    
    is_engulfing = (close_p >= prev_open) and (open_p <= prev_close) and (curr_body >= prev_body * 1.02)
    
    vol_confirmed = True
    if prev_vol > 0 and curr_vol > 0:
        vol_confirmed = curr_vol >= prev_vol * 1.05
        
    return is_engulfing and vol_confirmed


def check_morning_star(
    o1: float, c1: float, o2: float, c2: float, o3: float, c3: float,
    h2: float, l2: float
) -> bool:
    """
    Syarat High-Probability Morning Star (3 Candles):
    - Candle 1: Merah solid / bearish
    - Candle 2: Body kecil di level bawah (spinning top / doji)
    - Candle 3: Hijau solid, menembus >= 50% body Candle 1
    """
    c1_is_red = c1 < o1
    body_1 = o1 - c1
    
    body_2 = abs(o2 - c2)
    range_2 = h2 - l2
    is_c2_small = body_2 <= (0.35 * range_2) if range_2 > 0 else True
    
    c3_is_green = c3 > o3
    midpoint_1 = (o1 + c1) / 2
    is_c3_strong = c3 > midpoint_1
    
    return c1_is_red and is_c2_small and c3_is_green and is_c3_strong


def check_piercing_line(o1: float, c1: float, o2: float, c2: float) -> bool:
    """
    Syarat Piercing Line (2 Candles Reversal Bullish):
    - Candle 1: Bearish signifikan
    - Candle 2: Dibuka di bawah low/close candle 1, lalu ditutup di atas midpoint candle 1 namun di bawah open candle 1.
    """
    if c1 >= o1 or c2 <= o2:
        return False
    midpoint_1 = (o1 + c1) / 2
    return (o2 <= c1) and (c2 > midpoint_1) and (c2 <= o1)


def check_tweezer_bottom(l1: float, l2: float, c1: float, o1: float, c2: float, o2: float) -> bool:
    """
    Syarat Tweezer Bottom (Double rejection di Support):
    - Low candle 1 dan low candle 2 hampir persis sama (toleransi <= 0.2%)
    - Candle 1 bearish atau doji, Candle 2 bullish
    """
    if l1 <= 0 or l2 <= 0:
        return False
    diff_ratio = abs(l1 - l2) / l1
    is_double_bottom = diff_ratio <= 0.002
    is_c1_bearish = c1 <= o1
    is_c2_bullish = c2 > o2
    return is_double_bottom and is_c1_bearish and is_c2_bullish


def check_consecutive_green_candles(df: pd.DataFrame, min_candles: int = 2) -> bool:
    """
    Syarat Konfirmasi Reversal Bullish:
    - min_candles candle terakhir semuanya berwarna hijau berturut-turut.
    - Candle terakhir ditutup lebih tinggi dari candle sebelumnya (higher close).
    """
    if len(df) < min_candles:
        return False
    
    for i in range(1, min_candles + 1):
        row = df.iloc[-i]
        if row['close'] <= row['open']:
            return False
            
    if min_candles >= 2:
        return df.iloc[-1]['close'] > df.iloc[-2]['close']
    return True


def is_small_body(open_p: float, high_p: float, low_p: float, close_p: float, threshold: float = 0.3) -> bool:
    """Mendeteksi apakah body candle kecil (<= threshold dari total range)."""
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    if range_total == 0:
        return True
    return body <= (threshold * range_total)


def is_large_body(open_p: float, high_p: float, low_p: float, close_p: float, threshold: float = 0.7) -> bool:
    """Mendeteksi apakah body candle besar (>= threshold dari total range)."""
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    if range_total == 0:
        return False
    return body >= (threshold * range_total)


def check_small_bodies_followed_by_green(df: pd.DataFrame, min_small_candles: int = 3) -> bool:
    """
    Syarat Base Consolidation Breakout (LONG):
    - 3 sampai 5 candle dengan body kecil berturut-turut (akumulasi / kompresi).
    - Diikuti oleh 1 candle terakhir berwarna hijau dengan body solid (breakout).
    """
    if len(df) < min_small_candles + 1:
        return False
        
    last_row = df.iloc[-1]
    last_o, last_c = last_row['open'], last_row['close']
    last_h, last_l = last_row['high'], last_row['low']
    is_last_green = last_c > last_o
    
    if not is_last_green:
        return False
        
    last_range = last_h - last_l
    last_body = last_c - last_o
    if last_range > 0 and (last_body / last_range < 0.45):
        return False
        
    small_count = 0
    for i in range(len(df)-2, -1, -1):
        row = df.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        if is_large_body(o, h, l, c):
            break
            
        if is_small_body(o, h, l, c):
            small_count += 1
            if small_count >= 5:
                break
        else:
            break
            
    return small_count >= min_small_candles


# ─── POLA BEARISH (UNTUK POSISI SHORT / JUAL) ──────────────────────────────────

def check_shooting_star(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Syarat Shooting Star (Bearish Reversal):
    - Body kecil di bagian bawah (<= 30% dari total range)
    - Upper shadow panjang (>= 58% dari total range, min 2x body)
    - Lower shadow sangat pendek (<= 12% dari total range)
    - Harga ditutup di separuh bawah range candle
    """
    range_tot = high_p - low_p
    if range_tot <= 0:
        return False
    body = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    
    is_body_small = body <= (0.30 * range_tot)
    is_upper_long = upper_shadow >= (0.58 * range_tot) and (upper_shadow >= 2.0 * max(body, 1e-9))
    is_lower_short = lower_shadow <= (0.12 * range_tot)
    is_closing_low = close_p <= (low_p + 0.5 * range_tot)
    
    return is_body_small and is_upper_long and is_lower_short and is_closing_low


def check_bearish_engulfing(
    prev_open: float, prev_close: float, open_p: float, close_p: float,
    prev_vol: float = 0.0, curr_vol: float = 0.0
) -> bool:
    """
    Syarat Bearish Engulfing:
    - Candle 1: Hijau (prev_close > prev_open)
    - Candle 2: Merah kuat (close_p < open_p)
    - Body Candle 2 menelan candle 1 secara signifikan.
    - Volume candle 2 lebih besar (jika tersedia).
    """
    prev_is_green = prev_close > prev_open
    curr_is_red = close_p < open_p
    if not (prev_is_green and curr_is_red):
        return False
        
    prev_body = prev_close - prev_open
    curr_body = open_p - close_p
    
    is_engulfing = (open_p >= prev_close) and (close_p <= prev_open) and (curr_body >= prev_body * 1.02)
    
    vol_confirmed = True
    if prev_vol > 0 and curr_vol > 0:
        vol_confirmed = curr_vol >= prev_vol * 1.05
        
    return is_engulfing and vol_confirmed


def check_evening_star(
    o1: float, c1: float, o2: float, c2: float, o3: float, c3: float,
    h2: float, l2: float
) -> bool:
    """
    Syarat High-Probability Evening Star (3 Candles Bearish Reversal):
    - Candle 1: Hijau solid / bullish
    - Candle 2: Body kecil di level atas (spinning top / doji)
    - Candle 3: Merah solid, menembus >= 50% body Candle 1 ke bawah
    """
    c1_is_green = c1 > o1
    body_1 = c1 - o1
    
    body_2 = abs(o2 - c2)
    range_2 = h2 - l2
    is_c2_small = body_2 <= (0.35 * range_2) if range_2 > 0 else True
    
    c3_is_red = c3 < o3
    midpoint_1 = (o1 + c1) / 2
    is_c3_strong = c3 < midpoint_1
    
    return c1_is_green and is_c2_small and c3_is_red and is_c3_strong


def check_dark_cloud_cover(o1: float, c1: float, o2: float, c2: float) -> bool:
    """
    Syarat Dark Cloud Cover (2 Candles Bearish Reversal):
    - Candle 1: Bullish signifikan
    - Candle 2: Dibuka di atas high/close candle 1, lalu ditutup di bawah midpoint candle 1 namun di atas open candle 1.
    """
    if c1 <= o1 or c2 >= o2:
        return False
    midpoint_1 = (o1 + c1) / 2
    return (o2 >= c1) and (c2 < midpoint_1) and (c2 >= o1)


def check_tweezer_top(h1: float, h2: float, c1: float, o1: float, c2: float, o2: float) -> bool:
    """
    Syarat Tweezer Top (Double rejection di Resistance):
    - High candle 1 dan high candle 2 hampir persis sama (toleransi <= 0.2%)
    - Candle 1 bullish atau doji, Candle 2 bearish
    """
    if h1 <= 0 or h2 <= 0:
        return False
    diff_ratio = abs(h1 - h2) / h1
    is_double_top = diff_ratio <= 0.002
    is_c1_bullish = c1 >= o1
    is_c2_bearish = c2 < o2
    return is_double_top and is_c1_bullish and is_c2_bearish


def check_hanging_man(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Syarat Hanging Man (Bearish Reversal di Puncak Uptrend):
    - Body kecil di bagian atas (<= 30% dari total range)
    - Lower shadow panjang (>= 58% dari total range, min 2x body)
    - Upper shadow sangat pendek (<= 12% dari total range)
    """
    range_tot = high_p - low_p
    if range_tot <= 0:
        return False
    body = abs(close_p - open_p)
    lower_shadow = min(open_p, close_p) - low_p
    upper_shadow = high_p - max(open_p, close_p)
    
    is_body_small = body <= (0.30 * range_tot)
    is_lower_long = lower_shadow >= (0.58 * range_tot) and (lower_shadow >= 2.0 * max(body, 1e-9))
    is_upper_short = upper_shadow <= (0.12 * range_tot)
    
    return is_body_small and is_lower_long and is_upper_short


def check_consecutive_red_candles(df: pd.DataFrame, min_candles: int = 2) -> bool:
    """
    Syarat Konfirmasi Reversal Bearish:
    - min_candles candle terakhir semuanya berwarna merah berturut-turut.
    - Candle terakhir ditutup lebih rendah dari candle sebelumnya (lower close).
    """
    if len(df) < min_candles:
        return False
    
    for i in range(1, min_candles + 1):
        row = df.iloc[-i]
        if row['close'] >= row['open']:
            return False
            
    if min_candles >= 2:
        return df.iloc[-1]['close'] < df.iloc[-2]['close']
    return True


def check_small_bodies_followed_by_red(df: pd.DataFrame, min_small_candles: int = 3) -> bool:
    """
    Syarat Base Consolidation Breakdown (SHORT):
    - 3 sampai 5 candle dengan body kecil berturut-turut (distribusi / kompresi).
    - Diikuti oleh 1 candle terakhir berwarna merah dengan body solid (breakdown).
    """
    if len(df) < min_small_candles + 1:
        return False
        
    last_row = df.iloc[-1]
    last_o, last_c = last_row['open'], last_row['close']
    last_h, last_l = last_row['high'], last_row['low']
    is_last_red = last_c < last_o
    
    if not is_last_red:
        return False
        
    last_range = last_h - last_l
    last_body = last_o - last_c
    if last_range > 0 and (last_body / last_range < 0.45):
        return False
        
    small_count = 0
    for i in range(len(df)-2, -1, -1):
        row = df.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        if is_large_body(o, h, l, c):
            break
            
        if is_small_body(o, h, l, c):
            small_count += 1
            if small_count >= 5:
                break
        else:
            break
            
    return small_count >= min_small_candles


# ─── FILTER ANTI-TRAP (PENCEGAHAN FALSE BREAKOUT/BREAKDOWN) ───────────────────

def is_bull_trap(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Mendeteksi Bull Trap: Candle dengan kenaikan tinggi namun sumbu atas (upper shadow) panjang
    (>= 50% dari range), mengindikasikan penolakan keras oleh seller.
    """
    range_tot = high_p - low_p
    if range_tot == 0:
        return False
        
    upper_shadow = high_p - max(open_p, close_p)
    return upper_shadow >= (0.50 * range_tot)


def is_bear_trap(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Mendeteksi Bear Trap: Candle dengan penurunan dalam namun sumbu bawah (lower shadow) panjang
    (>= 50% dari range), mengindikasikan penolakan keras oleh buyer (pantulan tajam).
    """
    range_tot = high_p - low_p
    if range_tot == 0:
        return False
        
    lower_shadow = min(open_p, close_p) - low_p
    return lower_shadow >= (0.50 * range_tot)


# ─── MASTER PATTERN DETECTOR ──────────────────────────────────────────────────

def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """
    Mendeteksi keberadaan pola Candlestick Reversal Tier-A (LONG / SHORT) dan Trap.
    Prioritas diberikan pada pola dengan konfirmasi volume tinggi (High Win-Rate).
    """
    if df.empty or len(df) < 5:
        return {"pattern": None, "type": None, "detected": False, "volume_ratio": 1.0, "is_high_quality": False}
        
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    prev2_row = df.iloc[-3]
    
    # Hitung Volume Surge Ratio (dibandingkan MA20 volume)
    vol_ma20 = df['volume'].tail(20).mean() if len(df) >= 20 else df['volume'].mean()
    vol_ratio = float(last_row['volume'] / vol_ma20) if vol_ma20 > 0 else 1.0
    has_vol_surge = vol_ratio >= 1.25

    # 1. Trap Checks
    if is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bull Trap",
            "type": "CLOSE_LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": True,
        }

    if is_bear_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bear Trap",
            "type": "CLOSE_SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": True,
        }

    # 2. Bullish Reversal Patterns (LONG)
    if check_hammer(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bullish Hammer",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_morning_star(
        prev2_row['open'], prev2_row['close'],
        prev_row['open'], prev_row['close'],
        last_row['open'], last_row['close'],
        prev_row['high'], prev_row['low']
    ):
        return {
            "pattern": "Morning Star",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": True,
        }

    if check_bullish_engulfing(
        prev_row['open'], prev_row['close'],
        last_row['open'], last_row['close'],
        prev_vol=prev_row.get('volume', 0.0),
        curr_vol=last_row.get('volume', 0.0)
    ):
        return {
            "pattern": "Bullish Engulfing",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_piercing_line(prev_row['open'], prev_row['close'], last_row['open'], last_row['close']):
        return {
            "pattern": "Piercing Line",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_tweezer_bottom(
        prev_row['low'], last_row['low'],
        prev_row['close'], prev_row['open'],
        last_row['close'], last_row['open']
    ):
        return {
            "pattern": "Tweezer Bottom",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_small_bodies_followed_by_green(df):
        return {
            "pattern": "3-5 Small Bodies Followed by Green",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    # 3. Bearish Reversal Patterns (SHORT)
    if check_shooting_star(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Shooting Star",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_evening_star(
        prev2_row['open'], prev2_row['close'],
        prev_row['open'], prev_row['close'],
        last_row['open'], last_row['close'],
        prev_row['high'], prev_row['low']
    ):
        return {
            "pattern": "Evening Star",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": True,
        }

    if check_bearish_engulfing(
        prev_row['open'], prev_row['close'],
        last_row['open'], last_row['close'],
        prev_vol=prev_row.get('volume', 0.0),
        curr_vol=last_row.get('volume', 0.0)
    ):
        return {
            "pattern": "Bearish Engulfing",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_dark_cloud_cover(prev_row['open'], prev_row['close'], last_row['open'], last_row['close']):
        return {
            "pattern": "Dark Cloud Cover",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_tweezer_top(
        prev_row['high'], last_row['high'],
        prev_row['close'], prev_row['open'],
        last_row['close'], last_row['open']
    ):
        return {
            "pattern": "Tweezer Top",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_hanging_man(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Hanging Man",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_small_bodies_followed_by_red(df):
        return {
            "pattern": "3-5 Small Bodies Followed by Red",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    return {"pattern": None, "type": None, "detected": False, "volume_ratio": vol_ratio, "is_high_quality": False}
