# Telegram Google Drive Bot

Bot Telegram ini memungkinkan Anda mengupload file ke Google Drive melalui bot, melihat daftar file yang sudah diupload, dan mengambil file tersebut. Bot juga mendukung login dan logout menggunakan OAuth2 Google Drive sehingga dapat digunakan oleh beberapa pengguna.

## Cara Mendapatkan Google API Credentials (client_id dan client_secret)

1. Buka [Google Cloud Console](https://console.cloud.google.com/).
2. Buat project baru atau pilih project yang sudah ada.
3. Aktifkan Google Drive API:
   - Pergi ke **APIs & Services > Library**.
   - Cari **Google Drive API** dan klik **Enable**.
4. Buat kredensial OAuth 2.0:
   - Pergi ke **APIs & Services > Credentials**.
   - Klik **Create Credentials > OAuth client ID**.
   - Jika diminta, konfigurasikan layar persetujuan OAuth (OAuth consent screen):
     - Pilih **External**.
     - Isi informasi yang diminta (nama aplikasi, email, dll).
     - Simpan.
   - Pilih **Application type**: `Desktop app`.
   - Beri nama, lalu klik **Create**.
5. Setelah dibuat, Anda akan mendapatkan `client_id` dan `client_secret`.
6. Simpan `client_id` dan `client_secret` ini, nanti akan digunakan di file konfigurasi bot.

## Cara Menjalankan Bot

1. Clone atau download kode bot ini.
2. Buat file `config.json` di folder yang sama dengan isi seperti berikut:

```json
{
  "telegram_token": "TOKEN_BOT_TELEGRAM_ANDA",
  "google_client_id": "CLIENT_ID_GOOGLE_ANDA",
  "google_client_secret": "CLIENT_SECRET_GOOGLE_ANDA"
}
```

Ganti nilai dengan token bot Telegram dan kredensial Google Anda.

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Jalankan bot:

```bash
python bot.py
```

## Deployment di Railway

Bot ini dapat dideploy di Railway dengan langkah-langkah berikut:

1. Push kode bot ini ke repository GitHub Anda.
2. Buat project baru di Railway dan hubungkan ke repository tersebut.
3. Atur environment variables di Railway:
   - `PORT` : Railway akan otomatis menyediakan port ini, biasanya 8080 atau port lain.
   - `REDIRECT_HOST` : Isi dengan domain Railway Anda, misal `your-app-name.up.railway.app:PORT` (ganti `your-app-name` dan `PORT` sesuai Railway).
   - `TELEGRAM_TOKEN` : Token bot Telegram Anda.
   - `GOOGLE_CLIENT_ID` : Client ID Google API Anda.
   - `GOOGLE_CLIENT_SECRET` : Client Secret Google API Anda.
4. Pastikan file `config.json` tidak berisi token dan credential, karena sudah diatur lewat environment variables.
5. Railway akan menjalankan bot dengan perintah:

```bash
python bot.py
```

6. Pastikan redirect URI di Google Cloud Console sudah disesuaikan dengan `http://your-app-name.up.railway.app:PORT/oauth2callback`.

## Menjalankan Bot Secara Lokal

Untuk menjalankan bot secara lokal, cukup jalankan:

```bash
python bot.py
```

Bot akan menggunakan `localhost:8080` sebagai redirect URI secara default.

Jika ingin mengubah port atau host redirect, Anda dapat mengatur environment variable sebelum menjalankan bot, misalnya:

```bash
export PORT=8080
export REDIRECT_HOST=localhost:8080
python bot.py
```

Pastikan redirect URI di Google Cloud Console sesuai dengan konfigurasi ini.


## Perintah Bot

- `/login` - Memulai proses login Google Drive.
- `/logout` - Logout dari akun Google Drive.
- Kirim file apa saja ke bot untuk diupload ke Google Drive.
- `/list` - Melihat daftar file yang sudah diupload.
- `/get <file_id>` - Mengunduh file dari Google Drive berdasarkan ID file.

## Catatan

- Proses login menggunakan OAuth2, Anda akan mendapatkan link untuk login dan memberikan izin akses.
- Data sesi dan metadata file disimpan secara lokal di file `sessions.json` dan `files.json`.
- Bot ini dapat digunakan oleh beberapa pengguna secara bersamaan.
# AlDrive-bot
# AlDrive-bot
# AlDrive-bot
# AlDrive-bot
