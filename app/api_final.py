"""Final API entry point for live progress, costs, and gated formal transcripts."""
from __future__ import annotations

from app.api_ext import app
from app.formal_features import router as formal_router


# Replace the original permissive segments reader with a formal-output gate.
app.router.routes = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) != "/api/v1/jobs/{job_id}/segments"
]
app.include_router(formal_router)
