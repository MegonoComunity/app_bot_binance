import pandas as pd
import numpy as np

def calculate_rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """
    Menghitung indikator Relative Strength Index (RSI) secara manual tanpa pandas_ta.
    """
    if len(df) < length:
        df['RSI'] = 50.0
        return df

    delta = df['close'].diff()
    
    # Memisahkan kenaikan dan penurunan
    gain = (delta.where(delta > 0, 0)).rolling(window=length, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length, min_periods=1).mean()
    
    # Menghindari pembagian dengan nol
    rs = gain / loss
    rs = rs.replace([np.inf, -np.inf], 100) # Jika loss 0, RSI akan mendekati 100
    
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Handle NaN values
    df['RSI'] = df['RSI'].fillna(50.0)
    
    return df
