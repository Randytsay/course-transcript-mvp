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
                _callable_identity,
                _effective_routes,
                _route_methods,
                app,
            )
            from app.subtitles.review_publish import publish_reviewed

            routes = [
                route
                for route in _effective_routes(app.router.routes)
                if str(getattr(route, "path", "")) == _PUBLISH_PATH
                and "POST" in _route_methods(route)
            ]
            assert len(routes) == 1, len(routes)
            assert _callable_identity(getattr(routes[0], "endpoint", None)) == _callable_identity(publish_reviewed)

            schema = app.openapi()
            operation = schema["paths"][_PUBLISH_PATH]["post"]
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            model_name = reference.rsplit("/", 1)[-1]
            request_schema = schema["components"]["schemas"][model_name]
            minimum = request_schema["properties"]["expected_revision"].get("minimum")
            assert model_name == "PublishReviewedRequest", model_name
            assert minimum == 0, minimum

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
            assert any(item.get("loc") == ["body", "expected_revision"] for item in detail), detail

            print(json.dumps({
                "publish_route_count": len(routes),
                "endpoint": ".".join(part for part in _callable_identity(getattr(routes[0], "endpoint", None)) if part),
                "request_model": model_name,
                "expected_revision_minimum": minimum,
                "revision_zero_status": zero.status_code,
                "negative_revision_status": negative.status_code,
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

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["publish_route_count"], 1)
        self.assertEqual(result["endpoint"], "app.subtitles.review_publish.publish_reviewed")
        self.assertEqual(result["request_model"], "PublishReviewedRequest")
        self.assertEqual(result["expected_revision_minimum"], 0)
        self.assertEqual(result["revision_zero_status"], 404)
        self.assertEqual(result["negative_revision_status"], 422)


if __name__ == "__main__":
    unittest.main()
