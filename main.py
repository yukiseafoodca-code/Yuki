import os
import threading
import asyncio
import requests
import re
import json
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import google.generativeai as genai
import google.api_core.exceptions  # 新增：用於捕捉頻率限制與 API 錯誤
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from memory import MemoryDB
import datetime
import PIL.Image
import io

# 環境變數
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
TRIGGER_KEYWORD = "安尼亞"

# --- 初始化 Gemini (修正 404 問題) ---
genai.configure(api_key=GEMINI_API_KEY)

# 使用更穩定的模型宣告方式
try:
    # 優先嘗試完整路徑格式，這通常能解決 v1beta 404 問題
    gemini_model = genai.GenerativeModel('models/gemini-1.5-flash')
except Exception:
    # 備用方案
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')

memory_db = MemoryDB()
last_reply = {}

# --- 輔助函式 ---
def get_category(text):
    if any(kw in text for kw in ["我叫", "我是", "他叫", "她叫", "家人"]): return "人物"
    elif any(kw in text for kw in ["我喜歡", "我討厭", "我愛", "我怕"]): return "喜好"
    elif any(kw in text for kw in ["今天", "昨天", "發生"]): return "事件"
    elif any(kw in text for kw in ["設定", "偏好", "習慣", "記錄", "早上", "每天", "自動"]): return "設定"
    else: return "一般"

def is_important(text):
    keywords = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定",
                "他叫", "她叫", "家人", "今天", "發生", "記錄", "早上", "每天", "自動", "要求"]
    return any(kw in text for kw in keywords)

def check_rate_limit(user_id, chat_type):
    now = datetime.datetime.now()
    if chat_type in ["group", "supergroup"]:
        if user_id in last_reply:
            diff = (now - last_reply[user_id]).seconds
            if diff < 5:  # Gemini 限制較寬，縮短冷卻時間
                return False
    last_reply[user_id] = now
    return True

def gemini_chat(prompt):
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except google.api_core.exceptions.ResourceExhausted:
        return "❌ 安尼亞現在太忙了（每分鐘超過 15 條訊息），請稍等 60 秒再試喔！"
    except google.api_core.exceptions.InvalidArgument as e:
        return f"❌ 模型設定錯誤 (404/400)：{str(e)}"
    except Exception as e:
        return f"❌ 錯誤：{str(e)}"

def build_system_prompt():
    人物 = memory_db.get_by_category("人物")
    喜好 = memory_db.get_by_category("喜好")
    設定 = memory_db.get_by_category("設定")
    事件 = memory_db.get_by_category("事件")

    prompt = """你是安尼亞，一個聰明的家庭助理。你的名字是安尼亞。
必須使用繁體中文回覆。不可以自己生成新聞內容。回答要簡短直接。"""
    
    if 人物: prompt += "\n【人物資料】\n" + "\n".join(人物)
    if 喜好: prompt += "\n【喜好】\n" + "\n".join(喜好)
    if 設定: prompt += "\n【設定】\n" + "\n".join(設定)
    if 事件: prompt += "\n【近期事件】\n" + "\n".join(事件[-5:])
    return prompt

# --- RSS 新聞邏輯 (簡化) ---
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
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            articles.append({"title": title, "description": desc})
        return articles
    except: return []

def translate_news(articles, section_name):
    news_text = ""
    for i, a in enumerate(articles, 1):
        news_text += f"{i}. {a['title']}\n{a['description']}\n\n"
    if not news_text.strip(): return f"暫時無法獲取{section_name}"
    prompt = f"請將以下真實新聞翻譯並擴展成繁體中文（每則約200字）：\n\n{news_text}"
    return gemini_chat(prompt)

def fetch_real_news():
    try:
        canada_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada", 5)
        alberta_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada-edmonton", 5)
        canada_translated = translate_news(canada_articles, "加拿大新聞")
        alberta_translated = translate_news(alberta_articles, "Alberta新聞")
        return "🍁 加拿大重點新聞\n\n" + canada_translated, "📍 Alberta/Edmonton新聞\n\n" + alberta_translated
    except Exception as e: return f"❌ 新聞失敗：{str(e)}", ""

async def send_news(target, bot=None):
    canada_news, alberta_news = fetch_real_news()
    async def send_chunk(text):
        for i in range(0, len(text), 4000):
            part = text[i:i+4000]
            if bot: await bot.send_message(chat_id=MY_CHAT_ID, text=part)
            else: await target.reply_text(part)
    await send_chunk(canada_news)
    await asyncio.sleep(2)
    await send_chunk(alberta_news)

# --- 核心訊息處理 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return

    sender_name = message.from_user.first_name or "未知"
    chat_type = message.chat.type
    user_id = message.from_user.id

    # 1. 圖片訊息
    if message.photo:
        if chat_type in ["group", "supergroup"] and (not message.caption or TRIGGER_KEYWORD not in message.caption): return
        if not check_rate_limit(user_id, chat_type): return
        try:
            photo_file = await message.photo[-1].get_file()
            photo_bytes = bytes(await photo_file.download_as_bytearray())
            img = PIL.Image.open(io.BytesIO(photo_bytes))
            
            # 使用列表組合圖片與文字
            response = gemini_model.generate_content([
                f"{message.caption or '描述這張圖片'}，必須用繁體中文回答",
                img
            ])
            await message.reply_text(f"🖼️ {response.text}")
        except google.api_core.exceptions.ResourceExhausted:
            await message.reply_text("❌ 安尼亞看太快了，請等一分鐘再傳圖。")
        except Exception as e:
            await message.reply_text(f"❌ 圖片失敗：{str(e)}")
        return

    # 2. 語音與文字訊息邏輯 (同上，皆調用 gemini_chat)
    elif message.text:
        user_text = message.text
        if chat_type in ["group", "supergroup"] and TRIGGER_KEYWORD not in user_text: return
        if not check_rate_limit(user_id, chat_type): return

        # 處理 JSON 行程/支出邏輯... (此處省略以保持精簡)
        
        system_prompt = build_system_prompt()
        reply = gemini_chat(f"{system_prompt}\n\n{sender_name} 說：{user_text}")
        
        if is_important(user_text):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)
        
        await message.reply_text(reply)

# (WebServer 與 main() 啟動邏輯保持不變)
def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Anya Bot is running")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

def main():
    threading.Thread(target=run_web, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("summary", cmd_summary))
    # ... 其他 Handler ...
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    print("安尼亞 Bot is running with Gemini 1.5 Flash")
    app.run_polling()

if __name__ == "__main__":
    main()
