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

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
TRIGGER_KEYWORD = "安尼亞"

genai.configure(api_key=GEMINI_API_KEY)

def get_stable_model():
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini-1.5-flash' in m.name:
                    print(f"✅ 使用模型: {m.name}")
                    return genai.GenerativeModel(model_name=m.name)
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name:
                    print(f"✅ 備用模型: {m.name}")
                    return genai.GenerativeModel(model_name=m.name)
    except Exception as e:
        print(f"⚠️ 模型查找失敗: {e}")
    return genai.GenerativeModel('gemini-1.5-flash')

gemini_model = get_stable_model()
memory_db = MemoryDB()
last_reply = {}

def get_category(text):
    if any(kw in text for kw in ["我叫", "我是", "他叫", "她叫", "家人"]):
        return "人物"
    elif any(kw in text for kw in ["我喜歡", "我討厭", "我愛", "我怕"]):
        return "喜好"
    elif any(kw in text for kw in ["今天", "昨天", "發生"]):
        return "事件"
    elif any(kw in text for kw in ["設定", "偏好", "習慣", "記錄", "早上", "每天", "自動"]):
        return "設定"
    else:
        return "一般"

def is_important(text):
    keywords = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定",
                "他叫", "她叫", "家人", "今天", "發生", "記錄", "早上", "每天", "自動", "要求"]
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

def gemini_chat(prompt):
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except google.api_core.exceptions.ResourceExhausted:
        return "❌ 安尼亞太忙了，請等60秒再試"
    except Exception as e:
        return f"❌ 錯誤：{str(e)}"

def build_system_prompt():
    人物 = memory_db.get_by_category("人物")
    喜好 = memory_db.get_by_category("喜好")
    設定 = memory_db.get_by_category("設定")
    事件 = memory_db.get_by_category("事件")

    prompt = """你是安尼亞，一個聰明的家庭助理。
你的名字是安尼亞，不是其他名字。
必須使用繁體中文回覆，絕對禁止使用簡體中文。
不可以自己生成新聞內容。
回答要簡短直接。

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
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            articles.append({"title": title, "description": desc})
        return articles
    except:
        return []

def translate_news(articles, section_name):
    news_text = ""
    for i, a in enumerate(articles, 1):
        news_text += f"{i}. {a['title']}\n{a['description']}\n\n"
    if not news_text.strip():
        return f"暫時無法獲取{section_name}"
    prompt = f"""請將以下5則真實新聞翻譯並擴展成繁體中文。
要求：每則最少200字，每則之間空一行，不要用簡體中文，不要加**或##符號。
格式：
1. 新聞標題
新聞內容

原文：
{news_text}"""
    return gemini_chat(prompt)

def fetch_real_news():
    try:
        canada_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada", 5)
        alberta_articles = parse_rss("https://www.cbc.ca/cmlink/rss-canada-edmonton", 5)
        if len(alberta_articles) < 3:
            extra = parse_rss("https://www.cbc.ca/cmlink/rss-canada-calgary", 5)
            alberta_articles = (alberta_articles + extra)[:5]
        canada_translated = translate_news(canada_articles, "加拿大新聞")
        alberta_translated = translate_news(alberta_articles, "Alberta/Edmonton 新聞")
        return "🍁 加拿大重點新聞\n\n" + canada_translated, "📍 Alberta 或 Edmonton 新聞\n\n" + alberta_translated
    except Exception as e:
        return f"❌ 新聞獲取失敗：{str(e)}", ""

async def send_news(target, bot=None):
    canada_news, alberta_news = fetch_real_news()

    async def send_chunk(text):
        parts = []
        while len(text) > 4000:
            split_pos = text[:4000].rfind("\n\n")
            if split_pos == -1:
                split_pos = 4000
            parts.append(text[:split_pos])
            text = text[split_pos:].strip()
        parts.append(text)
        for part in parts:
            if part.strip():
                if bot:
                    await bot.send_message(chat_id=MY_CHAT_ID, text=part)
                else:
                    await target.reply_text(part)

    await send_chunk(canada_news)
    await asyncio.sleep(2)
    await send_chunk(alberta_news)

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = memory_db.get_all_memory()
    if not memories:
        await update.message.reply_text("📭 記憶庫是空的")
        return
    await update.message.reply_text("📚 記憶庫：\n\n" + "\n".join(memories))

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_db.forget_all()
    await update.message.reply_text("🗑️ 所有記憶已清除")

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 正在獲取最新真實新聞，請稍等約30秒...")
    await send_news(update.message)

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = memory_db.get_upcoming_events(30)
    if not events:
        await update.message.reply_text("📅 未來30天沒有行程")
        return
    text = "📅 未來30天行程：\n\n"
    for e in events:
        text += f"📌 {e['event_date']} [{e['category']}] {e['title']}\n"
    await update.message.reply_text(text)

async def cmd_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = memory_db.get_shopping_list()
    if not items:
        await update.message.reply_text("🛒 購物清單是空的")
        return
    text = "🛒 購物清單：\n\n"
    for i, item in enumerate(items, 1):
        text += f"{i}. {item['item']} x{item['quantity']} （{item['added_by']}）\n"
    await update.message.reply_text(text)

async def cmd_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expenses = memory_db.get_monthly_expenses()
    if not expenses:
        await update.message.reply_text("💰 本月沒有記帳記錄")
        return
    total = sum(float(e['amount']) for e in expenses)
    categories = {}
    for e in expenses:
        cat = e['category']
        categories[cat] = categories.get(cat, 0) + float(e['amount'])
    text = f"💰 本月支出摘要：\n總計：${total:.2f}\n\n"
    for cat, amount in categories.items():
        text += f"• {cat}：${amount:.2f}\n"
    text += "\n詳細記錄：\n"
    for e in expenses:
        text += f"• {e['expense_date']} [{e['category']}] {e['description']} ${e['amount']}\n"
    await update.message.reply_text(text)

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text_to_summarize = update.message.reply_to_message.text
    elif context.args:
        text_to_summarize = " ".join(context.args)
    else:
        await update.message.reply_text("請回覆一條訊息並輸入 /summary")
        return
    result = gemini_chat(f"請用繁體中文將以下內容摘要成3-5點重點，每點一行：\n\n{text_to_summarize}")
    await update.message.reply_text("📝 摘要：\n\n" + result)

async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        models = genai.list_models()
        text = "可用模型：\n"
        for m in models:
            if "generateContent" in m.supported_generation_methods:
                text += f"• {m.name}\n"
        await update.message.reply_text(text[:4000])
    except Exception as e:
        await update.message.reply_text(f"錯誤：{str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    sender_name = message.from_user.first_name or "未知"
    chat_type = message.chat.type
    user_id = message.from_user.id

    # 自動摘要長訊息
    if message.text and len(message.text) > 500:
        if chat_type in ["group", "supergroup"]:
            result = gemini_chat(f"請用繁體中文將以下內容摘要成3-5點重點，每點一行：\n\n{message.text}")
            await message.reply_text("📝 自動摘要：\n\n" + result)
            return

    # 圖片訊息
    if message.photo:
        if chat_type in ["group", "supergroup"]:
            if not message.caption or TRIGGER_KEYWORD not in message.caption:
                return
        if not check_rate_limit(user_id, chat_type):
            return
        try:
            photo_file = await message.photo[-1].get_file()
            photo_bytes = bytes(await photo_file.download_as_bytearray())
            img = PIL.Image.open(io.BytesIO(photo_bytes))
            caption = message.caption or "請描述這張圖片"
            response = gemini_model.generate_content([
                f"{caption}，必須用繁體中文回答，不可用簡體中文",
                img
            ])
            await message.reply_text(f"🖼️ {response.text}")
        except google.api_core.exceptions.ResourceExhausted:
            await message.reply_text("❌ 安尼亞太忙了，請等60秒再試")
        except Exception as e:
            await message.reply_text(f"❌ 圖片辨識失敗：{str(e)}")
        return

    # 語音訊息
    elif message.voice:
        if chat_type in ["group", "supergroup"]:
            if not message.caption or TRIGGER_KEYWORD not in message.caption:
                return
        if not check_rate_limit(user_id, chat_type):
            return
        try:
            voice_file = await message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            with open("/tmp/voice.ogg", "wb") as f:
                f.write(voice_bytes)
            with open("/tmp/voice.ogg", "rb") as f:
                audio_data = f.read()
            response = gemini_model.generate_content([
                {"mime_type": "audio/ogg", "data": audio_data},
                "請將這段語音轉錄成繁體中文文字"
            ])
            await message.reply_text(f"🎤 你說：{response.text}")
        except Exception as e:
            await message.reply_text(f"❌ 語音辨識失敗：{str(e)}")
        return

    # 文字訊息
    elif message.text:
        user_text = message.text

        if chat_type in ["group", "supergroup"]:
            if TRIGGER_KEYWORD not in user_text:
                return

        if not check_rate_limit(user_id, chat_type):
            return

        if user_text.startswith("設定:"):
            parts = user_text[3:].split("=")
            if len(parts) == 2:
                memory_db.set_preference(parts[0].strip(), parts[1].strip())
                await message.reply_text(f"✅ 已記住偏好：{parts[0].strip()} = {parts[1].strip()}")
                return

        if any(kw in user_text for kw in ["記錄", "記住"]):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)
            await message.reply_text("✅ 已記錄！")
            return

        if "加入行程" in user_text or "新增行程" in user_text:
            result = gemini_chat(f"""從以下訊息提取行程資料，只回傳 JSON，不要其他文字：
{{"title": "標題", "category": "分類(家庭活動/醫生預約/垃圾回收/上課提醒/生日)", "date": "YYYY-MM-DD", "reminder_days": 1}}
訊息：{user_text}
今天日期：{datetime.date.today()}""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                memory_db.add_event(
                    title=data["title"],
                    category=data["category"],
                    event_date=data["date"],
                    reminder_days=data.get("reminder_days", 1),
                    created_by=sender_name
                )
                await message.reply_text(f"📅 已加入行程：{data['date']} {data['title']}")
            except:
                await message.reply_text("❌ 無法識別行程格式")
            return

        if "買" in user_text or "購物" in user_text or "加入清單" in user_text:
            result = gemini_chat(f"""從以下訊息提取購物項目，只回傳 JSON，不要其他文字：
{{"items": [{{"item": "物品名稱", "quantity": "數量"}}]}}
訊息：{user_text}""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                for item in data["items"]:
                    memory_db.add_shopping(item["item"], item.get("quantity", "1"), sender_name)
                items_text = "、".join([i["item"] for i in data["items"]])
                await message.reply_text(f"🛒 已加入購物清單：{items_text}")
            except:
                await message.reply_text("❌ 無法識別購物項目")
            return

        if "支出" in user_text or "花了" in user_text or "記帳" in user_text:
            result = gemini_chat(f"""從以下訊息提取支出資料，只回傳 JSON，不要其他文字：
{{"amount": 金額數字, "category": "分類(食物/交通/娛樂/醫療/購物/其他)", "description": "描述"}}
訊息：{user_text}""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                memory_db.add_expense(data["amount"], data["category"], data["description"], sender_name)
                await message.reply_text(f"💰 已記帳：{data['category']} ${data['amount']} - {data['description']}")
            except:
                await message.reply_text("❌ 無法識別支出格式")
            return

        if any(kw in user_text for kw in ["發新聞", "今日新聞", "要新聞", "給我新聞", "看新聞"]):
            await message.reply_text("📰 正在獲取最新真實新聞，請稍等約30秒...")
            await send_news(message)
            return

        system_prompt = build_system_prompt()
        reply = gemini_chat(f"{system_prompt}\n\n{sender_name} 說：{user_text}")

        if is_important(user_text):
            memory_db.add_memory(user_text, category=get_category(user_text), sender_name=sender_name)

        await message.reply_text(reply)

async def check_reminders():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent_today = False
    while True:
        now = datetime.datetime.now()
        if now.hour == 8 and now.minute == 0 and not sent_today:
            events = memory_db.get_upcoming_events(7)
            if events:
                text = "⏰ 本週提醒：\n\n"
                for e in events:
                    text += f"📌 {e['event_date']} [{e['category']}] {e['title']}\n"
                await bot.send_message(chat_id=MY_CHAT_ID, text=text)
            sent_today = True
        if now.hour != 8:
            sent_today = False
        await asyncio.sleep(60)

async def send_daily_news():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent_today = False
    while True:
        now = datetime.datetime.now()
        if now.hour == 9 and now.minute == 0 and not sent_today:
            await bot.send_message(chat_id=MY_CHAT_ID, text="📰 早晨新聞來了，請稍等約30秒...")
            await send_news(None, bot=bot)
            sent_today = True
        if now.hour != 9:
            sent_today = False
        await asyncio.sleep(60)

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
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("shopping", cmd_shopping))
    app.add_handler(CommandHandler("expenses", cmd_expenses))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    loop = asyncio.get_event_loop()
    loop.create_task(send_daily_news())
    loop.create_task(check_reminders())
    print("🚀 安尼亞 Bot 已成功啟動！")
    app.run_polling()

if __name__ == "__main__":
    main()
