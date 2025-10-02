import logging
import requests
import os
import tempfile
import time
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import yt_dlp
from playwright.async_api import async_playwright

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Get environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = int(os.getenv('CHAT_ID')) if os.getenv('CHAT_ID') else None

async def reset_webhook():
    """Reset Telegram webhook to ensure polling works."""
    try:
        response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
        response.raise_for_status()
        logger.info("Webhook reset successfully")
    except Exception as e:
        logger.error(f"Failed to reset webhook: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Sorry, this bot is private and only for the owner.")
        return
    welcome_message = (
        "🎉 Welcome to the Hanime Random Video Bot! 🎉\n"
        "Use /random to get a random video from hanime.tv.\n"
        "Videos are sent directly (up to 2GB); otherwise, a thumbnail and link are provided.\n"
        "Optional: Place cookies.txt in /app for authenticated videos if needed.\n"
        "Comply with hanime.tv's terms and local laws."
    )
    await update.message.reply_text(welcome_message)

async def scrape_m3u8_with_playwright(video_url):
    """Use Playwright to render the page and extract M3U8 URLs."""
    m3u8_urls = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
        )
        page = await context.new_page()
        try:
            # Capture network requests
            def handle_response(response):
                if 'm3u8' in response.url:
                    m3u8_urls.append(response.url)
                    logger.info(f"Found M3U8 URL via Playwright: {response.url}")

            page.on("response", handle_response)
            await page.goto(video_url, wait_until="networkidle", timeout=30000)
            content = await page.content()
            # Parse content with BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            # Search for M3U8 in scripts
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'm3u8' in script.string:
                    import re
                    urls = re.findall(r'https?://[^\s]*\.m3u8', script.string)
                    m3u8_urls.extend(urls)
            # Search for iframe or video sources
            iframe = soup.find('iframe', src=True)
            if iframe:
                m3u8_urls.append(iframe['src'])
            video_tag = soup.find('video')
            if video_tag and video_tag.find('source', src=True):
                m3u8_urls.append(video_tag.find('source')['src'])
            source_tags = soup.find_all('source', src=True)
            for source in source_tags:
                m3u8_urls.append(source['src'])
        except Exception as e:
            logger.error(f"Playwright scraping error: {str(e)}")
        finally:
            await browser.close()
    return list(set(m3u8_urls))  # Remove duplicates

async def random_hanime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /random command to fetch and send a random video."""
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Sorry, this command is only for the owner.")
        return

    temp_file = None
    try:
        # Step 1: Follow the /browse/random redirect
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36'
        })
        response = session.get('https://hanime.tv/browse/random', allow_redirects=True)
        response.raise_for_status()

        # Get the final video URL and ensure it's a video page
        video_url = response.url
        if '/videos/hentai/' not in video_url:
            soup = BeautifulSoup(response.text, 'html.parser')
            video_links = [a['href'] for a in soup.find_all('a', href=True) if '/videos/hentai/' in a['href']]
            if not video_links:
                await update.message.reply_text("No video links found on the random page. Try again.")
                return
            video_url = 'https://hanime.tv' + video_links[0]
            response = session.get(video_url)
            response.raise_for_status()

        logger.info(f"Processing video URL: {video_url}")

        # Parse page for title
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.find('title').text.strip() if soup.find('title') else 'Random Hentai Video'

        # Initialize message for fallback
        message = f"🎲 Random Video: {title}\n🔗 Backup Link: {video_url}"

        # Step 2: Try Playwright scraping first
        direct_url = video_url
        thumb = None
        m3u8_urls = await scrape_m3u8_with_playwright(video_url)
        if m3u8_urls:
            direct_url = m3u8_urls[0]
            logger.info(f"Using Playwright-scraped M3U8 URL: {direct_url}")
        else:
            logger.warning("Playwright found no M3U8 URLs; trying yt-dlp")

        # Step 3: Try yt-dlp if Playwright fails
        if direct_url == video_url:
            ydl_opts = {
                'quiet': False,
                'no_warnings': False,
                'extract_flat': False,
                'format': 'best[ext=mp4]/best',
                'outtmpl': '%(id)s.%(ext)s',
                'noplaylist': True,
                'merge_output_format': 'mp4',
                'ffmpeg_location': '/usr/bin/ffmpeg',
                'downloader': 'ffmpeg',
                'downloader_args': {'ffmpeg': ['-loglevel', 'debug']},
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36',
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
            if os.path.exists('cookies.txt'):
                ydl_opts['cookiefile'] = 'cookies.txt'
                logger.info("Using cookies.txt for authentication")
            else:
                logger.info("No cookies.txt found; proceeding without authentication")

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                        direct_url = info.get('url', video_url)
                        thumb = info.get('thumbnail', None)
                        logger.info(f"Extracted direct URL: {direct_url}")
                        break
                except Exception as ydl_error:
                    logger.error(f"yt-dlp extraction error (attempt {attempt+1}/{max_retries}): {str(ydl_error)}")
                    if attempt < max_retries - 1:
                        time.sleep(2)
                    else:
                        logger.error("Max retries reached for yt-dlp extraction")

            # Fallback: Scrape statically if yt-dlp fails
            if direct_url == video_url:
                logger.info("Falling back to static scraping for M3U8 URL")
                m3u8_urls = [a['href'] for a in soup.find_all('a', href=True) if 'm3u8' in a['href']]
                if not m3u8_urls:
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if script.string and 'm3u8' in script.string:
                            import re
                            urls = re.findall(r'https?://[^\s]*\.m3u8', script.string)
                            m3u8_urls.extend(urls)
                if m3u8_urls:
                    direct_url = m3u8_urls[0]
                    logger.info(f"Scraped M3U8 URL: {direct_url}")
                else:
                    logger.warning("No M3U8 URLs found in static scrape")

        # Step 4: Download the video
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp:
            temp_file = temp.name
        ydl_opts['outtmpl'] = temp_file
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([direct_url])
        except Exception as dl_error:
            logger.error(f"yt-dlp download error: {str(dl_error)}")

        # Check if file was downloaded
        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
            logger.error("Video download failed; file is empty or not created.")
            # Try iframe or video/source tags
            iframe = soup.find('iframe', src=True)
            if iframe:
                embed_url = iframe['src']
                logger.info(f"Fallback: Trying embed URL: {embed_url}")
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([embed_url])
                except Exception as embed_error:
                    logger.error(f"Embed download error: {str(embed_error)}")
            else:
                video_tag = soup.find('video')
                if video_tag and video_tag.find('source', src=True):
                    embed_url = video_tag.find('source')['src']
                    logger.info(f"Fallback: Trying video tag URL: {embed_url}")
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([embed_url])
                    except Exception as video_error:
                        logger.error(f"Video tag download error: {str(video_error)}")
                else:
                    source_tags = soup.find_all('source', src=True)
                    for source in source_tags:
                        embed_url = source['src']
                        logger.info(f"Fallback: Trying source tag URL: {embed_url}")
                        try:
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                ydl.download([embed_url])
                            break
                        except Exception as source_error:
                            logger.error(f"Source tag download error: {str(source_error)}")

        # Check file again
        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
            logger.error("All download attempts failed.")
            if 'm3u8' in direct_url:
                logger.info(f"Retrying M3U8 URL with FFmpeg: {direct_url}")
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([direct_url])
                except Exception as m3u8_error:
                    logger.error(f"M3U8 retry error: {str(m3u8_error)}")
            if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
                raise Exception("Failed to download video after all attempts. Check hanime.tv access or plugin.")

        # Check file size
        file_size = os.path.getsize(temp_file)
        logger.info(f"Downloaded video size: {file_size / (1024 * 1024):.2f} MB")
        if file_size < 2 * 1024 * 1024 * 1024:  # Less than 2GB
            await context.bot.send_video(
                chat_id=CHAT_ID,
                video=open(temp_file, 'rb'),
                caption=message[:200],
                supports_streaming=True
            )
            await update.message.reply_text("Video sent to your chat! 🎥")
        else:
            logger.info("Video exceeds 2GB; sending thumbnail and link instead.")
            if thumb:
                await context.bot.send_photo(chat_id=CHAT_ID, photo=thumb, caption=message)
            else:
                await context.bot.send_message(chat_id=CHAT_ID, text=message)
            await update.message.reply_text("Video too large (over 2GB); link sent to your chat! 🔗")

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        if thumb:
            await context.bot.send_photo(chat_id=CHAT_ID, photo=thumb, caption=message)
        else:
            await context.bot.send_message(chat_id=CHAT_ID, text=message)
        await update.message.reply_text(f"Oops, video download failed: {str(e)}. Sent link instead.")

    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception as e:
                logger.error(f"Failed to delete temp file: {e}")

def main() -> None:
    """Start the bot."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("BOT_TOKEN or CHAT_ID not set in environment variables.")
        return
    # Check for existing bot instance
    lock_file = '/tmp/bot_instance.lock'
    try:
        with open(lock_file, 'x') as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        logger.error("Another bot instance is running. Exiting to avoid conflicts.")
        return
    try:
        import asyncio
        asyncio.run(reset_webhook())
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("random", random_hanime))
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.error(f"Failed to remove lock file: {e}")

if __name__ == '__main__':
    main()