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

# ---------------------------------------------------------
# 基本設定
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
TRIGGER_KEYWORD = "安尼亞"

genai.configure(api_key=GEMINI_API_KEY)

memory_db = MemoryDB()
last_reply = {}

# ---------------------------------------------------------
# Gemini 模型（新版，不使用 tools）
# ---------------------------------------------------------
def get_stable_model():
    try:
        available = []
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                available.append(m.name)
                print("可用模型:", m.name)

        preferred_list = [
            "models/gemini-1.5-flash-latest",
            "models/gemini-1.5-flash",
            "models/gemini-1.5-pro-latest",
            "models/gemini-1.5-pro",
            "models/gemini-1.0-pro",
            "models/gemini-pro"
        ]

        for p in preferred_list:
            if p in available:
                print("✅ 使用模型:", p)
                return genai.GenerativeModel(model_name=p)

        if available:
            print("⚠️ 使用第一個可用模型:", available[0])
            return genai.GenerativeModel(model_name=available[0])

    except Exception as e:
        print("⚠️ 模型查找失敗:", e)

    return genai.GenerativeModel(model_name="gemini-pro")


gemini_model = get_stable_model()

# ---------------------------------------------------------
# Google Search Grounding（模式 A：永遠啟用）
# ---------------------------------------------------------
def gemini_chat(prompt):
    try:
        response = gemini_model.generate_content(
            prompt,
            generation_config={
                "grounding_config": {
                    "enable_grounding": True,
                    "sources": ["google_search"]
                }
            }
        )
        return response.text

    except google.api_core.exceptions.ResourceExhausted:
        return "❌ 安尼亞太忙了，請等60秒再試"

    except Exception as e:
        return f"❌ 錯誤：{str(e)}"

# ---------------------------------------------------------
# 記憶分類
# ---------------------------------------------------------
def get_category(text):
    if any(k in text for k in ["我叫", "我是", "他叫", "她叫", "家人"]):
        return "人物"
    if any(k in text for k in ["我喜歡", "我討厭", "我愛", "我怕"]):
        return "喜好"
    if any(k in text for k in ["今天", "昨天", "發生"]):
        return "事件"
    if any(k in text for k in ["設定", "偏好", "習慣", "記錄", "早上", "每天", "自動"]):
        return "設定"
    return "一般"

def is_important(text):
    keys = ["我叫", "我是", "我喜歡", "我討厭", "我住", "記住", "設定",
            "他叫", "她叫", "家人", "今天", "發生", "記錄", "早上", "每天", "自動", "要求"]
    return any(k in text for k in keys)

def check_rate_limit(user_id, chat_type):
    now = datetime.datetime.now()
    if chat_type in ["group", "supergroup"]:
        if user_id in last_reply:
            if (now - last_reply[user_id]).seconds < 30:
                return False
    last_reply[user_id] = now
    return True

# ---------------------------------------------------------
# 系統提示詞
# ---------------------------------------------------------
def build_system_prompt():
    人物 = memory_db.get_by_category("人物")
    喜好 = memory_db.get_by_category("喜好")
    設定 = memory_db.get_by_category("設定")
    事件 = memory_db.get_by_category("事件")

    prompt = """你是安尼亞，一個聰明的家庭助理。
你的名字是安尼亞。
必須使用繁體中文回覆。
不可以自己生成假新聞。
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

# ---------------------------------------------------------
# RSS 新聞
# ---------------------------------------------------------
def parse_rss(url, count=5):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        out = []
        for item in items[:count]:
            title = item.findtext("title") or ""
            desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
            out.append({"title": title, "description": desc})
        return out
    except:
        return []

def translate_news(articles, section_name):
    if not articles:
        return f"暫時無法獲取{section_name}"

    raw = ""
    for i, a in enumerate(articles, 1):
        raw += f"{i}. {a['title']}\n{a['description']}\n\n"

    prompt = f"""請將以下新聞翻譯成繁體中文，每則至少200字：

{raw}
"""
    return gemini_chat(prompt)

def fetch_real_news():
    try:
        ca = parse_rss("https://www.cbc.ca/cmlink/rss-canada", 5)
        ab = parse_rss("https://www.cbc.ca/cmlink/rss-canada-edmonton", 5)

        if len(ab) < 3:
            extra = parse_rss("https://www.cbc.ca/cmlink/rss-canada-calgary", 5)
            ab = (ab + extra)[:5]

        return (
            "🍁 加拿大新聞\n\n" + translate_news(ca, "加拿大新聞"),
            "📍 Alberta 新聞\n\n" + translate_news(ab, "Alberta 新聞")
        )
    except Exception as e:
        return f"❌ 新聞獲取失敗：{e}", ""

async def send_news(target, bot=None):
    ca, ab = fetch_real_news()

    async def send_chunk(text):
        parts = []
        while len(text) > 4000:
            pos = text[:4000].rfind("\n\n")
            if pos == -1:
                pos = 4000
            parts.append(text[:pos])
            text = text[pos:].strip()
        parts.append(text)

        for p in parts:
            if bot:
                await bot.send_message(chat_id=MY_CHAT_ID, text=p)
            else:
                await target.reply_text(p)

    await send_chunk(ca)
    await asyncio.sleep(2)
    await send_chunk(ab)

# ---------------------------------------------------------
# Telegram 指令
# ---------------------------------------------------------
async def cmd_memory(update, context):
    mem = memory_db.get_all_memory()
    if not mem:
        await update.message.reply_text("📭 記憶庫是空的")
    else:
        await update.message.reply_text("📚 記憶庫：\n\n" + "\n".join(mem))

async def cmd_forget(update, context):
    memory_db.forget_all()
    await update.message.reply_text("🗑️ 所有記憶已清除")

async def cmd_news(update, context):
    await update.message.reply_text("📰 正在獲取新聞，請稍等...")
    await send_news(update.message)

async def cmd_calendar(update, context):
    events = memory_db.get_upcoming_events(30)
    if not events:
        await update.message.reply_text("📅 未來30天沒有行程")
        return
    out = "📅 未來30天行程：\n\n"
    for e in events:
        out += f"📌 {e['event_date']} [{e['category']}] {e['title']}\n"
    await update.message.reply_text(out)

async def cmd_shopping(update, context):
    items = memory_db.get_shopping_list()
    if not items:
        await update.message.reply_text("🛒 購物清單是空的")
        return
    out = "🛒 購物清單：\n\n"
    for i, it in enumerate(items, 1):
        out += f"{i}. {it['item']} x{it['quantity']}（{it['added_by']}）\n"
    await update.message.reply_text(out)

async def cmd_expenses(update, context):
    ex = memory_db.get_monthly_expenses()
    if not ex:
        await update.message.reply_text("💰 本月沒有記帳")
        return
    total = sum(float(e["amount"]) for e in ex)
    cats = {}
    for e in ex:
        cats[e["category"]] = cats.get(e["category"], 0) + float(e["amount"])
    out = f"💰 本月支出：${total:.2f}\n\n"
    for c, amt in cats.items():
        out += f"• {c}：${amt:.2f}\n"
    out += "\n詳細記錄：\n"
    for e in ex:
        out += f"• {e['expense_date']} [{e['category']}] {e['description']} ${e['amount']}\n"
    await update.message.reply_text(out)

async def cmd_summary(update, context):
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text
    elif context.args:
        text = " ".join(context.args)
    else:
        await update.message.reply_text("請回覆一段文字並輸入 /summary")
        return
    result = gemini_chat(f"請摘要以下內容成3-5點：\n\n{text}")
    await update.message.reply_text("📝 摘要：\n\n" + result)

async def cmd_models(update, context):
    try:
        models = genai.list_models()
        out = "可用模型：\n"
        for m in models:
            if "generateContent" in m.supported_generation_methods:
                out += f"• {m.name}\n"
        await update.message.reply_text(out[:4000])
    except Exception as e:
        await update.message.reply_text(f"錯誤：{e}")

# ---------------------------------------------------------
# 訊息處理（語音 + 文字）
# ---------------------------------------------------------
async def handle_message(update, context):
    msg = update.message
    if not msg:
        return

    sender = msg.from_user.first_name or "未知"
    chat_type = msg.chat.type
    uid = msg.from_user.id

    # 長訊息自動摘要
    if msg.text and len(msg.text) > 500:
        if chat_type in ["group", "supergroup"]:
            result = gemini_chat(f"請摘要以下內容成3-5點：\n\n{msg.text}")
            await msg.reply_text("📝 自動摘要：\n\n" + result)
            return

    # 語音訊息
    if msg.voice:
        if chat_type in ["group", "supergroup"]:
            if not msg.caption or TRIGGER_KEYWORD not in msg.caption:
                return
        if not check_rate_limit(uid, chat_type):
            return
        try:
            vf = await msg.voice.get_file()
            data = await vf.download_as_bytearray()
            with open("/tmp/voice.ogg", "wb") as f:
                f.write(data)
            with open("/tmp/voice.ogg", "rb") as f:
                audio = f.read()
            response = gemini_model.generate_content([
                {"mime_type": "audio/ogg", "data": audio},
                "請將這段語音轉成繁體中文"
            ])
            await msg.reply_text("🎤 你說：" + response.text)
        except Exception as e:
            await msg.reply_text(f"❌ 語音辨識失敗：{e}")
        return

    # 文字訊息
    if msg.text:
        text = msg.text

        if chat_type in ["group", "supergroup"]:
            if TRIGGER_KEYWORD not in text:
                return

        if not check_rate_limit(uid, chat_type):
            return

        # 偏好設定
        if text.startswith("設定:"):
            parts = text[3:].split("=")
            if len(parts) == 2:
                memory_db.set_preference(parts[0].strip(), parts[1].strip())
                await msg.reply_text(f"✅ 已記住偏好：{parts[0].strip()} = {parts[1].strip()}")
                return

        # 記憶
        if any(k in text for k in ["記錄", "記住"]):
            memory_db.add_memory(text, category=get_category(text), sender_name=sender)
            await msg.reply_text("✅ 已記錄！")
            return

        # 行程
        if "加入行程" in text or "新增行程" in text:
            result = gemini_chat(f"""請從以下訊息提取行程資料，只回傳 JSON：
{{
 "title": "標題",
 "category": "分類",
 "date": "YYYY-MM-DD",
 "reminder_days": 1
}}
訊息：{text}
今天日期：{datetime.date.today()}
""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                memory_db.add_event(
                    title=data["title"],
                    category=data["category"],
                    event_date=data["date"],
                    reminder_days=data.get("reminder_days", 1),
                    created_by=sender
                )
                await msg.reply_text(f"📅 已加入行程：{data['date']} {data['title']}")
            except:
                await msg.reply_text("❌ 無法識別行程格式")
            return

        # 購物
        if any(k in text for k in ["買", "購物", "加入清單"]):
            result = gemini_chat(f"""請從以下訊息提取購物項目，只回傳 JSON：
{{"items":[{{"item":"名稱","quantity":"數量"}}]}}
訊息：{text}
""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                for it in data["items"]:
                    memory_db.add_shopping(it["item"], it.get("quantity", "1"), sender)
                await msg.reply_text("🛒 已加入購物清單")
            except:
                await msg.reply_text("❌ 無法識別購物項目")
            return

        # 記帳
        if any(k in text for k in ["支出", "花了", "記帳"]):
            result = gemini_chat(f"""請從以下訊息提取支出資料，只回傳 JSON：
{{"amount":數字,"category":"分類","description":"描述"}}
訊息：{text}
""")
            try:
                result = re.sub(r"```json|```", "", result).strip()
                data = json.loads(result)
                memory_db.add_expense(data["amount"], data["category"], data["description"], sender)
                await msg.reply_text(f"💰 已記帳：{data['category']} ${data['amount']} - {data['description']}")
            except:
                await msg.reply_text("❌ 無法識別支出格式")
            return

        # 新聞
        if any(k in text for k in ["新聞", "今日新聞", "看新聞"]):
            await msg.reply_text("📰 正在獲取新聞，請稍等...")
            await send_news(msg)
            return

        # 一般聊天
        system_prompt = build_system_prompt()
        reply = gemini_chat(f"{system_prompt}\n\n{sender} 說：{text}")

        if is_important(text):
            memory_db.add_memory(text, category=get_category(text), sender_name=sender)

        await msg.reply_text(reply)


    # ---------------------------------------------------------
# 排程（每日提醒 + 每日新聞）
# ---------------------------------------------------------
async def check_reminders():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent = False

    while True:
        now = datetime.datetime.now()

        # 每天 8:00 發送行程提醒
        if now.hour == 8 and now.minute == 0 and not sent:
            events = memory_db.get_upcoming_events(7)
            if events:
                out = "⏰ 本週提醒：\n\n"
                for e in events:
                    out += f"📌 {e['event_date']} [{e['category']}] {e['title']}\n"
                await bot.send_message(chat_id=MY_CHAT_ID, text=out)

            sent = True

        # 重置
        if now.hour != 8:
            sent = False

        await asyncio.sleep(60)


async def send_daily_news():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent = False

    while True:
        now = datetime.datetime.now()

        # 每天 9:00 自動發送新聞
        if now.hour == 9 and now.minute == 0 and not sent:
            await bot.send_message(chat_id=MY_CHAT_ID, text="📰 早晨新聞來了，請稍等約30秒...")
            await send_news(None, bot=bot)
            sent = True

        if now.hour != 9:
            sent = False

        await asyncio.sleep(60)


# ---------------------------------------------------------
# Render 健康檢查 HTTP Server
# ---------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Anya Bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# ---------------------------------------------------------
# 主程式
# ---------------------------------------------------------
def main():
    # Render 健康檢查
    threading.Thread(target=run_web, daemon=True).start()

    # Telegram Bot
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 指令
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("shopping", cmd_shopping))
    app.add_handler(CommandHandler("expenses", cmd_expenses))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("models", cmd_models))

    # 訊息處理
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # 排程
    loop = asyncio.get_event_loop()
    loop.create_task(send_daily_news())
    loop.create_task(check_reminders())

    print("🚀 安尼亞 Bot 已成功啟動！")
    app.run_polling()


if __name__ == "__main__":
    main()
