# Panduan Setup Environment Python untuk Menjalankan Telegram Google Drive Bot

Masalah yang Anda alami disebabkan oleh environment Python yang dikelola secara eksternal (misalnya pada sistem operasi berbasis Debian/Ubuntu dengan Python yang diatur oleh package manager).

Untuk menghindari masalah ini, disarankan menggunakan virtual environment agar dependencies terisolasi dan tidak mengganggu sistem Python.

## Langkah-langkah Setup Virtual Environment

1. Pastikan Python 3 dan paket `python3-venv` sudah terinstall:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip -y
```

2. Buat virtual environment baru di folder proyek:

```bash
python3 -m venv venv
```

3. Aktifkan virtual environment:

- Di Linux/macOS:

```bash
source venv/bin/activate
```

- Di Windows (PowerShell):

```powershell
.\venv\Scripts\Activate.ps1
```

4. Setelah virtual environment aktif, install dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

5. Jalankan bot:

```bash
python bot.py
```

## Catatan

- Setiap kali ingin menjalankan bot, aktifkan virtual environment terlebih dahulu.
- Jika ingin keluar dari virtual environment, ketik `deactivate`.
- Virtual environment ini terisolasi, sehingga tidak akan mengganggu paket Python sistem Anda.

## Alternatif: Menggunakan pipx

Jika Anda ingin menginstall aplikasi Python secara terisolasi tanpa virtual environment manual, Anda bisa menggunakan `pipx`:

```bash
sudo apt install pipx
pipx install python-telegram-bot google-auth google-auth-oauthlib google-api-python-client requests
```

Namun, untuk proyek ini, virtual environment lebih direkomendasikan.

---

Jika Anda ingin saya buatkan skrip otomatis untuk setup environment ini, silakan beritahu saya.
