"""Production API entry point with hardened subtitle and Drive routes."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.api_observed import app
from app.drive_api_routes import router as drive_api_router
from app.subtitles.editor_hardened import import_srt
from app.subtitles.publish_status import get_publish_status
from app.subtitles.review_publish import publish_reviewed

_PUBLISH_PATH = "/api/v1/subtitles/{subtitle_id}/publish"
_PUBLISH_STATUS_PATH = "/api/v1/subtitles/{subtitle_id}/publish-status"
_REPLACED_ROUTES = {
    ("/api/v1/subtitles/import", "POST"),
    (_PUBLISH_PATH, "POST"),
    (_PUBLISH_STATUS_PATH, "GET"),
    ("/api/v1/drive/browse", "POST"),
}


def _route_methods(route: object) -> set[str]:
    return {
        str(method).upper()
        for method in (getattr(route, "methods", None) or set())
    }


def _is_replaced_route(route: object) -> bool:
    path = str(getattr(route, "path", ""))
    methods = _route_methods(route)
    return any(
        path == target and method in methods
        for target, method in _REPLACED_ROUTES
    )


def _included_router(route: object) -> Any | None:
    """Return the original router wrapped by the production include shim.

    Production preserves included routers in wrapper objects. Filtering only the
    top-level app route list therefore leaves nested duplicate routes active. Use
    duck typing instead of importing the private wrapper class.
    """
    candidate = getattr(route, "original_router", None)
    if candidate is not None and isinstance(getattr(candidate, "routes", None), list):
        return candidate
    return None


def _remove_replaced_routes(routes: Iterable[object]) -> list[object]:
    kept: list[object] = []
    for route in routes:
        nested = _included_router(route)
        if nested is not None:
            nested.routes = _remove_replaced_routes(list(nested.routes))
        if not _is_replaced_route(route):
            kept.append(route)
    return kept


def _effective_routes(routes: Iterable[object]) -> list[object]:
    result: list[object] = []
    seen_routers: set[int] = set()

    def visit(items: Iterable[object]) -> None:
        for route in items:
            nested = _included_router(route)
            if nested is not None and id(nested) not in seen_routers:
                seen_routers.add(id(nested))
                visit(list(nested.routes))
            result.append(route)

    visit(routes)
    return result


def _callable_identity(value: object) -> tuple[str | None, str | None]:
    return (
        getattr(value, "__module__", None),
        getattr(value, "__qualname__", None),
    )


def _assert_unique_route(
    *,
    path: str,
    method: str,
    endpoint: object,
    description: str,
) -> None:
    matches = [
        route
        for route in _effective_routes(app.router.routes)
        if str(getattr(route, "path", "")) == path
        and method in _route_methods(route)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Production {description} route must be unique; found {len(matches)}"
        )
    actual = getattr(matches[0], "endpoint", None)
    if _callable_identity(actual) != _callable_identity(endpoint):
        raise RuntimeError(
            f"Production {description} route resolved to an unexpected endpoint"
        )


def _assert_publish_route() -> None:
    """Fail closed when runtime routing can reach any legacy publish handler."""
    _assert_unique_route(
        path=_PUBLISH_PATH,
        method="POST",
        endpoint=publish_reviewed,
        description="publish",
    )


def _assert_publish_status_route() -> None:
    """Fail closed unless the read-only reconciliation endpoint is unique."""
    _assert_unique_route(
        path=_PUBLISH_STATUS_PATH,
        method="GET",
        endpoint=get_publish_status,
        description="publish-status",
    )


# Keep every existing read/edit route from the observed API, replacing only the
# endpoints that require hardened production behavior. The recursive pass is
# required because production wraps include_router() calls.
app.router.routes = _remove_replaced_routes(list(app.router.routes))
app.post("/api/v1/subtitles/import", status_code=201)(import_srt)
app.post(_PUBLISH_PATH)(publish_reviewed)
app.get(_PUBLISH_STATUS_PATH)(get_publish_status)
app.include_router(drive_api_router)
app.openapi_schema = None
_assert_publish_route()
_assert_publish_status_route()
