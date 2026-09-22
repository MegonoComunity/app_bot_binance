# 🤖 Bot Trading Crypto Futures Binance & AI Analytics Dashboard

Bot ini adalah sistem algorithmic trading otomatis 24/7 untuk Binance Futures. Menggabungkan analisis teknikal kuantitatif (Bollinger Bands, Dynamic Support/Resistance, Volume Surge, Historical 20-30 Day Rebound Modules) dan Machine Learning (Computer Vision) untuk deteksi pola candlestick akurasi tinggi, dilengkapi dengan **Real-Time Web Analytics Dashboard** dan **Notifikasi Telegram Interaktif**.

---

## 🌟 Fitur Utama
- **⚡ Scanner Realtime Top Volume Coins**: Memindai koin dengan likuiditas dan volume surge tinggi.
- **🧠 Brain AI & Vision Engine**: Mengklasifikasikan pola candlestick (Hammer, Morning Star, Engulfing, dll) dengan filter Tier-A.
- **🛡️ Risk & Money Management**: Auto TP/SL dinamis, Margin & Leverage selector, Trailing Stop, Daily Loss Limit protection.
- **📊 Real-time Web Dashboard (Port 8000)**:
  - PnL Harian (Realized), Floating PnL Live (Unrealized), Winrate Harian, dan Profit Factor.
  - Live Active Open Positions dengan MFE/MAE dan durasi holding.
  - Performa Net Strategi/Pola Candlestick dan status Auto-Blacklist.
  - Riwayat Transaksi Real & Virtual Paper Trading logs.
- **📱 Notifikasi Telegram Lengkap**: Notifikasi entry, trailing updates, take profit, stop loss, dan daily executive summary report.

---

## 📋 Persyaratan Sistem
- VPS / Server Linux (Ubuntu 20.04/22.04/24.04, Debian 11/12, CentOS/AlmaLinux) atau Komputer Lokal.
- Docker & Docker Compose (Direkomendasikan) ATAU Python 3.10+.
- Akun Binance (API Key & Secret Key dengan izin Futures Trading).
- Bot Telegram (Token dari [@BotFather](https://t.me/BotFather) dan Chat ID dari [@userinfobot](https://t.me/userinfobot)).
- Optional: Domain/Subdomain jika ingin Web Dashboard bisa diakses online dengan HTTPS.

---

## 🚀 Panduan Deploy di aaPanel dengan Docker Compose

Berikut adalah panduan lengkap langkah demi langkah (*step-by-step*) untuk menjalankan bot di VPS menggunakan panel **aaPanel**.

### 1. Persiapan di aaPanel
1. Login ke web panel **aaPanel** Anda.
2. Masuk ke menu **App Store** di bilah kiri:
   - Cari dan install **Docker** (versi terbaru).
   - Pastikan web server **Nginx** sudah terinstall.

---

### 2. Clone Repository di VPS
Buka menu **Terminal** di aaPanel (atau gunakan SSH client seperti PuTTY/Termius):

1. Masuk ke direktori root web:
   ```bash
   cd /www/wwwroot
   ```
2. Clone repository project:
   ```bash
   git clone https://github.com/MegonoComunity/app_bot_binance.git
   cd app_bot_binance
   ```

---

### 3. Konfigurasi Environment Variables (`.env`)
Salin template konfigurasi dari `.env.example`:
```bash
cp .env.example .env
nano .env
```
*(Atau gunakan menu **Files** di GUI aaPanel $\rightarrow$ arahkan ke `/www/wwwroot/app_bot_binance/.env`)*.

Lengkapi kredensial utama Anda:
```env
# Binance API Credentials
BINANCE_API_KEY=api_key_binance_anda
BINANCE_API_SECRET=api_secret_binance_anda
TRADING_MODE=TESTNET   # Gunakan TESTNET untuk latihan atau PRODUCTION untuk akun riil

# Telegram Notification
TELEGRAM_BOT_TOKEN=token_bot_anda
TELEGRAM_ADMIN_CHAT_ID=chat_id_anda
TELEGRAM_ERROR_CHAT_ID=chat_id_error_anda

# Trading Parameters
MARGIN_USDT=50
LEVERAGE=20
TP_PERCENT=40.0
SL_PERCENT=25.0
TIMEFRAME=5m

# PostgreSQL (Opsional jika menggunakan DB eksternal)
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres_password
DB_NAME=binance_bot
```
> Simpan file konfigurasi (`Ctrl+O`, lalu `Enter`, lalu `Ctrl+X` pada nano).

---

### 4. Menjalankan Bot dengan Docker Compose

Tersedia 2 metode yang dapat Anda pilih:

#### **Metode A: Melalui Terminal (Cepat & Direkomendasikan)**
Di dalam direktori `/www/wwwroot/app_bot_binance`, jalankan:
```bash
# Build image dan jalankan container di background
docker-compose up -d --build
```

Periksa status container:
```bash
# Cek apakah container berjalan (Up)
docker-compose ps

# Memantau log bot secara realtime
docker-compose logs -f --tail=100
```

---

#### **Metode B: Melalui GUI aaPanel (Docker Compose Menu)**
1. Buka menu **Docker** $\rightarrow$ sub-menu **Compose** di panel aaPanel.
2. Klik **Add Compose Template**:
   - **Template Name**: `binance_crypto_bot`
   - **File Path / Content**: Pilih file `/www/wwwroot/app_bot_binance/docker-compose.yml`.
3. Klik **Save**.
4. Klik **Deploy / Run** pada template yang baru ditambahkan.

---

### 5. Setup Web Dashboard ke Domain / Subdomain (Nginx Reverse Proxy & SSL)

Web Dashboard analitik berjalan di port internal `8000`. Untuk menghubungkannya ke domain/subdomain dengan sertifikat SSL (HTTPS):

1. Masuk ke menu **Website** $\rightarrow$ **Add Site** di aaPanel:
   - **Domain**: Masukkan domain/subdomain (contoh: `bot.domainanda.com`).
   - **FTP & Database**: Pilih *No FTP* dan *No Database*.
   - Klik **Submit**.
2. Klik nama domain yang baru dibuat, lalu lakukan 2 pengaturan berikut:
   - **Aktifkan SSL (HTTPS)**:
     - Masuk ke tab **SSL** $\rightarrow$ pilih tab **Let's Encrypt**.
     - Centang domain Anda dan klik **Apply**.
   - **Konfigurasi Reverse Proxy**:
     - Masuk ke tab **Reverse Proxy** $\rightarrow$ klik **Add Reverse Proxy**.
     - **Proxy Name**: `bot-dashboard`
     - **Target URL**: `http://127.0.0.1:8000`
     - **Sent Domain**: `$host`
     - Klik **Submit**.
3. Buka browser dan akses dashboard Anda di:
   👉 `https://bot.domainanda.com`

---

## 🛠️ Perintah Maintenance Server

| Kebutuhan | Perintah |
| :--- | :--- |
| **Lihat Log Realtime** | `docker-compose logs -f --tail=100` |
| **Restart Bot** | `docker-compose restart` |
| **Hentikan Bot** | `docker-compose down` |
| **Update Bot ke Versi Terbaru** | `git pull origin main && docker-compose up -d --build` |
| **Cek Status Resource/RAM** | `docker stats binance_crypto_bot` |

---

## 💻 Menjalankan Secara Lokal (Tanpa Docker)

Jika ingin menjalankan bot secara lokal di komputer pengembang:

```bash
# 1. Buat virtual environment
python -m venv venv

# 2. Aktifkan venv
# Linux / macOS:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 3. Install dependency
pip install -r requirements.txt

# 4. Salin dan isi konfigurasi .env
copy .env.example .env

# 5. Jalankan bot
python main.py
```
*Dashboard lokal dapat dibuka di `http://localhost:8000`.*

---

## 📂 Struktur Proyek
```text
app_bot_binance/
├── config/           # Konfigurasi parameter & environment
├── core/             # Scanner koin, eksekutor order, risk management
├── dashboard/        # Server Web Dashboard (aiohttp), routes & UI templates
│   └── templates/    # UI HTML modern & visualisasi KPI analitik
├── database/         # Repositori database trade, schema & restore guides
├── indicators/       # Perhitungan teknikal (Bollinger, S/R, Volume, RSI)
├── ml_vision/        # Candlestick chart image rendering & pattern inference
├── telegram/         # Modul interaksi & notifikasi Telegram bot
├── Dockerfile        # Definisi image Docker
├── docker-compose.yml# Konfigurasi container service & port mapping
├── requirements.txt  # Dependensi Python
├── main.py           # Entry point utama asinkron (asyncio event loop)
└── README.md         # Dokumentasi & panduan deployment
```

---

## ⚠️ Disclaimer
Trading aset kripto derivatif (Futures) memiliki tingkat risiko finansial yang tinggi karena penggunaan leverage. Pastikan untuk selalu menguji strategi pada mode **TESTNET** atau menggunakan margin yang terukur sebelum beralih ke mode akun riil (**PRODUCTION**).
