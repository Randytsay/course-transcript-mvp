"""Production API entry point with hardened subtitle and Drive routes."""
from __future__ import annotations

from app.api_observed import app
from app.drive_api_routes import router as drive_api_router
from app.subtitles.editor_hardened import import_srt, publish_edited

# 1. Filter out ONLY the legacy endpoints from observed routes
_LEGACY_PATHS = {
    "/api/v1/subtitles/import",
    "/api/v1/subtitles/{subtitle_id}/publish",
    "/api/v1/drive/browse",
}
app.router.routes = [
    route
    for route in app.router.routes
    if str(getattr(route, "path", "")) not in _LEGACY_PATHS
]

# 2. Register the new hardened subtitle routes
app.post("/api/v1/subtitles/import", status_code=201)(import_srt)
app.post("/api/v1/subtitles/{subtitle_id}/publish")(publish_edited)

# 3. Include the new Google Drive API + health + search endpoints router
app.include_router(drive_api_router)

