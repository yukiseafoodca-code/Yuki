import os
import threading
import asyncio
import base64
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from memory import MemoryDB
import datetime

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
NEWS_API_KEY = os.environ["NEWS_API_KEY"]
TRIGGER_KEYWORD = "安尼亞"

groq_client = Groq(api_key=GROQ_API_KEY)
memory_db = MemoryDB()
last_reply = {}

def get_category(text):
    if any(kw in text for kw in ["我叫", "我是", "他叫", "她叫", "家人"]):
        return "人物"
    elif any(kw in text for kw in ["我喜歡", "我討厭", "我愛", "我怕"]):
        return "喜好"
    elif any(kw in text for kw in ["今天", "昨天", "發生"]):
        return "事件"
    elif any(kw in text for kw in ["設定", "偏好", "習慣", "記錄", "早上", "每天", "自動", "新聞"]):
        return "設定"
    else:
        return "一般"

def is_important(text):
    keywords = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定",
                "他叫", "她叫", "家人", "今天", "發生", "記錄", "早上", "每天", "自動", "新聞", "要求"]
    return any(kw in text for kw in keywords)

def check_rate_limit(user_id, chat_type):
    now = datetime.datetime.now()
    if chat_type in ["group", "supergroup"]:
        if user_id in last_reply:
            diff = (now - last_reply[user_id]).seconds
            if diff < 30:
                return False
    last_reply[user_id] = now
    return True

def build_system_prompt():
    人物 = memory_db.get_by_category("人物")
    喜好 = memory_db.get_by_category("喜好")
    設定 = memory_db.get_by_category("設定")
    事件 = memory_db.get_by_category("事件")

    prompt = """你是 Yuki，一個聰明的家庭助理。
必須只用繁體中文回覆，絕對不可以用簡體中文。
你只回答用戶的問題，不會自動發新聞或執行任何任務。
只有用戶明確要求時才執行特定任務。

"""
    if 人物:
        prompt += "【人物資料】\n" + "\n".join(人物) + "\n\n"
    if 喜好:
        prompt += "【喜好】\n" + "\n".join(喜好) + "\n\n"
    if 設定:
        prompt += "【設定】\n" + "\n".join(設定) + "\n\n"
    if 事件:
        prompt += "【近期事件】\n" + "\n".join(事件[-5:]) + "\n\n"

    return prompt

def fetch_real_news():
    try:
        # 加拿大重點新聞
        canada_url = (
            f"https://newsapi.org/v2/top-headlines"
            f"?country=ca&pageSize=5&apiKey={NEWS_API_KEY}"
        )
        canada_res = requests.get(canada_url).json()
        canada_articles = canada_res.get("articles", [])

        # Alberta/Edmonton 新聞
        alberta_url = (
            f"https://newsapi.org/v2/everything"
            f"?q=Alberta+OR+Edmonton&language=en&sortBy=publishedAt&pageSize=5&apiKey={NEWS_API_KEY}"
        )
        alberta_res = requests.get(alberta_url).json()
        alberta_articles = alberta_res.get("articles", [])

        # 整理加拿大新聞
        canada_text = ""
        for i, a in enumerate(canada_articles, 1):
            title = a.get("title", "無標題")
            desc = a.get("description") or ""
            content = a.get("content") or ""
            full = desc + " " + content
            canada_text += f"{i}. {title}\n{full}\n\n"

        # 整理 Alberta/Edmonton 新聞
        alberta_text = ""
        for i, a in enumerate(alberta_articles, 1):
            title = a.get("title", "無標題")
            desc = a.get("description") or ""
            content = a.get("content") or ""
            full = desc + " " + content
            alberta_text += f"{i}. {title}\n{full}\n\n"

        # 用 Groq 翻譯並擴展成繁體中文，每則最少200字
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": f"""請將以下新聞翻譯並擴展成繁體中文。
要求：
- 每則新聞最少200字
- 保持原有編號
- 每則新聞之間空一行
- 不可以用簡體中文
- 不要加 ** 或 ## 等符號

🍁 加拿大重點新聞：
{canada_text}

📍 Alberta 或 Edmonton 新聞：
{alberta_text}"""
            }]
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"❌ 新聞獲取失敗：{str(e)}"

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = memory_db.get_all_memory()
    if not memories:
        await update.message.reply_text("📭 記憶庫是空的")
        return
    text = "📚 記憶庫：\n\n" + "\n".join(memories)
    await update.message.reply_text(text)

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_db.forget_all()
    await update.message.reply_text("🗑️ 所有記憶已清除")

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 正在獲取最新新聞，請稍等...")
    news = fetch_real_news()
    # Telegram 訊息有長度限制，分段發送
    if len(news) > 4000:
        mid = len(news) // 2
        await update.message.reply_text(news[:mid])
        await update.message.reply_text(news[mid:])
    else:
        await update.message.reply_text(news)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    sender_name = message.from_user.first_name or "未知"
    chat_type = message.chat.type
    user_id = message.from_user.id

    # 語音訊息
    if message.voice:
        if chat_type in ["group", "supergroup"]:
            return
        if not check_rate_limit(user_id, chat_type):
            return
        try:
            voice_file = await message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            with open("/tmp/voice.ogg", "wb") as f:
                f.write(voice_bytes)
            with open("/tmp/voice.ogg", "rb") as f:
                transcription = groq_client.audio.transcriptions.create(
                    file=("voice.ogg", f.read()),
                    model="whisper-large-v3",
                    language="zh"
                )
            user_text = transcription.text
            await message.reply_text(f"🎤 你說：{user_text}")
        except:
            await message.reply_text("❌ 語音辨識失敗，請再試一次")
        return

    # 圖片訊息
    elif message.photo:
        if chat_type in ["group", "supergroup"]:
            return
        if not check_rate_limit(user_id, chat_type):
            return
        try:
            photo = message.photo[-1]
            photo_file = await photo.get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            base64_image = base64.b64encode(photo_bytes).decode("utf-8")
            caption = message.caption or "請描述這張圖片"
            response = groq_client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{caption}，請用繁體中文回答"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }]
            )
            await message.reply_text(f"🖼️ {response.choices[0].message.content}")
        except:
            await message.reply_text("❌ 圖片辨識失敗，請再試一次")
        return

    # 文字訊息
    elif message.text:
        user_text = message.text

        if chat_type in ["group", "supergroup"]:
            if TRIGGER_KEYWORD not in user_text:
                return

        if not check_rate_limit(user_id, chat_type):
            return

        # 設定指令
        if user_text.startswith("設定:"):
            parts = user_text[3:].split("=")
            if len(parts) == 2:
                memory_db.set_preference(parts[0].strip(), parts[1].strip())
                await message.reply_text(f"✅ 已記住偏好：{parts[0].strip()} = {parts[1].strip()}")
                return

        # 明確要求新聞
        if any(kw in user_text for kw in ["發新聞", "新聞", "今日新聞"]):
            await message.reply_text("📰 正在獲取最新新聞，請稍等...")
            news = fetch_real_news()
            if len(news) > 4000:
                mid = len(news) // 2
                await message.reply_text(news[:mid])
                await message.reply_text(news[mid:])
            else:
                await message.reply_text(news)
            # 記錄用戶新聞要求
            memory_db.add_memory(user_text, category="設定", sender_name=sender_name)
            return

        # 強制記憶
        if any(kw in user_text for kw in ["記錄", "記住", "記住我"]):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)
            system_prompt = build_system_prompt()
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{sender_name} 說：{user_text}"}
                ]
            )
            reply = response.choices[0].message.content
            await message.reply_text("✅ 已記錄！\n\n" + reply)
            return

        # 一般對話
        system_prompt = build_system_prompt()
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{sender_name} 說：{user_text}"}
            ]
        )
        reply = response.choices[0].message.content

        # 自動記憶重要資訊
        if is_important(user_text):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)

        await message.reply_text(reply)
    else:
        return

async def send_daily_news():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent_today = False
    while True:
        now = datetime.datetime.now()
        if now.hour == 9 and now.minute == 0 and not sent_today:
            await bot.send_message(chat_id=MY_CHAT_ID, text="📰 正在獲取早晨新聞，請稍等...")
            news = fetch_real_news()
            if len(news) > 4000:
                mid = len(news) // 2
                await bot.send_message(chat_id=MY_CHAT_ID, text=news[:mid])
                await bot.send_message(chat_id=MY_CHAT_ID, text=news[mid:])
            else:
                await bot.send_message(chat_id=MY_CHAT_ID, text=news)
            sent_today = True
        if now.hour != 9:
            sent_today = False
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
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    loop = asyncio.get_event_loop()
    loop.create_task(send_daily_news())
    print("Yuki Bot is running")
    app.run_polling()

if __name__ == "__main__":
    main()
