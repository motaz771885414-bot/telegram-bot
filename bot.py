import os
import time
import asyncio
import logging
import sqlite3
import subprocess
from collections import deque
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== ENV ==================
load_dotenv(".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN غير موجود في .env")

# ================== LOG ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ================== DB ==================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    daily_count INTEGER DEFAULT 0,
    last_day TEXT
)
""")
conn.commit()

# ================== CONST ==================
DAILY_LIMIT = 4
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ================== QUEUE ==================
download_queue = deque()
queue_lock = asyncio.Lock()
downloader_running = False

# ================== HELPERS ==================
def today():
    return datetime.utcnow().strftime("%Y-%m-%d")

def is_admin(uid: int):
    return uid == ADMIN_ID

def get_user(uid):
    cur.execute("SELECT daily_count, last_day FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users VALUES (?, 0, ?)",
            (uid, today())
        )
        conn.commit()
        return 0, today()
    return row

def update_user(uid, count, day):
    cur.execute(
        "UPDATE users SET daily_count=?, last_day=? WHERE user_id=?",
        (count, day, uid)
    )
    conn.commit()

# ================== SUB CHECK ==================
async def is_subscribed(uid, context):
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL, uid)
        return m.status in ("member", "administrator", "creator")
    except:
        return False

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check")]
    ])
    await update.message.reply_text(
        "👋 أهلاً بك في بوت معتز العلقمي\n\n"
        f"🔒 اشترك أولًا في القناة:\n{REQUIRED_CHANNEL}\n\n"
        "ثم اضغط تحقق 👇",
        reply_markup=kb
    )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_subscribed(q.from_user.id, context):
        await q.edit_message_text("✅ تم التحقق — أرسل رابط الفيديو الآن")
    else:
        await q.edit_message_text("❌ لم تشترك بعد")

# ================== MESSAGE ==================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if not await is_subscribed(uid, context):
        await update.message.reply_text("❌ يجب الاشتراك أولًا")
        return

    count, day = get_user(uid)
    if day != today():
        count = 0
        day = today()

    if count >= DAILY_LIMIT:
        await update.message.reply_text("🚫 وصلت الحد اليومي (4)")
        return

    async with queue_lock:
        download_queue.append((uid, text))
        pos = len(download_queue)

    update_user(uid, count + 1, day)
    await update.message.reply_text(f"📥 تم إضافة طلبك إلى الطابور\n🔢 ترتيبك: {pos}")

# ================== DOWNLOADER ==================
async def downloader_loop(app: Application):
    global downloader_running
    if downloader_running:
        return

    downloader_running = True
    logging.info("⬇️ Downloader loop started")

    while True:
        try:
            if not download_queue:
                await asyncio.sleep(2)
                continue

            async with queue_lock:
                uid, url = download_queue.popleft()

            out_file = f"{DOWNLOAD_DIR}/{uid}_{int(time.time())}.%(ext)s"
            cmd = [
                "yt-dlp",
                "-f", "mp4",
                "-o", out_file,
                url
            ]

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600
            )

            if proc.returncode != 0:
                await app.bot.send_message(
                    uid,
                    "❌ فشل التحميل، حاول رابط آخر"
                )
                continue

            file_path = max(
                [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)],
                key=os.path.getctime
            )

            await app.bot.send_video(uid, video=open(file_path, "rb"))
            os.remove(file_path)

        except Exception as e:
            logging.error(f"Downloader error: {e}")
            await asyncio.sleep(5)

# ================== ADMIN ==================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cur.execute("SELECT COUNT(*) FROM users")
    await update.message.reply_text(f"📊 عدد المستخدمين: {cur.fetchone()[0]}")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("♻️ إعادة تشغيل البوت...")
    os._exit(0)

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CallbackQueryHandler(check, pattern="check"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    loop = asyncio.get_event_loop()
    loop.create_task(downloader_loop(app))

    logging.info("🚀 Bot started successfully")
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
