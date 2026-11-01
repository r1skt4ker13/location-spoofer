# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local job-search assistant. It scrapes intern-list.com, filters postings by category, location, degree level, GPA/transcript requirements and duplicates, then scores the survivors against `resume/resume.pdf` with Claude. There is no per-job tailoring: the same resume is used for every application, served at `/api/resume`. It does **not** submit applications. The user applies by hand from the dashboard and marks each job applied or skipped.

## Commands

All commands run from `backend/` with the venv active (`source .venv/bin/activate`).

```bash
pip install -r requirements.txt && playwright install chromium   # setup
uvicorn app.main:app --port 8787                                  # run server + scheduler + dashboard at http://localhost:8787
python -c "from app.pipeline import run_pipeline; print(run_pipeline())"   # one pipeline run without the server
```

There is no test suite, linter or build step. The filters in `app/filters.py` are pure functions, so you can check them directly with `python -c`.

## Architecture

- **`app/main.py`:** FastAPI app.
  - Its lifespan runs `init_db()` (schema plus additive migrations) and re-filters existing rows.
  - It also starts an APScheduler `BackgroundScheduler` that calls `pipeline.run_pipeline` every `SCRAPE_INTERVAL_HOURS` (default 4), starting with a run at startup.
  - The pipeline runs on a background thread because Playwright's sync API can't run on the asyncio loop.
  - It serves `frontend/index.html`, a single-file vanilla-JS dashboard.
- **`app/pipeline.py`:** `run_pipeline()` holds a non-blocking lock so the scheduler and the "Run now" button can't overlap. Each stage reads and writes job rows by `status`:
  - `new` → `filtered` (fails a filter, so no Claude call)
  - or `new` → `ready` when `fit_score >= FIT_THRESHOLD`, otherwise `scored`
  - The user then moves a ready job to `applied` or `skipped`.
  - A new job with the same normalized company + title as an existing row is inserted as `filtered` ("duplicate of …"). Together with the id-based known-id check, this prevents re-applying.
  - Claude can also move a job to `filtered` while scoring (see the flags below).
- **Sources** (`SOURCES` in `app/config.py`, dispatched by `scraper.scrape_all`):
  - **`ml_ai`:** intern-list's ML/AI list is an embedded Airtable view that only fills in through JavaScript and only draws the rows on screen. `app/scraper.py` renders it in headless Chromium, scrolls, and collects rows by `data-rowid`. It maps columns by `data-columnindex` through `COLUMN_MAP`, which breaks if the Airtable view's columns are reordered. These job ids are the raw Airtable rowids, with no prefix.
  - **`pm`, `data_analysis`:** these lists are server-rendered Webflow pages, scraped by `app/webflow_scraper.py` with stdlib `urllib`. Cards (`div.w-dyn-item`) give the title, company and date. Location, qualifications and the apply link only appear on each job's detail page, which is fetched only for unseen ids. These job ids are `<source>:<numeric id>`.
  - Every source is sorted newest first. A scrape stops after 25 consecutive already-known ids.
- **`app/filters.py`:** the deterministic filters. A location passes if it is in the Bay Area, the NYC metro (all of NJ counts) or remote. A title fails if it contains research, PhD, Master's or MBA keywords. A title or qualifications text fails if it mentions GPA or transcript.
- **`app/claude_client.py`:**
  - Scoring calls `claude-haiku-5-5`.
  - It forces a tool call (`tool_choice`) to get structured output.
  - The scoring tool also returns `requires_graduate_degree`, `is_research_role` and `asks_for_gpa_or_transcript`. Any of these being true moves the job to `filtered`.
- **`app/db.py`:** SQLite at `data/jobs.db`. Add schema changes as idempotent `ALTER TABLE ... ADD COLUMN` entries in `MIGRATIONS` (applied by `init_db`), because existing databases must keep working.

Config comes from `backend/.env` (see `.env.example`): `ANTHROPIC_API_KEY`, `SCRAPE_INTERVAL_HOURS`, `FIT_THRESHOLD`, `MAX_SCRAPE_ROWS`, `PORT`, `AIRTABLE_EMBED_URL`.
