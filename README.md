# Job Agent

Watches intern-list.com's Machine Learning & AI, Product Management, and Data Analysis internship lists. It drops postings outside the Bay Area, the NYC metro (including all of NJ) or remote, and postings that need an MS/PhD/MBA or are research roles. Then it scores each remaining posting against your resume with Claude, and drafts tailored resume bullets and a cover letter for the strong matches. You review them in a local dashboard and submit applications yourself.

## Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then set ANTHROPIC_API_KEY
```

Your resume goes in `resume/resume.pdf`.

## Run

```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --port 8787
```

Then open http://localhost:8787. The pipeline runs once at startup, then every `SCRAPE_INTERVAL_HOURS` hours (default 4) while the server is up. Click **Run now** to start a run right away.

## How it works

1. **Scrape** (`app/scraper.py` for ML/AI, `app/webflow_scraper.py` for PM and Data Analysis): the PM and DA lists are plain HTML pages, and each job's detail page is fetched for its location and qualifications. For ML/AI, intern-list.com loads its listings from an embedded Airtable view that only fills in through JavaScript, so a headless Chromium renders it. The grid only draws the rows on screen, so the scraper scrolls and collects rows as they appear. The list is sorted newest first, so a run stops once it reaches 25 jobs in a row that it has already seen.
2. **Filter** (`app/filters.py`): a job whose location isn't Bay Area, NYC/NJ or remote, or whose title mentions research, PhD, Master's or MBA, is marked `filtered` without calling Claude.
3. **Score** (`claude-haiku-5-5`): each remaining job gets a 0–100 fit score and a short explanation. The scorer also flags postings that require a graduate degree or are research roles, and those are moved to `filtered`.
4. **Draft** (`claude-sonnet-5-5`): jobs scoring at or above `FIT_THRESHOLD` get tailored resume bullets and a cover letter. Drafts use only what is in your resume.
5. **Review**: the dashboard's "Ready to apply" tab lists the drafted jobs. Open the application link, paste in your materials, then click "Mark applied" or "Skip".

Data is stored in a local SQLite file at `data/jobs.db`.

## Config (`backend/.env`)

| Var | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required |
| `SCRAPE_INTERVAL_HOURS` | 4 | |
| `FIT_THRESHOLD` | 70 | minimum score that gets a draft |
| `MAX_SCRAPE_ROWS` | 250 | most rows read per run; the first run reads the most |
| `AIRTABLE_EMBED_URL` | ML/AI view | the ML/AI source. Other categories are set in `SOURCES` in `app/config.py` |
