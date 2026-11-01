import logging
from datetime import datetime
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import BASE_DIR, SCRAPE_INTERVAL_HOURS
from app.db import init_db
from app.pipeline import apply_filters, run_pipeline
from app.routers import jobs

logging.basicConfig(level=logging.INFO)

FRONTEND_INDEX = BASE_DIR / "frontend" / "index.html"
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Re-check already-scored jobs against the current filters.
    apply_filters(("new", "scored", "ready"))
    # Background thread: Playwright's sync API can't run on the asyncio loop.
    scheduler.add_job(run_pipeline, "interval", hours=SCRAPE_INTERVAL_HOURS, id="pipeline",
                      max_instances=1, next_run_time=datetime.now())
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Job Agent", lifespan=lifespan)
app.include_router(jobs.router)


@app.get("/")
def index():
    return FileResponse(FRONTEND_INDEX)
