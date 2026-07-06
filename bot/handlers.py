"""
All Telegram command and callback handlers.

Conversation states
-------------------
/start onboarding:  CHOOSE_TYPE → ENTER_CHALLENGE → ENTER_WANT → CONFIRM
/log urge:          LOG_TRIGGER → LOG_GAVE_IN → LOG_INTENSITY → LOG_NOTES
/reset:             RESET_CONFIRM

Evening check-in (scheduler-driven):
  Uses user_data["checkin"] dict to pass state between inline-keyboard steps.
  Free-text collection uses user_data["awaiting"] = "obs" | "reflect" | "sleep".
"""

import logging
from datetime import date, datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from config import OWNER_CHAT_ID, load_program

logger = logging.getLogger(__name__)

# ── conversation state constants ──────────────────────────────────────────────
CHOOSE_TYPE, ENTER_CHALLENGE, ENTER_WANT, CONFIRM = range(4)
LOG_TRIGGER, LOG_GAVE_IN, LOG_INTENSITY, LOG_NOTES = range(4, 8)
RESET_CONFIRM = 8


# ── helpers ───────────────────────────────────────────────────────────────────

def _kb(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
         for row in buttons]
    )


def _type_label(t: str) -> str:
    return {"i_will": "I Will", "i_wont": "I Won't", "i_want": "I Want"}.get(t, t)


def _adherence_emoji(a: Optional[str]) -> str:
    return {"yes": "✅", "no": "❌", "partial": "〰️"}.get(a or "", "—")


async def _no_cycle(update: Update) -> None:
    await update.effective_message.reply_text(
        "No active cycle. Use /start to begin a new cycle."
    )


async def _get_week_data(cycle: dict) -> dict:
    program = load_program()
    return program.get(cycle["current_week"], {})


# ── /start onboarding ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if cycle:
        await update.message.reply_text(
            f"You already have an active cycle (Week {cycle['current_week']}).\n"
            "Use /status to see your progress or /reset to start fresh."
        )
        return ConversationHandler.END

    kb = _kb([
        [("I Will (add a habit)", "start:type:i_will")],
        [("I Won't (break a habit)", "start:type:i_wont")],
        [("I Want (pursue a goal)", "start:type:i_want")],
    ])
    await update.message.reply_text(
        "Welcome to the Willpower Instinct tracker.\n\n"
        "Which power is your focus this cycle?",
        reply_markup=kb,
    )
    return CHOOSE_TYPE


async def start_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[-1]
    context.user_data["challenge_type"] = choice
    label = _type_label(choice)
    await query.edit_message_text(
        f"Good — your challenge power is *{label}*.\n\n"
        f"Describe your specific challenge in one sentence.\n"
        f"Example: \"I will meditate for 10 minutes every morning.\"",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENTER_CHALLENGE


async def start_enter_challenge(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["challenge_text"] = update.message.text.strip()
    await update.message.reply_text(
        "Now your *I Want* anchor — the deeper reason behind this challenge.\n"
        "What long-term outcome are you really after?\n"
        "Example: \"I want to feel calmer and more present.\"",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENTER_WANT


async def start_enter_want(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["i_want_anchor"] = update.message.text.strip()
    ct = context.user_data["challenge_type"]
    ch = context.user_data["challenge_text"]
    iw = context.user_data["i_want_anchor"]
    kb = _kb([
        [("Yes, start the cycle", "start:confirm:yes"),
         ("Cancel", "start:confirm:no")],
    ])
    await update.message.reply_text(
        f"Here's your cycle:\n\n"
        f"*Challenge ({_type_label(ct)}):* {ch}\n"
        f"*I Want anchor:* {iw}\n\n"
        "Start Week 1?",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return CONFIRM


async def start_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if "no" in query.data:
        await query.edit_message_text("Cancelled. Use /start when you're ready.")
        context.user_data.clear()
        return ConversationHandler.END

    user_id = update.effective_user.id
    cycle_id = await db.create_cycle(
        user_id,
        context.user_data["challenge_type"],
        context.user_data["challenge_text"],
        context.user_data["i_want_anchor"],
    )
    context.user_data.clear()
    program = load_program()
    w1 = program.get(1, {})
    await query.edit_message_text(
        f"Cycle started! You're on *Week 1: {w1.get('title', '')}*.\n\n"
        f"_{w1.get('theme', '')}_\n\n"
        "Use /week to see this week's exercises, /log to capture an urge, "
        "and /today to see your daily status.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def start_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


# ── /log — in-the-moment urge capture ────────────────────────────────────────

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return ConversationHandler.END
    context.user_data["log_cycle_id"] = cycle["id"]
    await update.message.reply_text(
        "What triggered this urge? Describe it briefly (or type *skip*).",
        parse_mode=ParseMode.MARKDOWN,
    )
    return LOG_TRIGGER


async def log_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["log_trigger"] = None if text.lower() == "skip" else text
    kb = _kb([
        [("Yes, I gave in", "log:gavein:1"), ("No, I resisted", "log:gavein:0")],
    ])
    await update.message.reply_text("Did you give in?", reply_markup=kb)
    return LOG_GAVE_IN


async def log_gave_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["log_gave_in"] = query.data.split(":")[-1] == "1"
    kb = _kb([
        [("1", "log:intensity:1"), ("2", "log:intensity:2"), ("3", "log:intensity:3"),
         ("4", "log:intensity:4"), ("5", "log:intensity:5")],
    ])
    await query.edit_message_text("Intensity of the urge (1 = mild, 5 = overwhelming)?",
                                  reply_markup=kb)
    return LOG_INTENSITY


async def log_intensity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["log_intensity"] = int(query.data.split(":")[-1])
    await query.edit_message_text(
        "Any notes? (what helped / what didn't / what you noticed) — or type *skip*.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return LOG_NOTES


async def log_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    notes = None if text.lower() == "skip" else text
    user_id = update.effective_user.id
    cycle_id = context.user_data["log_cycle_id"]
    urge_id = await db.log_urge(
        user_id=user_id,
        cycle_id=cycle_id,
        trigger_text=context.user_data.get("log_trigger"),
        gave_in=context.user_data.get("log_gave_in"),
        intensity=context.user_data.get("log_intensity"),
        notes=notes,
    )
    gave_in = context.user_data.get("log_gave_in")
    result = "Logged. " + ("You gave in — that's data, not failure." if gave_in
                           else "You resisted. That's real.")
    context.user_data.pop("log_cycle_id", None)
    context.user_data.pop("log_trigger", None)
    context.user_data.pop("log_gave_in", None)
    context.user_data.pop("log_intensity", None)
    await update.message.reply_text(result)
    return ConversationHandler.END


async def log_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("log_cycle_id", "log_trigger", "log_gave_in", "log_intensity"):
        context.user_data.pop(k, None)
    await update.message.reply_text("Urge log cancelled.")
    return ConversationHandler.END


# ── /today ────────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return

    entry = await db.get_daily_entry(cycle["id"])
    urges = await db.get_today_urges(cycle["id"])
    boosters = await db.get_today_boosters(cycle["id"])
    program = load_program()
    week = program.get(cycle["current_week"], {})

    today_str = date.today().strftime("%A, %B %d")
    lines = [
        f"*{today_str} — Week {cycle['current_week']}: {week.get('title', '')}*\n",
        f"Challenge: _{cycle['challenge_text']}_",
        f"I Want: _{cycle['i_want_anchor']}_\n",
    ]

    if entry:
        lines.append("*Today's check-in:*")
        if entry.get("energy_level"):
            lines.append(f"  Energy: {'⚡' * entry['energy_level']}")
        if entry.get("challenge_adherence"):
            lines.append(f"  Challenge: {_adherence_emoji(entry['challenge_adherence'])}")
        if entry.get("microscope_obs"):
            lines.append(f"  Observation: _{entry['microscope_obs']}_")
        if entry.get("reflection_text"):
            lines.append(f"  Reflection: _{entry['reflection_text']}_")
    else:
        lines.append("_No evening check-in yet today._")

    lines.append(f"\n*Urges today:* {len(urges)}")
    if urges:
        gave_in = sum(1 for u in urges if u.get("gave_in"))
        lines.append(f"  Gave in: {gave_in} / {len(urges)}")

    if boosters:
        lines.append("\n*Boosters:*")
        if boosters.get("sleep_hours"):
            lines.append(f"  Sleep: {boosters['sleep_hours']}h")
        lines.append(f"  Exercise: {'✅' if boosters.get('exercise_done') else '—'}")
        lines.append(f"  Meditation: {boosters.get('meditation_minutes', 0)} min")
        lines.append(f"  Breathing: {'✅' if boosters.get('breathing_done') else '—'}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── /week ─────────────────────────────────────────────────────────────────────

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return

    program = load_program()
    week = program.get(cycle["current_week"], {})
    microscope = week.get("microscope", {})
    experiment = week.get("experiment", {})

    lines = [
        f"*Week {cycle['current_week']}: {week.get('title', '')}*\n",
        f"_{week.get('theme', '')}_\n",
        f"*Under the Microscope*\n{microscope.get('text', '')}",
        f"\n*This Week's Experiment*\n{experiment.get('text', '')}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return

    started = date.fromisoformat(cycle["started_at"])
    days_elapsed = (date.today() - started).days
    day_in_week = (days_elapsed % 7) + 1

    await update.message.reply_text(
        f"*Active Cycle*\n"
        f"Challenge: _{cycle['challenge_text']}_\n"
        f"I Want: _{cycle['i_want_anchor']}_\n\n"
        f"Week {cycle['current_week']} of 10 — Day {day_in_week} of 7\n"
        f"Started: {started.strftime('%B %d, %Y')}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /reset ────────────────────────────────────────────────────────────────────

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return ConversationHandler.END

    kb = _kb([
        [("Yes, archive & restart", "reset:yes"), ("Cancel", "reset:no")],
    ])
    await update.message.reply_text(
        f"Archive current cycle (Week {cycle['current_week']}, "
        f"started {cycle['started_at']}) and start fresh?\n\n"
        "History is preserved — nothing is deleted.",
        reply_markup=kb,
    )
    return RESET_CONFIRM


async def reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if "no" in query.data:
        await query.edit_message_text("Reset cancelled.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if cycle:
        await db.archive_cycle(cycle["id"])
    await query.edit_message_text(
        "Cycle archived. Use /start to begin a new one."
    )
    return ConversationHandler.END


async def reset_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Reset cancelled.")
    return ConversationHandler.END


# ── /history ──────────────────────────────────────────────────────────────────

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        await _no_cycle(update)
        return

    week_num = cycle["current_week"]
    if context.args:
        try:
            week_num = int(context.args[0])
        except ValueError:
            pass

    synthesis = await db.get_synthesis(cycle["id"], week_num)
    if not synthesis:
        await update.message.reply_text(
            f"No synthesis for Week {week_num} yet.\n"
            "Syntheses are generated Sunday evening after the week completes."
        )
        return

    gen_at = datetime.fromisoformat(synthesis["generated_at"]).strftime("%B %d")
    await update.message.reply_text(
        f"*Week {week_num} Synthesis* (generated {gen_at})\n\n"
        f"{synthesis['claude_response_text']}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Evening check-in callbacks (scheduler-driven) ────────────────────────────
# State flows via user_data["checkin"] dict and user_data["awaiting"] for text.

async def _send_energy_prompt(context: ContextTypes.DEFAULT_TYPE,
                              chat_id: int) -> None:
    kb = _kb([[
        ("⚡", "checkin:energy:1"), ("⚡⚡", "checkin:energy:2"),
        ("⚡⚡⚡", "checkin:energy:3"), ("⚡⚡⚡⚡", "checkin:energy:4"),
        ("⚡⚡⚡⚡⚡", "checkin:energy:5"),
    ]])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Evening check-in 🌙\n\nEnergy level today (1 = drained, 5 = full)?",
        reply_markup=kb,
    )


async def checkin_energy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    level = int(query.data.split(":")[-1])
    context.user_data.setdefault("checkin", {})["energy_level"] = level
    kb = _kb([[
        ("✅ Yes", "checkin:adherence:yes"),
        ("〰️ Partial", "checkin:adherence:partial"),
        ("❌ No", "checkin:adherence:no"),
    ]])
    await query.edit_message_text(
        f"Energy: {'⚡' * level}\n\nDid you honor your challenge today?",
        reply_markup=kb,
    )


async def checkin_adherence(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    val = query.data.split(":")[-1]
    context.user_data.setdefault("checkin", {})["challenge_adherence"] = val
    kb = _kb([[
        ("0", "checkin:urges:0"), ("1", "checkin:urges:1"),
        ("2", "checkin:urges:2"), ("3", "checkin:urges:3"),
        ("4", "checkin:urges:4"), ("5+", "checkin:urges:5"),
    ]])
    await query.edit_message_text("How many urges did you notice today?", reply_markup=kb)


async def checkin_urges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    count = int(query.data.split(":")[-1])
    context.user_data.setdefault("checkin", {})["urge_count"] = count
    # Ask about boosters based on current week's structured questions
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return
    program = load_program()
    week = program.get(cycle["current_week"], {})
    sq_ids = [q["id"] for q in week.get("structured_questions", [])]
    # Check if this week tracks sleep
    if "sleep_hours" in sq_ids:
        await query.edit_message_text(
            "How many hours did you sleep last night?\n(Reply with a number, e.g. 7.5)"
        )
        context.user_data["awaiting"] = "sleep"
    elif "breathing_done" in sq_ids:
        kb = _kb([[("✅ Yes", "checkin:breathing:1"), ("❌ No", "checkin:breathing:0")]])
        await query.edit_message_text("Did you do the slow breathing exercise today?",
                                      reply_markup=kb)
    elif "exercise_done" in sq_ids:
        kb = _kb([[("✅ Yes", "checkin:exercise:1"), ("❌ No", "checkin:exercise:0")]])
        await query.edit_message_text("Did you exercise today?", reply_markup=kb)
    else:
        await _ask_observation(query, context, user_id)


async def checkin_breathing(update: Update,
                             context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    val = int(query.data.split(":")[-1])
    context.user_data.setdefault("checkin", {})["breathing_done"] = val
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return
    program = load_program()
    week = program.get(cycle["current_week"], {})
    sq_ids = [q["id"] for q in week.get("structured_questions", [])]
    if "exercise_done" in sq_ids:
        kb = _kb([[("✅ Yes", "checkin:exercise:1"), ("❌ No", "checkin:exercise:0")]])
        await query.edit_message_text("Did you exercise today?", reply_markup=kb)
    else:
        await _ask_observation(query, context, user_id)


async def checkin_exercise(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    val = int(query.data.split(":")[-1])
    context.user_data.setdefault("checkin", {})["exercise_done"] = val
    await _ask_observation(update.callback_query, context, user_id)


async def _ask_observation(query, context: ContextTypes.DEFAULT_TYPE,
                            user_id: int) -> None:
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return
    program = load_program()
    week = program.get(cycle["current_week"], {})
    microscope_prompt = week.get("microscope", {}).get("daily_prompt", "")
    await query.edit_message_text(
        f"Microscope observation:\n_{microscope_prompt}_\n\n"
        "(Free text — or reply *skip*)",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["awaiting"] = "obs"


async def checkin_free_text(update: Update,
                             context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles free-text steps: sleep hours, microscope obs, evening reflection."""
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    skipped = text.lower() == "skip"
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return

    if awaiting == "sleep":
        try:
            hours = float(text)
        except ValueError:
            await update.message.reply_text("Please enter a number (e.g. 7 or 7.5).")
            return
        context.user_data.setdefault("checkin", {})["sleep_hours"] = hours
        context.user_data.pop("awaiting")
        program = load_program()
        week = program.get(cycle["current_week"], {})
        sq_ids = [q["id"] for q in week.get("structured_questions", [])]
        if "breathing_done" in sq_ids:
            kb = _kb([[("✅ Yes", "checkin:breathing:1"),
                       ("❌ No", "checkin:breathing:0")]])
            await update.message.reply_text("Did you do the slow breathing exercise today?",
                                            reply_markup=kb)
        elif "exercise_done" in sq_ids:
            kb = _kb([[("✅ Yes", "checkin:exercise:1"),
                       ("❌ No", "checkin:exercise:0")]])
            await update.message.reply_text("Did you exercise today?", reply_markup=kb)
        else:
            await _ask_observation_msg(update, context, user_id)
        return

    if awaiting == "obs":
        context.user_data.setdefault("checkin", {})["microscope_obs"] = (
            None if skipped else text
        )
        context.user_data["awaiting"] = "reflect"
        program = load_program()
        week = program.get(cycle["current_week"], {})
        exp_prompt = week.get("experiment", {}).get("daily_prompt", "")
        await update.message.reply_text(
            f"Evening reflection:\n_{exp_prompt}_\n\n(Free text — or reply *skip*)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if awaiting == "reflect":
        context.user_data.setdefault("checkin", {})["reflection_text"] = (
            None if skipped else text
        )
        context.user_data.pop("awaiting")
        await _save_checkin(update, context, user_id, cycle)


async def _ask_observation_msg(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                user_id: int) -> None:
    cycle = await db.get_active_cycle(user_id)
    if not cycle:
        return
    program = load_program()
    week = program.get(cycle["current_week"], {})
    microscope_prompt = week.get("microscope", {}).get("daily_prompt", "")
    await update.message.reply_text(
        f"Microscope observation:\n_{microscope_prompt}_\n\n(Free text — or reply *skip*)",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["awaiting"] = "obs"


async def _save_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         user_id: int, cycle: dict) -> None:
    data = context.user_data.pop("checkin", {})
    week_num = cycle["current_week"]

    entry_fields = {k: v for k, v in data.items()
                    if k in ("energy_level", "challenge_adherence", "urge_count",
                             "microscope_obs", "reflection_text")}
    booster_fields = {k: v for k, v in data.items()
                      if k in ("sleep_hours", "exercise_done",
                               "meditation_minutes", "breathing_done")}

    if entry_fields:
        await db.upsert_daily_entry(user_id, cycle["id"], week_num, **entry_fields)
    if booster_fields:
        await db.upsert_boosters(user_id, cycle["id"], **booster_fields)

    await update.message.reply_text(
        "Check-in saved ✅\nUse /today to see your full day summary."
    )


# ── handler registration helper ───────────────────────────────────────────────

def register_handlers(app) -> None:
    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSE_TYPE:       [CallbackQueryHandler(start_choose_type,
                                                     pattern=r"^start:type:")],
            ENTER_CHALLENGE:   [MessageHandler(filters.TEXT & ~filters.COMMAND,
                                               start_enter_challenge)],
            ENTER_WANT:        [MessageHandler(filters.TEXT & ~filters.COMMAND,
                                               start_enter_want)],
            CONFIRM:           [CallbackQueryHandler(start_confirm,
                                                     pattern=r"^start:confirm:")],
        },
        fallbacks=[CommandHandler("cancel", start_cancel)],
    )

    log_conv = ConversationHandler(
        entry_points=[CommandHandler("log", cmd_log)],
        states={
            LOG_TRIGGER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, log_trigger)],
            LOG_GAVE_IN:   [CallbackQueryHandler(log_gave_in, pattern=r"^log:gavein:")],
            LOG_INTENSITY: [CallbackQueryHandler(log_intensity,
                                                  pattern=r"^log:intensity:")],
            LOG_NOTES:     [MessageHandler(filters.TEXT & ~filters.COMMAND, log_notes)],
        },
        fallbacks=[CommandHandler("cancel", log_cancel)],
    )

    reset_conv = ConversationHandler(
        entry_points=[CommandHandler("reset", cmd_reset)],
        states={
            RESET_CONFIRM: [CallbackQueryHandler(reset_confirm, pattern=r"^reset:")],
        },
        fallbacks=[CommandHandler("cancel", reset_cancel)],
    )

    app.add_handler(start_conv)
    app.add_handler(log_conv)
    app.add_handler(reset_conv)

    app.add_handler(CommandHandler("today",   cmd_today))
    app.add_handler(CommandHandler("week",    cmd_week))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("history", cmd_history))

    # Evening check-in callbacks
    app.add_handler(CallbackQueryHandler(checkin_energy,    pattern=r"^checkin:energy:"))
    app.add_handler(CallbackQueryHandler(checkin_adherence, pattern=r"^checkin:adherence:"))
    app.add_handler(CallbackQueryHandler(checkin_urges,     pattern=r"^checkin:urges:"))
    app.add_handler(CallbackQueryHandler(checkin_breathing, pattern=r"^checkin:breathing:"))
    app.add_handler(CallbackQueryHandler(checkin_exercise,  pattern=r"^checkin:exercise:"))

    # Free-text replies for check-in and reflection (lower priority — runs after convs)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            checkin_free_text,
        ),
        group=1,
    )
