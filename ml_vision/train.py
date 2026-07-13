import os
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from ml_vision.preprocessor import preprocess_chart_image
from ml_vision.model import CandlePatternCNN

class CandleDataset(Dataset):
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.classes = {"BEARISH": 0, "NEUTRAL": 1, "BULLISH": 2}
        self.image_paths = []
        self.labels = []
        
        for cls_name, cls_idx in self.classes.items():
            cls_dir = os.path.join(root_dir, cls_name)
            if not os.path.exists(cls_dir):
                continue
            for fname in os.listdir(cls_dir):
                if fname.lower().endswith(('.png', '.jpg', '.jpeg')):
                    self.image_paths.append(os.path.join(cls_dir, fname))
                    self.labels.append(cls_idx)
                    
    def __len__(self):
        return len(self.image_paths)
        
    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        img = cv2.imread(img_path)
        if img is None:
            # Fallback jika gambar korup
            img = np.zeros((128, 128, 3), dtype=np.uint8)
            
        tensor_img = preprocess_chart_image(img)
        return torch.tensor(tensor_img, dtype=torch.float32), torch.tensor(self.labels[idx], dtype=torch.long)

def train_model(epochs=10, batch_size=8, model_save_path="ml_vision/candle_model.pth"):
    """
    Melatih model menggunakan data dari folder dataset.
    """
    dataset = CandleDataset("dataset")
    if len(dataset) < 5:
        return "Gagal: Dataset masih terlalu sedikit (butuh minimal 5 gambar). Silakan /add_data lagi."
        
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    
    model = CandlePatternCNN()
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    model.train()
    
    correct = 0
    total = 0
    for epoch in range(epochs):
        running_loss = 0.0
        correct = 0
        total = 0
        
        for inputs, labels in dataloader:
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item()
            _, predicted = torch.max(outputs.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
            
        acc = 100 * correct / total
        print(f"Epoch {epoch+1}/{epochs}, Loss: {running_loss/len(dataloader):.4f}, Accuracy: {acc:.2f}%")
        
    # Buat folder jika belum ada
    os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
    torch.save(model.state_dict(), model_save_path)
    
    final_acc = 100 * correct / total
    return f"Belajar selesai! Akurasi model: {final_acc:.2f}% (Total {len(dataset)} gambar)"

if __name__ == "__main__":
    print(train_model())
