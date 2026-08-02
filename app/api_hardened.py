"""Production API entry point with hardened subtitle mutation routes."""
from __future__ import annotations

from app.api_observed import app
from app.subtitles.editor_hardened import import_srt, publish_edited

_REPLACED_SUBTITLE_PATHS = {
    "/api/v1/subtitles/import",
    "/api/v1/subtitles/{subtitle_id}/publish",
}

# Keep every existing read/edit route from the observed API, replacing only the
# two mutation endpoints whose implementations require strict parsing and
# cross-process publication locking. Registering directly on the application
# avoids composing routers that already carry an absolute subtitle prefix.
app.router.routes = [
    route
    for route in app.router.routes
    if str(getattr(route, "path", "")) not in _REPLACED_SUBTITLE_PATHS
]
app.post("/api/v1/subtitles/import", status_code=201)(import_srt)
app.post("/api/v1/subtitles/{subtitle_id}/publish")(publish_edited)
