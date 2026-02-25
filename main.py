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
from googlesearch import search # 修正處：確保這一行沒有多餘的非程式碼文字

# --- 環境變數與初始化 ---
[span_3](start_span)TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"][span_3](end_span)
[span_4](start_span)GEMINI_API_KEY = os.environ["GEMINI_API_KEY"][span_4](end_span)
[span_5](start_span)MY_CHAT_ID = os.environ["MY_CHAT_ID"][span_5](end_span)
[span_6](start_span)TRIGGER_KEYWORD = "安尼亞"[span_6](end_span)

[span_7](start_span)genai.configure(api_key=GEMINI_API_KEY)[span_7](end_span)

def get_stable_model():
    try:
        available = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                [span_8](start_span)available.append(m.name)[span_8](end_span)
        for preferred in ['models/gemini-1.5-flash-latest', 'models/gemini-1.5-flash', 'models/gemini-1.0-pro', 'models/gemini-pro']:
            if preferred in available:
                [span_9](start_span)return genai.GenerativeModel(model_name=preferred)[span_9](end_span)
        if available:
            [span_10](start_span)return genai.GenerativeModel(model_name=available[0])[span_10](end_span)
    except Exception as e:
        print(f"! 查找失敗: {e}")
    [span_11](start_span)return genai.GenerativeModel('models/gemini-1.5-flash')[span_11](end_span)

gemini_model = get_stable_model()
memory_db = MemoryDB()
last_reply = {}

# --- 新增：手動搜尋工具 ---
def manual_google_search(query):
    try:
        results = []
        # 抓取前 5 筆 Google 搜尋結果
        for result in search(query, num_results=5, lang="zh-TW", advanced=True):
            results.append(f"標題: {result.title}\n摘要: {result.description}\n連結: {result.url}")
        return "\n\n".join(results) if results else "查無相關網路即時資料。"
    except Exception as e:
        return f"搜尋功能暫時不可用: {str(e)}"

# --- 基礎邏輯函式 ---
def get_category(text):
    [span_12](start_span)if any(kw in text for kw in ["我叫","我是","他叫","她叫","家人"]): return "人物"[span_12](end_span)
    [span_13](start_span)elif any(kw in text for kw in ["我喜歡","我討厭","我愛","我怕"]): return "喜好"[span_13](end_span)
    [span_14](start_span)elif any(kw in text for kw in ["今天","昨天","發生"]): return "事件"[span_14](end_span)
    [span_15](start_span)elif any(kw in text for kw in ["設定","偏好","習慣","記錄","早上","每天","自動"]): return "設定"[span_15](end_span)
    [span_16](start_span)else: return "一般"[span_16](end_span)

def is_important(text):
    [span_17](start_span)keywords = ["我叫","我是","我喜歡","我討厭","我住","記住","設定", "他叫","她叫","家人", "今天","發生","記錄","早上","每天","自動","要求"][span_17](end_span)
    [span_18](start_span)return any(kw in text for kw in keywords)[span_18](end_span)

def check_rate_limit(user_id, chat_type):
    now = datetime.datetime.now()
    if chat_type in ["group", "supergroup"]:
        if user_id in last_reply:
            diff = (now - last_reply[user_id]).seconds
            [span_19](start_span)if diff < 30: return False[span_19](end_span)
        last_reply[user_id] = now
    [span_20](start_span)return True[span_20](end_span)

def gemini_chat(prompt):
    try:
        response = gemini_model.generate_content(prompt)
        [span_21](start_span)return response.text[span_21](end_span)
    except google.api_core.exceptions.ResourceExhausted:
        [span_22](start_span)return "安尼亞太忙了,請等60秒再試"[span_22](end_span)
    except Exception as e:
        return f"錯誤: {str(e)}"

def build_system_prompt():
    [span_23](start_span)人物 = memory_db.get_by_category("人物")[span_23](end_span)
    [span_24](start_span)喜好 = memory_db.get_by_category("喜好")[span_24](end_span)
    [span_25](start_span)設定 = memory_db.get_by_category("設定")[span_25](end_span)
    [span_26](start_span)事件 = memory_db.get_by_category("事件")[span_26](end_span)
    [span_27](start_span)prompt = "你是安尼亞,一個聰明的家庭助理。\n你的名字是安尼亞,不是其他名字。\n必須使用繁體中文回覆,絕對禁止使用簡體中文。\n不可以自己生成新聞內容。\n回答要簡短直接。\n"[span_27](end_span)
    [span_28](start_span)if 人物: prompt += "【人物資料】\n" + "\n".join(人物) + "\n\n"[span_28](end_span)
    [span_29](start_span)if 喜好: prompt += "【喜好】\n"+"\n".join(喜好)+"\n\n"[span_29](end_span)
    [span_30](start_span)if 設定: prompt += "【設定】\n" + "\n".join(設定)+"\n\n"[span_30](end_span)
    [span_31](start_span)if 事件: prompt += "【近期事件】\n"+"\n".join(事件[-5:]) + "\n\n"[span_31](end_span)
    return prompt

# --- 新聞處理系統 (完整保留) ---
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
        [span_32](start_span)return articles[span_32](end_span)
    except: return []

def translate_news(articles, section_name):
    news_text = ""
    for i, a in enumerate(articles, 1):
        [span_33](start_span)news_text += f"{i}. {a['title']}\n{a['description']}\n\n"[span_33](end_span)
    [span_34](start_span)if not news_text.strip(): return f"暫時無法獲取{section_name}"[span_34](end_span)
    [span_35](start_span)prompt = f"請將以下5則真實新聞翻譯並擴展成繁體中文。要求:每則最少200字,每則之間空一行,不要用簡體中文,不要加**或##符號。格式:\n1. 新聞標題\n新聞內容\n\n原文:\n{news_text}"[span_35](end_span)
    return gemini_chat(prompt)

def fetch_real_news():
    try:
        [span_36](start_span)canada_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada", 5)[span_36](end_span)
        [span_37](start_span)alberta_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada-edmonton", 5)[span_37](end_span)
        if len(alberta_articles) < 3:
            [span_38](start_span)extra = parse_rss("https://www.cbc.ca/cmlink/rss-canada-calgary", 5)[span_38](end_span)
            [span_39](start_span)alberta_articles = (alberta_articles + extra)[:5][span_39](end_span)
        [span_40](start_span)canada_translated = translate_news(canada_articles, "加拿大新聞")[span_40](end_span)
        [span_41](start_span)alberta_translated = translate_news(alberta_articles, "Alberta/Edmonton 新聞")[span_41](end_span)
        [span_42](start_span)return " 加拿大重點新聞\n\n" + canada_translated, " Alberta 或 Edmonton 新聞\n\n" + alberta_translated[span_42](end_span)
    except Exception as e:
        [span_43](start_span)return f" 新聞獲取失敗: {str(e)}", ""[span_43](end_span)

async def send_news(target, bot=None):
    [span_44](start_span)canada_news, alberta_news = fetch_real_news()[span_44](end_span)
    async def send_chunk(text):
        parts = []
        while len(text) > 4000:
            split_pos = text[:4000].rfind("\n\n")
            if split_pos == -1: split_pos = 4000
            parts.append(text[:split_pos])
            text = text[split_pos:].strip()
        [span_45](start_span)parts.append(text)[span_45](end_span)
        for part in parts:
            if part.strip():
                [span_46](start_span)if bot: await bot.send_message(chat_id=MY_CHAT_ID, text=part)[span_46](end_span)
                [span_47](start_span)else: await target.reply_text(part)[span_47](end_span)
    [span_48](start_span)await send_chunk(canada_news)[span_48](end_span)
    await asyncio.sleep(2)
    [span_49](start_span)await send_chunk(alberta_news)[span_49](end_span)

# --- Telegram 指令處理 (完整保留) ---
async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_50](start_span)memories = memory_db.get_all_memory()[span_50](end_span)
    [span_51](start_span)await update.message.reply_text("記憶庫:\n\n" + "\n".join(memories)) if memories else await update.message.reply_text("記憶庫是空的")[span_51](end_span)

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_52](start_span)memory_db.forget_all()[span_52](end_span)
    [span_53](start_span)await update.message.reply_text("所有記憶已清除")[span_53](end_span)

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_54](start_span)await update.message.reply_text(" 正在獲取最新真實新聞,請稍等約30秒...")[span_54](end_span)
    [span_55](start_span)await send_news(update.message)[span_55](end_span)

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_56](start_span)events = memory_db.get_upcoming_events(30)[span_56](end_span)
    [span_57](start_span)if not events: await update.message.reply_text("未來30天沒有行程"); return[span_57](end_span)
    text = "未來30天行程:\n\n"
    [span_58](start_span)for e in events: text += f" {e['event_date']} [{e['category']}] {e['title']}\n"[span_58](end_span)
    [span_59](start_span)await update.message.reply_text(text)[span_59](end_span)

async def cmd_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_60](start_span)items = memory_db.get_shopping_list()[span_60](end_span)
    [span_61](start_span)if not items: await update.message.reply_text("購物清單是空的"); return[span_61](end_span)
    text = " 購物清單:\n\n"
    [span_62](start_span)for i, item in enumerate(items, 1): text += f"{i}. {item['item']} x{item['quantity']} ({item['added_by']})\n"[span_62](end_span)
    [span_63](start_span)await update.message.reply_text(text)[span_63](end_span)

async def cmd_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    [span_64](start_span)expenses = memory_db.get_monthly_expenses()[span_64](end_span)
    [span_65](start_span)if not expenses: await update.message.reply_text(" 本月沒有記帳記錄"); return[span_65](end_span)
    [span_66](start_span)total = sum(float(e['amount']) for e in expenses)[span_66](end_span)
    categories = {}
    for e in expenses:
        cat = e['category']
        [span_67](start_span)categories[cat] = categories.get(cat, 0) + float(e['amount'])[span_67](end_span)
    [span_68](start_span)text = f" 本月支出摘要:\n總計:${total:.2f}\n\n"[span_68](end_span)
    [span_69](start_span)for cat, amount in categories.items(): text += f"• {cat}: ${amount:.2f}\n"[span_69](end_span)
    [span_70](start_span)text += "\n詳細記錄:\n"[span_70](end_span)
    [span_71](start_span)for e in expenses: text += f" {e['expense_date']} [{e['category']}] {e['description']} ${e['amount']}\n"[span_71](end_span)
    [span_72](start_span)await update.message.reply_text(text)[span_72](end_span)

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_summarize = ""
    if update.message.reply_to_message and update.message.reply_to_message.text:
        [span_73](start_span)text_to_summarize = update.message.reply_to_message.text[span_73](end_span)
    [span_74](start_span)elif context.args: text_to_summarize = " ".join(context.args)[span_74](end_span)
    [span_75](start_span)else: await update.message.reply_text("請回覆一條訊息並輸入/summary"); return[span_75](end_span)
    [span_76](start_span)result = gemini_chat(f"請用繁體中文將以下內容摘要成3-5點重點,每點一行:\n\n{text_to_summarize}")[span_76](end_span)
    [span_77](start_span)await update.message.reply_text("摘要:\n\n" + result)[span_77](end_span)

async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        models = genai.list_models()
        text = "可用模型:\n"
        for m in models:
            [span_78](start_span)if "generateContent" in m.supported_generation_methods: text += f" {m.name}\n"[span_78](end_span)
        [span_79](start_span)await update.message.reply_text(text[:4000])[span_79](end_span)
    [span_80](start_span)except Exception as e: await update.message.reply_text(f"錯誤:{str(e)}")[span_80](end_span)

# --- 核心訊息處理 (整合搜尋功能與所有原有判斷) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    [span_81](start_span)if not message: return[span_81](end_span)
    [span_82](start_span)sender_name = message.from_user.first_name or "未知"[span_82](end_span)
    [span_83](start_span)chat_type = message.chat.type[span_83](end_span)
    [span_84](start_span)user_id = message.from_user.id[span_84](end_span)

    # [span_85](start_span)長訊息自動摘要[span_85](end_span)
    if message.text and len(message.text) > 500:
        if chat_type in ["group", "supergroup"]:
            [span_86](start_span)result = gemini_chat(f"請用繁體中文將以下內容摘要成3-5點重點,每點一行:\n\n{message.text}")[span_86](end_span)
            [span_87](start_span)await message.reply_text("自動摘要:\n\n" + result)[span_87](end_span)
            return

    # [span_88](start_span)圖片辨識[span_88](end_span)
    if message.photo:
        [span_89](start_span)if chat_type in ["group", "supergroup"] and (not message.caption or TRIGGER_KEYWORD not in message.caption): return[span_89](end_span)
        [span_90](start_span)if not check_rate_limit(user_id, chat_type): return[span_90](end_span)
        try:
            [span_91](start_span)photo_file = await message.photo[-1].get_file()[span_91](end_span)
            [span_92](start_span)photo_bytes = bytes(await photo_file.download_as_bytearray())[span_92](end_span)
            [span_93](start_span)img = PIL.Image.open(io.BytesIO(photo_bytes))[span_93](end_span)
            [span_94](start_span)caption = message.caption or "請描述這張圖片"[span_94](end_span)
            [span_95](start_span)response = gemini_model.generate_content([f"{caption},必須用繁體中文回答,不可用簡體中文", img])[span_95](end_span)
            [span_96](start_span)await message.reply_text(response.text)[span_96](end_span)
        [span_97](start_span)except Exception as e: await message.reply_text(f"圖片辨識失敗:{str(e)}")[span_97](end_span)
        return

    # [span_98](start_span)語音轉錄[span_98](end_span)
    elif message.voice:
        [span_99](start_span)if chat_type in ["group", "supergroup"] and (not message.caption or TRIGGER_KEYWORD not in message.caption): return[span_99](end_span)
        [span_100](start_span)if not check_rate_limit(user_id, chat_type): return[span_100](end_span)
        try:
            [span_101](start_span)voice_file = await message.voice.get_file()[span_101](end_span)
            [span_102](start_span)voice_bytes = await voice_file.download_as_bytearray()[span_102](end_span)
            [span_103](start_span)response = gemini_model.generate_content([{"mime_type": "audio/ogg", "data": bytes(voice_bytes)}, "請將這段語音轉錄成繁體中文文字"])[span_103](end_span)
            [span_104](start_span)await message.reply_text(f"你說: {response.text}")[span_104](end_span)
        [span_105](start_span)except Exception as e: await message.reply_text(f" 語音辨識失敗: {str(e)}")[span_105](end_span)
        return

    # [span_106](start_span)文字訊息處理[span_106](end_span)
    elif message.text:
        user_text = message.text
        if chat_type in ["group", "supergroup"]:
            [span_107](start_span)if TRIGGER_KEYWORD not in user_text: return[span_107](end_span)
            [span_108](start_span)if not check_rate_limit(user_id, chat_type): return[span_108](end_span)

        # [span_109](start_span)偏好設定[span_109](end_span)
        if user_text.startswith("設定:"):
            [span_110](start_span)parts = user_text[3:].split("=")[span_110](end_span)
            if len(parts) == 2:
                [span_111](start_span)memory_db.set_preference(parts[0].strip(), parts[1].strip())[span_111](end_span)
                [span_112](start_span)await message.reply_text(f" 已記住偏好: {parts[0].strip()} = {parts[1].strip()}")[span_112](end_span)
            return

        # [span_113](start_span)記憶儲存[span_113](end_span)
        if any(kw in user_text for kw in ["記錄","記住"]):
            [span_114](start_span)memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)[span_114](end_span)
            [span_115](start_span)await message.reply_text(" 已記錄!")[span_115](end_span)
            return

        # [span_116](start_span)行程管理[span_116](end_span)
        if "加入行程" in user_text or "新增行程" in user_text:
            [span_117](start_span)result = gemini_chat(f"從以下訊息提取行程資料,只回傳JSON:\n{{\"title\":\"標題\",\"category\":\"分類\",\"date\":\"YYYY-MM-DD\",\"reminder_days\":1}}\n訊息:{user_text}\n今天:{datetime.date.today()}")[span_117](end_span)
            try:
                [span_118](start_span)data = json.loads(re.sub(r"```json|```", "", result).strip())[span_118](end_span)
                [span_119](start_span)memory_db.add_event(title=data["title"], category=data["category"], event_date=data["date"], reminder_days=data.get("reminder_days", 1), created_by=sender_name)[span_119](end_span)
                [span_120](start_span)await message.reply_text(f" 已加入行程:{data['date']} {data['title']}")[span_120](end_span)
            [span_121](start_span)except: await message.reply_text("無法識別行程格式")[span_121](end_span)
            return

        # [span_122](start_span)購物清單[span_122](end_span)
        if any(kw in user_text for kw in ["買", "購物", "加入清單"]):
            [span_123](start_span)result = gemini_chat(f"從以下訊息提取購物項目,只回傳JSON:\n{{\"items\": [{{\"item\":\"物品\",\"quantity\":\"數量\"}}]}}\n訊息:{user_text}")[span_123](end_span)
            try:
                [span_124](start_span)data = json.loads(re.sub(r"```json|```", "", result).strip())[span_124](end_span)
                [span_125](start_span)for item in data["items"]: memory_db.add_shopping(item["item"], item.get("quantity", "1"), sender_name)[span_125](end_span)
                [span_126](start_span)await message.reply_text(f" 已加入購物清單:{'、'.join([i['item'] for i in data['items']])}")[span_126](end_span)
            [span_127](start_span)except: await message.reply_text(" 無法識別購物項目")[span_127](end_span)
            return

        # [span_128](start_span)記帳功能[span_128](end_span)
        if any(kw in user_text for kw in ["支出", "花了", "記帳"]):
            [span_129](start_span)result = gemini_chat(f"從以下訊息提取支出資料,只回傳JSON:\n{{\"amount\":金額,\"category\":\"分類\",\"description\":\"描述\"}}\n訊息:{user_text}")[span_129](end_span)
            try:
                [span_130](start_span)data = json.loads(re.sub(r"```json|```", "", result).strip())[span_130](end_span)
                [span_131](start_span)memory_db.add_expense(data["amount"], data["category"], data["description"], sender_name)[span_131](end_span)
                [span_132](start_span)await message.reply_text(f" 已記帳:{data['category']} ${data['amount']} - {data['description']}")[span_132](end_span)
            [span_133](start_span)except: await message.reply_text(" 無法識別支出格式")[span_133](end_span)
            return

        # [span_134](start_span)新聞指令[span_134](end_span)
        if any(kw in user_text for kw in ["發新聞","今日新聞","要新聞","看新聞"]):
            [span_135](start_span)await cmd_news(update, context); return[span_135](end_span)

        # --- 整合 Google 搜尋邏輯 ---
        [span_136](start_span)system_prompt = build_system_prompt()[span_136](end_span)
        # 定義哪些問題會觸發外部搜尋
        search_keywords = ["是什麼", "查詢", "誰是", "天氣", "股價", "最新", "搜尋"]
        
        if any(kw in user_text for kw in search_keywords):
            search_info = manual_google_search(user_text)
            [span_137](start_span)reply = gemini_chat(f"{system_prompt}\n\n【參考外部即時資料】\n{search_info}\n\n請根據以上資訊回覆 {sender_name} 的問題：{user_text}")[span_137](end_span)
        else:
            [span_138](start_span)reply = gemini_chat(f"{system_prompt}\n\n{sender_name} 說:{user_text}")[span_138](end_span)

        if is_important(user_text):
            [span_139](start_span)memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)[span_139](end_span)
        [span_140](start_span)await message.reply_text(reply)[span_140](end_span)

# --- 背景任務與 Web Server (完整保留) ---
async def check_reminders():
    [span_141](start_span)bot = Bot(token=TELEGRAM_BOT_TOKEN)[span_141](end_span)
    sent_today = False
    while True:
        now = datetime.da
