# Plan: Redesigned /log Flow with Week Picker, Type-Aware Outcomes, and Week-Specific Questions

## Context
The current `/log` command goes straight to challenge picker → trigger → gave_in (binary) → intensity → notes. The user wants a richer log that disambiguates which week's *skill* was in play.

Key insight: each week introduces a skill that stacks on top of prior weeks. You keep using all prior skills even in later weeks. So the **week picker is a skill lens**, not a time filter. In Week 5 you can still log a "Week 2 skill" because you paused and breathed. Week 1's skill is: pause to remember your "I Want" anchor. Week 2's is: pause and breathe. Week 3's is: recovery action.

The week picker shows **Week 0** plus weeks 1 through `current_week`. Phase 2 (not built now) would be a unified log that tracks all skills simultaneously.

## New Conversation Flow

```
/log
 → LOG_WEEK     pick which skill week (buttons: Week 0, Week 1 … Week N up to current_week)
 → LOG_PICK     pick challenge (from challenges table — all challenges are cycle-wide)
 → LOG_OUTCOME  pick outcome — varies by challenge_type:
                  i_wont / i_want: "Resisted ✓" | "Gave in ✗"
                  i_will:          "Started ✓" | "Completed ✅" | "Gave in ✗"
 → [skill week 0: no extra question — basic urge/temptation log, skip to LOG_TRIGGER]
 → LOG_WEEK1    [skill week 1] "Did you pause to remember your 'I Want'?" Yes / No
 → LOG_WEEK2    [skill week 2] "Did you pause and breathe?" Yes / No
 → LOG_WEEK3    [skill week 3] "Which recovery action?" Sleep / Exercise / Strategic rest / None
                [skill weeks 4-10 STUB: skip to LOG_TRIGGER]
 → LOG_TRIGGER  "What triggered this?" (free text, skip allowed)
 → LOG_INTENSITY 1-5 intensity buttons
 → LOG_NOTES    free text notes (skip allowed)
 → save + END
```

Week 0 label: **"Just an urge/temptation"** — always shown, no skill question, bare-bones log.

## DB Changes — bot/db.py

Add 5 columns to `urges` table via `ALTER TABLE` migrations in `init_db()` (try/except pattern already exists at line 84):

```sql
outcome         TEXT     -- "resisted" | "gave_in" | "started" | "completed"
skill_week      INTEGER  -- which week's skill this log applies (0, 1, 2, 3…)
i_want_recalled INTEGER  -- 0|1, week 1: did you pause to remember I Want?
breathing_done  INTEGER  -- 0|1, week 2: did you pause and breathe?
recovery_action TEXT     -- "sleep"|"exercise"|"rest"|null, week 3
```

`gave_in` stays but is derived from outcome: `1 if outcome == "gave_in" else 0`

Update `log_urge()` signature to accept 5 new optional kwargs:
```python
async def log_urge(user_id, cycle_id, trigger_text, gave_in, intensity,
                   notes=None, challenge_id=None,
                   outcome=None, skill_week=None,
                   i_want_recalled=None, breathing_done=None,
                   recovery_action=None) -> int
```

Migrations to append to the list in `init_db()`:
```python
"ALTER TABLE urges ADD COLUMN outcome TEXT",
"ALTER TABLE urges ADD COLUMN skill_week INTEGER",
"ALTER TABLE urges ADD COLUMN i_want_recalled INTEGER",
"ALTER TABLE urges ADD COLUMN breathing_done INTEGER",
"ALTER TABLE urges ADD COLUMN recovery_action TEXT",
```

## State Constants — bot/handlers.py

Replace current log states (currently `range(4, 9)`) with:
```python
LOG_WEEK, LOG_PICK, LOG_OUTCOME, LOG_WEEK1, LOG_WEEK2, LOG_WEEK3, \
    LOG_TRIGGER, LOG_INTENSITY, LOG_NOTES = range(4, 13)
RESET_CONFIRM = 13
```

## Handler Changes — bot/handlers.py

### `cmd_log` (entry point)
- Get active cycle → store `log_cycle_id`
- Build week buttons: always starts with Week 0, then weeks 1–current_week from `load_program()`
  - `[("Just an urge / temptation", "log:week:0")]`
  - `[("Week N: {title}", "log:week:N")]` for N in 1..current_week
- Return `LOG_WEEK`  (no skip — Week 0 is always an option)

### New: `log_pick_week`  [callback `^log:week:`]
- Store `user_data["log_skill_week"] = N`
- For N >= 1: load week data from `load_program()` and build a context message:
  ```
  *Week N: {title}*

  🔬 Under the Microscope:
  {microscope.text}

  💡 This Week's Experiment:
  {experiment.text}

  Which challenge are you logging for?
  ```
- For N == 0: message is simply `"Which challenge are you logging for?"`
- Load challenges for cycle → show as inline buttons below the message
- Return `LOG_PICK`

### `log_pick_challenge`  [callback `^log:pick:`]
- Store `challenge_id` AND `challenge_type` in user_data
- Show outcome buttons based on type:
  - `i_wont` / `i_want`: `[("✓ Resisted", "log:outcome:resisted"), ("✗ Gave in", "log:outcome:gave_in")]`
  - `i_will`: `[("▶ Started", "log:outcome:started"), ("✅ Completed", "log:outcome:completed"), ("✗ Gave in", "log:outcome:gave_in")]`
- Return `LOG_OUTCOME`

### New: `log_outcome`  [callback `^log:outcome:`]
- Store `user_data["log_outcome"] = outcome_str`
- Derive `user_data["log_gave_in"] = (outcome_str == "gave_in")`
- Branch on `user_data["log_skill_week"]`:
  - 0 → ask trigger prompt → return `LOG_TRIGGER`
  - 1 → ask "Did you pause to remember your 'I Want'?" [Yes/No] → return `LOG_WEEK1`
  - 2 → ask "Did you pause and breathe?" [Yes/No] → return `LOG_WEEK2`
  - 3 → ask "Which recovery action did you take?" [Sleep / Exercise / Strategic rest / None] → return `LOG_WEEK3`
  - else (4-10, STUB) → ask trigger prompt → return `LOG_TRIGGER`

### New: `log_week1`  [callback `^log:w1want:`]
- Store `user_data["log_i_want_recalled"] = 1 or 0`
- Ask trigger prompt → return `LOG_TRIGGER`

### New: `log_week2`  [callback `^log:w2breath:`]
- Store `user_data["log_breathing"] = 1 or 0`
- Ask trigger prompt → return `LOG_TRIGGER`

### New: `log_week3`  [callback `^log:w3recovery:`]
- Store `user_data["log_recovery"] = "sleep"|"exercise"|"rest"|None`
- Ask trigger prompt → return `LOG_TRIGGER`

### `log_trigger`, `log_intensity`
- No change to logic; updated state int values only

### `log_notes`
- Call `db.log_urge()` with added fields:
  - `outcome=user_data.get("log_outcome")`
  - `skill_week=user_data.get("log_skill_week")`
  - `i_want_recalled=user_data.get("log_i_want_recalled")`
  - `breathing_done=user_data.get("log_breathing")`
  - `recovery_action=user_data.get("log_recovery")`
  - `gave_in` derived from outcome
- Confirmation message: distinguish "Gave in" vs "Resisted"/"Completed"/"Started"

### `log_cancel`
- Pop all new keys: `log_skill_week`, `log_outcome`, `log_i_want_recalled`, `log_breathing`, `log_recovery`

### ConversationHandler registration
- Add `LOG_WEEK:    [CallbackQueryHandler(log_pick_week, pattern=r"^log:week:")]`
- Add `LOG_OUTCOME: [CallbackQueryHandler(log_outcome,   pattern=r"^log:outcome:")]`
- Add `LOG_WEEK1:   [CallbackQueryHandler(log_week1,     pattern=r"^log:w1want:")]`
- Add `LOG_WEEK2:   [CallbackQueryHandler(log_week2,     pattern=r"^log:w2breath:")]`
- Add `LOG_WEEK3:   [CallbackQueryHandler(log_week3,     pattern=r"^log:w3recovery:")]`
- Keep `LOG_PICK` and `LOG_TRIGGER`/`LOG_INTENSITY`/`LOG_NOTES` as-is

## Edge Cases
- **No challenges in DB**: skip LOG_PICK, use `challenge_type="i_wont"` default; show Resisted / Gave in
- **`i_want` challenge type**: treat same as `i_wont` (Resisted / Gave in)
- **Week 0 selected**: after LOG_OUTCOME, skip directly to LOG_TRIGGER — no skill question
- **Weeks 4-10 (STUB) skill selected**: after LOG_OUTCOME, skip directly to LOG_TRIGGER — no skill question
- **`skill_week=0` stored in DB** as `0` (not null) so it's distinguishable from "no skill logged"

## Files Changed
- `bot/db.py` — DDL, migrations, `log_urge()` signature
- `bot/handlers.py` — state constants, all LOG_* handlers, ConversationHandler registration

## Verification
1. Push to GitHub, pull on VM, restart `app-wp-instinct`
2. Send `/log` — should show "Just an urge/temptation" + Week 1, Week 2, etc.
3. Tap Week 0 → challenge buttons → outcome → trigger → intensity → notes → "Logged."
4. Tap Week 2 → see microscope + experiment context → challenge buttons → outcome → "Did you pause and breathe?" → trigger → ...
5. Tap an i_will challenge → see Started / Completed / Gave in
6. Tap an i_wont challenge → see Resisted / Gave in
7. Check DB: `SELECT outcome, skill_week, i_want_recalled, breathing_done, recovery_action FROM urges ORDER BY id DESC LIMIT 1;`
