import os
import logging
import asyncio
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from aiohttp import web

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DriveBot:
    def __init__(self):
        # Validasi environment variables
        required_vars = ['TELEGRAM_TOKEN', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        self.token = os.getenv('TELEGRAM_TOKEN')
        self.client_id = os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        self.redirect_uri = os.getenv('REDIRECT_URI', 'https://aldrive-bot.up.railway.app/oauth2callback')
        self.user_sessions = {}

    async def upload_to_drive(self, user_id, file_path, file_name):
        """Helper function for drive upload"""
        creds = Credentials(**self.user_sessions[user_id]['creds'])
        service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path)
        return service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name'
        ).execute()

    async def handle_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            if user_id not in self.user_sessions:
                await update.message.reply_text("❌ Silakan login dulu dengan /login")
                return

            document = update.message.document or update.message.photo[-1]
            file = await context.bot.get_file(document.file_id)
            temp_path = f"/tmp/{document.file_id}"
            
            await file.download_to_drive(temp_path)
            result = await self.upload_to_drive(user_id, temp_path, document.file_name or f"file_{document.file_id}")
            
            if 'files' not in self.user_sessions[user_id]:
                self.user_sessions[user_id]['files'] = []
            self.user_sessions[user_id]['files'].append({'id': result['id'], 'name': result['name']})
            
            await update.message.reply_text(f"✅ Berhasil upload: {result['name']}")

        except Exception as e:
            logger.error(f"Upload error: {str(e)}")
            await update.message.reply_text(f"❌ Error: {str(e)}")

    def get_auth_url(self, user_id):
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
        return flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=str(user_id)
        )[0]

    async def handle_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        auth_url = self.get_auth_url(update.effective_user.id)
        await update.message.reply_text(f"🔗 Login disini: {auth_url}")

    async def oauth_callback(self, request):
        try:
            user_id = int(request.query.get('state'))
            code = request.query.get('code')
            
            if not code:
                return web.Response(text="Missing authorization code", status=400)

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
            
            self.user_sessions[user_id] = {
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
                text="Login berhasil! Kembali ke Telegram",
                headers={'Content-Type': 'text/plain; charset=utf-8'}
            )

        except Exception as e:
            logger.error(f"OAuth callback error: {str(e)}")
            return web.Response(
                text=f"Error: {str(e)}",
                status=500
            )

async def setup_bot():
    bot = DriveBot()
    application = ApplicationBuilder().token(bot.token).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("Bot siap!")))
    application.add_handler(CommandHandler("login", bot.handle_login))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, bot.handle_upload))
    
    # Setup web server
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot running"))
    app.router.add_get('/oauth2callback', bot.oauth_callback)
    
    return application, app

async def main():
    application, web_app = await setup_bot()
    
    # Run both applications
    await application.initialize()
    await application.start()
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8000))).start()
    
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
