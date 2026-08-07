import os
import logging

logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
API_ID: int = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH: str = os.environ.get("TELEGRAM_API_HASH", "")
MONGO_URI: str = os.environ.get("MONGO_URI", "")

def validate():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not API_ID:
        missing.append("TELEGRAM_API_ID")
    if not API_HASH:
        missing.append("TELEGRAM_API_HASH")
    if not MONGO_URI:
        missing.append("MONGO_URI")
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")
    logger.info("Configuration validated successfully.")
