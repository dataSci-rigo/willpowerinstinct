import logging
import sys
import os

# Make sibling modules importable when running `python bot/main.py`
sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import Application

import db
from config import TELEGRAM_TOKEN, OWNER_CHAT_ID, EVENING_HOUR, TIMEZONE
from handlers import register_handlers, _send_energy_prompt
from scheduler import register_jobs

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    await db.init_db()
    logger.info("Database initialised")

    # Catch-up: if the bot restarted after the evening check-in time and
    # there's no entry saved for today yet, send the check-in now.
    now_local = datetime.now(ZoneInfo(TIMEZONE))
    if now_local.hour >= EVENING_HOUR:
        cycle = await db.get_active_cycle(OWNER_CHAT_ID)
        if cycle:
            today = now_local.date().isoformat()
            entry = await db.get_daily_entry(cycle["id"], today)
            if not entry or not entry.get("energy_level"):
                logger.info("Sending catch-up evening check-in for %s", today)

                class _FakeContext:
                    def __init__(self, bot):
                        self.bot = bot
                        self.user_data = {}

                await _send_energy_prompt(_FakeContext(application.bot), OWNER_CHAT_ID)


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
