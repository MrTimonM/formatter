import os
import logging
import tempfile
import time
import re
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Bot token from environment or fallback
BOT_TOKEN = os.getenv("BOT_TOKEN", "8309584216:AAGdAKCK1C-3hikzybWI_O2r5L_NE7NRYQA")

# Admin user IDs (can download unlimited duration)
ADMIN_USER_IDS = set()
admin_ids_str = os.getenv("ADMIN_USER_IDS", "")
if admin_ids_str:
    ADMIN_USER_IDS = set(int(uid.strip()) for uid in admin_ids_str.split(",") if uid.strip())

# Configuration
MAX_DURATION_MINUTES = int(os.getenv("MAX_DURATION_MINUTES", "120"))  # 2 hours default
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2048"))  # 2GB default

def load_env_file():
    """Load environment variables from .env file manually"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
        except Exception as e:
            print(f"Warning: Could not load .env file: {e}")
    else:
        print("Warning: .env file not found, using defaults")

# Load environment variables
load_env_file()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # Changed from INFO to WARNING to reduce logs
)
logger = logging.getLogger(__name__)

# Suppress HTTP request logs from httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Thread pool for blocking operations
executor = ThreadPoolExecutor(max_workers=1)  # Only 1 worker for sequential processing

# Global state to track active downloads
active_downloads = set()
download_stats = {}

# Database file path
DATABASE_FILE = "user_downloads.json"

def load_user_database():
    """Load user download database from JSON file"""
    try:
        if os.path.exists(DATABASE_FILE):
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Error loading database: {e}")
        return {}

def save_user_database(db):
    """Save user download database to JSON file"""
    try:
        with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving database: {e}")

def add_download_record(user_id, username, title, url, file_size_mb):
    """Add a download record for a user"""
    db = load_user_database()
    user_id_str = str(user_id)
    
    # Initialize user record if doesn't exist
    if user_id_str not in db:
        db[user_id_str] = {
            'username': username,
            'total_downloads': 0,
            'total_size_mb': 0,
            'first_download': datetime.now().isoformat(),
            'last_download': datetime.now().isoformat(),
            'downloads': []
        }
    
    # Update user stats
    db[user_id_str]['username'] = username  # Update in case username changed
    db[user_id_str]['total_downloads'] += 1
    db[user_id_str]['total_size_mb'] += file_size_mb
    db[user_id_str]['last_download'] = datetime.now().isoformat()
    
    # Add download record
    download_record = {
        'title': title,
        'url': url,
        'file_size_mb': round(file_size_mb, 2),
        'download_date': datetime.now().isoformat()
    }
    
    db[user_id_str]['downloads'].append(download_record)
    
    # Keep only last 50 downloads per user to prevent database bloat
    if len(db[user_id_str]['downloads']) > 50:
        db[user_id_str]['downloads'] = db[user_id_str]['downloads'][-50:]
    
    save_user_database(db)
    return db[user_id_str]

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMIN_USER_IDS

def format_duration(seconds):
    """Format duration in seconds to human readable format"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def get_user_stats(user_id):
    """Get user download statistics"""
    db = load_user_database()
    user_id_str = str(user_id)
    return db.get(user_id_str, None)
    """Get user download statistics"""
    db = load_user_database()
    user_id_str = str(user_id)
    return db.get(user_id_str, None)

class ProgressHook:
    def __init__(self, chat_id, message):
        self.chat_id = chat_id
        self.message = message
        self.last_update = 0
        self.start_time = time.time()
        self.latest_progress = None
        
    def clean_ansi(self, text):
        """Remove ANSI color codes from text"""
        if not text:
            return text
        # Remove ANSI escape sequences
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', str(text)).strip()
        
    def __call__(self, d):
        current_time = time.time()
        # Update every 2 seconds to avoid API rate limits
        if current_time - self.last_update < 2:
            return
            
        self.last_update = current_time
        
        if d['status'] == 'downloading':
            try:
                # Extract download info and clean ANSI codes
                percent_raw = self.clean_ansi(d.get('_percent_str', '0.0%'))
                speed_raw = self.clean_ansi(d.get('_speed_str', 'N/A'))
                eta_raw = self.clean_ansi(d.get('_eta_str', 'N/A'))
                
                # Extract numeric percentage
                percent_match = re.search(r'(\d+\.?\d*)%', percent_raw)
                if percent_match:
                    percent_num = float(percent_match.group(1))
                    percent_display = f"{percent_num:.1f}%"
                else:
                    percent_num = 0.0
                    percent_display = "0.0%"
                
                total_bytes = d.get('total_bytes_estimate') or d.get('total_bytes', 0)
                downloaded_bytes = d.get('downloaded_bytes', 0)
                
                # Format file size
                if total_bytes > 0:
                    size_mb = total_bytes / (1024 * 1024)
                    downloaded_mb = downloaded_bytes / (1024 * 1024)
                    size_info = f"{downloaded_mb:.1f}MB / {size_mb:.1f}MB"
                else:
                    size_info = f"{downloaded_bytes / (1024 * 1024):.1f}MB"
                
                # Create progress bar (safe calculation)
                progress_bars = int(percent_num / 10) if percent_num <= 100 else 10
                progress_bars = max(0, min(10, progress_bars))  # Ensure between 0-10
                filled_bars = '█' * progress_bars
                empty_bars = '░' * (10 - progress_bars)
                
                # Create progress text (escaped for Markdown)
                progress_text = f"""🎵 *Downloading Audio*

📊 *Progress:* {percent_display}
📦 *Size:* {size_info}
⚡ *Speed:* {speed_raw}
⏱️ *ETA:* {eta_raw}

{filled_bars}{empty_bars}
"""
                
                # Store stats for potential use
                download_stats[self.chat_id] = {
                    'percent': percent_display,
                    'speed': speed_raw,
                    'eta': eta_raw,
                    'size_info': size_info
                }
                
                # Schedule message update for the next event loop iteration
                # Store the progress text to be updated by the main async function
                self.latest_progress = progress_text
                
            except Exception as e:
                logger.warning(f"Error in progress hook: {e}")
            
    async def _update_message(self, text):
        try:
            await self.message.edit_text(text, parse_mode='Markdown')
        except Exception as e:
            logger.warning(f"Failed to update progress message: {e}")

async def download_youtube_audio(url: str, chat_id: str, progress_message) -> str:
    """Download YouTube audio and return the file path"""
    # Create progress hook
    progress_hook = ProgressHook(chat_id, progress_message)
    
    async def update_progress_periodically():
        """Periodically update the progress message"""
        while chat_id in active_downloads:
            try:
                if hasattr(progress_hook, 'latest_progress') and progress_hook.latest_progress:
                    await progress_hook._update_message(progress_hook.latest_progress)
                    progress_hook.latest_progress = None
                await asyncio.sleep(3)  # Update every 3 seconds
            except Exception as e:
                logger.warning(f"Progress update error: {e}")
                break
    
    def download():
        # Get the directory where the script is located (for ffmpeg binaries)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{chat_id}_%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'ffmpeg_location': script_dir,  # Tell yt-dlp where to find ffmpeg
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 60,  # Increased timeout for large files
            'retries': 5,  # More retries for large files
            'fragment_retries': 5,  # Retry fragments
            'file_access_retries': 3,  # Retry file access
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                # Replace extension with .mp3
                mp3_filename = os.path.splitext(filename)[0] + '.mp3'
                return mp3_filename, info.get('title', 'Unknown Title')
        except Exception as e:
            logger.error(f"Download error: {e}")
            raise e
    
    # Start progress update task
    progress_task = asyncio.create_task(update_progress_periodically())
    
    try:
        # Run download in executor
        result = await asyncio.get_event_loop().run_in_executor(executor, download)
        return result
    finally:
        # Cancel progress update task
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

async def download_youtube_video(url: str, chat_id: str, quality: str, progress_message) -> str:
    """Download YouTube video with specified quality and return the file path"""
    # Create progress hook
    progress_hook = ProgressHook(chat_id, progress_message)
    
    async def update_progress_periodically():
        """Periodically update the progress message"""
        while chat_id in active_downloads:
            try:
                if hasattr(progress_hook, 'latest_progress') and progress_hook.latest_progress:
                    # Update progress text to show video download
                    video_progress = progress_hook.latest_progress.replace(
                        "🎵 *Downloading Audio*", 
                        f"🎬 *Downloading Video ({quality})*"
                    )
                    await progress_hook._update_message(video_progress)
                    progress_hook.latest_progress = None
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"Progress update error: {e}")
                break
    
    def download():
        # Get the directory where the script is located (for ffmpeg binaries)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Define quality format mapping
        quality_formats = {
            '480p': 'best[height<=480]+bestaudio/best[height<=480]',
            '720p': 'best[height<=720]+bestaudio/best[height<=720]', 
            '1080p': 'best[height<=1080]+bestaudio/best[height<=1080]'
        }
        
        ydl_opts = {
            'format': quality_formats.get(quality, 'best[height<=720]+bestaudio/best[height<=720]'),
            'outtmpl': f'{chat_id}_%(title)s.%(ext)s',
            'ffmpeg_location': script_dir,
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 60,
            'retries': 5,
            'fragment_retries': 5,
            'file_access_retries': 3,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return filename, info.get('title', 'Unknown Title')
        except Exception as e:
            logger.error(f"Download error: {e}")
            raise e
    
    # Start progress update task
    progress_task = asyncio.create_task(update_progress_periodically())
    
    try:
        # Run download in executor
        result = await asyncio.get_event_loop().run_in_executor(executor, download)
        return result
    finally:
        # Cancel progress update task
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

async def handle_youtube_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube URL and show quality options"""
    chat_id = str(update.effective_chat.id)
    
    # Check if there's already an active download
    if chat_id in active_downloads:
        await update.message.reply_text(
            "⚠️ *You already have an active download!*\n\n"
            "Please wait for the current download to complete before starting a new one.\n"
            "This helps ensure better download speeds and prevents errors.",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "📋 *Usage Instructions*\n\n"
            "Please provide a YouTube URL:\n"
            "`/download <youtube_url>`\n\n"
            "*Example:*\n"
            "`/download https://www.youtube.com/watch?v=dQw4w9WgXcQ`",
            parse_mode='Markdown'
        )
        return
    
    youtube_url = context.args[0]
    
    # Validate URL
    if "youtube.com" not in youtube_url and "youtu.be" not in youtube_url:
        await update.message.reply_text(
            "❌ *Invalid URL*\n\n"
            "Please provide a valid YouTube URL.\n"
            "Supported formats:\n"
            "• `https://www.youtube.com/watch?v=VIDEO_ID`\n"
            "• `https://youtu.be/VIDEO_ID`",
            parse_mode='Markdown'
        )
        return
    
    # Send initial message
    processing_msg = await update.message.reply_text(
        "🔍 *Analyzing Video...*\n\n"
        "📋 Fetching video information...\n"
        "⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        # Get video info to check duration and available qualities
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            duration_seconds = info.get('duration', 0)
            title = info.get('title', 'Unknown Title')
            
            # Check duration limits
            max_duration_seconds = MAX_DURATION_MINUTES * 60
            user_id = update.effective_user.id
            
            if duration_seconds and duration_seconds > max_duration_seconds and not is_admin(user_id):
                duration_formatted = format_duration(duration_seconds)
                max_duration_formatted = format_duration(max_duration_seconds)
                
                await processing_msg.edit_text(
                    f"⏱️ *Video Too Long*\n\n"
                    f"🎵 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
                    f"⏰ *Duration:* {duration_formatted}\n"
                    f"🚫 *Limit:* {max_duration_formatted} for regular users\n\n"
                    f"💡 *This video is too long for regular users.*\n"
                    f"Please try a shorter video or contact admin for special access.",
                    parse_mode='Markdown'
                )
                return
            
            # Create inline keyboard with quality options
            keyboard = [
                [
                    InlineKeyboardButton("🎬 480p Video", callback_data=f"video_480p_{youtube_url}"),
                    InlineKeyboardButton("🎬 720p Video", callback_data=f"video_720p_{youtube_url}")
                ],
                [
                    InlineKeyboardButton("🎬 1080p Video", callback_data=f"video_1080p_{youtube_url}"),
                    InlineKeyboardButton("🎵 Audio Only (192kbps)", callback_data=f"audio_192_{youtube_url}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            duration_text = f"⏰ *Duration:* {format_duration(duration_seconds)}" if duration_seconds else ""
            
            await processing_msg.edit_text(
                f"📺 *Video Ready for Download*\n\n"
                f"🎵 *Title:* {title[:60]}{'...' if len(title) > 60 else ''}\n"
                f"{duration_text}\n\n"
                f"📋 *Choose your preferred quality:*\n"
                f"🎬 Video options include audio\n"
                f"🎵 Audio only is MP3 format",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    
    except Exception as e:
        logger.error(f"Error analyzing video: {e}")
        await processing_msg.edit_text(
            f"❌ *Error Analyzing Video*\n\n"
            f"*Error:* {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}\n\n"
            f"💡 *Tips:*\n"
            f"• Check if the video is available\n"
            f"• Make sure the URL is correct\n"
            f"• Some videos might be region-blocked",
            parse_mode='Markdown'
        )

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()
    
    chat_id = str(update.effective_chat.id)
    
    # Check if there's already an active download
    if chat_id in active_downloads:
        await query.edit_message_text(
            "⚠️ *You already have an active download!*\n\n"
            "Please wait for the current download to complete before starting a new one.",
            parse_mode='Markdown'
        )
        return
    
    # Parse callback data
    data_parts = query.data.split('_', 2)
    if len(data_parts) < 3:
        await query.edit_message_text("❌ Invalid selection.")
        return
    
    download_type = data_parts[0]  # 'video' or 'audio'
    quality = data_parts[1]        # '480p', '720p', '1080p', or quality for audio
    youtube_url = data_parts[2]    # The YouTube URL
    
    # Add to active downloads
    active_downloads.add(chat_id)
    
    try:
        if download_type == "audio":
            await process_audio_download(query, youtube_url, chat_id)
        elif download_type == "video":
            await process_video_download(query, youtube_url, quality, chat_id)
        else:
            await query.edit_message_text("❌ Unknown download type.")
            
    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        await query.edit_message_text(
            f"❌ *Download Failed*\n\n"
            f"*Error:* {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}\n\n"
            f"💡 Try again with a different video or quality.",
            parse_mode='Markdown'
        )
    finally:
        # Remove from active downloads
        active_downloads.discard(chat_id)
        download_stats.pop(chat_id, None)

async def process_audio_download(query, youtube_url: str, chat_id: str):
    """Process audio download"""
    # Update message to show download starting
    progress_msg = await query.edit_message_text(
        "🚀 *Starting Audio Download...*\n\n"
        "📋 Preparing audio extraction...\n"
        "⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        # Download audio
        file_path, title = await download_youtube_audio(youtube_url, chat_id, progress_msg)
        
        # Update status for upload
        await progress_msg.edit_text(
            f"📤 *Uploading Audio to Telegram*\n\n"
            f"🎵 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
            f"📁 *Format:* MP3 (192 kbps)\n"
            f"🚀 Uploading...",
            parse_mode='Markdown'
        )
        
        # Get file size
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        
        # Check file size limits
        if file_size_mb > MAX_FILE_SIZE_MB:
            await progress_msg.edit_text(
                f"⚠️ *File Too Large*\n\n"
                f"🎵 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
                f"📦 *Size:* {file_size_mb:.1f} MB\n"
                f"❌ *Upload limit:* {MAX_FILE_SIZE_MB} MB\n\n"
                f"*The file was downloaded but cannot be sent via Telegram.*",
                parse_mode='Markdown'
            )
            os.remove(file_path)
            return
        
        # Send audio file
        with open(file_path, 'rb') as audio_file:
            await query.message.reply_audio(
                audio=audio_file,
                caption=f"🎵 *{title}*\n\n"
                       f"📦 Size: {file_size_mb:.1f} MB\n"
                       f"🎧 Quality: 192 kbps MP3",
                title=title[:64],
                parse_mode='Markdown'
            )
        
        # Record download and show completion
        user = query.from_user
        user_stats = add_download_record(
            user_id=user.id,
            username=user.username or user.first_name or "Unknown",
            title=title,
            url=youtube_url,
            file_size_mb=file_size_mb
        )
        
        await progress_msg.edit_text(
            f"✅ *Audio Download Complete!*\n\n"
            f"🎵 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
            f"📦 *Size:* {file_size_mb:.1f} MB\n"
            f"🎧 *Quality:* 192 kbps MP3\n\n"
            f"📊 *Your Stats:* {user_stats['total_downloads']} downloads, {user_stats['total_size_mb']:.1f} MB total",
            parse_mode='Markdown'
        )
        
        # Clean up file
        os.remove(file_path)
        
        # Delete completion message after 5 seconds
        await asyncio.sleep(5)
        try:
            await progress_msg.delete()
        except:
            pass
            
    except Exception as e:
        raise e

async def process_video_download(query, youtube_url: str, quality: str, chat_id: str):
    """Process video download"""
    # Update message to show download starting
    progress_msg = await query.edit_message_text(
        f"🚀 *Starting Video Download ({quality})*\n\n"
        f"📋 Preparing {quality} video download...\n"
        f"⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        # Download video
        file_path, title = await download_youtube_video(youtube_url, chat_id, quality, progress_msg)
        
        # Update status for upload
        await progress_msg.edit_text(
            f"📤 *Uploading Video to Telegram*\n\n"
            f"🎬 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
            f"📺 *Quality:* {quality}\n"
            f"🚀 Uploading...",
            parse_mode='Markdown'
        )
        
        # Get file size
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        
        # Check file size limits
        if file_size_mb > MAX_FILE_SIZE_MB:
            await progress_msg.edit_text(
                f"⚠️ *File Too Large*\n\n"
                f"🎬 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
                f"📦 *Size:* {file_size_mb:.1f} MB\n"
                f"❌ *Upload limit:* {MAX_FILE_SIZE_MB} MB\n\n"
                f"💡 Try a lower quality option.",
                parse_mode='Markdown'
            )
            os.remove(file_path)
            return
        
        # Send video file
        with open(file_path, 'rb') as video_file:
            await query.message.reply_video(
                video=video_file,
                caption=f"🎬 *{title}*\n\n"
                       f"📦 Size: {file_size_mb:.1f} MB\n"
                       f"📺 Quality: {quality}",
                parse_mode='Markdown'
            )
        
        # Record download and show completion
        user = query.from_user
        user_stats = add_download_record(
            user_id=user.id,
            username=user.username or user.first_name or "Unknown",
            title=title,
            url=youtube_url,
            file_size_mb=file_size_mb
        )
        
        await progress_msg.edit_text(
            f"✅ *Video Download Complete!*\n\n"
            f"🎬 *Title:* {title[:50]}{'...' if len(title) > 50 else ''}\n"
            f"📦 *Size:* {file_size_mb:.1f} MB\n"
            f"📺 *Quality:* {quality}\n\n"
            f"📊 *Your Stats:* {user_stats['total_downloads']} downloads, {user_stats['total_size_mb']:.1f} MB total",
            parse_mode='Markdown'
        )
        
        # Clean up file
        os.remove(file_path)
        
        # Delete completion message after 5 seconds
        await asyncio.sleep(5)
        try:
            await progress_msg.delete()
        except:
            pass
            
    except Exception as e:
        raise e

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /audio command - redirect to new download system"""
    await update.message.reply_text(
        "🎵 *Audio Download*\n\n"
        "ℹ️ *Just send me a YouTube link directly!*\n\n"
        "No need for commands anymore - I'll automatically detect YouTube URLs and show you options:\n"
        "• 🎵 Audio only (192kbps MP3)\n"
        "• � Video in 480p, 720p, or 1080p\n\n"
        "*Example:*\n"
        "`https://www.youtube.com/watch?v=dQw4w9WgXcQ`",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any message and check for YouTube URLs"""
    if update.message and update.message.text:
        text = update.message.text.strip()
        
        # Check if the message contains a YouTube URL
        if ("youtube.com" in text or "youtu.be" in text) and ("http" in text):
            # Extract the URL from the text
            import re
            url_pattern = r'(https?://(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[^\s]+)'
            urls = re.findall(url_pattern, text)
            
            if urls:
                youtube_url = urls[0]  # Take the first URL found
                # Call the YouTube handler with the extracted URL
                context.args = [youtube_url]
                await handle_youtube_url(update, context)
                return
        
        # If no YouTube URL found, show help message
        await update.message.reply_text(
            "👋 *Hi there!*\n\n"
            "🎵 I'm a YouTube downloader bot!\n\n"
            "📋 *To download:*\n"
            "Just send me a YouTube link like:\n"
            "`https://www.youtube.com/watch?v=dQw4w9WgXcQ`\n\n"
            "✨ I'll show you quality options to choose from!",
            parse_mode='Markdown'
        )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_text = """🤖 *I'm Alive!*

🎵 Welcome to the YouTube Video & Audio Downloader Bot!

📋 *How to use:*
Send: `/download <youtube_url>`

*Example:*
`/download https://www.youtube.com/watch?v=dQw4w9WgXcQ`

✨ *Quality Options:*
• � Video: 480p, 720p, 1080p (with audio)
• 🎵 Audio Only: MP3 (192 kbps)

🎯 *Features:*
• 📊 Real-time download progress
• 🚀 Fast processing with quality selection
• 📱 Interactive buttons for easy selection
• 🗂️ Automatic file cleanup to save space

⚠️ *Important:*
Only one download per user at a time for optimal performance.

Type /help for more information!"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """🤖 *Bot Commands & Help*

*📋 Commands:*
• `/start` - Welcome message
• `/help` - Show this help
• `/audio <url>` - Legacy audio command
• `/status` - Check if you have active downloads
• `/stats` - View your download statistics
• `/leaderboard` - See top users
• `/admin` - Admin panel / User info

*📖 How to Use:*
1. Simply send me any YouTube URL directly (no commands needed!)
2. I'll automatically detect it and show quality options
3. Choose your preferred quality from the buttons:
   • 🎬 480p Video (includes audio)
   • 🎬 720p Video (includes audio)  
   • 🎬 1080p Video (includes audio)
   • 🎵 Audio Only (192kbps MP3)
4. Wait for download and upload to complete
5. Receive your file!

*✨ Features:*
• � Multiple video quality options
• 🎵 High-quality audio extraction
• 📊 Real-time progress tracking
• 📦 File size information
• ⚡ Speed and ETA display
• 🚀 Fast upload to Telegram
• 🗂️ Automatic file cleanup

*⚠️ Limitations:*
• Regular users: Max {format_duration(MAX_DURATION_MINUTES * 60)} duration
• Admins: Unlimited duration
• File size limit: {MAX_FILE_SIZE_MB} MB
• YouTube links only

*🔧 Supported URLs:*
• `https://www.youtube.com/watch?v=VIDEO_ID`
• `https://youtu.be/VIDEO_ID`
• `https://m.youtube.com/watch?v=VIDEO_ID`

*❓ Having issues?*
• Check your URL is correct
• Wait for current download to finish
• Some videos may be region-blocked
• Try a lower quality if file is too large"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command - show user download statistics"""
    user = update.effective_user
    user_stats = get_user_stats(user.id)
    
    if not user_stats:
        await update.message.reply_text(
            "📊 *Your Download Statistics*\n\n"
            "❌ No downloads yet!\n\n"
            "Use `/audio <youtube_url>` to start downloading.",
            parse_mode='Markdown'
        )
        return
    
    # Format last downloads
    recent_downloads = ""
    if user_stats['downloads']:
        recent_count = min(5, len(user_stats['downloads']))
        recent_downloads = "\n*📋 Recent Downloads:*\n"
        for download in user_stats['downloads'][-recent_count:]:
            date = datetime.fromisoformat(download['download_date']).strftime("%m/%d %H:%M")
            title = download['title'][:30] + "..." if len(download['title']) > 30 else download['title']
            recent_downloads += f"• `{date}` - {title} ({download['file_size_mb']}MB)\n"
    
    first_date = datetime.fromisoformat(user_stats['first_download']).strftime("%B %d, %Y")
    last_date = datetime.fromisoformat(user_stats['last_download']).strftime("%B %d, %Y at %H:%M")
    
    stats_text = f"""📊 *Your Download Statistics*

👤 *User:* {user_stats['username']}
📈 *Total Downloads:* {user_stats['total_downloads']}
💾 *Total Size:* {user_stats['total_size_mb']:.1f} MB
📅 *Member Since:* {first_date}
🕐 *Last Download:* {last_date}

{recent_downloads}

Use `/audio <url>` to download more!"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /leaderboard command - show top users"""
    db = load_user_database()
    
    if not db:
        await update.message.reply_text(
            "🏆 *Download Leaderboard*\n\n"
            "❌ No users yet!\n\n"
            "Be the first to download something!",
            parse_mode='Markdown'
        )
        return
    
    # Sort users by total downloads
    sorted_users = sorted(db.items(), key=lambda x: x[1]['total_downloads'], reverse=True)
    top_users = sorted_users[:10]  # Top 10 users
    
    leaderboard_text = "🏆 *Download Leaderboard*\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, stats) in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        username = stats['username'][:15] + "..." if len(stats['username']) > 15 else stats['username']
        
        leaderboard_text += f"{medal} *{username}*\n"
        leaderboard_text += f"   📈 {stats['total_downloads']} downloads\n"
        leaderboard_text += f"   💾 {stats['total_size_mb']:.1f} MB\n\n"
    
    # Add current user's position if not in top 10
    current_user_id = str(update.effective_user.id)
    if current_user_id in db:
        current_user_pos = next((i+1 for i, (uid, _) in enumerate(sorted_users) if uid == current_user_id), None)
        if current_user_pos and current_user_pos > 10:
            current_stats = db[current_user_id]
            leaderboard_text += f"📍 *Your Position: #{current_user_pos}*\n"
            leaderboard_text += f"   📈 {current_stats['total_downloads']} downloads\n"
            leaderboard_text += f"   💾 {current_stats['total_size_mb']:.1f} MB"
    
    await update.message.reply_text(leaderboard_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    chat_id = str(update.effective_chat.id)
    
    if chat_id in active_downloads:
        stats = download_stats.get(chat_id, {})
        status_text = f"""📊 *Download Status*

🔄 *Status:* Active download in progress
📊 *Progress:* {stats.get('percent', 'N/A')}
⚡ *Speed:* {stats.get('speed', 'N/A')}
⏱️ *ETA:* {stats.get('eta', 'N/A')}
📦 *Size:* {stats.get('size_info', 'N/A')}

Please wait for completion before starting a new download."""
    else:
        status_text = """✅ *Download Status*

🔄 *Status:* No active downloads
🚀 *Ready:* You can start a new download!

Use `/audio <youtube_url>` to begin downloading."""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')
    """Handle /status command"""
    chat_id = str(update.effective_chat.id)
    
    if chat_id in active_downloads:
        stats = download_stats.get(chat_id, {})
        status_text = f"""📊 *Download Status*

🔄 *Status:* Active download in progress
📊 *Progress:* {stats.get('percent', 'N/A')}
⚡ *Speed:* {stats.get('speed', 'N/A')}
⏱️ *ETA:* {stats.get('eta', 'N/A')}
📦 *Size:* {stats.get('size_info', 'N/A')}

Please wait for completion before starting a new download."""
    else:
        status_text = """✅ *Download Status*

🔄 *Status:* No active downloads
🚀 *Ready:* You can start a new download!

Use `/audio <youtube_url>` to begin downloading."""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command - show admin status and controls"""
    user = update.effective_user
    
    if is_admin(user.id):
        admin_text = f"""🔧 *Admin Panel*

👤 *User:* {user.username or user.first_name}
🛡️ *Status:* Administrator
⏰ *Download Limit:* Unlimited duration

*🎛️ Current Settings:*
• Max Duration (Regular): {MAX_DURATION_MINUTES} minutes ({format_duration(MAX_DURATION_MINUTES * 60)})
• Max File Size: {MAX_FILE_SIZE_MB} MB
• Admin Users: {len(ADMIN_USER_IDS)} configured

*💡 Admin Privileges:*
• Can download videos of any length
• Access to admin panel
• Can view system statistics"""
    else:
        admin_text = f"""ℹ️ *User Information*

👤 *User:* {user.username or user.first_name}
🛡️ *Status:* Regular User
⏰ *Download Limit:* {format_duration(MAX_DURATION_MINUTES * 60)} maximum

*📋 Your Limits:*
• Maximum Duration: {format_duration(MAX_DURATION_MINUTES * 60)}
• Maximum File Size: {MAX_FILE_SIZE_MB} MB

*💼 Need admin access?*
Contact the bot administrator to get unlimited duration access."""
    
    await update.message.reply_text(admin_text, parse_mode='Markdown')

def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("audio", audio_command))  # Legacy command
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(callback_query_handler))  # Handle button clicks
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  # Handle all text messages
    
    # Start the bot
    print("🎵 YouTube Video & Audio Downloader Bot is running...")
    print(f"📊 Max Duration: {format_duration(MAX_DURATION_MINUTES * 60)} (Regular users)")
    print(f"🛡️ Admins: {len(ADMIN_USER_IDS)} configured")
    print(f"📦 Max File Size: {MAX_FILE_SIZE_MB} MB")
    print("⚡ Ready to serve video and audio downloads!")
    
    try:
        application.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
        print("🔄 Restarting in 10 seconds...")
        time.sleep(10)

if __name__ == "__main__":
    main()
