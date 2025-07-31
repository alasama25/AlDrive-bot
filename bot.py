import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import googleapiclient.http
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.json'
SESSIONS_FILE = 'sessions.json'
FILES_FILE = 'files.json'
import sys

REDIRECT_PORT = int(os.getenv('PORT', '8080'))
REDIRECT_HOST = os.getenv('REDIRECT_HOST', f'localhost:{REDIRECT_PORT}')
REDIRECT_URI = f'http://{REDIRECT_HOST}/oauth2callback'

# Determine server bind address
# If running on Railway or other cloud, bind to 0.0.0.0, else localhost
if 'PORT' in os.environ:
    SERVER_BIND_ADDRESS = '0.0.0.0'
else:
    SERVER_BIND_ADDRESS = 'localhost'

# Load config
if not os.path.exists(CONFIG_FILE):
    logger.error(f"{CONFIG_FILE} not found. Please create it with your Telegram token and Google API credentials.")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

TELEGRAM_TOKEN = config.get('telegram_token')
GOOGLE_CLIENT_ID = config.get('google_client_id')
GOOGLE_CLIENT_SECRET = config.get('google_client_secret')

if not TELEGRAM_TOKEN or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.error("Missing telegram_token or google_client_id or google_client_secret in config.json")
    exit(1)

# Load or initialize sessions and files data
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

sessions = load_json(SESSIONS_FILE)  # user_id -> credentials dict
files = load_json(FILES_FILE)        # user_id -> list of files metadata

# OAuth2 flow setup
def create_flow(state=None):
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=['https://www.googleapis.com/auth/drive.file'],
        redirect_uri=REDIRECT_URI,
        state=state
    )

# Simple HTTP server to handle OAuth2 redirect
class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path != '/oauth2callback':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            return

        query = parse_qs(parsed_path.query)
        if 'state' not in query or 'code' not in query:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Missing state or code in query')
            return

        state = query['state'][0]
        code = query['code'][0]

        # Save code and state for the bot to process
        self.server.auth_code = code
        self.server.auth_state = state

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Login berhasil! Anda dapat kembali ke Telegram dan melanjutkan.')

def run_http_server(server):
    server.handle_request()

# Start HTTP server in a thread and wait for auth code
def get_auth_code(state):
    server = HTTPServer((SERVER_BIND_ADDRESS, REDIRECT_PORT), OAuthHandler)
    server.auth_code = None
    server.auth_state = None
    thread = threading.Thread(target=run_http_server, args=(server,))
    thread.start()
    thread.join()
    if server.auth_state != state:
        return None
    return server.auth_code

# Save credentials for user
def save_credentials(user_id, creds):
    sessions[str(user_id)] = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }
    save_json(SESSIONS_FILE, sessions)

# Load credentials for user
def load_credentials(user_id):
    data = sessions.get(str(user_id))
    if not data:
        return None
    creds = Credentials(
        token=data.get('token'),
        refresh_token=data.get('refresh_token'),
        token_uri=data.get('token_uri'),
        client_id=data.get('client_id'),
        client_secret=data.get('client_secret'),
        scopes=data.get('scopes')
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(user_id, creds)
        except Exception as e:
            logger.error(f"Failed to refresh token for user {user_id}: {e}")
            return None
    return creds

# Google Drive service for user
def get_drive_service(user_id):
    creds = load_credentials(user_id)
    if not creds:
        return None
    return build('drive', 'v3', credentials=creds)

# Save file metadata
def save_file_metadata(user_id, file_metadata):
    user_files = files.get(str(user_id), [])
    user_files.append(file_metadata)
    files[str(user_id)] = user_files
    save_json(FILES_FILE, files)

# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Selamat datang! Gunakan /login untuk login ke Google Drive Anda."
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    flow = create_flow(state=str(user_id))
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline', include_granted_scopes='true')
    await update.message.reply_text(
        f"Silakan klik link berikut untuk login:\n{auth_url}\n\n"
        "Setelah login, Anda akan diarahkan ke halaman yang mengatakan login berhasil."
    )

    # Start HTTP server to catch the redirect and get auth code
    auth_code = get_auth_code(str(user_id))
    if not auth_code:
        await update.message.reply_text("Login gagal atau dibatalkan.")
        return

    flow.fetch_token(code=auth_code)
    creds = flow.credentials
    save_credentials(user_id, creds)
    await update.message.reply_text("Login berhasil! Anda sekarang dapat mengupload file.")

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        save_json(SESSIONS_FILE, sessions)
        await update.message.reply_text("Logout berhasil.")
    else:
        await update.message.reply_text("Anda belum login.")

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    user_files = files.get(str(user_id), [])
    if not user_files:
        await update.message.reply_text("Anda belum mengupload file apapun.")
        return

    msg = "File yang sudah Anda upload:\n"
    for idx, f in enumerate(user_files, start=1):
        mime = f.get('mime_type', 'unknown')
        msg += f"{idx}. {f['name']} ({mime})\n"
    msg += "\nGunakan perintah /delete <nomor_file> untuk menghapus file."
    await update.message.reply_text(msg)

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Gunakan perintah: /get <nomor_file>")
        return

    try:
        file_index = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("Nomor file harus berupa angka.")
        return

    user_files = files.get(str(user_id), [])
    if file_index < 0 or file_index >= len(user_files):
        await update.message.reply_text("Nomor file tidak valid.")
        return

    file_metadata = user_files[file_index]
    file_id = file_metadata['id']
    file_name = file_metadata.get('name', 'file')

    temp_file_path = f'temp_{file_id}'

    try:
        request = service.files().get_media(fileId=file_id)
        with open(temp_file_path, 'wb') as fh:
            downloader = googleapiclient.http.MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

        if not os.path.exists(temp_file_path):
            await update.message.reply_text("File tidak ditemukan setelah diunduh.")
            return

        with open(temp_file_path, 'rb') as f:
            await update.message.reply_document(f, filename=file_name)

        os.remove(temp_file_path)
    except Exception as e:
        logger.error(f"Error downloading file {file_id}: {e}")
        await update.message.reply_text(f"Gagal mengunduh file: {e}")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    file = update.message.document or (update.message.photo[-1] if update.message.photo else None)
    if not file:
        await update.message.reply_text("File tidak ditemukan.")
        return

    file_id = file.file_id
    mime_type = file.mime_type if hasattr(file, 'mime_type') else 'application/octet-stream'

    # Check if caption exists and use it as filename
    caption = update.message.caption
    if caption and caption.strip():
        file_name = caption.strip()
        # Download file from Telegram
        file_obj = await context.bot.get_file(file_id)
        file_path = f'temp_{file_id}'
        await file_obj.download_to_drive(file_path)

        try:
            # Upload to Google Drive with caption as filename
            media = googleapiclient.http.MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            file_metadata = {'name': file_name}
            uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

            # Save metadata including mime_type
            await update.message.reply_text(f"File '{file_name}' berhasil diupload ke Google Drive.")
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            await update.message.reply_text("Gagal mengupload file.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        original_file_name = file.file_name if hasattr(file, 'file_name') else f"photo_{file_id}.jpg"
        # Save file info in user_data to get filename from user input next
        context.user_data['upload_file_info'] = {
            'file_id': file_id,
            'original_file_name': original_file_name,
            'mime_type': mime_type
        }

        await update.message.reply_text(
            f"File diterima: {original_file_name}\n"
            "Silakan kirim nama file yang ingin Anda gunakan untuk menyimpan file ini (termasuk ekstensi)."
        )

from telegram.ext import ConversationHandler, MessageHandler, filters

ASK_FILENAME = 1

async def receive_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if 'upload_file_info' not in context.user_data:
        await update.message.reply_text("Tidak ada file yang sedang diupload. Silakan kirim file terlebih dahulu.")
        return ConversationHandler.END

    file_info = context.user_data['upload_file_info']
    file_id = file_info['file_id']
    mime_type = file_info['mime_type']

    file_name = update.message.text.strip()
    if not file_name:
        await update.message.reply_text("Nama file tidak boleh kosong. Silakan kirim nama file yang valid.")
        return ASK_FILENAME

    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return ConversationHandler.END

    # Download file from Telegram
    file_obj = await context.bot.get_file(file_id)
    file_path = f'temp_{file_id}'
    await file_obj.download_to_drive(file_path)

    try:
        # Upload to Google Drive with correct mime type and user-given name
        media = googleapiclient.http.MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        file_metadata = {'name': file_name}
        uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

        # Save metadata including mime_type
        user_files = files.get(str(user_id), [])
        user_files.append({'id': uploaded_file['id'], 'name': file_name, 'mime_type': mime_type})
        files[str(user_id)] = user_files
        save_json(FILES_FILE, files)

        await update.message.reply_text(f"File '{file_name}' berhasil diupload ke Google Drive.")
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        await update.message.reply_text("Gagal mengupload file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    context.user_data.pop('upload_file_info', None)
    return ConversationHandler.END

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    user_files = files.get(str(user_id), [])
    if not user_files:
        await update.message.reply_text("Anda belum mengupload file apapun.")
        return

    msg = "File yang sudah Anda upload:\n"
    for idx, f in enumerate(user_files, start=1):
        mime = f.get('mime_type', 'unknown')
        msg += f"{idx}. {f['name']} ({mime})\n"
    msg += "\nGunakan perintah /delete <nomor_file> untuk menghapus file."
    await update.message.reply_text(msg)

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Gunakan perintah: /get <nomor_file>")
        return

    try:
        file_index = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("Nomor file harus berupa angka.")
        return

    user_files = files.get(str(user_id), [])
    if file_index < 0 or file_index >= len(user_files):
        await update.message.reply_text("Nomor file tidak valid.")
        return

    file_metadata = user_files[file_index]
    file_id = file_metadata['id']
    file_name = file_metadata.get('name', 'file')

    try:
        request = service.files().get_media(fileId=file_id)
        fh = open(f'temp_{file_id}', 'wb')
        downloader = googleapiclient.http.MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.close()

        with open(f'temp_{file_id}', 'rb') as f:
            await update.message.reply_document(f, filename=file_name)

        os.remove(f'temp_{file_id}')
    except Exception as e:
        logger.error(f"Error downloading file {file_id}: {e}")
        await update.message.reply_text("Gagal mengunduh file.")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands_text = (
        "Menu perintah yang tersedia:\n"
        "/start - Mulai bot\n"
        "/login - Login ke Google Drive\n"
        "/logout - Logout dari Google Drive\n"
        "/list - Daftar file yang diupload\n"
        "/get <nomor_file> - Unduh file berdasarkan nomor\n"
        "/delete <nomor_file> - Hapus file berdasarkan nomor\n"
        "/menu - Tampilkan menu perintah ini\n\n"
        "Instruksi Upload File:\n"
        "- Kirim file yang ingin diupload ke bot.\n"
        "- Anda dapat memberikan nama file dengan mengirim caption saat mengirim file.\n"
        "- Jika tidak memberikan caption, bot akan meminta Anda mengirim nama file (termasuk ekstensi).\n"
        "- Contoh nama file yang valid:\n"
        "  - dokumen.pdf\n"
        "  - foto_liburan.jpg\n"
        "  - laporan_keuangan.xlsx\n"
        "  - presentasi.pptx\n"
        "  - musik.mp3\n"
        "- Pastikan menyertakan ekstensi file yang sesuai agar file dapat dikenali dengan benar."
    )
    await update.message.reply_text(commands_text)

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Gunakan perintah: /delete <nomor_file>")
        return

    try:
        file_index = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("Nomor file harus berupa angka.")
        return

    user_files = files.get(str(user_id), [])
    if file_index < 0 or file_index >= len(user_files):
        await update.message.reply_text("Nomor file tidak valid.")
        return

    file_metadata = user_files[file_index]
    file_id = file_metadata['id']
    file_name = file_metadata.get('name', 'file')

    try:
        service.files().delete(fileId=file_id).execute()
        user_files.pop(file_index)
        files[str(user_id)] = user_files
        save_json(FILES_FILE, files)
        await update.message.reply_text(f"File '{file_name}' berhasil dihapus.")
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}")
        await update.message.reply_text("Gagal menghapus file.")

def main():
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file)],
        states={
            ASK_FILENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_filename)],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("login", login))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("get", get_file))
    application.add_handler(CommandHandler("delete", delete_file))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
