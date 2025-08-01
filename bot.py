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

REDIRECT_PORT = int(os.getenv('PORT', '8080'))
raw_redirect_host = os.getenv('REDIRECT_HOST', '0.0.0.0')
REDIRECT_HOST = re.sub(r':\\d+$', '', raw_redirect_host)
REDIRECT_URI = f'https://{REDIRECT_HOST}/oauth2callback'

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

if not TELEGRAM_TOKEN or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.error("Missing TELEGRAM_TOKEN or GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET environment variables")
    exit(1)

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

# Define routes for the web server
routes = web.RouteTableDef()

@routes.get('/oauth2callback')
async def oauth2callback(request):
    code = request.query.get('code')
    state = request.query.get('state')
    if not code or not state:
        return web.Response(text="Code or state not found in URL.", status=400)
    
    flow = create_flow(state=state)
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        sessions[str(state)] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        return web.Response(text="Login successful! You can now return to Telegram and upload files.")
    except Exception as e:
        logger.error(f"Error fetching token in callback: {e}")
        return web.Response(text="Failed to fetch token. Please try logging in again.", status=400)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Use /login to log in to your Google Drive."
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    flow = create_flow(state=str(user_id))
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline', include_granted_scopes='true')
    await update.message.reply_text(
        f"Please click the following link to log in:\n{auth_url}\n\n"
        "After logging in, you will be redirected to a page indicating successful login."
    )

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        if str(user_id) in files:
            del files[str(user_id)]
        await update.message.reply_text("Logout successful.")
    else:
        await update.message.reply_text("You are not logged in.")

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
        await update.message.reply_text("You are not logged in. Use /login to log in.")
        return

    user_files = files.get(str(user_id), [])
    if not user_files:
        await update.message.reply_text("You have not uploaded any files.")
        return

    msg = "Files you have uploaded:\n"
    for idx, f in enumerate(user_files, start=1):
        mime = f.get('mime_type', 'unknown')
        msg += f"{idx}. {f['name']} ({mime})\n"
    msg += "\nUse the command /delete <file_number> to delete a file."
    await update.message.reply_text(msg)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("You are not logged in. Use /login to log in.")
        return

    file = update.message.document or (update.message.photo[-1] if update.message.photo else None)
    if not file:
        await update.message.reply_text("File not found.")
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

            await update.message.reply_text(f"File '{file_name}' successfully uploaded to Google Drive.")
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            await update.message.reply_text("Failed to upload file.")
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
            f"File received: {original_file_name}\n"
            "Please send the name you want to use to save this file (including the extension)."
        )

ASK_FILENAME = 1

async def receive_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if 'upload_file_info' not in context.user_data:
        await update.message.reply_text("No file is being uploaded. Please send a file first.")
        return ConversationHandler.END

    file_info = context.user_data['upload_file_info']
    file_id = file_info['file_id']
    mime_type = file_info['mime_type']

    file_name = update.message.text.strip()
    if not file_name:
        await update.message.reply_text("File name cannot be empty. Please send a valid file name.")
        return ASK_FILENAME

    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("You are not logged in. Use /login to log in.")
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

        await update.message.reply_text(f"File '{file_name}' successfully uploaded to Google Drive.")
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        await update.message.reply_text("Failed to upload file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    context.user_data.pop('upload_file_info', None)
    return ConversationHandler.END

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands_text = (
        "Available command menu:\n"
        "/start - Start the bot\n"
        "/login - Log in to Google Drive\n"
        "/logout - Log out from Google Drive\n"
        "/list - List uploaded files\n"
        "/get <file_number> - Download file by number\n"
        "/delete <file_number> - Delete file by number\n"
        "/menu - Show this command menu\n\n"
        "File Upload Instructions:\n"
        "- Send the file you want to upload to the bot.\n"
        "- You can provide a file name by sending a caption when sending the file.\n"
        "- If you do not provide a caption, the bot will ask you to send a file name (including the extension).\n"
        "- Example of valid file names:\n"
        "  - document.pdf\n"
        "  - vacation_photo.jpg\n"
        "  - financial_report.xlsx\n"
        "  - presentation.pptx\n"
        "  - music.mp3\n"
        "- Make sure to include the appropriate file extension for the file to be recognized correctly."
    )
    await update.message.reply_text(commands_text)

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    service = get_drive_service(user_id)
    if not service:
        await update.message.reply_text("You are not logged in. Use /login to log in.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use the command: /delete <file_number>")
        return

    try:
        file_index = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("File number must be a number.")
        return

    user_files = files.get(str(user_id), [])
    if file_index < 0 or file_index >= len(user_files):
        await update.message.reply_text("Invalid file number.")
        return

    file_metadata = user_files[file_index]
    file_id = file_metadata['id']
    file_name = file_metadata.get('name', 'file')

    try:
        service.files().delete(fileId=file_id).execute()
        user_files.pop(file_index)
        files[str(user_id)] = user_files
        await update.message.reply_text(f"File '{file_name}' successfully deleted.")
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}")
        await update.message.reply_text("Failed to delete file.")

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

    # Start the web server for OAuth2 callback
    app = web.Application()
    app.add_routes(routes)
    web.run_app(app, port=REDIRECT_PORT)

    application.run_polling()

if __name__ == '__main__':
    main()
