import pandas as pd
import numpy as np

def check_hammer(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Syarat High-Probability Bullish Hammer:
    - Body kecil di bagian atas (<= 30% dari total range)
    - Shadow bawah panjang (>= 60% dari total range, min 2x body)
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
    
    # Body saat ini menelan body sebelumnya
    is_engulfing = (close_p >= prev_open) and (open_p <= prev_close) and (curr_body >= prev_body * 1.05)
    
    # Validasi volume jika tersedia
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
    - Candle 2: Body kecil di level bawah (keraguan/spinning top/doji)
    - Candle 3: Hijau solid, menembus >= 50% body Candle 1
    """
    # Candle 1: Bearish
    c1_is_red = c1 < o1
    body_1 = o1 - c1
    
    # Candle 2: Body kecil
    body_2 = abs(o2 - c2)
    range_2 = h2 - l2
    is_c2_small = body_2 <= (0.35 * range_2) if range_2 > 0 else True
    
    # Candle 3: Bullish, close melewati 50% body candle 1
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
    - Low candle 1 dan low candle 2 hampir persis sama (toleransi <= 0.15%)
    - Candle 1 bearish atau doji, Candle 2 bullish
    """
    if l1 <= 0 or l2 <= 0:
        return False
    diff_ratio = abs(l1 - l2) / l1
    is_double_bottom = diff_ratio <= 0.0015
    is_c1_bearish = c1 <= o1
    is_c2_bullish = c2 > o2
    return is_double_bottom and is_c1_bearish and is_c2_bullish

def is_small_body(open_p: float, high_p: float, low_p: float, close_p: float, threshold: float = 0.3) -> bool:
    """
    Mendeteksi apakah body candle kecil (<= threshold dari total range).
    """
    body = abs(close_p - open_p)
    range_total = high_p - low_p
    if range_total == 0:
        return True
    return body <= (threshold * range_total)

def is_large_body(open_p: float, high_p: float, low_p: float, close_p: float, threshold: float = 0.7) -> bool:
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
    Syarat Base Consolidation Breakout:
    - 3 sampai 5 candle dengan body kecil berturut-turut (akumulasi / kompresi volatilitas).
    - Dipastikan tidak ada body candle besar berlawanan arah.
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
        
    # Candle penutup harus memiliki body solid (bukan doji)
    last_range = last_h - last_l
    last_body = last_c - last_o
    if last_range > 0 and (last_body / last_range < 0.45):
        return False
        
    small_count = 0
    for i in range(len(df)-2, -1, -1):
        row = df.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        # Jika menemukan candle dengan body besar, batalkan
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

def is_bull_trap(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Mendeteksi Bull Trap: Candle dengan range besar (tiba-tiba naik tinggi) 
    namun ditutup dengan sumbu atas (upper shadow) yang panjang (penolakan keras).
    """
    range_tot = high_p - low_p
    if range_tot == 0:
        return False
        
    is_green = close_p > open_p
    upper_shadow = high_p - max(open_p, close_p)
    
    # Sumbu atas >= 50% dari keseluruhan panjang candle = indikasi trap/penolakan kuat
    return is_green and (upper_shadow >= 0.50 * range_tot)

def check_shooting_star(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """
    Syarat Shooting Star (Bearish Reversal):
    - Body kecil di bagian bawah (<= 30% dari total range)
    - Upper shadow panjang (>= 60% dari total range)
    - Lower shadow sangat pendek (<= 12% dari total range)
    """
    range_tot = high_p - low_p
    if range_tot <= 0:
        return False
    body = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    return (body <= 0.30 * range_tot) and (upper_shadow >= 0.58 * range_tot) and (lower_shadow <= 0.12 * range_tot)

def check_bearish_engulfing(prev_open: float, prev_close: float, open_p: float, close_p: float) -> bool:
    """
    Syarat Bearish Engulfing:
    - Candle 1: Hijau
    - Candle 2: Merah, body menelan candle 1 sepenuhnya
    """
    prev_is_green = prev_close > prev_open
    curr_is_red = close_p < open_p
    if not (prev_is_green and curr_is_red):
        return False
    return (open_p >= prev_close) and (close_p <= prev_open)

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

    # 1. Bull Trap Check (Close Long / Avoid Entry)
    if is_bull_trap(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bull Trap",
            "type": "CLOSE_LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": True,
        }

    # 2. Bullish Hammer Check (High Probability jika ada volume support)
    if check_hammer(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Bullish Hammer",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    # 3. Morning Star Check (3 Candles Reversal)
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

    # 4. Bullish Engulfing Check
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

    # 5. Piercing Line Check
    if check_piercing_line(prev_row['open'], prev_row['close'], last_row['open'], last_row['close']):
        return {
            "pattern": "Piercing Line",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    # 6. Tweezer Bottom Check
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

    # 7. 3-5 Small Bodies Followed by Green (Consolidation Breakout)
    if check_small_bodies_followed_by_green(df):
        return {
            "pattern": "3-5 Small Bodies Followed by Green",
            "type": "LONG",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    # 8. Bearish Reversal Patterns (untuk SHORT / Close Long)
    if check_shooting_star(last_row['open'], last_row['high'], last_row['low'], last_row['close']):
        return {
            "pattern": "Shooting Star",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    if check_bearish_engulfing(prev_row['open'], prev_row['close'], last_row['open'], last_row['close']):
        return {
            "pattern": "Bearish Engulfing",
            "type": "SHORT",
            "detected": True,
            "volume_ratio": vol_ratio,
            "is_high_quality": has_vol_surge,
        }

    return {"pattern": None, "type": None, "detected": False, "volume_ratio": vol_ratio, "is_high_quality": False}
