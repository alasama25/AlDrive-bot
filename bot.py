import json
import logging
import os
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import googleapiclient.http
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from aiohttp import web

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
REDIRECT_PORT = int(os.getenv('PORT', '8080'))
raw_redirect_host = os.getenv('REDIRECT_HOST', '0.0.0.0')
REDIRECT_HOST = re.sub(r':\d+$', '', raw_redirect_host) # Remove port from host if present
REDIRECT_URI = f'https://{REDIRECT_HOST}/oauth2callback'

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

# Check for essential environment variables
if not TELEGRAM_TOKEN or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.error("Missing TELEGRAM_TOKEN or GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET environment variables")
    exit(1)

# In-memory storage for user sessions and file metadata
sessions = {}  # user_id -> credentials dict
files = {}     # user_id -> list of files metadata (id, name, mime_type)

# Conversation states for file upload
ASK_FILENAME = 1

# OAuth2 flow setup
def create_flow(state=None):
    """Creates a Google OAuth2 flow object."""
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
        scopes=['https://www.googleapis.com/auth/drive.file'], # Scope for file-specific access
        redirect_uri=REDIRECT_URI,
        state=state
    )

# Define routes for the web server (for OAuth2 callback)
routes = web.RouteTableDef()

@routes.get('/oauth2callback')
async def oauth2callback(request):
    """Handles the OAuth2 callback from Google."""
    code = request.query.get('code')
    state = request.query.get('state') # user_id is passed as state
    if not code or not state:
        logger.error("Code or state not found in OAuth2 callback URL.")
        return web.Response(text="Code or state not found in URL.", status=400)
    
    flow = create_flow(state=state)
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        # Store credentials in session
        sessions[str(state)] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        logger.info(f"User {state} successfully logged in.")
        return web.Response(text="Login successful! You can now return to Telegram and upload files.")
    except Exception as e:
        logger.error(f"Error fetching token in callback for user {state}: {e}")
        return web.Response(text="Failed to fetch token. Please try logging in again.", status=400)

# --- Telegram Bot Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and prompts for login."""
    await update.message.reply_text(
        "Selamat datang! Gunakan /login untuk masuk ke Google Drive Anda."
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates the Google OAuth2 login process."""
    user_id = update.effective_user.id
    flow = create_flow(state=str(user_id))
    # Generate authorization URL
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline', include_granted_scopes='true')
    await update.message.reply_text(
        f"Silakan klik tautan berikut untuk login:\n{auth_url}\n\n"
        "Setelah login, Anda akan diarahkan ke halaman yang menunjukkan login berhasil."
    )

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logs out the user by clearing their session data."""
    user_id = update.effective_user.id
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        if str(user_id) in files:
            del files[str(user_id)] # Also clear file list
        await update.message.reply_text("Logout berhasil.")
        logger.info(f"User {user_id} logged out.")
    else:
        await update.message.reply_text("Anda belum login.")

def load_credentials(user_id):
    """Loads and refreshes Google API credentials for a user."""
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
    # Refresh token if expired and refresh token is available
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
            logger.info(f"Credentials refreshed for user {user_id}.")
        except Exception as e:
            logger.error(f"Gagal memperbarui token untuk pengguna {user_id}: {e}")
            return None
    return creds

def get_drive_service(user_id):
    """Returns a Google Drive API service object for the user."""
    creds = load_credentials(user_id)
    if not creds:
        return None
    return build('drive', 'v3', credentials=creds)

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists files uploaded by the user."""
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    user_files = files.get(str(user_id), [])
    if not user_files:
        await update.message.reply_text("Anda belum mengunggah file apa pun.")
        return

    msg = "File yang telah Anda unggah:\n"
    for idx, f in enumerate(user_files, start=1):
        mime = f.get('mime_type', 'unknown')
        msg += f"{idx}. {f['name']} ({mime})\n"
    msg += "\nGunakan perintah /delete <nomor_file> untuk menghapus file."
    msg += "\nGunakan perintah /get <nomor_file> untuk mengunduh file."
    await update.message.reply_text(msg)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming document or photo messages for upload."""
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
        # If caption is provided, use it as filename and upload directly
        file_name = caption.strip()
        file_obj = await context.bot.get_file(file_id)
        file_path = f'temp_{file_id}'
        await file_obj.download_to_drive(file_path)

        try:
            media = googleapiclient.http.MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            file_metadata = {'name': file_name}
            uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()

            # Save metadata in memory
            user_files = files.get(str(user_id), [])
            user_files.append({'id': uploaded_file['id'], 'name': file_name, 'mime_type': mime_type})
            files[str(user_id)] = user_files

            await update.message.reply_text(f"File '{file_name}' berhasil diunggah ke Google Drive.")
            logger.info(f"User {user_id} uploaded file: {file_name}")
        except Exception as e:
            logger.error(f"Error uploading file for user {user_id}: {e}")
            await update.message.reply_text("Gagal mengunggah file.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path) # Clean up temporary file
    else:
        # If no caption, ask for filename
        original_file_name = file.file_name if hasattr(file, 'file_name') else f"photo_{file_id}.jpg"
        context.user_data['upload_file_info'] = {
            'file_id': file_id,
            'original_file_name': original_file_name,
            'mime_type': mime_type
        }

        await update.message.reply_text(
            f"File diterima: {original_file_name}\n"
            "Silakan kirim nama yang ingin Anda gunakan untuk menyimpan file ini (termasuk ekstensi)."
        )
        return ASK_FILENAME # Transition to conversation state

async def receive_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the filename from the user for a previously sent file."""
    user_id = update.effective_user.id
    if 'upload_file_info' not in context.user_data:
        await update.message.reply_text("Tidak ada file yang sedang diunggah. Silakan kirim file terlebih dahulu.")
        return ConversationHandler.END

    file_info = context.user_data['upload_file_info']
    file_id = file_info['file_id']
    mime_type = file_info['mime_type']

    file_name = update.message.text.strip()
    if not file_name:
        await update.message.reply_text("Nama file tidak boleh kosong. Silakan kirim nama file yang valid.")
        return ASK_FILENAME # Stay in the same state

    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        context.user_data.pop('upload_file_info', None) # Clear info
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

        await update.message.reply_text(f"File '{file_name}' berhasil diunggah ke Google Drive.")
        logger.info(f"User {user_id} uploaded file with custom name: {file_name}")
    except Exception as e:
        logger.error(f"Error uploading file with custom name for user {user_id}: {e}")
        await update.message.reply_text("Gagal mengunggah file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    context.user_data.pop('upload_file_info', None) # Clear info after upload
    return ConversationHandler.END # End the conversation

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads a file from Google Drive based on its index."""
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("Anda belum login. Gunakan /login untuk login.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Gunakan perintah: /get <nomor_file>")
        return

    try:
        file_index = int(context.args[0]) - 1 # Convert to 0-based index
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
    mime_type = file_metadata.get('mime_type', 'application/octet-stream')

    try:
        # Request the file content
        # Using MediaIoBaseDownload for larger files is generally better,
        # but for simplicity, direct execute() is used here.
        # For very large files, consider streaming or chunking.
        request = service.files().get_media(fileId=file_id)
        
        # Create a temporary file to store the downloaded content
        temp_download_path = f'download_temp_{file_id}_{user_id}'
        with open(temp_download_path, 'wb') as fh:
            downloader = googleapiclient.http.MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                # You can add progress updates here if needed
                # print(f"Download progress: {int(status.progress() * 100)}%")

        # Send the file to the user
        with open(temp_download_path, 'rb') as f_to_send:
            await update.message.reply_document(
                document=f_to_send,
                filename=file_name,
                caption=f"File '{file_name}' berhasil diunduh."
            )
        logger.info(f"User {user_id} downloaded file: {file_name}")

    except Exception as e:
        logger.error(f"Error downloading file {file_id} for user {user_id}: {e}")
        await update.message.reply_text("Gagal mengunduh file. Pastikan file tersebut ada dan Anda memiliki izin.")
    finally:
        if os.path.exists(temp_download_path):
            os.remove(temp_download_path) # Clean up temporary downloaded file

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a file from Google Drive based on its index."""
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
        user_files.pop(file_index) # Remove from in-memory list
        files[str(user_id)] = user_files # Update the list
        await update.message.reply_text(f"File '{file_name}' berhasil dihapus.")
        logger.info(f"User {user_id} deleted file: {file_name}")
    except Exception as e:
        logger.error(f"Error deleting file {file_id} for user {user_id}: {e}")
        await update.message.reply_text("Gagal menghapus file.")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the command menu and file upload instructions."""
    commands_text = (
        "Menu perintah yang tersedia:\n"
        "/start - Memulai bot\n"
        "/login - Masuk ke Google Drive\n"
        "/logout - Keluar dari Google Drive\n"
        "/list - Menampilkan daftar file yang diunggah\n"
        "/get <nomor_file> - Mengunduh file berdasarkan nomor\n"
        "/delete <nomor_file> - Menghapus file berdasarkan nomor\n"
        "/menu - Menampilkan menu perintah ini\n\n"
        "Instruksi Unggah File:\n"
        "- Kirim file yang ingin Anda unggah ke bot.\n"
        "- Anda dapat memberikan nama file dengan mengirimkan caption saat mengirim file.\n"
        "- Jika Anda tidak memberikan caption, bot akan meminta Anda untuk mengirimkan nama file (termasuk ekstensi).\n"
        "- Contoh nama file yang valid:\n"
        "  - document.pdf\n"
        "  - vacation_photo.jpg\n"
        "  - financial_report.xlsx\n"
        "  - presentation.pptx\n"
        "  - music.mp3\n"
        "- Pastikan untuk menyertakan ekstensi file yang sesuai agar file dikenali dengan benar."
    )
    await update.message.reply_text(commands_text)

def main():
    """Main function to set up and run the Telegram bot and web server."""
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Conversation handler for file upload (when no caption is provided)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file)],
        states={
            ASK_FILENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_filename)],
        },
        fallbacks=[], # No specific fallbacks needed for this simple conversation
    )

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("login", login))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("get", get_file)) # Now 'get_file' is defined
    application.add_handler(CommandHandler("delete", delete_file))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(conv_handler) # Add the conversation handler

    # Start the web server for OAuth2 callback in a separate process/thread
    # Note: For production, consider using a proper WSGI server like Gunicorn/Waitress
    # and managing processes. For simple deployments, this might suffice.
    logger.info(f"Starting web server for OAuth2 callback on {REDIRECT_HOST}:{REDIRECT_PORT}")
    app = web.Application()
    app.add_routes(routes)
    # Use aiohttp's run_app to start the web server
    # This will block the main thread if not run in a separate process/thread.
    # For simple deployments, running polling after this might not work as expected
    # if run_app blocks. A common pattern is to run the web server in a separate thread
    # or process, or use a framework that integrates both.
    # For this example, we'll assume the environment handles concurrent execution
    # (e.g., a cloud platform running multiple processes/containers).
    # If running locally and you need both to run, you might need to use asyncio.gather
    # or threading.
    
    # A more robust way for local development might be:
    # import threading
    # web_server_thread = threading.Thread(target=lambda: web.run_app(app, port=REDIRECT_PORT))
    # web_server_thread.start()
    # application.run_polling()

    # For simplicity and common cloud deployment patterns where web server and bot polling
    # might be handled by different entry points or concurrent processes:
    # We'll run the web app first, assuming it's the primary entry point for the web hook.
    # If you're using long polling for Telegram, you might need to adjust this.
    # For this setup, it's assumed the web server is for OAuth callback only,
    # and the bot runs via polling.
    # The `run_app` call is blocking, so `run_polling` won't execute unless `run_app` is
    # in a separate thread/process or if you're using webhooks for Telegram.
    # Given the original error, the user is likely running `main()` directly.
    # To make both run, we'll use a simple threading approach for the web server.

    import threading
    web_server_thread = threading.Thread(target=lambda: web.run_app(app, host='0.0.0.0', port=REDIRECT_PORT, access_log=None))
    web_server_thread.daemon = True # Allow main program to exit even if thread is running
    web_server_thread.start()
    logger.info("Web server thread started.")

    logger.info("Starting Telegram bot polling...")
    application.run_polling()
    logger.info("Telegram bot polling stopped.")


if __name__ == '__main__':
    main()
