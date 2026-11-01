import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / "backend" / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SCRAPE_INTERVAL_HOURS = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "4"))
FIT_THRESHOLD = int(os.environ.get("FIT_THRESHOLD", "70"))
MAX_SCRAPE_ROWS = int(os.environ.get("MAX_SCRAPE_ROWS", "250"))
PORT = int(os.environ.get("PORT", "8787"))
AIRTABLE_EMBED_URL = os.environ.get(
    "AIRTABLE_EMBED_URL",
    "https://airtable.com/embed/appjSXAWiVF4d1HoZ/shrf04yGbrK3IebAl?viewControls=on",
)

# Categories to watch. "airtable" sources are JS-rendered embeds (app/scraper.py);
# "webflow" sources are server-rendered intern-list.com lists (app/webflow_scraper.py).
SOURCES = [
    {"key": "ml_ai", "label": "Machine Learning & AI", "kind": "airtable", "url": AIRTABLE_EMBED_URL},
    {"key": "pm", "label": "Product Management", "kind": "webflow", "url": "https://intern-list.com/pm-intern-list"},
    {"key": "data_analysis", "label": "Data Analysis", "kind": "webflow", "url": "https://intern-list.com/da-intern-list"},
]

DB_PATH = BASE_DIR / "data" / "jobs.db"
RESUME_PATH = BASE_DIR / "resume" / "resume.pdf"

SCORING_MODEL = "claude-haiku-5-5"
DRAFTING_MODEL = "claude-sonnet-5-5"
