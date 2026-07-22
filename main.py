"""Square Auto Bot — main entry point."""
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes

from config import TG_BOT_TOKEN
from bot import setup_handlers, processing_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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
