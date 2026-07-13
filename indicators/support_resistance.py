import pandas as pd
import numpy as np

def detect_support_zones(df: pd.DataFrame, lookback: int = 50, window: int = 5) -> list[float]:
    """
    Mendeteksi zona support berdasarkan local minima dalam periode lookback.
    """
    if len(df) < lookback:
        lookback = len(df)
        
    recent_data = df.tail(lookback).copy()
    recent_data = recent_data.reset_index(drop=True)
    
    supports = []
    for i in range(window, len(recent_data) - window):
        if all(recent_data['low'].iloc[i] < recent_data['low'].iloc[i - window:i]) and \
           all(recent_data['low'].iloc[i] < recent_data['low'].iloc[i + 1:i + window + 1]):
            supports.append(recent_data['low'].iloc[i])
            
    # Menggabungkan zona support yang berdekatan (contoh: toleransi 1%)
    merged_supports = []
    if supports:
        supports.sort()
        current_zone = [supports[0]]
        for s in supports[1:]:
            if s <= current_zone[-1] * 1.01:
                current_zone.append(s)
            else:
                merged_supports.append(sum(current_zone) / len(current_zone))
                current_zone = [s]
        merged_supports.append(sum(current_zone) / len(current_zone))
        
    return merged_supports

def detect_resistance_zones(df: pd.DataFrame, window: int = 20) -> list:
    """
    Mendeteksi zona resistance menggunakan local maxima.
    window: jumlah bar kiri-kanan untuk menentukan titik puncak
    """
    if len(df) < window * 2:
        return []
        
    resistances = []
    highs = df['high'].values
    
    for i in range(window, len(df) - window):
        # Cek apakah i adalah local maxima
        if highs[i] == max(highs[i - window : i + window + 1]):
            resistances.append(highs[i])
            
    # Mengelompokkan resistance yang berdekatan (selisih < 1%)
    zones = []
    if not resistances:
        return zones
        
    current_zone = [resistances[0]]
    
    for i in range(1, len(resistances)):
        if abs(resistances[i] - current_zone[0]) / current_zone[0] < 0.01:
            current_zone.append(resistances[i])
        else:
            zones.append(sum(current_zone) / len(current_zone))
            current_zone = [resistances[i]]
            
    zones.append(sum(current_zone) / len(current_zone))
    
    return zones

def is_near_support(current_price: float, support_zones: list, threshold: float = 0.01) -> bool:
    """
    Mengecek apakah harga saat ini berada di dekat salah satu zona support.
    threshold: 1% margin of error
    """
    for support in support_zones:
        if abs(current_price - support) / support <= threshold:
            return True
    return False

def is_near_resistance(current_price: float, resistance_zones: list, threshold: float = 0.01) -> bool:
    """
    Mengecek apakah harga saat ini berada di dekat salah satu zona resistance.
    threshold: 1% margin of error
    """
    for resistance in resistance_zones:
        if abs(current_price - resistance) / resistance <= threshold:
            return True
    return False
