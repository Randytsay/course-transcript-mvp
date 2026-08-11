from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.validate_outputs import _published_subtitle_count


class ValidateOutputsTests(unittest.TestCase):
    def test_uses_display_layer_count_for_published_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "subtitles-cleaned.json"
            path.write_text(
                json.dumps({"segments": [{}, {}, {}], "display_segments": [{}, {}]}),
                encoding="utf-8",
            )
            self.assertEqual(_published_subtitle_count(path, 3), 2)

    def test_falls_back_to_raw_count_without_display_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "subtitles-cleaned.json"
            path.write_text(json.dumps({"segments": [{}, {}]}), encoding="utf-8")
            self.assertEqual(_published_subtitle_count(path, 3), 3)


if __name__ == "__main__":
    unittest.main()
