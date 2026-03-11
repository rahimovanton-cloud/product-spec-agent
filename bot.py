import asyncio
import logging
import os
import threading

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT           = int(os.getenv("PORT", 8080))

# ── Flask app (Render health check) ──────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "ok", 200


# ── Telegram bot (runs in background thread) ──────────────────────────────────
def run_bot():
    from telegram.ext import Application, MessageHandler, filters

    async def handle_message(update, context):
        if not update.message or not update.message.text:
            return
        product_name = update.message.text.strip()
        chat_id      = update.message.chat.id

        await update.message.reply_text(f"Ищу характеристики: {product_name}...")

        from pipeline import run_pipeline
        result = await run_pipeline(product_name, chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=result,
            parse_mode="Markdown",
        )

    async def main():
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        logger.info("Bot polling started")
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()  # run forever

    asyncio.run(main())


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    # Start bot in background thread
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    logger.info(f"Bot thread started, Flask on port {PORT}")

    # Flask serves health checks on main thread
    flask_app.run(host="0.0.0.0", port=PORT)
