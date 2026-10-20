"""Golden V2 Chirp protobuf Duration omission is not a license to invent timings."""
import json
import unittest

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_chirp_stage import _parse_words


class RawClockTests(unittest.TestCase):
    window = {"start_ms": 100000, "end_ms": 105000}

    @staticmethod
    def blob(words):
        return json.dumps({"results": [{"alternatives": [{"words": words}]}]}).encode()

    def test_only_first_missing_zero_start_is_exactly_recovered(self):
        blob = self.blob([
            {"word": "南", "endOffset": "0.4s"},
            {"word": "無", "startOffset": "0.5s", "endOffset": "0.7s"},
        ])
        self.assertEqual(_parse_words(blob, self.window), [
            {"word": "南", "start_ms": 100000, "end_ms": 100400},
            {"word": "無", "start_ms": 100500, "end_ms": 100700},
        ])

    def test_nonfirst_missing_start_is_forbidden(self):
        with self.assertRaisesRegex(V2Conflict, "invalid Speech word time offset"):
            _parse_words(self.blob([
                {"word": "阿", "startOffset": "0s", "endOffset": "0.2s"},
                {"word": "彌", "endOffset": "0.4s"},
            ]), self.window)

    def test_missing_end_is_forbidden(self):
        with self.assertRaisesRegex(V2Conflict, "invalid Speech word time offset"):
            _parse_words(self.blob([{"word": "佛", "startOffset": "0s"}]), self.window)

    def test_zero_length_and_out_of_window_is_forbidden(self):
        for start, end in [("0.2s", "0.2s"), ("1s", "0.5s"), ("0s", "60s")]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(V2Conflict):
                    _parse_words(self.blob([
                        {"word": "法", "startOffset": start, "endOffset": end},
                    ]), self.window)

    def test_no_disguising_invalid_offset_by_converting_to_zero(self):
        with self.assertRaises(V2Conflict):
            _parse_words(self.blob([
                {"word": "會", "startOffset": "154017527235.840s",
                 "endOffset": "0.8s"},
            ]), self.window)


if __name__ == "__main__":
    unittest.main()