"""
Entry point for the Multi-User Telegram Userbot Manager.
"""
import asyncio
import logging
import sys

from telegram.ext import ApplicationBuilder, Application

import config
from database.mongo import connect
from userbot.manager import UserbotManager
from bot.handlers import register_all

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """Called once after the Application is fully initialised."""
    await connect()
    manager: UserbotManager = application.bot_data["manager"]
    await manager.start_all()
    logger.info("All systems up.")


async def post_shutdown(application: Application) -> None:
    """Called once during shutdown."""
    manager: UserbotManager = application.bot_data["manager"]
    clients = list(manager._clients.values())
    await asyncio.gather(*(c.stop() for c in clients), return_exceptions=True)
    logger.info("All userbots stopped.")


def main() -> None:
    config.validate()

    manager = UserbotManager()

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["manager"] = manager
    register_all(app, manager)

    logger.info("Starting bot polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
