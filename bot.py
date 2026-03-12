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

@flask_app.route("/debug-perplexity", methods=["POST"])
def debug_perplexity():
    """Temporary debug: show raw Perplexity response."""
    data = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    mode = (data.get("mode") or "manual").strip()
    if not query:
        return jsonify({"error": "empty query"}), 400
    try:
        async def _dbg():
            from tools.perplexity import search_perplexity
            if mode == "manual":
                from pipeline_manual import MANUAL_SYSTEM_PROMPT, MANUAL_USER_PROMPT
                r = await search_perplexity(query, MANUAL_SYSTEM_PROMPT, MANUAL_USER_PROMPT.format(product_name=query))
            else:
                r = await search_perplexity(query)
            content = r.get("choices", [{}])[0].get("message", {}).get("content", "")
            sources = r.get("citations", []) or []
            return {"content": content[:2000], "sources": sources[:10]}
        return jsonify(asyncio.run(_dbg()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/search", methods=["POST"])
def search():
    data  = request.get_json(force=True)
    query = (data.get("query") or "").strip()
    mode  = (data.get("mode") or "specs").strip()

    if not query:
        return jsonify({"error": "Пустой запрос"}), 400

    try:
        result = asyncio.run(_run(query, mode))
        # Save to Sheets in background (don't block response)
        threading.Thread(target=_save_to_sheets, args=(query, mode, result), daemon=True).start()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Search error [{mode}]: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


async def _run(product_name: str, mode: str) -> dict:
    if mode == "manual":
        from pipeline_manual import run_manual_pipeline
        return await run_manual_pipeline(product_name)
    elif mode == "images":
        from pipeline_images import run_images_pipeline
        return await run_images_pipeline(product_name)
    else:
        from pipeline import run_pipeline_dict
        return await run_pipeline_dict(product_name)


def _save_to_sheets(product_name: str, mode: str, result: dict):
    """Save any search result to Google Sheets."""
    try:
        import json, os
        from datetime import datetime
        import gspread
        from google.oauth2.service_account import Credentials

        sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            return
        creds = Credentials.from_service_account_info(
            json.loads(sa_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(os.getenv("GOOGLE_SHEETS_ID", ""))
        ws = sh.worksheet(os.getenv("GOOGLE_SHEETS_NAME", "Specs"))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if mode == "specs":
            row = [
                product_name,
                result.get("official_name") or product_name,
                result.get("weight_kg", ""),
                result.get("width_mm", ""),
                result.get("height_mm", ""),
                result.get("depth_mm", ""),
                result.get("confidence", ""),
                result.get("notes", ""),
                now, mode,
                result.get("pdf_url", ""),
            ]
        elif mode == "manual":
            manuals = result.get("manuals", [])
            urls = " | ".join(m["url"] for m in manuals)
            row = [product_name, result.get("official_name") or product_name,
                   "", "", "", "", "", urls, now, mode, ""]
        elif mode == "images":
            images = result.get("images", [])
            urls = " | ".join(i["url"] for i in images)
            row = [product_name, result.get("official_name") or product_name,
                   "", "", "", "", "", urls, now, mode, ""]
        else:
            return

        ws.append_row(row)
        logger.info(f"Saved to Sheets: {product_name} [{mode}]")
    except Exception as e:
        logger.warning(f"Sheets save failed: {e}")


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
