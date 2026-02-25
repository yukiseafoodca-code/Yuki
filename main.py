import os
import threading
import asyncio
import requests
import re
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
import google.generativeai as genai  # 更換為 Gemini SDK
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from memory import MemoryDB
import datetime

# 環境變數
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"] # 請在 Render 設定此變數
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
TRIGGER_KEYWORD = "安尼亞"

# 初始化 Gemini
genai.configure(api_key=GEMINI_API_KEY)
# 使用 1.5-flash，速度快且免費額度高
model = genai.GenerativeModel('gemini-1.5-flash')

memory_db = MemoryDB()
last_reply = {}

# --- 以下邏輯保持不變 ---
def get_category(text):
    if any(kw in text for kw in ["我叫", "我是", "他叫", "她叫", "家人"]): return "人物"
    elif any(kw in text for kw in ["我喜歡", "我討厭", "我愛", "我怕"]): return "喜好"
    elif any(kw in text for kw in ["今天", "昨天", "發生"]): return "事件"
    elif any(kw in text for kw in ["設定", "偏好", "習慣", "記錄", "早上", "每天", "自動"]): return "設定"
    else: return "一般"

def is_important(text):
    keywords = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定", "他叫", "她叫", "家人", "今天", "發生", "記錄", "早上", "每天", "自動", "要求"]
    return any(kw in text for kw in keywords)

def check_rate_limit(user_id, chat_type):
    now = datetime.datetime.now()
    if chat_type in ["group", "supergroup"]:
        if user_id in last_reply:
            diff = (now - last_reply[user_id]).seconds
            if diff < 10: # Gemini 限制較寬，縮短冷卻時間
                return False
    last_reply[user_id] = now
    return True

def build_system_prompt():
    人物 = memory_db.get_by_category("人物")
    喜好 = memory_db.get_by_category("喜好")
    設定 = memory_db.get_by_category("設定")
    事件 = memory_db.get_by_category("事件")

    prompt = """你是安尼亞，一個聰明的家庭助理。你的名字是安尼亞。
必須只用繁體中文回覆。嚴格禁止提供任何新聞內容（除非用戶明確要求）。
回答要簡短，不要主動提及記憶庫內容。"""
    
    if 人物: prompt += "\n【人物資料】\n" + "\n".join(人物)
    if 喜好: prompt += "\n【喜好】\n" + "\n".join(喜好)
    if 設定: prompt += "\n【設定】\n" + "\n".join(設定)
    if 事件: prompt += "\n【近期事件】\n" + "\n".join(事件[-5:])
    return prompt

# --- 新聞抓取邏輯 (Gemini 版本) ---
def translate_news(articles, section_name):
    news_text = ""
    for i, a in enumerate(articles, 1):
        news_text += f"{i}. {a['title']}\n{a['description']}\n\n"
    if not news_text.strip(): return f"暫時無法獲取{section_name}"

    prompt = f"請將以下5則真實新聞翻譯並擴展成繁體中文（每則最少200字，格式清晰）：\n\n{news_text}"
    response = model.generate_content(prompt)
    return response.text

# --- (parse_rss, fetch_real_news, send_news, cmd_系列 保持不變，唯獨 cmd_summary 需改動) ---
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_summarize = ""
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text_to_summarize = update.message.reply_to_message.text
    elif context.args:
        text_to_summarize = " ".join(context.args)
    else:
        await update.message.reply_text("請回覆訊息並輸入 /summary")
        return

    response = model.generate_content(f"請用繁體中文摘要重點：\n\n{text_to_summarize}")
    await update.message.reply_text("📝 摘要：\n\n" + response.text)

# --- 核心訊息處理 (改動最大處) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return

    sender_name = message.from_user.first_name or "未知"
    chat_type = message.chat.type
    user_id = message.from_user.id

    # 1. 圖片訊息處理
    if message.photo:
        if chat_type in ["group", "supergroup"] and (not message.caption or TRIGGER_KEYWORD not in message.caption):
            return
        if not check_rate_limit(user_id, chat_type): return
        
        try:
            photo_file = await message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            caption = message.caption or "請描述這張圖片"
            
            # Gemini Vision 調用
            contents = [
                {"mime_type": "image/jpeg", "data": bytes(photo_bytes)},
                f"{caption}，請以「安尼亞」的身分用繁體中文回答"
            ]
            response = model.generate_content(contents)
            await message.reply_text(f"🖼️ {response.text}")
        except Exception as e:
            await message.reply_text(f"❌ 圖片辨識失敗：{str(e)}")
        return

    # 2. 文字訊息處理
    elif message.text:
        user_text = message.text
        if chat_type in ["group", "supergroup"] and TRIGGER_KEYWORD not in user_text:
            return
        if not check_rate_limit(user_id, chat_type): return

        # 特殊功能（記憶、行程、購物的 JSON 提取邏輯同理，僅更換模型調用）
        if any(kw in user_text for kw in ["記錄", "記住", "支出", "買", "加入行程"]):
            # 這邊為了簡化，示範一般對話的更換方式
            pass 

        # 一般對話
        system_prompt = build_system_prompt()
        full_prompt = f"{system_prompt}\n\n{sender_name} 說：{user_text}"
        
        response = model.generate_content(full_prompt)
        reply = response.text

        if is_important(user_text):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)

        await message.reply_text(reply)

# --- 剩餘的定時任務與伺服器啟動邏輯不變，確保 GEMINI_API_KEY 已填入 ---
# (此處省略重複的 run_web, main 等啟動代碼)
