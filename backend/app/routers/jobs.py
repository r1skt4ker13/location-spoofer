from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import FIT_THRESHOLD, SCRAPE_INTERVAL_HOURS, SOURCES
from app.db import get_conn, row_to_dict
from app import pipeline

router = APIRouter(prefix="/api")

Status = Literal["new", "scored", "drafted", "applied", "skipped", "filtered"]


class StatusUpdate(BaseModel):
    status: Status


@router.get("/jobs")
def list_jobs(status: str | None = None, min_score: int | None = None, category: str | None = None):
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    else:
        sql += " AND status != 'filtered'"
    if category:
        sql += " AND category = ?"
        params.append(category)
    if min_score is not None:
        sql += " AND fit_score >= ?"
        params.append(min_score)
    sql += " ORDER BY (fit_score IS NULL), fit_score DESC, date_posted DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return row_to_dict(row)


@router.post("/jobs/{job_id}/status")
def update_status(job_id: str, body: StatusUpdate):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, status_updated_at = ? WHERE id = ?",
            (body.status, now, job_id),
        )
    if cur.rowcount == 0:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


@router.post("/jobs/{job_id}/draft")
def draft_job(job_id: str):
    draft = pipeline.redraft_job(job_id)
    if draft is None:
        raise HTTPException(404, "Job not found")
    return get_job(job_id)


@router.post("/run")
def run_now(background: BackgroundTasks):
    if pipeline.last_run["running"]:
        return {"started": False, "reason": "already running"}
    background.add_task(pipeline.run_pipeline)
    return {"started": True}


@router.get("/status")
def status():
    with get_conn() as conn:
        counts = {
            r["status"]: r["n"]
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        }
    return {
        "last_run": pipeline.last_run,
        "counts": counts,
        "fit_threshold": FIT_THRESHOLD,
        "interval_hours": SCRAPE_INTERVAL_HOURS,
        "categories": [s["label"] for s in SOURCES],
    }
