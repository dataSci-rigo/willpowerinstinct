"""
Scheduled jobs wired into python-telegram-bot's job_queue (APScheduler).

Jobs:
  morning_nudge     — 8:00 AM LA daily
  evening_checkin   — 9:00 PM LA daily
  weekly_kickoff    — Monday 8:00 AM LA
  weekly_synthesis  — Sunday 9:00 PM LA
"""

import logging
from datetime import time as dtime
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db
from config import MORNING_HOUR, EVENING_HOUR, OWNER_CHAT_ID, load_program
from handlers import _send_energy_prompt
from synthesis import run_weekly_synthesis

logger = logging.getLogger(__name__)

LA = ZoneInfo("America/Los_Angeles")


async def morning_nudge(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = OWNER_CHAT_ID
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return

    program = load_program()
    week = program.get(cycle["current_week"], {})
    microscope = week.get("microscope", {})

    from datetime import date
    from datetime import timedelta
    started = date.fromisoformat(cycle["started_at"])
    day_in_week = ((date.today() - started).days % 7)
    prompts = week.get("daily_prompts", [])
    prompt = prompts[day_in_week % len(prompts)] if prompts else ""

    lines = [
        f"Good morning ☀️ — Week {cycle['current_week']}: _{week.get('title', '')}_\n",
        f"*Today's microscope prompt:*\n{prompt}\n",
        f"*Experiment reminder:* {week.get('experiment', {}).get('text', '')}",
    ]
    await context.bot.send_message(
        chat_id=user_id,
        text="\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Morning nudge sent to %d", user_id)


async def evening_checkin(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = OWNER_CHAT_ID
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return
    await _send_energy_prompt(context, user_id)
    logger.info("Evening check-in sent to %d", user_id)


async def weekly_kickoff(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires Monday morning: advance to the next week if 7+ days have passed."""
    from datetime import date, timedelta
    user_id = OWNER_CHAT_ID
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return

    started = date.fromisoformat(cycle["started_at"])
    days_elapsed = (date.today() - started).days
    expected_week = min(days_elapsed // 7 + 1, 10)

    if expected_week > cycle["current_week"]:
        await db.advance_week(cycle["id"], expected_week)
        cycle["current_week"] = expected_week

    program = load_program()
    week = program.get(cycle["current_week"], {})
    microscope = week.get("microscope", {})
    experiment = week.get("experiment", {})

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"New week! 📖 *Week {cycle['current_week']}: {week.get('title', '')}*\n\n"
            f"_{week.get('theme', '')}_\n\n"
            f"*Under the Microscope*\n{microscope.get('text', '')}\n\n"
            f"*This Week's Experiment*\n{experiment.get('text', '')}"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Weekly kickoff sent for week %d", cycle["current_week"])


async def weekly_synthesis_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires Sunday evening: call Claude, DM synthesis, store in DB."""
    user_id = OWNER_CHAT_ID
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return

    await context.bot.send_message(
        chat_id=user_id,
        text=f"Generating your Week {cycle['current_week']} synthesis... (this takes a moment)"
    )

    try:
        text = await run_weekly_synthesis(user_id, cycle)
    except Exception:
        logger.exception("Synthesis failed for cycle %d week %d",
                         cycle["id"], cycle["current_week"])
        await context.bot.send_message(
            chat_id=user_id,
            text="Synthesis failed — check logs. You can retry with /history."
        )
        return

    await context.bot.send_message(
        chat_id=user_id,
        text=f"*Week {cycle['current_week']} Synthesis*\n\n{text}",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Weekly synthesis sent for week %d", cycle["current_week"])


def register_jobs(app) -> None:
    jq = app.job_queue

    jq.run_daily(
        morning_nudge,
        time=dtime(hour=MORNING_HOUR, minute=0, tzinfo=LA),
        name="morning_nudge",
    )
    jq.run_daily(
        evening_checkin,
        time=dtime(hour=EVENING_HOUR, minute=0, tzinfo=LA),
        name="evening_checkin",
    )
    jq.run_daily(
        weekly_kickoff,
        time=dtime(hour=MORNING_HOUR, minute=0, tzinfo=LA),
        days=(0,),  # Monday
        name="weekly_kickoff",
    )
    jq.run_daily(
        weekly_synthesis_job,
        time=dtime(hour=EVENING_HOUR, minute=0, tzinfo=LA),
        days=(6,),  # Sunday
        name="weekly_synthesis",
    )
