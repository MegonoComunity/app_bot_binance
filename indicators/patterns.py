import pandas as pd
import numpy as np

def check_hammer(open_p, high_p, low_p, close_p):
    """
    Syarat Hammer:
    - Body kecil di bagian atas (<= 30% dari total range)
    - Shadow bawah panjang (>= 60% dari total range)
    - Shadow atas pendek (<= 10% dari total range)
    """
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    
    if range_total == 0:
        return False
        
    lower_shadow = min(open_p, close_p) - low_p
    upper_shadow = high_p - max(open_p, close_p)
    
    is_body_small = body <= (0.3 * range_total)
    is_lower_long = lower_shadow >= (0.6 * range_total)
    is_upper_short = upper_shadow <= (0.1 * range_total)
    
    return is_body_small and is_lower_long and is_upper_short

def is_bull_trap(open_p, high_p, low_p, close_p):
    """
    Mendeteksi Bull Trap: Candle hijau dengan range besar namun ditutup 
    dengan sumbu atas (upper shadow) yang sangat panjang.
    """
    range_tot = high_p - low_p
    if range_tot == 0:
        return False
        
    is_green = close_p > open_p
    upper_shadow = high_p - close_p
    
    # Sumbu atas lebih panjang atau sama dengan setengah dari keseluruhan panjang candle
    return is_green and (upper_shadow >= 0.5 * range_tot)

def check_bullish_engulfing(prev_open, prev_close, open_p, close_p):
    """
    Syarat Bullish Engulfing:
    - Candle 1: Merah (prev_close < prev_open)
    - Candle 2: Hijau (close_p > open_p)
    - Body Candle 2 menutupi (engulf) Body Candle 1 sepenuhnya.
    """
    prev_is_red = prev_close < prev_open
    curr_is_green = close_p > open_p
    
    is_engulfing = (close_p > prev_open) and (open_p < prev_close)
    
    return prev_is_red and curr_is_green and is_engulfing

def check_morning_star(o1, c1, o2, c2, o3, c3, h2, l2):
    """
    Syarat Morning Star (3 Candles):
    - Candle 1: Merah kuat
    - Candle 2: Body kecil (keraguan)
    - Candle 3: Hijau kuat, menembus pertengahan body Candle 1
    """
    # Candle 1: Bearish
    c1_is_red = c1 < o1
    body_1 = abs(o1 - c1)
    
    # Candle 2: Body kecil (Doji / Spinning Top)
    body_2 = abs(o2 - c2)
    range_2 = h2 - l2
    is_c2_small = body_2 <= (0.3 * range_2) if range_2 > 0 else True
    
    # Candle 3: Bullish, close melewati 50% body candle 1
    c3_is_green = c3 > o3
    midpoint_1 = (o1 + c1) / 2
    is_c3_strong = c3 > midpoint_1
    
    return c1_is_red and is_c2_small and c3_is_green and is_c3_strong

def check_consecutive_small_marubozu_bullish(df: pd.DataFrame, min_consecutive: int = 3) -> bool:
    """
    Syarat:
    - 3 sampai 5 candle hijau berturut-turut.
    - Ukuran kecil dan hampir tidak ada sumbu atas/bawah (Marubozu-like).
    """
    if len(df) < min_consecutive:
        return False
        
    consecutive_count = 0
    
    for i in range(len(df)-1, -1, -1):
        row = df.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        is_green = c > o
        range_tot = h - l
        
        if range_tot == 0 or not is_green:
            break
            
        upper_shadow = h - c
        lower_shadow = o - l
        
        # Hampir tidak ada sumbu (toleransi <= 10% dari total range untuk masing-masing sumbu)
        is_no_shadows = (upper_shadow <= 0.1 * range_tot) and (lower_shadow <= 0.1 * range_tot)
        
        if is_no_shadows:
            consecutive_count += 1
            if consecutive_count >= 5: # Max cek 5 saja sudah cukup
                break
        else:
            break
            
    return consecutive_count >= min_consecutive

# --- POLA BEARISH (UNTUK SHORT/SELL) ---

def check_shooting_star(open_p, high_p, low_p, close_p):
    """
    Syarat Shooting Star:
    - Ekor (shadow) atas sangat panjang (>= 60% dari range)
    - Body kecil di bawah (<= 30% dari range)
    - Ekor bawah sangat pendek (<= 10% dari range)
    """
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    
    if range_total == 0:
        return False
        
    lower_shadow = min(open_p, close_p) - low_p
    upper_shadow = high_p - max(open_p, close_p)
    
    is_body_small = body <= (0.3 * range_total)
    is_upper_long = upper_shadow >= (0.6 * range_total)
    is_lower_short = lower_shadow <= (0.1 * range_total)
    
    return is_body_small and is_upper_long and is_lower_short

def check_bearish_engulfing(prev_open, prev_close, open_p, close_p):
    """
    Syarat Bearish Engulfing:
    - Candle 1: Hijau (prev_close > prev_open)
    - Candle 2: Merah (close_p < open_p)
    - Body Candle 2 menutupi Body Candle 1 sepenuhnya.
    """
    prev_is_green = prev_close > prev_open
    curr_is_red = close_p < open_p
    
    is_engulfing = (close_p < prev_open) and (open_p > prev_close)
    
    return prev_is_green and curr_is_red and is_engulfing

def check_evening_star(o1, c1, o2, c2, o3, c3, h2, l2):
    """
    Syarat Evening Star (3 Candles):
    - Candle 1: Hijau kuat
    - Candle 2: Body kecil (keraguan) di atas
    - Candle 3: Merah kuat, menembus ke bawah 50% body Candle 1
    """
    # Candle 1: Bullish
    c1_is_green = c1 > o1
    body_1 = abs(o1 - c1)
    
    # Candle 2: Body kecil
    body_2 = abs(o2 - c2)
    range_2 = h2 - l2
    is_c2_small = body_2 <= (0.3 * range_2) if range_2 > 0 else True
    
    # Candle 3: Bearish, close melewati 50% body candle 1
    c3_is_red = c3 < o3
    midpoint_1 = (o1 + c1) / 2
    is_c3_strong = c3 < midpoint_1
    
    return c1_is_green and is_c2_small and c3_is_red and is_c3_strong

def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """
    Mendeteksi keberadaan pola Bullish (LONG) atau Bearish (SHORT)
    pada bar terakhir. Mengembalikan tipe, pattern, dan boolean.
    """
    if len(df) < 3:
        return {"pattern": None, "type": None, "detected": False}
        
    last_3 = df.tail(3)
    c1, c2, c3 = last_3.iloc[0], last_3.iloc[1], last_3.iloc[2]
    
    # === POLA BULLISH (LONG) ===
    # Cek pola marubozu berturut-turut terlebih dahulu (paling kuat)
    if check_consecutive_small_marubozu_bullish(df):
        return {"pattern": "3+ Consecutive Small Bullish Marubozu", "type": "LONG", "detected": True}
        
    if check_morning_star(
        c1['open'], c1['close'],
        c2['open'], c2['close'],
        c3['open'], c3['close'],
        c2['high'], c2['low']
    ):
        return {"pattern": "Morning Star", "type": "LONG", "detected": True}
        
    if check_bullish_engulfing(c2['open'], c2['close'], c3['open'], c3['close']):
        return {"pattern": "Bullish Engulfing", "type": "LONG", "detected": True}
        
    if check_hammer(c3['open'], c3['high'], c3['low'], c3['close']):
        return {"pattern": "Hammer", "type": "LONG", "detected": True}
        
    # === POLA BEARISH (SHORT) ===
    if check_evening_star(
        c1['open'], c1['close'],
        c2['open'], c2['close'],
        c3['open'], c3['close'],
        c2['high'], c2['low']
    ):
        return {"pattern": "Evening Star", "type": "SHORT", "detected": True}
        
    if check_bearish_engulfing(c2['open'], c2['close'], c3['open'], c3['close']):
        return {"pattern": "Bearish Engulfing", "type": "SHORT", "detected": True}
        
    if check_shooting_star(c3['open'], c3['high'], c3['low'], c3['close']):
        return {"pattern": "Shooting Star", "type": "SHORT", "detected": True}
        
    return {"pattern": None, "type": None, "detected": False}
