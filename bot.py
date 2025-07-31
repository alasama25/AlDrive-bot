import json
import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import googleapiclient.http
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

REDIRECT_PORT = int(os.getenv('PORT', '8080'))
REDIRECT_HOST = os.getenv('REDIRECT_HOST', '0.0.0.0')
REDIRECT_URI = f'https://{REDIRECT_HOST}/oauth2callback'

SERVER_BIND_ADDRESS = '0.0.0.0'

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

if not TELEGRAM_TOKEN or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.error("Missing TELEGRAM_TOKEN or GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET environment variables")
    exit(1)

# Since we remove local session and file storage, we keep sessions and files in memory only (will reset on restart)
sessions = {}  # user_id -> credentials dict
files = {}     # user_id -> list of files metadata

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

# We remove HTTP server and get_auth_code function
# Instead, user must manually provide the auth code from the redirect URL

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
        "Setelah login, Anda akan diarahkan ke halaman yang mengatakan login berhasil.\n"
        "Salin kode 'code' dari URL dan kirim ke bot dengan perintah /auth <kode>."
    )

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("Gunakan perintah: /auth <kode>")
        return
    code = context.args[0]
    flow = create_flow(state=str(user_id))
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        sessions[str(user_id)] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        await update.message.reply_text("Login berhasil! Anda sekarang dapat mengupload file.")
    except Exception as e:
        logger.error(f"Error fetching token: {e}")
        await update.message.reply_text("Gagal login, kode tidak valid atau sudah kadaluarsa.")

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        if str(user_id) in files:
            del files[str(user_id)]
        await update.message.reply_text("Logout berhasil.")
    else:
        await update.message.reply_text("Anda belum login.")

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
            # Update session with refreshed token
            sessions[str(user_id)] = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
        except Exception as e:
            logger.error(f"Failed to refresh token for user {user_id}: {e}")
            return None
    return creds

def get_drive_service(user_id):
    creds = load_credentials(user_id)
    if not creds:
        return None
    return build('drive', 'v3', credentials=creds)

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

    caption = update.message.caption
    if caption and caption.strip():
        file_name = caption.strip()
        file_obj = await context.bot.get_file(file_id)
        file_path = f'temp_{file_id}'
        await file_obj.download_to_drive(file_path)

        try:
            media = googleapiclient.http.MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            file_metadata = {'name': file_name}
            uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

            # Save metadata in memory only
            user_files = files.get(str(user_id), [])
            user_files.append({'id': uploaded_file['id'], 'name': file_name, 'mime_type': mime_type})
            files[str(user_id)] = user_files

            await update.message.reply_text(f"File '{file_name}' berhasil diupload ke Google Drive.")
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            await update.message.reply_text("Gagal mengupload file.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        original_file_name = file.file_name if hasattr(file, 'file_name') else f"photo_{file_id}.jpg"
        context.user_data['upload_file_info'] = {
            'file_id': file_id,
            'original_file_name': original_file_name,
            'mime_type': mime_type
        }

        await update.message.reply_text(
            f"File diterima: {original_file_name}\n"
            "Silakan kirim nama file yang ingin Anda gunakan untuk menyimpan file ini (termasuk ekstensi)."
        )

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

    file_obj = await context.bot.get_file(file_id)
    file_path = f'temp_{file_id}'
    await file_obj.download_to_drive(file_path)

    try:
        media = googleapiclient.http.MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        file_metadata = {'name': file_name}
        uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

        user_files = files.get(str(user_id), [])
        user_files.append({'id': uploaded_file['id'], 'name': file_name, 'mime_type': mime_type})
        files[str(user_id)] = user_files

        await update.message.reply_text(f"File '{file_name}' berhasil diupload ke Google Drive.")
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        await update.message.reply_text("Gagal mengupload file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    context.user_data.pop('upload_file_info', None)
    return ConversationHandler.END

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands_text = (
        "Menu perintah yang tersedia:\n"
        "/start - Mulai bot\n"
        "/login - Login ke Google Drive\n"
        "/auth <kode> - Kirim kode otentikasi setelah login\n"
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
    application.add_handler(CommandHandler("auth", auth))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("get", get_file))
    application.add_handler(CommandHandler("delete", delete_file))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
