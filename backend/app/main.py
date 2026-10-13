from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .jobs.monitor import JobMonitor
from .jobs.queue_manager import JobQueueManager
from .jobs.real_runner import RealRunner
from .routers import auth, jobs, servers, settings as settings_router, viewer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    runner = RealRunner()
    queue = JobQueueManager(runner, settings.DATA_DIR / "jobs.json", Path(settings.get_app_settings().get("nfs_root", ".")))
    job_monitor = JobMonitor(queue, runner)
    app.state.queue_manager = queue
    app.state.monitor = job_monitor
    task = asyncio.create_task(job_monitor.run())
    yield
    job_monitor.stop()
    await task


app = FastAPI(title="Aero404 CFD Platform", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(servers.router)
app.include_router(settings_router.router)
app.include_router(viewer.router)
app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "templates" / "index.html")
