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

# 1. 環境變數設定
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
TRIGGER_KEYWORD = "安尼亞"

# 2. 初始化 Gemini (解決 v1beta 404 問題的穩定寫法)
genai.configure(api_key=GEMINI_API_KEY)

def get_stable_model():
    """動態查找可用模型 ID，避免手寫 ID 導致 404"""
    try:
        # 優先搜尋包含 gemini-1.5-flash 的可用模型名稱
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini-1.5-flash' in m.name:
                    print(f"✅ 成功匹配模型路徑: {m.name}")
                    return genai.GenerativeModel(model_name=m.name)
        # 若自動查找失敗，強制使用穩定版路徑
        return genai.GenerativeModel(model_name='models/gemini-1.5-flash')
    except Exception as e:
        print(f"⚠️ 模型查找出錯，使用預設 ID: {e}")
        return genai.GenerativeModel('gemini-1.5-flash')

gemini_model = get_stable_model()
memory_db = MemoryDB()

# 3. 核心功能：新聞抓取與翻譯
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
    """抓取並發送新聞"""
    try:
        res = requests.get("https://www.cbc.ca/cmlink/rss-canada", timeout=10)
        root = ET.fromstring(res.content)
        items = root.findall(".//item")[:5]
        news_text = "🍁 加拿大重點新聞：\n\n"
        for i, item in enumerate(items, 1):
            news_list = item.findtext('title')
            news_text += f"{i}. {news_list}\n"
        
        if bot:
            await bot.send_message(chat_id=MY_CHAT_ID, text=news_text)
        else:
            await target.reply_text(news_text)
    except Exception as e:
        msg = f"❌ 獲取新聞失敗: {str(e)}"
        if bot: await bot.send_message(chat_id=MY_CHAT_ID, text=msg)
        else: await target.reply_text(msg)

# 4. 指令處理器 (修正 NameError)
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 正在幫你找新聞，請稍等...")
    await send_news(update.message)

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.reply_to_message.text if update.message.reply_to_message else " ".join(context.args)
    if not text:
        await update.message.reply_text("請回覆一則訊息或在指令後輸入文字。")
        return
    try:
        response = gemini_model.generate_content(f"請用繁體中文摘要以下內容：\n\n{text}")
        await update.message.reply_text(f"📝 摘要結果：\n\n{response.text}")
    except Exception as e:
        await update.message.reply_text(f"❌ 摘要失敗: {str(e)}")

# 5. 訊息處理邏輯 (文字對話與圖片識別)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return

    # 圖片辨識邏輯
    if message.photo:
        if message.chat.type != "private" and (not message.caption or TRIGGER_KEYWORD not in message.caption):
            return
        try:
            photo_file = await message.photo[-1].get_file()
            photo_bytes = bytes(await photo_file.download_as_bytearray())
            img = PIL.Image.open(io.BytesIO(photo_bytes))
            
            # 傳送圖片與 Prompt 給 Gemini
            response = gemini_model.generate_content([
                f"{message.caption or '這張圖裡面有什麼？'} (請用繁體中文以安尼亞的語氣回答)",
                img
            ])
            await message.reply_text(response.text)
        except google.api_core.exceptions.ResourceExhausted:
            await message.reply_text("安尼亞累了，請等一分鐘再傳圖... (15條限制)")
        except Exception as e:
            await message.reply_text(f"❌ 圖片識別出錯：{str(e)}")
        return

    # 文字對話邏輯
    if message.text:
        # 群組內需有關鍵字才觸發
        if message.chat.type != "private" and TRIGGER_KEYWORD not in message.text:
            return
        try:
            response = gemini_model.generate_content(message.text)
            await message.reply_text(response.text)
        except google.api_core.exceptions.ResourceExhausted:
            await message.reply_text("安尼亞太忙了，請等 60 秒再跟我講話。")
        except Exception as e:
            await message.reply_text(f"發生錯誤：{str(e)}")

# 6. Web Server (防止 Render 休眠)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Anya Bot is running!")

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# 7. 主啟動程式
def main():
    # 啟動 Web Server 線程
    threading.Thread(target=run_web, daemon=True).start()
    
    # 建立 Telegram Application
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 註冊指令
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("summary", cmd_summary))
    
    # 註冊普通訊息處理器
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("🚀 安尼亞 Bot 已成功啟動！")
    app.run_polling()

if __name__ == "__main__":
    main()
