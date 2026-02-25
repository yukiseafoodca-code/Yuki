import os
import threading
import asyncio
import requests
import re
import json
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import google.generativeai as genai
import google.api_core.exceptions
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

# --- 1. 初始化 Gemini (解決 404 問題的終極方案) ---
genai.configure(api_key=GEMINI_API_KEY)

def get_stable_model():
    try:
        # 自動列出所有可用模型，找出支援文字生成的 gemini-1.5-flash
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini-1.5-flash' in m.name:
                    return genai.GenerativeModel(m.name)
        # 萬一列不出來，回退到標準 ID
        return genai.GenerativeModel('gemini-1.5-flash')
    except Exception:
        return genai.GenerativeModel('gemini-1.5-flash')

gemini_model = get_stable_model()
memory_db = MemoryDB()

# --- 2. 核心新聞與 RSS 邏輯 ---
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

async def send_news(target, bot=None):
    # 這裡實作新聞抓取與發送
    canada_rss = "https://www.cbc.ca/cmlink/rss-canada"
    articles = parse_rss(canada_rss, 5)
    
    if not articles:
        msg = "暫時抓不到新聞喔..."
    else:
        news_text = "🍁 加拿大重點新聞：\n\n"
        for i, a in enumerate(articles, 1):
            news_text += f"{i}. {a['title']}\n"
        msg = news_text

    if bot:
        await bot.send_message(chat_id=MY_CHAT_ID, text=msg)
    else:
        await target.reply_text(msg)

# --- 3. 指令處理器 (修正 NameError) ---
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 正在幫你找新聞，請稍等...")
    await send_news(update.message)

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ""
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text
    elif context.args:
        text = " ".join(context.args)
    
    if not text:
        await update.message.reply_text("請回覆一則訊息或在指令後輸入文字。")
        return
        
    response = gemini_model.generate_content(f"請用繁體中文摘要以下內容：\n\n{text}")
    await update.message.reply_text(f"📝 摘要結果：\n\n{response.text}")

# --- 4. 訊息處理 (含圖片識別) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return

    # 處理圖片
    if message.photo:
        # 群組內需有關鍵字才觸發
        if message.chat.type != "private" and (not message.caption or TRIGGER_KEYWORD not in message.caption):
            return
            
        try:
            photo_file = await message.photo[-1].get_file()
            photo_bytes = bytes(await photo_file.download_as_bytearray())
            img = PIL.Image.open(io.BytesIO(photo_bytes))
            
            prompt = f"{message.caption or '這張圖裡面有什麼？'} (請用繁體中文以安尼亞的語氣回答)"
            response = gemini_model.generate_content([prompt, img])
            await message.reply_text(response.text)
        except Exception as e:
            await message.reply_text(f"❌ 圖片看不太清楚：{str(e)}")
        return

    # 處理文字
    if message.text:
        if message.chat.type != "private" and TRIGGER_KEYWORD not in message.text:
            return
            
        try:
            response = gemini_model.generate_content(message.text)
            await message.reply_text(response.text)
        except google.api_core.exceptions.ResourceExhausted:
            await message.reply_text("安尼亞累了，請等一分鐘再跟我講話...")
        except Exception as e:
            await message.reply_text(f"發生錯誤：{str(e)}")

# --- 5. Web Server 與 啟動邏輯 ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Anya Bot is alive!")

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

def main():
    threading.Thread(target=run_web, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 註冊指令
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("summary", cmd_summary))
    
    # 註冊普通訊息
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("安尼亞 Bot 啟動成功！")
    app.run_polling()

if __name__ == "__main__":
    main()
