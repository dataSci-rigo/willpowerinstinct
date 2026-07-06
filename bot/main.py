import logging
import sys
import os

# Make sibling modules importable when running `python bot/main.py`
sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import Application

import db
from config import TELEGRAM_TOKEN
from handlers import register_handlers
from scheduler import register_jobs

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await db.init_db()
    logger.info("Database initialised")


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    register_handlers(app)
    register_jobs(app)

    logger.info("Willpower Instinct bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
