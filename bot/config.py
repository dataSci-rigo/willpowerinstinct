import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

DOCS_DIR = Path(__file__).parent.parent.parent  # ~/Documents
load_dotenv(DOCS_DIR / ".env")
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

_raw_token = os.getenv("WP_TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not _raw_token:
    raise RuntimeError("Set WP_TELEGRAM_TOKEN (or TELEGRAM_TOKEN) in .env")
TELEGRAM_TOKEN: str = _raw_token

ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

_raw_owner = os.getenv("OWNER_CHAT_ID", "")
OWNER_CHAT_ID: int = int(_raw_owner.strip("'\""))

TIMEZONE = "America/Los_Angeles"
MORNING_HOUR = 8
EVENING_HOUR = 21

DB_PATH = Path(__file__).parent.parent / "data" / "tracker.db"
PROGRAM_PATH = Path(__file__).parent.parent / "program" / "program.yaml"

SYNTHESIS_MODEL = "claude-sonnet-4-6"


def load_program() -> dict:
    with open(PROGRAM_PATH) as f:
        data = yaml.safe_load(f)
    return {w["week_number"]: w for w in data["weeks"]}
