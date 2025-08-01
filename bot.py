import os
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from aiohttp import web

# Konfigurasi logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DriveBot:
    def __init__(self):
        # Ambil konfigurasi dari environment variables
        self.token = os.getenv('TELEGRAM_TOKEN')
        self.client_id = os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        self.redirect_uri = os.getenv('REDIRECT_URI', 'https://aldrive-bot.up.railway.app/oauth2callback')
        
        # In-memory storage (produksi sebaiknya pakai database)
        self.user_sessions = {}  # Format: {user_id: {'creds': creds_dict, 'files': [file_list]}}
        
        if not all([self.token, self.client_id, self.client_secret]):
            raise ValueError("Missing required environment variables")

    async def handle_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Menangani upload file"""
        user_id = update.effective_user.id
        
        if user_id not in self.user_sessions:
            await update.message.reply_text("❌ Anda belum login. Gunakan /login terlebih dahulu.")
            return

        try:
            document = update.message.document or update.message.photo[-1]
            file = await context.bot.get_file(document.file_id)
            file_path = f"/tmp/{document.file_id}"
            await file.download_to_drive(file_path)
            
            # Upload ke Google Drive
            creds = Credentials(**self.user_sessions[user_id]['creds'])
            service = build('drive', 'v3', credentials=creds)
            
            file_metadata = {'name': document.file_name or f"file_{document.file_id}"}
            media = MediaFileUpload(file_path)
            
            drive_file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,name'
            ).execute()
            
            # Simpan file metadata
            if 'files' not in self.user_sessions[user_id]:
                self.user_sessions[user_id]['files'] = []
            
            self.user_sessions[user_id]['files'].append({
                'id': drive_file.get('id'),
                'name': drive_file.get('name')
            })
            
            await update.message.reply_text(f"✅ File {drive_file.get('name')} berhasil diunggah!")
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            await update.message.reply_text(f"❌ Gagal mengupload file: {str(e)}")

    def get_auth_url(self, user_id):
        """Generate OAuth URL"""
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }
            },
            scopes=['https://www.googleapis.com/auth/drive.file'],
            redirect_uri=self.redirect_uri
        )
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=str(user_id)
        )
        return auth_url

    async def handle_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler perintah /login"""
        auth_url = self.get_auth_url(update.effective_user.id)
        await update.message.reply_text(
            f"🔗 Silakan login via Google:\n{auth_url}\n\n"
            "Setelah berhasil, Anda bisa langsung mengupload file."
        )

    async def handle_oauth_callback(self, request):
        """Menangani OAuth callback"""
        try:
            user_id = request.query.get('state')
            code = request.query.get('code')
            
            if not code:
                return web.Response(text="Error: Missing authorization code")
            
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"
                    }
                },
                scopes=['https://www.googleapis.com/auth/drive.file'],
                redirect_uri=self.redirect_uri
            )
            
            flow.fetch_token(code=code)
            creds = flow.credentials
            
            # Simpan credentials
            self.user_sessions[int(user_id)] = {
                'creds': {
                    'token': creds.token,
                    'refresh_token': creds.refresh_token,
                    'token_uri': creds.token_uri,
                    'client_id': creds.client_id,
                    'client_secret': creds.client_secret,
                    'scopes': creds.scopes
                }
            }
            
            return web.Response(
                text="Login berhasil! Kembali ke Telegram untuk mulai mengupload.",
                headers={'Content-Type': 'text/plain; charset=utf-8'}
            )
            
        except Exception as e:
            logger.error(f"Callback error: {e}")
            return web.Response(
                text=f"Error: {str(e)}",
                status=500
            )

async def main():
    bot = DriveBot()
    
    # Setup Telegram Bot
    application = ApplicationBuilder().token(bot.token).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Aldrive Bot siap digunakan!")))
    application.add_handler(CommandHandler("login", bot.handle_login))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, bot.handle_upload))
    
    # Setup web server
    web_app = web.Application()
    web_app.router.add_get('/', lambda r: web.Response(text="Aldrive Bot Running"))
    web_app.router.add_get('/oauth2callback', bot.handle_oauth_callback)
    
    # Jalankan server web dan bot dalam satu loop
    await application.initialize()
    await application.start_polling()
    await web.run_app(web_app, port=int(os.getenv('PORT', 8000)))

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
