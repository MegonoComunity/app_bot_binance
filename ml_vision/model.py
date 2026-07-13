import torch
import torch.nn as nn

class CandlePatternCNN(nn.Module):
    """
    CNN Sederhana untuk klasifikasi pola candlestick.
    Input: Grayscale image (1, 128, 128)
    Output: 3 classes (0: BEARISH, 1: NEUTRAL, 2: BULLISH)
    """
    def __init__(self, num_classes=3):
        super(CandlePatternCNN, self).__init__()
        
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2), # 64x64
            
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2), # 32x32
            
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2)  # 16x16
        )
        
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 16 * 16, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, num_classes)
        )
        
    def forward(self, x):
        x = self.features(x)
        x = self.classifier(x)
        return x

def get_model(model_path=None):
    model = CandlePatternCNN()
    if model_path:
        try:
            model.load_state_dict(torch.load(model_path, map_location=torch.device('cpu')))
            model.eval()
        except FileNotFoundError:
            print(f"Warning: Model weights not found at {model_path}. Using untrained weights.")
    return model
