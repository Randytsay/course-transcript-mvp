"""Production API entry point with optional live-progress and billing routes."""
from __future__ import annotations

from app.api import app
from app.live_features import router as live_router


# The feature branch originally added prototype handlers directly to app.api.
# Remove only those exact read-only paths before registering the reviewed
# implementations. All existing job mutations, preflight, review, and artifact
# endpoints remain owned by app.api.
_REPLACED_PATHS = {
    "/api/v1/jobs/{job_id}/chunks",
    "/api/v1/jobs/{job_id}/chunks/{chunk_index}/transcript",
    "/api/v1/jobs/{job_id}/live-cost",
    "/api/v1/billing/summary",
}
app.router.routes = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) not in _REPLACED_PATHS
]
app.include_router(live_router)
