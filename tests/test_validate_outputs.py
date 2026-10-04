from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.providers.validate_outputs import _published_subtitle_count, _reconstructed_base_chunks


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

    def test_reads_explicit_reconstructed_base_chunk_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "merged-words.json").write_text(
                json.dumps({"reconstructed_base_chunks": [0, "8", "bad"]}),
                encoding="utf-8",
            )
            self.assertEqual(_reconstructed_base_chunks(root), {0, 8})


if __name__ == "__main__":
    unittest.main()
