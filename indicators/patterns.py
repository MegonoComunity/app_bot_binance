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

def is_small_body(open_p, high_p, low_p, close_p, threshold=0.3):
    """
    Mendeteksi apakah body candle kecil (<= threshold dari total range).
    """
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    if range_total == 0:
        return True
    return body <= (threshold * range_total)

def is_large_body(open_p, high_p, low_p, close_p, threshold=0.7):
    """
    Mendeteksi apakah body candle besar (>= threshold dari total range).
    """
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    if range_total == 0:
        return False
    return body >= (threshold * range_total)

def check_small_bodies_followed_by_green(df: pd.DataFrame, min_small_candles: int = 3) -> bool:
    """
    Syarat:
    - 3 sampai 5 candle dengan body kecil berturut-turut.
    - Dipastikan tidak ada body candle besar.
    - Diikuti oleh 1 candle terakhir (ke-6 atau setelahnya) berwarna hijau (bullish).
    """
    if len(df) < min_small_candles + 1:
        return False
        
    last_row = df.iloc[-1]
    last_o, last_c = last_row['open'], last_row['close']
    is_last_green = last_c > last_o
    
    if not is_last_green:
        return False
        
    small_count = 0
    for i in range(len(df)-2, -1, -1):
        row = df.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        # Jika menemukan candle dengan body besar, langsung batalkan deteksi
        if is_large_body(o, h, l, c):
            break
            
        if is_small_body(o, h, l, c):
            small_count += 1
            if small_count >= 5: # Maksimal cek 5
                break
        else:
            break
            
    return small_count >= min_small_candles

# --- POLA BEARISH / TRAP (UNTUK CLOSE LONG ATAU SHORT) ---

def is_bull_trap(open_p, high_p, low_p, close_p):
    """
    Mendeteksi Bull Trap: Candle dengan range besar (tiba-tiba naik tinggi) 
    namun ditutup dengan sumbu atas (upper shadow) yang panjang (penolakan).
    """
    range_tot = high_p - low_p
    if range_tot == 0:
        return False
        
    is_green = close_p > open_p
    upper_shadow = high_p - close_p
    
    # Sumbu atas >= 50% dari keseluruhan panjang candle = indikasi trap/penolakan kuat
    return is_green and (upper_shadow >= 0.5 * range_tot)

def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """
    Mendeteksi keberadaan pola Candlestick Reversal Tier-A (LONG / SHORT) dan Trap.
    """
    if df.empty or len(df) < 5:
        return {"pattern": None, "type": None, "detected": False, "volume_ratio": 1.0}
        
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    prev2_row = df.iloc[-3]
    
    # Hitung Volume Surge Ratio (dibandingkan MA20 volume)
    vol_ma20 = df['volume'].tail(20).mean() if len(df) >= 20 else df['volume'].mean()
    vol_ratio = float(last_row['volume'] / vol_ma20) if vol_ma20 > 0 else 1.0

    # 1. Bull Trap Check (Close Long / Avoid Entry)
    if is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bull Trap",
            "type": "CLOSE_LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
        }

    # 2. Bullish Hammer Check
    if check_hammer(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bullish Hammer",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
        }

    # 3. Morning Star Check (3 Candles)
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
        }

    # 4. Bullish Engulfing Check
    if check_bullish_engulfing(prev_row['open'], prev_row['close'], last_row['open'], last_row['close']):
        return {
            "pattern": "Bullish Engulfing",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
        }

    # 5. 3-5 Small Bodies Followed by Green Check
    if check_small_bodies_followed_by_green(df):
        return {
            "pattern": "3-5 Small Bodies Followed by Green",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
        }

    # 6. Bearish Reversal Patterns (untuk SHORT / Close Long)
    # Shooting Star (Upper shadow panjang, body kecil di bawah)
    range_last = last_row['high'] - last_row['low']
    if range_last > 0:
        body_last = abs(last_row['close'] - last_row['open'])
        upper_shadow = last_row['high'] - max(last_row['open'], last_row['close'])
        lower_shadow = min(last_row['open'], last_row['close']) - last_row['low']
        if body_last <= 0.3 * range_last and upper_shadow >= 0.6 * range_last and lower_shadow <= 0.1 * range_last:
            return {
                "pattern": "Shooting Star",
                "type": "SHORT",
                "detected": True,
                "volume_ratio": vol_ratio,
            }

    # Bearish Engulfing
    if prev_row['close'] > prev_row['open'] and last_row['close'] < last_row['open']:
        if last_row['open'] > prev_row['close'] and last_row['close'] < prev_row['open']:
            return {
                "pattern": "Bearish Engulfing",
                "type": "SHORT",
                "detected": True,
                "volume_ratio": vol_ratio,
            }

    return {"pattern": None, "type": None, "detected": False, "volume_ratio": vol_ratio}
