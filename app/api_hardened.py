"""Production API entry point with hardened subtitle mutation routes."""
from __future__ import annotations

from app.api_observed import app
from app.subtitles.editor_hardened import router as hardened_subtitle_router

app.router.routes = [
    route
    for route in app.router.routes
    if not str(getattr(route, "path", "")).startswith("/api/v1/subtitles")
]
app.include_router(hardened_subtitle_router)
