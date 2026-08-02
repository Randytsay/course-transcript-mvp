from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NewFeatureTests(unittest.TestCase):
    def test_output_compatibility_and_production_filter(self) -> None:
        from app.jobs.exports import normalize_output_formats, production_output_formats

        self.assertEqual(normalize_output_formats(None), ["srt", "txt", "csv"])
        self.assertEqual(
            normalize_output_formats(["srt", "docx", "pdf", "txt"]),
            ["srt", "docx", "pdf", "txt"],
        )
        self.assertEqual(
            production_output_formats(["srt", "docx", "pdf", "txt"]),
            ["srt", "txt"],
        )

    def test_dynamic_chunk_plan_uses_uniform_first_chunk(self) -> None:
        module_name = "app.providers.run_chirp_pipeline"
        with patch.dict(os.environ, {"CHIRP_DYNAMIC_BATCHING": "true"}):
            sys.modules.pop(module_name, None)
            module = importlib.import_module(module_name)
        plan = module.compute_chunk_plan(2_000)
        self.assertEqual(plan[0], (0, 0.0, 900.0))
        self.assertEqual(plan[1][1], 890.0)

    def test_safe_drive_publish_backs_up_existing_file(self) -> None:
        from app.jobs.drive_publish import publish_outputs

        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp) / "job-123"
            job.mkdir()
            local = job / "subtitles-corrected.srt"
            local.write_text("new subtitle", encoding="utf-8")
            remote: dict[str, bytes] = {"gdrive:course/lesson.srt": b"old subtitle"}

            def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                operation = command[1]
                if operation == "size":
                    path = command[-1]
                    if path not in remote:
                        return subprocess.CompletedProcess(command, 1, "", "object not found")
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({"count": 1, "bytes": len(remote[path])}),
                        "",
                    )
                if operation == "copyto":
                    source, destination = command[-2], command[-1]
                    remote[destination] = Path(source).read_bytes()
                    return subprocess.CompletedProcess(command, 0, "", "")
                if operation == "moveto":
                    source, destination = command[-2], command[-1]
                    if source not in remote:
                        return subprocess.CompletedProcess(command, 1, "", "object not found")
                    remote[destination] = remote.pop(source)
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError(command)

            state = publish_outputs(
                job,
                source_name="lesson.mp3",
                destination="gdrive:course",
                output_formats=["srt"],
                authorized=True,
                runner=runner,
                sleeper=lambda _: None,
                jitter=lambda: 0,
                clock=lambda: 100,
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["backup_count"], 1)
            self.assertEqual(remote["gdrive:course/lesson.srt"], b"new subtitle")
            backup = state["files"]["srt"]["backup_remote_path"]
            self.assertEqual(remote[backup], b"old subtitle")


if __name__ == "__main__":
    unittest.main()
