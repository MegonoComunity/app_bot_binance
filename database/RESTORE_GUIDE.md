# 📖 Panduan Restore Database PostgreSQL (Komputer Rumah / Server Lain)

Dokumentasi ini menjelaskan langkah demi langkah cara me-restore database `db_crypto_learn` dari file backup `.sql` ke PostgreSQL di komputer lain (misal: komputer rumah).

---

## 📋 Persyaratan Awal
1. **PostgreSQL** sudah terinstall dan service aktif (port default: `5432`).
2. Tools command line PostgreSQL (`psql` / `pg_restore`) atau GUI (**pgAdmin 4** / **DBeaver**).

---

## 🚀 Cara 1: Restore via Command Line (PowerShell / CMD) — *Paling Cepat & Direkomendasikan*

### 1. Buat Database Baru (Jika belum ada)
Buka PowerShell / Command Prompt:
```powershell
# Set password postgres (sesuaikan dengan password lokal komputer rumah)
$env:PGPASSWORD="postgres"

# Buat database db_crypto_learn
createdb -h localhost -p 5432 -U postgres db_crypto_learn
```
> *Catatan:* Jika `createdb` belum ada di PATH, Anda bisa masuk ke `psql -U postgres` lalu jalankan `CREATE DATABASE db_crypto_learn;`.

### 2. Jalankan Restore File Backup SQL
Arahkan ke folder repository bot, lalu jalankan perintah berikut:
```powershell
$env:PGPASSWORD="postgres"
psql -h localhost -p 5432 -U postgres -d db_crypto_learn -f "database/backup_2026_09_14_145519.sql"
```
*(Ganti nama file dengan nama file backup terbaru jika berbeda).*

---

## 🖥️ Cara 2: Restore via GUI (pgAdmin 4)

1. Buka **pgAdmin 4**.
2. Di panel kiri, klik kanan pada **Databases** $\rightarrow$ **Create** $\rightarrow$ **Database...**
3. Beri nama: `db_crypto_learn` $\rightarrow$ klik **Save**.
4. Klik kanan pada database `db_crypto_learn` yang baru dibuat $\rightarrow$ pilih **Query Tool**.
5. Buka file backup: Klik ikon **Open File (Folder)** di toolbar atas $\rightarrow$ arahkan ke file `database/backup_YYYY_MM_DD_HHMMSS.sql`.
6. Klik tombol **Execute (Play / F5)** untuk menjalankan query restore.
7. Selesai! Seluruh tabel (`trade_history`, `pattern_memory`, `pattern_entries`, `ohlcv_candles`) beserta seluruh data pembelajarannya telah terisi.

---

## ⚙️ Cara 3: Restore via GUI (DBeaver)

1. Buka **DBeaver** dan koneksikan ke PostgreSQL lokal.
2. Klik kanan database $\rightarrow$ **Create New Database** $\rightarrow$ beri nama `db_crypto_learn`.
3. Buka **SQL Editor** $\rightarrow$ **Open SQL Script** $\rightarrow$ pilih file backup `.sql`.
4. Tekan `Alt + X` (Execute SQL Script).

---

## 🔍 Verifikasi Data Setelah Restore
Buka query tool dan jalankan perintah berikut untuk memastikan data berhasil masuk:
```sql
-- Cek jumlah riwayat trade
SELECT COUNT(*) FROM trade_history;

-- Cek jumlah memori pola yang dipelajari
SELECT COUNT(*) FROM pattern_memory;

-- Cek jumlah candle daily yang tersimpan
SELECT COUNT(*) FROM ohlcv_candles;
```

---

## 🛠️ Konfigurasi File `.env` di Komputer Rumah
Pastikan file `.env` di komputer rumah sudah sesuai dengan kredensial PostgreSQL lokal Anda:
```env
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/db_crypto_learn"
DB_HOST="localhost"
DB_PORT="5432"
DB_USER="postgres"
DB_PASSWORD="postgres"
DB_NAME="db_crypto_learn"
```
Setelah itu, jalankan bot dengan:
```bash
python main.py
```
Bot akan otomatis membaca hasil belajar dari database yang sudah di-restore dan Web Dashboard analitik dapat dibuka di `http://localhost:8000`.
