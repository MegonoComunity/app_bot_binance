import pandas as pd
import numpy as np
import mplfinance as mpf
import io
import matplotlib.pyplot as plt

def render_ohlcv_to_image(df: pd.DataFrame, n_candles: int = 20) -> np.ndarray:
    """
    Merender data OHLCV menjadi gambar candlestick (numpy array).
    """
    if len(df) < n_candles:
        n_candles = len(df)
        
    plot_df = df.tail(n_candles).copy()
    
    # Pastikan index adalah datetime untuk mplfinance
    if not isinstance(plot_df.index, pd.DatetimeIndex):
        if 'timestamp' in plot_df.columns:
            plot_df['timestamp'] = pd.to_datetime(plot_df['timestamp'])
            plot_df.set_index('timestamp', inplace=True)
            
    # Buat figure di memory
    fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
    
    # Style minimalis tanpa grid/axis untuk ML
    mc = mpf.make_marketcolors(up='g', down='r', inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc, gridstyle='', y_on_right=False)
    
    mpf.plot(plot_df, type='candle', style=s, ax=ax, axisoff=True)
    
    # Simpan ke buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, transparent=False, facecolor='w')
    buf.seek(0)
    
    # Konversi buffer ke numpy array
    import cv2
    img_arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
    
    plt.close(fig)
    return img
