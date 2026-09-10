import pandas as pd
import numpy as np

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    
    return true_range.rolling(window=period).mean()

def calculate_bbw(df: pd.DataFrame, period: int = 20, std_dev: int = 2) -> pd.Series:
    sma = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    
    # BB Width
    bbw = (upper - lower) / sma
    return bbw

def calculate_donchian_channel(df: pd.DataFrame, period: int = 20):
    donchian_high = df['high'].rolling(window=period).max()
    donchian_low = df['low'].rolling(window=period).min()
    return donchian_high, donchian_low

def calculate_obv(df: pd.DataFrame) -> pd.Series:
    obv = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
    return obv

def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return macd, macd_signal, macd_hist

def calculate_squeeze_score(df: pd.DataFrame) -> float:
    """
    Hitung nilai 'Squeeze Score' (0-100).
    Semakin tinggi nilainya, semakin 'tidur' / tight konsolidasi koin tersebut.
    Berdasarkan: BBW percentile dan ATR percentile.
    """
    if len(df) < 50:
        return 0.0
        
    df['ATR'] = calculate_atr(df, 14)
    df['BBW'] = calculate_bbw(df, 20)
    
    # Ambil 50 data terakhir untuk menghitung persentil
    recent_atr = df['ATR'].tail(50)
    recent_bbw = df['BBW'].tail(50)
    
    current_atr = df['ATR'].iloc[-1]
    current_bbw = df['BBW'].iloc[-1]
    
    # Hitung di persentil berapa ATR & BBW saat ini (rendah = bagus untuk squeeze)
    atr_percentile = (recent_atr > current_atr).mean() * 100
    bbw_percentile = (recent_bbw > current_bbw).mean() * 100
    
    # Rata-rata dari seberapa banyak historis yang lebih besar dari saat ini
    # Jika 90% waktu ATR lebih besar dari sekarang, berarti sekarang sangat tight (score 90)
    score = (atr_percentile + bbw_percentile) / 2
    return round(score, 2)

def check_breakout(df: pd.DataFrame) -> dict:
    """
    Mendeteksi fase "Bangun" / Breakout.
    Syarat:
    1. Volume Spike (>2x SMA 20)
    2. MACD Histogram flip (dari negatif ke positif atau positif membesar)
    3. Price menembus / dekat Donchian High atau resistance
    """
    if len(df) < 30:
        return {"detected": False, "reason": "Not enough data"}
        
    # Kalkulasi
    df['SMA_Volume'] = df['volume'].rolling(window=20).mean()
    macd, macd_signal, macd_hist = calculate_macd(df)
    donchian_high, donchian_low = calculate_donchian_channel(df, 20)
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Volume Spike
    is_volume_spike = last['volume'] > (2 * last['SMA_Volume'])
    
    # MACD flip atau strong positive
    is_macd_bullish = macd_hist.iloc[-1] > 0 and macd_hist.iloc[-2] <= 0
    
    # Breakout Donchian High
    # Memastikan close tembus donchian high sebelumnya, atau sangat mendekati
    prev_dh = donchian_high.iloc[-2]
    is_donchian_breakout = last['close'] >= prev_dh * 0.998  # Toleransi sangat dekat
    
    if is_volume_spike and (is_macd_bullish or is_donchian_breakout):
        reason = "Volume Spike"
        if is_macd_bullish: reason += " + MACD Flip"
        if is_donchian_breakout: reason += " + Donchian Breakout"
        return {"detected": True, "reason": reason}
        
    return {"detected": False, "reason": ""}
