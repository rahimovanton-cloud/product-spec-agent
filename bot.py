import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
PORT           = int(os.getenv("PORT", 8080))

# ── Health check server (keeps Render happy) ──────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass

health_server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
threading.Thread(target=health_server.serve_forever, daemon=True).start()
logger.info(f"Health server listening on port {PORT}")

# ── Bot imports (after health server is up) ───────────────────────────────────
try:
    from telegram import Update
    from telegram.ext import Application, MessageHandler, filters, ContextTypes
    from pipeline import run_pipeline
    logger.info("All imports OK")
except Exception as e:
    logger.error(f"Import failed: {e}", exc_info=True)
    # Keep running so health check stays alive and Render shows the error
    import time
    while True:
        time.sleep(60)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id      = update.message.chat.id
    product_name = update.message.text.strip()

    await update.message.reply_text(f"Ищу характеристики: {product_name}...")

    result = await run_pipeline(product_name, chat_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text=result,
        parse_mode="Markdown",
    )


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if WEBHOOK_URL:
        logger.info(f"Webhook mode: {WEBHOOK_URL}/webhook")
        # Stop the health server — webhook server takes over the port
        health_server.shutdown()
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="webhook",
        )
    else:
        logger.info("Polling mode (local)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
