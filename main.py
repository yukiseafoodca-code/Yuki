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
TRIGGER_KEYWORD = "安尼亞"

groq_client = Groq(api_key=GROQ_API_KEY)
memory_db = MemoryDB()

# 防洗版：記錄每個用戶最後回覆時間
last_reply = {}

def get_category(text):
    if any(kw in text for kw in ["我叫", "我是", "他叫", "她叫", "家人"]):
        return "人物"
    elif any(kw in text for kw in ["我喜歡", "我討厭", "我愛", "我怕"]):
        return "喜好"
    elif any(kw in text for kw in ["今天", "昨天", "記住", "發生"]):
        return "事件"
    elif any(kw in text for kw in ["設定", "偏好", "習慣"]):
        return "設定"
    else:
        return "一般"

def is_important(text):
    keywords = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定", "他叫", "她叫", "家人", "今天", "發生"]
    return any(kw in text for kw in keywords)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user_text = message.text
    sender_name = message.from_user.first_name or "未知"
    chat_type = message.chat.type
    user_id = message.from_user.id
    now = datetime.datetime.now()

    # 群組模式：只回應關鍵字
    if chat_type in ["group", "supergroup"]:
        if TRIGGER_KEYWORD not in user_text:
            return
        # 防洗版：同一人1分鐘內只回一次
        if user_id in last_reply:
            diff = (now - last_reply[user_id]).seconds
            if diff < 30:
                return
    last_reply[user_id] = now

    # 設定指令
    if user_text.startswith("設定:"):
        parts = user_text[3:].split("=")
        if len(parts) == 2:
            memory_db.set_preference(parts[0].strip(), parts[1].strip())
            await message.reply_text(f"✅ 已記住偏好：{parts[0].strip()} = {parts[1].strip()}")
            return

    memories = memory_db.get_all_memory()
    memory_text = "\n".join(memories[-20:])

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "你是 Yuki，一個有長期記憶的家庭助理。用繁體中文回覆。記住每個家庭成員的喜好和資料。"},
            {"role": "system", "content": f"記憶資料庫：\n{memory_text}"},
            {"role": "user", "content": f"{sender_name} 說：{user_text}"}
        ]
    )

    reply = response.choices[0].message.content

    if is_important(user_text):
        category = get_category(user_text)
        memory_db.add_memory(user_text, category=category, sender_name=sender_name)

    await message.reply_text(reply)

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
