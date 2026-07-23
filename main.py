"""Square Auto Bot — main entry point."""
import logging
import logging.handlers
import os
from telegram import Update
from telegram.ext import Application, ContextTypes

from config import TG_BOT_TOKEN
from bot import setup_handlers, processing_worker

# Log directory
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

# File handler — rotate daily, keep 3 days
file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=3,
    atTime=None,
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

# Root logger
logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])

# Suppress noisy telegram/httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(app: Application):
    """Start background worker after bot init."""
    import asyncio
    asyncio.create_task(processing_worker(app))
    logger.info("🔄 Processing worker launched")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    import telegram
    if isinstance(context.error, telegram.error.Conflict):
        logger.debug("Conflict (normal at startup), ignoring")
        return
    logger.error(f"Unhandled error: {context.error}", exc_info=context.error)
    # Send critical errors to admin via TG
    try:
        from config import ADMIN_ID
        err_text = str(context.error)[:500]
        await context.bot.send_message(
            ADMIN_ID,
            f"🚨 Критическая ошибка:\n{err_text}"
        )
    except Exception:
        pass


def main():
    if not TG_BOT_TOKEN:
        print("ERROR: TG_BOT_TOKEN not set in .env")
        return

    app = Application.builder().token(TG_BOT_TOKEN).build()
    setup_handlers(app)
    app.add_error_handler(error_handler)
    app.post_init = post_init

    logger.info("🟧 Square Auto Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
