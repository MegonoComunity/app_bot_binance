import pandas as pd

def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """
    Menghitung Bollinger Bands secara manual tanpa library pandas_ta dan menambahkan flag is_near_lower_band.
    """
    df = df.copy()
    
    # Simple Moving Average (BBM)
    df['bb_middle'] = df['close'].rolling(window=period).mean()
    
    # Standard Deviation
    rolling_std = df['close'].rolling(window=period).std()
    
    # Upper and Lower Bands
    df['bb_upper'] = df['bb_middle'] + (rolling_std * std_dev)
    df['bb_lower'] = df['bb_middle'] - (rolling_std * std_dev)
    
    # Kondisi harga (low) menyentuh atau mendekati lower band (toleransi 0.5%)
    df['is_near_lower_band'] = df['low'] <= (df['bb_lower'] * 1.005)
        
    return df
