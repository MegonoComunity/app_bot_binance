import pandas as pd

def calculate_sma(df: pd.DataFrame, period: int = 50) -> pd.DataFrame:
    """
    Menghitung Simple Moving Average (SMA).
    """
    df_copy = df.copy()
    df_copy[f'SMA_{period}'] = df_copy['close'].rolling(window=period).mean()
    return df_copy

def get_htf_trend(df: pd.DataFrame, period: int = 50) -> str:
    """
    Menentukan tren berdasarkan perbandingan harga terakhir dengan SMA.
    Return: "UPTREND", "DOWNTREND", atau "SIDEWAYS"
    """
    df_sma = calculate_sma(df, period)
    
    if df_sma.empty or f'SMA_{period}' not in df_sma.columns:
        return "SIDEWAYS"
        
    last_row = df_sma.iloc[-1]
    current_price = last_row['close']
    sma_value = last_row[f'SMA_{period}']
    
    if pd.isna(sma_value):
        return "SIDEWAYS"
        
    # Toleransi 0.1% untuk menganggap sideways / ranging
    tolerance = sma_value * 0.001
    
    if current_price > sma_value + tolerance:
        return "UPTREND"
    elif current_price < sma_value - tolerance:
        return "DOWNTREND"
    else:
        return "SIDEWAYS"
