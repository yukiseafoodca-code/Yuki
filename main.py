import os
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from memory import MemoryDB
import datetime

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]

groq_client = Groq(api_key=GROQ_API_KEY)
memory_db = MemoryDB()

IMPORTANT_KEYWORDS = ["我叫", "我是", "我喜歡", "我討厭", "我住", "我的工作", "記住", "偏好", "設定"]

def is_important(text):
    return any(kw in text for kw in IMPORTANT_KEYWORDS)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    if user_text.startswith("設定:"):
        parts = user_text[3:].split("=")
        if len(parts) == 2:
            memory_db.set_preference(parts[0].strip(), parts[1].strip())
            await update.message.reply_text(f"✅ 已記住偏好：{parts[0].strip()} = {parts[1].strip()}")
            return

    memories = memory_db.get_all_memory()
    memory_text = "\n".join(memories[-20:])

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are Yuki, a helpful assistant with long-term memory. Reply in the same language as the user."},
            {"role": "system", "content": f"Important memories:\n{memory_text}"},
            {"role": "user", "content": user_text}
        ]
    )

    reply = response.choices[0].message.content

    if is_important(user_text):
        memory_db.add_memory(user_text)

    await update.message.reply_text(reply)

async def send_daily_news():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while True:
        now = datetime.datetime.now()
        if now.hour == 9 and now.minute == 0:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "user", "content": "請用繁體中文提供今天5條重要國際新聞摘要，每條一行。"}
                ]
            )
            news = response.choices[0].message.content
            await bot.send_message(chat_id=MY_CHAT_ID, text=f"📰 早晨新聞：\n\n{news}")
        await asyncio.sleep(60)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Yuki Bot is running")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
    def log_message(self, format, *args):
        pass

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

def main():
    threading.Thread(target=run_web, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    loop = asyncio.get_event_loop()
    loop.create_task(send_daily_news())
    print("Yuki Bot is running")
    app.run_polling()

if __name__ == "__main__":
    main()
