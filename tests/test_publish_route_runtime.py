from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ProductionPublishRouteTests(unittest.TestCase):
    def test_production_publish_contract_in_isolated_process(self) -> None:
        """Exercise the assembled production app without mutating test globals."""
        script = textwrap.dedent(
            r'''
            import json

            from fastapi.testclient import TestClient

            from app.api_hardened import (
                _PUBLISH_PATH,
                _PUBLISH_STATUS_PATH,
                _callable_identity,
                _effective_routes,
                _route_methods,
                app,
            )
            from app.subtitles.publish_status import get_publish_status
            from app.subtitles.review_publish import publish_reviewed

            def matches(path, method):
                return [
                    route
                    for route in _effective_routes(app.router.routes)
                    if str(getattr(route, "path", "")) == path
                    and method in _route_methods(route)
                ]

            publish_routes = matches(_PUBLISH_PATH, "POST")
            assert len(publish_routes) == 1, len(publish_routes)
            assert _callable_identity(
                getattr(publish_routes[0], "endpoint", None)
            ) == _callable_identity(publish_reviewed)

            status_routes = matches(_PUBLISH_STATUS_PATH, "GET")
            assert len(status_routes) == 1, len(status_routes)
            assert _callable_identity(
                getattr(status_routes[0], "endpoint", None)
            ) == _callable_identity(get_publish_status)

            schema = app.openapi()
            operation = schema["paths"][_PUBLISH_PATH]["post"]
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            model_name = reference.rsplit("/", 1)[-1]
            request_schema = schema["components"]["schemas"][model_name]
            minimum = request_schema["properties"]["expected_revision"].get("minimum")
            assert model_name == "PublishReviewedRequest", model_name
            assert minimum == 0, minimum
            assert "get" in schema["paths"][_PUBLISH_STATUS_PATH]

            client = TestClient(app)
            zero = client.post(
                "/api/v1/subtitles/route-regression-missing/publish",
                json={"expected_revision": 0, "output_formats": ["srt", "txt"]},
            )
            assert zero.status_code == 404, (zero.status_code, zero.text)

            negative = client.post(
                "/api/v1/subtitles/route-regression-missing/publish",
                json={"expected_revision": -1, "output_formats": ["srt", "txt"]},
            )
            assert negative.status_code == 422, (negative.status_code, negative.text)
            detail = negative.json()["detail"]
            assert any(
                item.get("loc") == ["body", "expected_revision"]
                for item in detail
            ), detail

            missing_status = client.get(
                "/api/v1/subtitles/route-regression-missing/publish-status"
            )
            assert missing_status.status_code == 404, (
                missing_status.status_code,
                missing_status.text,
            )

            print(json.dumps({
                "publish_route_count": len(publish_routes),
                "publish_endpoint": ".".join(
                    part
                    for part in _callable_identity(
                        getattr(publish_routes[0], "endpoint", None)
                    )
                    if part
                ),
                "publish_status_route_count": len(status_routes),
                "publish_status_endpoint": ".".join(
                    part
                    for part in _callable_identity(
                        getattr(status_routes[0], "endpoint", None)
                    )
                    if part
                ),
                "request_model": model_name,
                "expected_revision_minimum": minimum,
                "revision_zero_status": zero.status_code,
                "negative_revision_status": negative.status_code,
                "missing_publish_status": missing_status.status_code,
            }, sort_keys=True))
            '''
        )

        with tempfile.TemporaryDirectory() as data_dir:
            environment = os.environ.copy()
            environment.update(
                {
                    "COURSE_TRANSCRIPT_DATA_DIR": data_dir,
                    "COURSE_TRANSCRIPT_REQUIRE_ACCESS_HEADERS": "false",
                    "COURSE_TRANSCRIPT_PUBLIC_ORIGIN": "",
                }
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
                timeout=60,
            )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["publish_route_count"], 1)
        self.assertEqual(
            result["publish_endpoint"],
            "app.subtitles.review_publish.publish_reviewed",
        )
        self.assertEqual(result["publish_status_route_count"], 1)
        self.assertEqual(
            result["publish_status_endpoint"],
            "app.subtitles.publish_status.get_publish_status",
        )
        self.assertEqual(result["request_model"], "PublishReviewedRequest")
        self.assertEqual(result["expected_revision_minimum"], 0)
        self.assertEqual(result["revision_zero_status"], 404)
        self.assertEqual(result["negative_revision_status"], 422)
        self.assertEqual(result["missing_publish_status"], 404)


if __name__ == "__main__":
    unittest.main()
