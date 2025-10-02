import logging
import requests
import os
import tempfile
import asyncio
import time
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import yt_dlp
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
class Config:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.chat_id = os.getenv('CHAT_ID')
        self.max_file_size = 2 * 1024 * 1024 * 1024  # 2GB
        self.request_timeout = 30
        self.user_agent = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36'
        self.rate_limit_calls = 5  # Calls per minute
        self.rate_limit_period = 60  # Seconds
        
        # Validate required environment variables
        if not self.bot_token:
            raise ValueError("BOT_TOKEN environment variable is required")
        if not self.chat_id:
            raise ValueError("CHAT_ID environment variable is required")
        
        try:
            self.chat_id = int(self.chat_id)
        except ValueError:
            raise ValueError("CHAT_ID must be a valid integer")

config = Config()

# Rate Limiter
class RateLimiter:
    def __init__(self, calls_per_minute=5):
        self.calls_per_minute = calls_per_minute
        self.last_calls = []
    
    def is_allowed(self):
        now = time.time()
        # Remove calls older than 1 minute
        self.last_calls = [call for call in self.last_calls if now - call < 60]
        
        if len(self.last_calls) < self.calls_per_minute:
            self.last_calls.append(now)
            return True
        return False

rate_limiter = RateLimiter(config.rate_limit_calls)

def get_ydl_opts(temp_file=None):
    """Get yt-dlp configuration options"""
    opts = {
        'quiet': True,
        'no_warnings': False,
        'extract_flat': False,
        'format': 'best[ext=mp4]/best',
        'outtmpl': temp_file or '%(id)s.%(ext)s',
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'http_headers': {
            'User-Agent': config.user_agent,
            'Referer': 'https://hanime.tv/',
            'Origin': 'https://hanime.tv',
            'Accept': 'video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'video',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1'
        }
    }
    
    # Add cookies if available
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'
        logger.info("Using cookies.txt for authentication")
    
    return opts

def extract_video_info(video_url):
    """Extract video information using yt-dlp"""
    ydl_opts = get_ydl_opts()
    ydl_opts['quiet'] = True
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return {
                'title': info.get('title', 'Random Hentai Video'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'direct_url': info.get('url', video_url),
                'formats': info.get('formats', []),
                'description': info.get('description', '')
            }
    except Exception as e:
        logger.error(f"Failed to extract video info: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    if update.effective_chat.id != config.chat_id:
        await update.message.reply_text("Sorry, this bot is private and only for the owner.")
        return
    
    welcome_message = (
        "🎉 Welcome to the Hanime Random Video Bot! 🎉\n"
        "Use /random to get a random video from hanime.tv.\n"
        "Videos are sent directly (up to 2GB); otherwise, a thumbnail and link are provided.\n"
        "Use /status to check bot status.\n"
        "Optional: Place cookies.txt in /app for authenticated videos if needed.\n"
        "Comply with hanime.tv's terms and local laws."
    )
    await update.message.reply_text(welcome_message)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if update.effective_chat.id != config.chat_id:
        await update.message.reply_text("Sorry, this command is only for the owner.")
        return
        
    status_msg = (
        "🤖 Bot Status:\n"
        "✅ Online and functioning\n"
        f"📊 Temp directory: {tempfile.gettempdir()}\n"
        f"🍪 Cookies: {'✅ Loaded' if os.path.exists('cookies.txt') else '❌ Not found'}\n"
        f"🚦 Rate limit: {rate_limiter.calls_per_minute} calls per minute\n"
        f"💾 Max file size: {config.max_file_size / (1024**3):.1f} GB"
    )
    await update.message.reply_text(status_msg)

async def random_hanime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /random command to fetch and send a random video."""
    if update.effective_chat.id != config.chat_id:
        await update.message.reply_text("Sorry, this command is only for the owner.")
        return

    # Check rate limiting
    if not rate_limiter.is_allowed():
        await update.message.reply_text("🚫 Rate limit exceeded. Please wait a minute before trying again.")
        return

    temp_file = None
    thumb = None
    message = ""
    
    try:
        # Send initial processing message
        processing_msg = await update.message.reply_text("🔄 Processing your random video...")

        # Step 1: Follow the /browse/random redirect
        session = requests.Session()
        session.headers.update({
            'User-Agent': config.user_agent
        })
        
        response = session.get('https://hanime.tv/browse/random', 
                             allow_redirects=True, 
                             timeout=config.request_timeout)
        response.raise_for_status()

        # Get the final video URL and ensure it's a video page
        video_url = response.url
        if '/videos/hentai/' not in video_url:
            soup = BeautifulSoup(response.text, 'html.parser')
            video_links = [a['href'] for a in soup.find_all('a', href=True) 
                          if '/videos/hentai/' in a['href']]
            if not video_links:
                await processing_msg.edit_text("No video links found on the random page. Try again.")
                return
            video_url = 'https://hanime.tv' + video_links[0]
            response = session.get(video_url, timeout=config.request_timeout)
            response.raise_for_status()

        logger.info(f"Processing video URL: {video_url}")

        # Parse page for title and extract video info
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.find('title').text.strip() if soup.find('title') else 'Random Hentai Video'
        
        # Extract video information
        video_info = extract_video_info(video_url)
        if video_info:
            title = video_info['title']
            thumb = video_info['thumbnail']
            # Format duration if available
            if video_info['duration']:
                minutes, seconds = divmod(video_info['duration'], 60)
                duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = "Unknown"
        else:
            duration_str = "Unknown"

        # Create informative message
        message = (
            f"🎲 {title}\n"
            f"⏱ Duration: {duration_str}\n"
            f"🔗 {video_url}"
        )

        await processing_msg.edit_text("📥 Downloading video...")

        # Step 2: Download the video to a temporary file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp:
            temp_file = temp.name
        
        ydl_opts = get_ydl_opts(temp_file)
        
        download_success = False
        download_attempts = [
            video_url,  # Primary attempt with original URL
        ]

        # Add fallback URLs from page scraping
        iframe = soup.find('iframe', src=True)
        if iframe:
            download_attempts.append(iframe['src'])
        
        video_tag = soup.find('video')
        if video_tag and video_tag.find('source', src=True):
            download_attempts.append(video_tag.find('source')['src'])
        
        source_tags = soup.find_all('source', src=True)
        for source in source_tags:
            download_attempts.append(source['src'])

        # Try each download URL
        for attempt_url in download_attempts:
            if download_success:
                break
                
            try:
                logger.info(f"Attempting download from: {attempt_url}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([attempt_url])
                
                if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                    download_success = True
                    logger.info("Download successful")
                    break
                    
            except Exception as dl_error:
                logger.warning(f"Download attempt failed for {attempt_url}: {dl_error}")
                continue

        if not download_success:
            raise Exception("All download attempts failed")

        # Check file size
        file_size = os.path.getsize(temp_file)
        logger.info(f"Downloaded video size: {file_size / (1024 * 1024):.2f} MB")
        
        await processing_msg.edit_text("📤 Sending video...")

        if file_size < config.max_file_size:
            # Send video with caption
            await context.bot.send_video(
                chat_id=config.chat_id,
                video=open(temp_file, 'rb'),
                caption=message[:1024],  # Telegram caption limit
                supports_streaming=True,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30
            )
            await processing_msg.edit_text("✅ Video sent successfully! 🎥")
        else:
            # Fallback: Send thumbnail with link if too large
            logger.info("Video exceeds size limit; sending thumbnail and link instead.")
            if thumb:
                await context.bot.send_photo(
                    chat_id=config.chat_id, 
                    photo=thumb, 
                    caption=message
                )
            else:
                await context.bot.send_message(
                    chat_id=config.chat_id, 
                    text=message
                )
            await processing_msg.edit_text("📦 Video too large; link sent instead! 🔗")

    except Exception as e:
        logger.error(f"Error in random_hanime: {str(e)}")
        
        # Final fallback: Send available information
        error_message = (
            f"❌ Failed to download video: {str(e)[:100]}\n"
            f"🔗 Here's the link: {video_url if 'video_url' in locals() else 'Not available'}"
        )
        
        try:
            if thumb:
                await context.bot.send_photo(
                    chat_id=config.chat_id,
                    photo=thumb,
                    caption=error_message
                )
            else:
                await context.bot.send_message(
                    chat_id=config.chat_id,
                    text=error_message
                )
            await update.message.reply_text("⚠️ Sent fallback information due to download error.")
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            await update.message.reply_text("❌ Complete failure. Please try again later.")

    finally:
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                logger.info("Temporary file cleaned up")
            except Exception as e:
                logger.error(f"Failed to delete temp file: {e}")

def main() -> None:
    """Start the bot with health checks."""
    # Singleton lock
    lock_file = '/tmp/bot_instance.lock'
    try:
        with open(lock_file, 'x') as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        logger.error("Another bot instance is running. Exiting to avoid conflicts.")
        return
        
    try:
        # Initialize application
        application = Application.builder().token(config.bot_token).build()
        
        # Test bot token validity
        bot_info = application.bot.get_me()
        logger.info(f"Bot started successfully: @{bot_info.username}")
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("random", random_hanime))
        application.add_handler(CommandHandler("status", status))
        
        # Start polling
        logger.info("Bot is starting...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        
    finally:
        # Clean up lock file
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info("Lock file cleaned up")
            except Exception as e:
                logger.error(f"Failed to remove lock file: {e}")

if __name__ == '__main__':
    main()