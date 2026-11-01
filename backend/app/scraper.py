import logging
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from app.config import AIRTABLE_EMBED_URL, MAX_SCRAPE_ROWS, SOURCES
from app import webflow_scraper

logger = logging.getLogger("scraper")

COLUMN_MAP = {
    "1": "date_posted",
    "2": "apply_url",  # href only, text is just "Apply"
    "3": "work_model",
    "4": "location",
    "5": "company",
    "6": "salary",
    "7": "grad_year",
    "8": "grad_time",
    "9": "company_size",
    "10": "company_industry",
    "11": "qualifications",
}


def _extract_visible_rows(page) -> dict:
    """Read currently-rendered rows from the virtualized grid, keyed by rowid."""
    rows = {}
    left_rows = page.locator(".dataRow.leftPane")
    right_rows = page.locator(".dataRow.rightPane")
    count = min(left_rows.count(), right_rows.count())
    for i in range(count):
        lr = left_rows.nth(i)
        rowid = lr.get_attribute("data-rowid")
        if not rowid or rowid in rows:
            continue
        title = lr.inner_text().strip()
        # strip leading row number line, e.g. "12\nTitle text"
        lines = title.split("\n", 1)
        if len(lines) == 2 and lines[0].strip().isdigit():
            title = lines[1].strip()

        rr = right_rows.nth(i)
        cells = rr.locator("[data-columnindex]")
        job = {"id": rowid, "title": title}
        for c in range(cells.count()):
            cell = cells.nth(c)
            idx = cell.get_attribute("data-columnindex")
            field = COLUMN_MAP.get(idx)
            if not field:
                continue
            if field == "apply_url":
                link = cell.locator("a")
                job[field] = link.first.get_attribute("href") if link.count() > 0 else None
            else:
                job[field] = cell.inner_text().strip()
        rows[rowid] = job
    return rows


def scrape_jobs(max_rows: int = None, known_ids: set[str] = None, url: str = None) -> list[dict]:
    """
    Scrape the (JS-rendered, virtualized) Airtable embed behind intern-list.com's
    ML/AI category. Scrolls the grid, collecting newly-rendered rows, and stops
    early once it has walked past a run of rows already seen in a previous scrape
    (the view is sorted newest-first, so that means we've caught up).
    """
    max_rows = max_rows or MAX_SCRAPE_ROWS
    known_ids = known_ids or set()
    collected: dict[str, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.goto(url or AIRTABLE_EMBED_URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(4000)

        grid_box = page.locator(".dataLeftPane").first.bounding_box()
        cx = (grid_box["x"] + grid_box["width"] / 2) if grid_box else 400
        cy = (grid_box["y"] + grid_box["height"] / 2) if grid_box else 400

        stale_rounds = 0
        consecutive_known = 0
        for _ in range(200):
            new_rows = _extract_visible_rows(page)
            added_new = 0
            for rowid, job in new_rows.items():
                if rowid not in collected:
                    collected[rowid] = job
                    added_new += 1
                    if rowid in known_ids:
                        consecutive_known += 1
                    else:
                        consecutive_known = 0

            if len(collected) >= max_rows:
                break
            if consecutive_known >= 25:
                break
            if added_new == 0:
                stale_rounds += 1
                if stale_rounds >= 4:
                    break
            else:
                stale_rounds = 0

            page.mouse.move(cx, cy)
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(350)

        browser.close()

    now = datetime.now(timezone.utc).isoformat()
    jobs = list(collected.values())
    for job in jobs:
        job["first_seen_at"] = now
    return jobs[:max_rows]


def scrape_all(known_ids: set[str]) -> list[dict]:
    """Scrape every configured category source, tagging each job with its category.
    One failing source doesn't stop the others."""
    all_jobs: list[dict] = []
    for source in SOURCES:
        try:
            if source["kind"] == "airtable":
                # Airtable rowids stay unprefixed so rows from before multi-source keep their ids.
                jobs = scrape_jobs(known_ids=known_ids, url=source["url"])
            else:
                jobs = webflow_scraper.scrape(source, known_ids, max_rows=MAX_SCRAPE_ROWS)
        except Exception:
            logger.exception("scrape failed for source %s", source["key"])
            continue
        for job in jobs:
            job["category"] = source["label"]
        logger.info("source %s: %d jobs", source["key"], len(jobs))
        all_jobs.extend(jobs)
    return all_jobs
