import json
import logging
import threading
from datetime import datetime, timezone

from app.config import FIT_THRESHOLD
from app.db import get_conn
from app.resume import get_resume_text
from app.scraper import scrape_all
from app.filters import filter_reason
from app.claude_client import score_job, draft_application

logger = logging.getLogger("pipeline")


def _known_ids() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM jobs").fetchall()
    return {r["id"] for r in rows}


def _insert_new_jobs(jobs: list[dict]) -> list[dict]:
    known = _known_ids()
    fresh = [j for j in jobs if j["id"] not in known]
    with get_conn() as conn:
        for job in fresh:
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (id, title, company, location, work_model, salary, grad_time,
                     company_size, company_industry, qualifications, apply_url,
                     date_posted, first_seen_at, category, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                """,
                (
                    job["id"], job.get("title"), job.get("company"), job.get("location"),
                    job.get("work_model"), job.get("salary"),
                    job.get("grad_time") or job.get("grad_year"),
                    job.get("company_size"), job.get("company_industry"),
                    job.get("qualifications"), job.get("apply_url"),
                    job.get("date_posted"), job.get("first_seen_at"),
                    job.get("category"),
                ),
            )
    return fresh


def _mark_filtered(conn, job_id: str, reason: str):
    conn.execute(
        "UPDATE jobs SET status = 'filtered', filter_reason = ?, status_updated_at = ? WHERE id = ?",
        (reason, datetime.now(timezone.utc).isoformat(), job_id),
    )


def apply_filters(statuses: tuple[str, ...] = ("new",)) -> int:
    """Mark jobs that fail the location/title filters as 'filtered' (no Claude call)."""
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
        with get_conn() as conn:
            if reason:
                conn.execute(
                    "UPDATE jobs SET fit_score = ?, fit_reasoning = ?, scored_at = ? WHERE id = ?",
                    (result["fit_score"], result["fit_reasoning"], now, job["id"]),
                )
                _mark_filtered(conn, job["id"], reason)
                filtered += 1
                continue
            conn.execute(
                "UPDATE jobs SET fit_score = ?, fit_reasoning = ?, status = 'scored', scored_at = ? WHERE id = ?",
                (result["fit_score"], result["fit_reasoning"], now, job["id"]),
            )
    return filtered


def _draft_high_fit_jobs():
    resume_text = get_resume_text()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'scored' AND fit_score >= ?",
            (FIT_THRESHOLD,),
        ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        job = dict(row)
        try:
            draft = draft_application(resume_text, job)
        except Exception:
            logger.exception("drafting failed for job %s", job["id"])
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET resume_bullets = ?, cover_letter = ?, status = 'drafted', drafted_at = ? WHERE id = ?",
                (json.dumps(draft["resume_bullets"]), draft["cover_letter"], now, job["id"]),
            )


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
        _draft_high_fit_jobs()
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


def redraft_job(job_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    draft = draft_application(get_resume_text(), dict(row))
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET resume_bullets = ?, cover_letter = ?, drafted_at = ?, "
            "status = CASE WHEN status IN ('new', 'scored') THEN 'drafted' ELSE status END WHERE id = ?",
            (json.dumps(draft["resume_bullets"]), draft["cover_letter"], now, job_id),
        )
    return draft
