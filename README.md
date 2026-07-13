# Bot Trading Crypto Futures Binance

Bot ini adalah aplikasi trading otomatis untuk Binance Futures. Bot ini menggunakan analisis teknikal (seperti Bollinger Bands, Support/Resistance) dan terintegrasi dengan Machine Learning (Computer Vision) untuk mendeteksi pola candlestick. Bot juga akan mengirimkan notifikasi melalui Telegram.

## Persyaratan Sistem
- Python 3.10+
- Git
- Akun Binance (dengan akses API Key & Secret Key, aktifkan Futures)
- Bot Telegram (Token & Chat ID)
- Docker & Docker Compose (Opsional, jika ingin dijalankan dengan Docker)

## Cara Instalasi dan Menjalankan Bot

### 1. Clone Repository
Pertama, clone repository ini ke komputer/server lokal Anda:
```bash
git clone <URL_REPOSITORY_ANDA>
cd app_bot_binance
```

### 2. Konfigurasi Environment Variables
Salin file `.env.example` menjadi `.env`:
```bash
# Untuk Linux/macOS
cp .env.example .env

# Untuk Windows (Command Prompt/PowerShell)
copy .env.example .env
```
Buka file `.env` dan isi kredensial Anda:
- `BINANCE_API_KEY`: API Key Binance Anda.
- `BINANCE_API_SECRET`: Secret Key Binance Anda.
- `TELEGRAM_BOT_TOKEN`: Token Bot Telegram (dapatkan dari BotFather).
- `TELEGRAM_ADMIN_CHAT_ID`: Chat ID Telegram Anda (dapatkan dari userinfobot).
- Sesuaikan pengaturan trading lainnya seperti `TRADING_MODE` (TESTNET/PRODUCTION), `LEVERAGE`, `MARGIN_USDT`, `TP_PERCENT`, dan `SL_PERCENT` sesuai preferensi Anda.

---

### Opsi A: Menjalankan Menggunakan Python (Virtual Environment)

**1. Buat dan Aktifkan Virtual Environment (Direkomendasikan)**
```bash
# Linux/macOS
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

**2. Install Dependencies**
```bash
pip install -r requirements.txt
```

**3. Jalankan Aplikasi**
```bash
python main.py
```

---

### Opsi B: Menjalankan Menggunakan Docker Compose (Sangat Direkomendasikan untuk Server 24/7)

Jika Anda ingin menjalankan bot secara 24/7 dengan mudah tanpa harus mengurus dependensi Python secara manual, gunakan Docker Compose.

**1. Build dan Jalankan Container**
```bash
docker-compose up -d --build
```
*Parameter `-d` digunakan untuk menjalankan container di latar belakang (detached mode).*

**2. Melihat Log (Opsional)**
Untuk melihat log aktivitas bot, gunakan perintah:
```bash
docker-compose logs -f
```

**3. Menghentikan Bot (Opsional)**
```bash
docker-compose down
```

## Struktur Proyek
- `config/`: Pengaturan sistem dan mode API.
- `core/`: Logika scanner koin dan manajemen order Binance.
- `indicators/`: Logika perhitungan teknikal.
- `ml_vision/`: Modul Machine Learning untuk mendeteksi pola candlestick.
- `telegram/`: Penanganan notifikasi Telegram.
- `main.py`: Entry point utama aplikasi (berjalan secara asinkron).
- `.env`: File untuk menyimpan konfigurasi rahasia.
