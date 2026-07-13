import torch
import numpy as np

LABELS = {
    0: "BEARISH",
    1: "NEUTRAL",
    2: "BULLISH"
}

def predict_candle_pattern(processed_img: np.ndarray, model: torch.nn.Module) -> tuple[str, float]:
    """
    Melakukan inferensi pola candlestick.
    Input: processed_img dari preprocessor (C, H, W)
    """
    model.eval()
    
    # Tambahkan batch dimension (1, C, H, W)
    tensor_input = torch.tensor(processed_img, dtype=torch.float32).unsqueeze(0)
    
    with torch.no_grad():
        outputs = model(tensor_input)
        probabilities = torch.nn.functional.softmax(outputs, dim=1)
        
        max_prob, predicted_idx = torch.max(probabilities, 1)
        
        idx = predicted_idx.item()
        confidence = max_prob.item()
        
    return LABELS[idx], confidence
