import os
import threading
import asyncio
import base64
import requests
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from memory import MemoryDB
import datetime

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
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

    prompt = """你是 安尼亞，一個聰明的家庭助理。
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

def parse_rss(url, count=5):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        articles = []
        for item in items[:count]:
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            # 清除 HTML 標籤
            import re
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            articles.append({"title": title, "description": desc})
        return articles
    except Exception as e:
        return []

def fetch_real_news():
    try:
        # 加拿大重點新聞 RSS
        canada_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada", 5)

        # Alberta/Edmonton 新聞 RSS
        alberta_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada-edmonton", 5)
        if not alberta_articles:
            alberta_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada-calgary", 5)

        # 整理成文字
        canada_text = ""
        for i, a in enumerate(canada_articles, 1):
            canada_text += f"{i}. {a['title']}\n{a['description']}\n\n"

        alberta_text = ""
        for i, a in enumerate(alberta_articles, 1):
            alberta_text += f"{i}. {a['title']}\n{a['description']}\n\n"

        if not canada_text:
            canada_text = "暫時無法獲取加拿大新聞"
        if not alberta_text:
            alberta_text = "暫時無法獲取 Alberta/Edmonton 新聞"

        # 用 Groq 翻譯並擴展成繁體中文
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": f"""請將以下真實新聞翻譯並擴展成繁體中文。
嚴格要求：
- 每則新聞最少200字
- 保持原有編號格式
- 每則新聞之間空一行
- 絕對不可以用簡體中文
- 不要加 ** 或 ## 等符號
- 標題用「標題：」開頭
- 內容用「內容：」開頭
- 根據標題和描述擴展更多相關背景資訊

🍁 加拿大重點新聞：
{canada_text}

📍 Alberta 或 Edmonton 新聞：
{alberta_text}"""
            }]
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"❌ 新聞獲取失敗：{str(e)}"

async def send_news_message(target, news_text):
    chunks = []
    while len(news_text) > 4000:
        split_pos = news_text[:4000].rfind("\n\n")
        if split_pos == -1:
            split_pos = 4000
        chunks.append(news_text[:split_pos])
        news_text = news_text[split_pos:].strip()
    chunks.append(news_text)

    for chunk in chunks:
        if hasattr(target, "reply_text"):
            await target.reply_text(chunk)
        else:
            await target.send_message(chat_id=MY_CHAT_ID, text=chunk)

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = memory_db.get_all_memory()
    if not memories:
        await update.message.reply_text("📭 記憶庫是空的")
        return
    text = "📚 記憶庫：\n\n" + "\n".join(memories)
    await update.message.reply_text(text[:4000])

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_db.forget_all()
    await update.message.reply_text("🗑️ 所有記憶已清除")

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 正在獲取最新真實新聞，請稍等約30秒...")
    news = fetch_real_news()
    await send_news_message(update.message, news)

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

        # 強制記憶
        if any(kw in user_text for kw in ["記錄", "記住"]):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)
            await message.reply_text("✅ 已記錄！")
            return

        # 明確要求新聞
        if any(kw in user_text for kw in ["發新聞", "新聞", "今日新聞"]):
            await message.reply_text("📰 正在獲取最新真實新聞，請稍等約30秒...")
            news = fetch_real_news()
            await send_news_message(message, news)
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
            await bot.send_message(chat_id=MY_CHAT_ID, text="📰 早晨新聞來了，請稍等約30秒...")
            news = fetch_real_news()
            await send_news_message(bot, news)
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
