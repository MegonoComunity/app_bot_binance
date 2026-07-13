import cv2
import numpy as np

def preprocess_chart_image(img: np.ndarray, target_size: tuple = (128, 128)) -> np.ndarray:
    """
    Preprocessing gambar chart untuk input CNN:
    1. Resize ke target_size
    2. Convert ke grayscale (opsional, tapi untuk pola candle bentuk lebih penting)
    3. Normalisasi nilai pixel (0-1)
    """
    # Resize
    resized = cv2.resize(img, target_size)
    
    # Convert to grayscale (1 channel)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    
    # Expand dims (H, W, 1)
    gray = np.expand_dims(gray, axis=-1)
    
    # Normalize
    normalized = gray.astype('float32') / 255.0
    
    # Transpose ke format PyTorch (C, H, W)
    tensor_format = np.transpose(normalized, (2, 0, 1))
    
    return tensor_format
