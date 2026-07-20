"""FastAPI app entry point. Run with:
    uvicorn mailmind.api.main:app --host 0.0.0.0 --port 8000

In production this also serves the built React SPA (frontend/dist, copied to
mailmind/api/static at image-build time — see Dockerfile) so the whole app is
one process/one origin. In local dev, run the Vite dev server separately
(it proxies /api to this process — see frontend/vite.config.ts) and don't
rely on the static mount at all.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mailmind.api import auth as auth_module
from mailmind.api.routers import (
    automate,
    drafts,
    folders,
    history,
    inbox,
    insights,
    meta,
    now,
    queue,
    review,
    search,
)

app = FastAPI(title="MailMind API")

for router in (
    auth_module.router,
    meta.router,
    now.router,
    queue.router,
    review.router,
    inbox.router,
    search.router,
    folders.router,
    history.router,
    insights.router,
    automate.router,
    drafts.router,
):
    app.include_router(router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


_STATIC_DIR = Path(__file__).parent / "static"

if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """SPA fallback: any non-API, non-asset path serves index.html so the
        client-side router (React Router) can handle it."""
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
