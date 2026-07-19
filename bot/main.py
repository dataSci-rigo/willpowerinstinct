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
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo

    await db.init_db()
    logger.info("Database initialised")

    cycle = await db.get_active_cycle(OWNER_CHAT_ID)
    if not cycle:
        return

    now_local = datetime.now(ZoneInfo(TIMEZONE))
    today = now_local.date()

    # ── Catch-up: advance week if weekly_kickoff was missed ───────────────────
    started = date.fromisoformat(cycle["started_at"])
    days_elapsed = (today - started).days
    expected_week = min(days_elapsed // 7 + 1, 10)
    if expected_week > cycle["current_week"]:
        await db.advance_week(cycle["id"], expected_week)
        logger.info("Catch-up: advanced cycle %d to week %d", cycle["id"], expected_week)

    # ── Catch-up: evening check-in if missed AND within the same evening ──────
    # Only send if it's between 9 PM and 11:59 PM — not during daytime restarts.
    if EVENING_HOUR <= now_local.hour <= 23:
        entry = await db.get_daily_entry(cycle["id"], today.isoformat())
        if not entry or not entry.get("energy_level"):
            logger.info("Catch-up: sending missed evening check-in for %s", today)

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
