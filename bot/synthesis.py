"""Weekly synthesis: builds context from the DB, calls Claude, stores the result."""

import json
import logging
from datetime import date

import anthropic

import db
from config import ANTHROPIC_API_KEY, SYNTHESIS_MODEL, load_program

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _summarise_entry(e: dict) -> str:
    parts = []
    if e.get("energy_level"):
        parts.append(f"energy={e['energy_level']}/5")
    if e.get("challenge_adherence"):
        parts.append(f"adherence={e['challenge_adherence']}")
    if e.get("urge_count") is not None:
        parts.append(f"urges={e['urge_count']}")
    if e.get("microscope_obs"):
        parts.append(f"observation: {e['microscope_obs']}")
    if e.get("reflection_text"):
        parts.append(f"reflection: {e['reflection_text']}")
    return f"  {e['date']}: " + " | ".join(parts) if parts else f"  {e['date']}: (no data)"


def _summarise_urge(u: dict) -> str:
    gave = "gave in" if u.get("gave_in") else "resisted"
    intensity = f"intensity={u.get('intensity', '?')}/5"
    trigger = f"trigger: {u['trigger_text']}" if u.get("trigger_text") else ""
    notes = f"notes: {u['notes']}" if u.get("notes") else ""
    parts = [gave, intensity] + [p for p in (trigger, notes) if p]
    ts = u["timestamp"][:10]
    return f"  {ts}: " + " | ".join(parts)


def _summarise_boosters(bs: list[dict]) -> str:
    if not bs:
        return "  No booster data."
    lines = []
    for b in bs:
        parts = []
        if b.get("sleep_hours"):
            parts.append(f"sleep={b['sleep_hours']}h")
        if b.get("exercise_done"):
            parts.append("exercise=yes")
        if b.get("meditation_minutes"):
            parts.append(f"meditation={b['meditation_minutes']}min")
        if b.get("breathing_done"):
            parts.append("breathing=yes")
        if parts:
            lines.append(f"  {b['date']}: " + " | ".join(parts))
    return "\n".join(lines) if lines else "  No booster data."


async def run_weekly_synthesis(user_id: int, cycle: dict) -> str:
    """Generate and store a weekly synthesis. Returns the response text."""
    week_num = cycle["current_week"]
    program = load_program()
    week = program.get(week_num, {})

    entries = await db.get_week_entries(cycle["id"], week_num)
    urges = await db.get_week_urges(cycle["id"], week_num, cycle["started_at"])
    boosters = await db.get_week_boosters(cycle["id"], week_num, cycle["started_at"])

    entry_text = "\n".join(_summarise_entry(e) for e in entries) or "  No entries."
    urge_text = "\n".join(_summarise_urge(u) for u in urges) or "  No urges logged."
    booster_text = _summarise_boosters(boosters)

    input_summary = (
        f"Week {week_num}: {week.get('title', '')}\n"
        f"Theme: {week.get('theme', '')}\n\n"
        f"Daily entries:\n{entry_text}\n\n"
        f"Urges:\n{urge_text}\n\n"
        f"Boosters:\n{booster_text}"
    )

    system_prompt = (
        "You are a thoughtful, non-judgmental coach helping someone work through "
        "Kelly McGonigal's Willpower Instinct program. Your job is to synthesise "
        "one week of self-tracking data and reflect it back to the user through the "
        "lens of that week's specific chapter theme. Be specific to the data — cite "
        "actual observations. Be warm but analytical. Avoid generic advice. "
        "Keep the response under 400 words."
    )

    user_message = (
        f"Here is my Week {week_num} data from The Willpower Instinct program.\n\n"
        f"This week's theme: {week.get('theme', '')}\n\n"
        f"Microscope exercise: {week.get('microscope', {}).get('text', '')}\n\n"
        f"Experiment: {week.get('experiment', {}).get('text', '')}\n\n"
        f"My challenge: {cycle['challenge_text']}\n"
        f"My I Want anchor: {cycle['i_want_anchor']}\n\n"
        f"Daily log:\n{entry_text}\n\n"
        f"Urges this week:\n{urge_text}\n\n"
        f"Recovery boosters:\n{booster_text}\n\n"
        "Please synthesise this week. What patterns do you see? What does the data "
        "say through the lens of this week's theme? What's worth paying attention to "
        "going into next week?"
    )

    logger.info("Calling Claude for Week %d synthesis (cycle %d)", week_num, cycle["id"])
    response = _client.messages.create(
        model=SYNTHESIS_MODEL,
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    response_text = response.content[0].text

    await db.save_synthesis(user_id, cycle["id"], week_num, response_text, input_summary)
    return response_text
