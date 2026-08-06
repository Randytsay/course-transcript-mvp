"""Production API entry point with hardened subtitle and Drive routes."""
from __future__ import annotations

from app.api_observed import app
from app.drive_api_routes import router as drive_api_router
from app.subtitles.editor_hardened import import_srt
from app.subtitles.review_publish import publish_reviewed

_REPLACED_PATHS = {
    "/api/v1/subtitles/import",
    "/api/v1/subtitles/{subtitle_id}/publish",
    "/api/v1/drive/browse",
}

# Keep every existing read/edit route from the observed API, replacing only the
# mutation endpoints that need strict parsing/locking and the Drive browser that
# now uses the native Google Drive API with an explicit rclone fallback.
app.router.routes = [
    route
    for route in app.router.routes
    if str(getattr(route, "path", "")) not in _REPLACED_PATHS
]
app.post("/api/v1/subtitles/import", status_code=201)(import_srt)
app.post("/api/v1/subtitles/{subtitle_id}/publish")(publish_reviewed)
app.include_router(drive_api_router)
