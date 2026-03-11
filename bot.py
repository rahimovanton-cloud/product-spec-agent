import asyncio
import logging
import os
import threading

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT           = int(os.getenv("PORT", 8080))

# ── Flask ─────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return render_template("index.html")

@flask_app.route("/health")
def health():
    return "ok", 200

@flask_app.route("/search", methods=["POST"])
def search():
    data = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400

    try:
        result = asyncio.run(_run(query))
        return jsonify(result)
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

async def _run(product_name: str) -> dict:
    from pipeline import run_pipeline_dict
    return await run_pipeline_dict(product_name)


# ── Telegram bot ──────────────────────────────────────────────────────────────
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
        logger.info("Telegram bot polling started")
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()

    asyncio.run(main())


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    if TELEGRAM_TOKEN:
        t = threading.Thread(target=run_bot, daemon=True)
        t.start()
        logger.info("Telegram bot thread started")

    logger.info(f"Flask starting on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)
