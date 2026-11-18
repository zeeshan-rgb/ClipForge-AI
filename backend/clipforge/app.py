"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import jobs as jobs_api
from .api import music as music_api
from .api import outreach as outreach_api
from .api import story as story_api
from .api import projects as projects_api
from .api import system as system_api
from .api import youtube as youtube_api
from .config import Settings
from .context import AppContext
from .db import make_engine, make_session_factory
from .events import EventBus
from .jobs.engine import JobWorker, Pipeline
from .outreach import OutreachWorker
from .pipelines import PIPELINES

log = logging.getLogger("clipforge")

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>ClipForge AI</title>
<body style="font-family:system-ui;background:#0e1014;color:#e8e9ec;padding:48px">
<h1>ClipForge AI is running, but the dashboard is not built yet.</h1>
<p>In a terminal, run <code>npm run build</code> inside the <code>frontend</code> folder, then reload.</p>
</body>"""


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger("clipforge")
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    settings.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        settings.logs_dir / "clipforge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    root.propagate = False


def create_app(
    settings: Settings | None = None,
    *,
    pipelines: dict[str, Pipeline] | None = None,
    configure_logging: bool = True,
) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    if configure_logging:
        setup_logging(settings)

    engine = make_engine(settings.db_path)
    session = make_session_factory(engine)
    bus = EventBus()
    worker = JobWorker(settings, session, bus, pipelines or PIPELINES)
    outreach = OutreachWorker(settings, session, bus, settings.outreach_interval_seconds, job_worker=worker)
    ctx = AppContext(settings=settings, engine=engine, session=session, bus=bus, worker=worker, outreach=outreach)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        outreach.start()
        log.info("ClipForge AI %s started; data in %s", __version__, settings.data_dir)
        try:
            yield
        finally:
            await asyncio.to_thread(worker.stop)
            await asyncio.to_thread(outreach.stop)
            engine.dispose()
            log.info("ClipForge AI stopped")

    app = FastAPI(title="ClipForge AI", version=__version__, lifespan=lifespan)
    app.state.ctx = ctx

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        message = str(first.get("msg", "Invalid request")).removeprefix("Value error, ")
        return JSONResponse({"error": message, "details": jsonable_encoder(exc.errors())}, status_code=422)

    app.include_router(system_api.router)
    app.include_router(music_api.router)
    app.include_router(projects_api.router)
    app.include_router(projects_api.shorts_router)
    app.include_router(youtube_api.router)
    app.include_router(outreach_api.router)
    app.include_router(story_api.router)
    app.include_router(jobs_api.router)

    @app.websocket("/api/ws")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        queue = bus.subscribe()
        try:
            await ws.send_json({"type": "hello", "version": __version__})
            while True:
                await ws.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(queue)

    dist = settings.frontend_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        # client-side routes all load the app shell; never cache it
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
