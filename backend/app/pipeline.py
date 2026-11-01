import logging
import re
import threading
from datetime import datetime, timezone

from app.config import FIT_THRESHOLD
from app.db import get_conn
from app.resume import get_resume_text
from app.scraper import scrape_all
from app.filters import filter_reason
from app.claude_client import score_job

logger = logging.getLogger("pipeline")


def _known_ids() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM jobs").fetchall()
    return {r["id"] for r in rows}


def _dedupe_key(company: str | None, title: str | None) -> str:
    """Normalized company+title, so the same posting listed under two categories
    (or re-posted under a new id) is recognized as one job."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{norm(company)}|{norm(title)}"


def _insert_new_jobs(jobs: list[dict]) -> list[dict]:
    known = _known_ids()
    fresh = [j for j in jobs if j["id"] not in known]
    with get_conn() as conn:
        seen = {
            _dedupe_key(r["company"], r["title"]): r["id"]
            for r in conn.execute("SELECT id, company, title FROM jobs")
        }
        for job in fresh:
            key = _dedupe_key(job.get("company"), job.get("title"))
            dup_of = seen.get(key)
            seen.setdefault(key, job["id"])
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (id, title, company, location, work_model, salary, grad_time,
                     company_size, company_industry, qualifications, apply_url,
                     date_posted, first_seen_at, category, status, filter_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"], job.get("title"), job.get("company"), job.get("location"),
                    job.get("work_model"), job.get("salary"),
                    job.get("grad_time") or job.get("grad_year"),
                    job.get("company_size"), job.get("company_industry"),
                    job.get("qualifications"), job.get("apply_url"),
                    job.get("date_posted"), job.get("first_seen_at"),
                    job.get("category"),
                    "filtered" if dup_of else "new",
                    f"duplicate of {dup_of}" if dup_of else None,
                ),
            )
    return fresh


def _mark_filtered(conn, job_id: str, reason: str):
    conn.execute(
        "UPDATE jobs SET status = 'filtered', filter_reason = ?, status_updated_at = ? WHERE id = ?",
        (reason, datetime.now(timezone.utc).isoformat(), job_id),
    )


def apply_filters(statuses: tuple[str, ...] = ("new",)) -> int:
    """Mark jobs that fail the keyword filters as 'filtered' (no Claude call)."""
    placeholders = ",".join("?" for _ in statuses)
    n = 0
    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM jobs WHERE status IN ({placeholders})", statuses).fetchall()
        for row in rows:
            reason = filter_reason(dict(row))
            if reason:
                _mark_filtered(conn, row["id"], reason)
                n += 1
    return n


def _score_new_jobs() -> int:
    """Score 'new' jobs. Eligible ones at/above FIT_THRESHOLD become 'ready',
    the rest 'scored'; ones Claude flags as ineligible become 'filtered'."""
    resume_text = get_resume_text()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE status = 'new'").fetchall()
    now = datetime.now(timezone.utc).isoformat()
    filtered = 0
    for row in rows:
        job = dict(row)
        try:
            result = score_job(resume_text, job)
        except Exception:
            logger.exception("scoring failed for job %s", job["id"])
            continue
        reason = None
        if result.get("requires_graduate_degree"):
            reason = "requires MS/PhD/MBA"
        elif result.get("is_research_role"):
            reason = "research role"
        elif result.get("asks_for_gpa_or_transcript"):
            reason = "asks for GPA or transcript"
        status = "ready" if result["fit_score"] >= FIT_THRESHOLD else "scored"
        with get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET fit_score = ?, fit_reasoning = ?, status = ?, scored_at = ? WHERE id = ?",
                (result["fit_score"], result["fit_reasoning"], status, now, job["id"]),
            )
            if reason:
                _mark_filtered(conn, job["id"], reason)
                filtered += 1
    return filtered


_lock = threading.Lock()
last_run: dict = {"started_at": None, "finished_at": None, "result": None, "error": None, "running": False}


def run_pipeline() -> dict:
    # Scheduler and the manual "run now" button can overlap; only one run at a time.
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    last_run.update(started_at=datetime.now(timezone.utc).isoformat(), running=True, error=None)
    try:
        known = _known_ids()
        scraped = scrape_all(known_ids=known)
        fresh = _insert_new_jobs(scraped)
        filtered = apply_filters()
        filtered += _score_new_jobs()
        result = {"scraped": len(scraped), "new": len(fresh), "filtered": filtered}
        last_run["result"] = result
        return result
    except Exception as e:
        logger.exception("pipeline run failed")
        last_run["error"] = str(e)
        raise
    finally:
        last_run.update(finished_at=datetime.now(timezone.utc).isoformat(), running=False)
        _lock.release()
