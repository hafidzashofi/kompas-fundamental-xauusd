# Kompas Fundamental XAUUSD

Dashboard fundamental XAU/USD yang menggabungkan data COT (CFTC), kalender
ekonomi ForexFactory, dan open interest kontrak Gold COMEX/CME menjadi satu
skor bias + narasi kesimpulan.

100% gratis selamanya: file statis di-host di GitHub Pages, data diperbarui
otomatis oleh GitHub Actions (juga gratis untuk repo publik/privat pribadi).
Tidak ada dependensi ke akun atau langganan Claude.

## Struktur

- `index.html` — halaman dashboard, membaca `data.json`.
- `data.json` — snapshot data terbaru (ditulis ulang oleh script/Actions).
- `scripts/update_data.py` — mengambil data dari CFTC, ForexFactory, dan
  Yahoo Finance, lalu menghitung skor bias dan menulis `data.json`.
- `.github/workflows/update.yml` — menjalankan script otomatis 2x sehari
  (06:00 & 18:00 WIB) lewat GitHub Actions.

## Setup sekali di awal

1. Buat repo baru di GitHub (bisa privat), lalu push folder ini:
   ```
   git init
   git add .
   git commit -m "init: kompas fundamental xauusd"
   git branch -M main
   git remote add origin https://github.com/<username>/<repo>.git
   git push -u origin main
   ```
2. Di repo GitHub → **Settings → Pages** → Source: `Deploy from a branch`,
   Branch: `main` / `root`. Simpan.
3. Tunggu 1-2 menit, dashboard akan aktif di:
   `https://<username>.github.io/<repo>/`
4. Di tab **Actions**, jalankan workflow "Update XAUUSD fundamental data"
   sekali secara manual (Run workflow) supaya `data.json` langsung terisi
   data terbaru.

Setelah itu, workflow berjalan sendiri 2x sehari selamanya, gratis, tanpa
perlu laptop menyala dan tanpa perlu Claude — kamu tinggal buka link Pages
kapan saja.

## Update manual (opsional)

```
python scripts/update_data.py
```

## Sumber data

- COT: [CFTC Public Reporting (Socrata)](https://publicreporting.cftc.gov/Market-Reports/Commitments-of-Traders/6dca-aqww)
- Kalender: feed publik ForexFactory (`ff_calendar_thisweek.json` / `ff_calendar_nextweek.json`)
- Harga: Yahoo Finance `GC=F` (COMEX Gold futures)

## Disclaimer

Skor bias dihitung dari formula heuristik sederhana (momentum posisi COT,
tingkat crowding, momentum harga). Ini alat bantu konteks fundamental,
**bukan sinyal trading** dan bukan saran investasi.
