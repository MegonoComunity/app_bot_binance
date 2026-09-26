---
name: fix-execution-fidelity
description: Gunakan skill ini saat mengerjakan repo app_bot_binance (multi-exchange, fokus Bitunix) untuk memperbaiki gap winrate demo-vs-real, memperbaiki slippage eksekusi order, membersihkan kontaminasi data pattern learner (real vs simulasi), menyederhanakan confluence_engine.py yang overfit, atau menyambungkan ulang position_monitor.py yang saat ini tidak terpakai. Trigger juga saat user minta "audit eksekusi", "kenapa winrate real lebih rendah dari demo", atau "perbaiki AI learner bot".
---

# Fix Execution Fidelity — app_bot_binance

Skill ini merangkum hasil audit kode nyata (bukan asumsi generik) terhadap `main.py`,
`core/confluence_engine.py`, `core/order_manager.py`, `core/learner.py`, dan
`core/position_monitor.py`. Ditemukan 4 akar masalah konkret yang menjelaskan kenapa
winrate akun demo > 50% sementara akun real < 50%, meskipun logika sinyalnya identik.

Kerjakan task di bawah **berurutan sesuai prioritas** — jangan lompat ke Task 3/4 sebelum
Task 1 dan 2 selesai, karena Task 1 & 2 adalah sumber bias data yang akan merusak hasil
evaluasi Task 3.

---

## Diagnosis (rujukan file:baris)

1. **Order eksekusi selalu MARKET, tanpa slippage guard.**
   `order_manager.py::place_long_order` / `place_short_order` punya parameter
   `use_limit: bool = False`, dan di `main.py` (baris ~1244–1250) keduanya dipanggil
   TANPA argumen `use_limit` — jadi selalu default ke `MARKET`. Tidak ada perbandingan
   antara `current_price` (harga saat sinyal dibaca) vs harga fill aktual
   (`_get_average_fill_price`). Di paper trading (`main.py` baris ~1234-1241), fill selalu
   persis di `current_price` tanpa slippage/fee — membuat demo terlihat lebih baik secara
   struktural, bukan karena strategi lebih unggul.

2. **Pattern learner (`core/learner.py`) mencampur hasil trade REAL dan SIMULASI dalam
   bucket win-rate yang sama.** Bukti: `record_trade_result()` dipanggil dari trade
   VIRTUAL/paper (`main.py` baris 513, di dalam blok "MONITORING PAPER TRADING") dan juga
   dari trade REAL (baris 1537, 1763, 1926, 2173, 2241) — keduanya menulis ke kategori yang
   sama di `data/pattern_stats.json` lewat `normalize_pattern_category()`. Artinya
   gatekeeper `is_pattern_reliable()` yang menentukan boleh/tidaknya entry REAL, sebagian
   "belajar" dari hasil trade dengan fill ideal tanpa slippage. Ini bias sistematis yang
   mengarahkan model untuk terlalu percaya diri pada pola yang sebenarnya cuma teruji di
   kondisi ideal.

3. **`confluence_engine.py` Pilar 2 (S/R & Pattern, maks 25 poin) punya ~10 jalur `elif`
   yang independen dan hampir semuanya memberi skor maksimal (20-25) begitu salah satu
   kondisi terpenuhi** (pump alert, QML, sniper zone, smart buy level, 2-candle reversal,
   compression reversal, ML vision, EMA21 pullback, AVWAP, pattern candlestick biasa).
   Ditambah pilar lain yang punya skor dasar/floor cukup tinggi (mis. Pilar 1 tetap
   memberi 15-20 poin bahkan saat `DOWNTREND` jika ada pump alert), threshold 60-80/100
   jadi relatif mudah tercapai lewat banyak kombinasi berbeda. Belum ada bukti kode ini
   divalidasi lewat walk-forward test terpisah — risikonya skor "confluence" terlihat
   meyakinkan tapi tidak benar-benar predictive di luar data yang dipakai saat tuning.

4. **`position_monitor.py::analyze_position` adalah modul exit-signal berbasis skor yang
   cukup baik (HTF reversal, RSI overbought/oversold, pattern bearish/bullish, candle body
   besar) TAPI tidak dipanggil sama sekali di `main.py`.** Exit position saat ini hanya
   memakai `evaluate_time_based_exit` dan `evaluate_auto_breakeven` dari `risk_manager.py`
   (murni berbasis waktu & ROI), bukan berbasis pembalikan sinyal teknikal. Ini artinya bot
   bisa menahan posisi lebih lama dari optimal padahal sudah ada early-warning teknikal
   yang terdeteksi tapi tidak pernah dibaca.

---

## Task 1 — Order Execution & Slippage Guard (prioritas tertinggi)

File: `core/order_manager.py`, `main.py`

1. Tambahkan fungsi `get_orderbook_mid_or_spread(client, symbol)` (atau pakai data yang
   sudah tersedia dari exchange adapter) untuk membaca spread bid-ask terkini sebelum
   order ditembak.
2. Di `place_long_order` / `place_short_order`, sebelum mengirim MARKET order, hitung
   selisih antara `current_price` (harga sinyal) dan harga market terkini
   (re-fetch harga sesaat sebelum order). Jika selisih melebihi toleransi
   (mis. `max_signal_to_execution_slippage_pct`, default 0.3-0.5%), batalkan order dan
   log sebagai `"SIGNAL_STALE"` alih-alih memaksa entry di harga yang sudah bergerak jauh.
3. Tambahkan opsi `use_limit=True` sebagai default yang bisa di-toggle dari `bot_config`,
   khususnya untuk Bitunix di pair dengan likuiditas lebih tipis dibanding Binance.
4. Setelah setiap order real berhasil, **log eksplisit** 3 angka ini ke tempat yang sama
   dengan `record_trade_result`: `entry_price_signal`, `entry_price_fill`, dan
   `slippage_pct = (fill - signal) / signal`. Ini akan jadi bukti kuantitatif untuk
   memverifikasi hipotesis #1 di atas dalam beberapa hari live.
5. Di paper trading (`main.py` baris ~1232-1241), **jangan lagi fill di `current_price`
   persis**. Simulasikan slippage rata-rata (bisa dari data slippage real hasil poin 4, atau
   estimasi awal 0.05-0.15% + fee taker exchange) supaya winrate demo jadi pembanding yang
   jujur terhadap real, bukan angka yang secara struktural lebih tinggi.

**Definisi selesai:** ada log per-trade yang menunjukkan slippage real, dan paper trading
tidak lagi fill sempurna tanpa biaya.

---

## Task 2 — Pisahkan Data Learner: REAL vs SIMULASI

File: `core/learner.py`, `main.py`, kemungkinan juga `core/pattern_memory.py` (belum
di-review, minta user upload jika ada)

1. Ubah signature `record_trade_result()` di `learner.py` untuk menerima parameter wajib
   baru `source: Literal["REAL", "SIM"]`.
2. Di `get_stats()` / struktur `data/pattern_stats.json`, pecah `history` menjadi dua
   sub-list per kategori: `history_real` dan `history_sim` (atau tag tiap entry history
   dengan field `"source"` lalu filter saat `calculate_window_stats`).
3. Di `main.py`, update SEMUA 6 titik panggilan `record_trade_result` (baris 513, 1537,
   1763, 1926, 2173, 2241) untuk mengirim `source` yang benar — baris 513 adalah SIM
   (virtual trade), baris lain kemungkinan besar REAL (perlu diverifikasi konteksnya
   masing-masing, terutama apakah ada juga panggilan dari blok paper trading lain selain
   513).
4. Ubah `is_pattern_reliable()` agar **default-nya hanya mengevaluasi `history_real`**
   untuk keputusan gatekeeper live trading. `history_sim` tetap disimpan untuk analisis
   terpisah (mis. membandingkan performa ideal vs performa real per pola), tapi tidak lagi
   ikut menentukan boleh/tidaknya entry real.
5. Untuk pola yang baru (`total_w < min_samples`) dan belum punya cukup data REAL, tetap
   pertahankan mekanisme probation yang sudah ada — tapi probation ini sekarang murni
   berbasis data real, bukan tercampur simulasi.

**Definisi selesai:** `is_pattern_reliable()` yang dipanggil di jalur live trading tidak
lagi bisa lolos/gagal hanya karena hasil trade virtual.

---

## Task 3 — Validasi & Sederhanakan Confluence Engine

File: `core/confluence_engine.py`

1. **Jangan langsung mengubah bobot/threshold dulu.** Sebelum mengubah apa pun, buat
   script backtest offline terpisah (`scripts/backtest_confluence.py`) yang:
   - Mengambil data OHLCV historis (beberapa bulan, multi-pair) dari DB (`ohlcv_repo`)
     atau exchange API.
   - Menjalankan `calculate_confluence_score()` persis seperti di live, dengan cost model
     realistis (fee + slippage dari hasil Task 1).
   - Split data in-sample (untuk lihat performa) vs out-of-sample (data yang tidak pernah
     "dilihat" saat threshold `min_confluence_score` awalnya ditentukan).
   - Laporkan winrate, profit factor, dan expectancy per rentang skor (60-70, 70-80,
     80-90, 90-100) — bukan cuma agregat. Ini akan menunjukkan apakah skor tinggi memang
     berkorelasi dengan winrate lebih tinggi, atau justru flat/acak (indikasi overfitting).
2. Kalau hasil backtest menunjukkan banyak dari 10 jalur `elif` di Pilar 2 (pump, QML,
   sniper, smart_buy, 2-candle, compression, ml_vision, ema21_pullback, avwap, pattern
   biasa) ternyata TIDAK sama-sama predictive, turunkan skor jalur yang lemah alih-alih
   menyamaratakan semua di 20-25 poin. Dokumentasikan per jalur: jumlah sampel, winrate,
   profit factor dari hasil backtest.
3. Pertimbangkan menambahkan **meta-labeling layer**: alih-alih confluence score langsung
   memutuskan approve/reject, jadikan skor ini sebagai salah satu fitur input ke model ML
   terpisah (LightGBM/XGBoost) yang ditrain khusus untuk memprediksi probabilitas sukses,
   memakai histori trade REAL (bukan simulasi) dari Task 2.

**Definisi selesai:** ada laporan backtest walk-forward yang membuktikan (atau membantah)
bahwa skor confluence saat ini benar-benar predictive di luar sampel, per jalur sinyal.

---

## Task 4 — Sambungkan atau Hapus `position_monitor.py`

File: `position_monitor.py`, `main.py::profitable_position_monitor_loop`

1. Konfirmasi ke user: apakah `analyze_position()` memang sengaja belum disambungkan
   (work-in-progress), atau ini regresi yang tidak disengaja.
2. Jika ingin dipakai: panggil `analyze_position()` di dalam
   `profitable_position_monitor_loop()` untuk setiap posisi terbuka, sejajar dengan
   `evaluate_time_based_exit` dan `evaluate_auto_breakeven` yang sudah ada. Gunakan hasil
   `decision == 'CLOSE'` sebagai sinyal tambahan untuk memicu `close_profitable_position`
   atau early-exit sebelum SL/TP tersentuh, terutama saat posisi masih floating profit
   kecil tapi momentum sudah berbalik.
3. Jika tidak ingin dipakai, hapus importnya dari codebase supaya tidak membingungkan
   kontributor lain di masa depan (kode mati yang terlihat seperti fitur aktif adalah
   sumber bug tersembunyi).

**Definisi selesai:** modul ini eksplisit aktif dan teruji, atau eksplisit dihapus —
tidak lagi dalam status "ada tapi tidak dipakai".

---

## Urutan Kerja yang Disarankan untuk Agent

1. Task 1 dulu (execution fidelity) — ini paling murah untuk diverifikasi (cukup lihat log
   beberapa hari) dan paling mungkin jadi penyebab dominan gap winrate.
2. Task 2 bersamaan/segera setelah Task 1 — supaya data learner ke depan sudah bersih.
3. Biarkan bot jalan beberapa hari-minggu untuk mengumpulkan data REAL yang bersih dari
   Task 1 & 2.
4. Baru kerjakan Task 3 (backtest & validasi confluence engine) dengan data yang lebih
   dipercaya.
5. Task 4 kapan saja, tidak bergantung pada task lain — tapi rendah risiko jadi bisa
   diselesaikan sambil menunggu data Task 1-2 terkumpul.

Setiap task, sebelum submit perubahan: jalankan bot di mode paper trading dulu untuk
memastikan tidak ada exception baru, lalu minta konfirmasi user sebelum deploy ke akun
real Bitunix.
