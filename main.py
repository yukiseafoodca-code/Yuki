import os
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

from memory import MemoryDB

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

groq_client = Groq(api_key=GROQ_API_KEY)
memory_db = MemoryDB()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text

    # 讀取所有記憶
    memories = memory_db.get_all_memory()
    memory_text = "\n".join(memories)

    # 把記憶 + 使用者訊息一起送給模型
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are ClawDBot with long-term memory."},
            {"role": "system", "content": f"Here is your memory:\n{memory_text}"},
            {"role": "user", "content": user_text}
        ]
    )

    reply = response.choices[0].message.content

    # 自動記憶（簡單版）
    if len(user_text) > 5:
        memory_db.add_memory(user_text)

    await update.message.reply_text(reply)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot with memory is running on Render")
    app.run_polling()

if __name__ == "__main__":
    main()
