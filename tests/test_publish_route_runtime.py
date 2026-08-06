from __future__ import annotations

import os
import unittest
from collections.abc import Iterable
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api_hardened import app
from app.subtitles.review_publish import PublishReviewedRequest, publish_reviewed


PUBLISH_PATH = "/api/v1/subtitles/{subtitle_id}/publish"


def _methods(route: object) -> set[str]:
    return {
        str(method).upper()
        for method in (getattr(route, "methods", None) or set())
    }


def _effective_routes(routes: Iterable[object]) -> list[object]:
    result: list[object] = []
    seen: set[int] = set()

    def visit(items: Iterable[object]) -> None:
        for route in items:
            nested = getattr(route, "original_router", None)
            if nested is not None and isinstance(getattr(nested, "routes", None), list):
                if id(nested) not in seen:
                    seen.add(id(nested))
                    visit(list(nested.routes))
            result.append(route)

    visit(routes)
    return result


class ProductionPublishRouteTests(unittest.TestCase):
    def _publish_routes(self) -> list[object]:
        return [
            route
            for route in _effective_routes(app.router.routes)
            if str(getattr(route, "path", "")) == PUBLISH_PATH
            and "POST" in _methods(route)
        ]

    def test_publish_route_is_unique_and_uses_review_handler(self) -> None:
        routes = self._publish_routes()
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertIs(getattr(route, "endpoint", None), publish_reviewed)
        self.assertIs(
            getattr(getattr(route, "body_field", None), "type_", None),
            PublishReviewedRequest,
        )

    def test_openapi_allows_revision_zero(self) -> None:
        schema = app.openapi()
        operation = schema["paths"][PUBLISH_PATH]["post"]
        reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        model_name = reference.rsplit("/", 1)[-1]
        request_schema = schema["components"]["schemas"][model_name]
        expected_revision = request_schema["properties"]["expected_revision"]
        self.assertEqual(expected_revision.get("minimum"), 0)
        self.assertEqual(model_name, "PublishReviewedRequest")

    def test_revision_zero_passes_request_validation(self) -> None:
        with patch.dict(
            os.environ,
            {"COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "false"},
            clear=False,
        ):
            response = TestClient(app).post(
                "/api/v1/subtitles/route-regression-missing/publish",
                json={
                    "expected_revision": 0,
                    "output_formats": ["srt", "txt"],
                },
            )
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(response.status_code, 422)

    def test_negative_revision_is_rejected_by_schema(self) -> None:
        with patch.dict(
            os.environ,
            {"COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "false"},
            clear=False,
        ):
            response = TestClient(app).post(
                "/api/v1/subtitles/route-regression-missing/publish",
                json={
                    "expected_revision": -1,
                    "output_formats": ["srt", "txt"],
                },
            )
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertTrue(
            any(item.get("loc") == ["body", "expected_revision"] for item in detail)
        )


if __name__ == "__main__":
    unittest.main()
