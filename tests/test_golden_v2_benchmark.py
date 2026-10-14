import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.evaluation.golden_v2_benchmark import (
    evaluate_character_errors, evaluate_paired_engine_benchmark,
)
from scripts.golden_v2_benchmark import main as benchmark_cli_main


class GoldenBenchmarkTests(unittest.TestCase):
    def test_identical_traditional_chinese_has_zero_cer(self):
        report = evaluate_character_errors(["復次文殊師利。"], ["復次，文殊師利"])
        self.assertEqual(report["character_error_rate"], 0)

    def test_substitution_rate_and_evidence_counts(self):
        report = evaluate_character_errors(["男身具七寶"], ["男生具七寶"])
        self.assertEqual(report["substitutions"], 1)
        self.assertEqual(report["character_error_rate"], 1/5)

    def test_true_omission_and_hallucinated_insert(self):
        report = evaluate_character_errors(["阿彌陀佛"], ["阿彌佛呀"])
        self.assertEqual(report["reference_characters"], 4)
        self.assertGreater(report["character_error_rate"], 0)

    def test_refuses_unmatched_or_oversized_windows(self):
        with self.assertRaises(ValueError):
            evaluate_character_errors(["經文"], [])
        with self.assertRaises(ValueError):
            evaluate_character_errors(["經"*5000], ["經"*5000])

    def _gold(self):
        return [
            {"window_id": "w1", "source_sha256": "a" * 64,
             "start_ms": 0, "end_ms": 4000, "category": "scripture",
             "text": "復次文殊師利", "human_verified": True,
             "gold_evidence_sha256": "b" * 64,
             "reviewer_reference": "gold-review-01"},
            {"window_id": "w2", "source_sha256": "a" * 64,
             "start_ms": 4000, "end_ms": 8000, "category": "mixed",
             "text": "台語交替測試", "human_verified": True,
             "gold_evidence_sha256": "c" * 64,
             "reviewer_reference": "gold-review-02"},
        ]

    def _engine(self, texts, *, missing_cost=False):
        rows = []
        for index, (window_id, text) in enumerate(zip(("w1", "w2"), texts)):
            row = {
                "window_id": window_id, "source_sha256": "a" * 64,
                "text": text, "raw_evidence_sha256": str(index + 1) * 64,
                "elapsed_seconds": 1.25 * (index + 1),
            }
            if not (missing_cost and index == 1):
                row["cost_usd"] = 0.01 * (index + 1)
            rows.append(row)
        return {"windows": rows}

    def test_paired_report_uses_same_source_windows_and_human_gold(self):
        report = evaluate_paired_engine_benchmark(
            self._gold(),
            {
                "engine_a": self._engine(["復次文殊師利", "台語交替測事"]),
                "engine_b": self._engine(["復次文殊", "台語測試"], missing_cost=True),
            },
        )
        self.assertEqual(report["pairing"], "EXACT_SOURCE_SHA_AND_WINDOW_ID")
        self.assertFalse(report["accuracy_claimed"])
        self.assertFalse(report["model_agreement_used_as_gold"])
        self.assertEqual(report["paired_window_manifest"][0]["window_id"], "w1")
        self.assertEqual(report["paired_window_manifest"][1]["gold_evidence_sha256"], "c" * 64)
        self.assertEqual(report["engines"]["engine_a"]["window_count"], 2)
        self.assertEqual(report["engines"]["engine_a"]["character_metrics"]["substitutions"], 1)
        self.assertEqual(report["engines"]["engine_a"]["by_category"]["mixed"]["substitutions"], 1)
        self.assertEqual(report["engines"]["engine_a"]["cost_usd"], 0.03)
        self.assertEqual(report["engines"]["engine_a"]["elapsed_seconds_sum"], 3.75)
        self.assertEqual(report["engines"]["engine_a"]["provider_evidence_by_window"]["w2"], "2" * 64)
        self.assertIsNone(report["engines"]["engine_b"]["cost_usd"])
        self.assertFalse(report["engines"]["engine_b"]["cost_complete"])

    def test_paired_report_rejects_unverified_or_unmatched_evidence(self):
        engine = self._engine(["復次文殊師利", "台語交替測試"])
        with self.assertRaisesRegex(ValueError, "human-review"):
            evaluate_paired_engine_benchmark(
                [{**self._gold()[0], "human_verified": False}, self._gold()[1]],
                {"engine": engine},
            )
        with self.assertRaisesRegex(ValueError, "exact paired"):
            evaluate_paired_engine_benchmark(self._gold(), {
                "engine": {"windows": engine["windows"][:1]},
            })
        bad_source = {**engine["windows"][0], "source_sha256": "d" * 64}
        with self.assertRaisesRegex(ValueError, "source differs"):
            evaluate_paired_engine_benchmark(
                self._gold(), {"engine": {"windows": [bad_source, engine["windows"][1]]}},
            )

    def test_paired_report_rejects_overlapping_windows_and_invalid_measurements(self):
        gold = self._gold()
        gold[1] = {**gold[1], "start_ms": 3999}
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            evaluate_paired_engine_benchmark(
                gold, {"engine": self._engine(["復次文殊師利", "台語交替測試"])},
            )
        gold = self._gold()
        bad_engine = self._engine(["復次文殊師利", "台語交替測試"])
        bad_engine["windows"][0]["cost_usd"] = float("nan")
        with self.assertRaisesRegex(ValueError, "cost or latency"):
            evaluate_paired_engine_benchmark(gold, {"engine": bad_engine})

    def test_cli_writes_new_report_without_exposing_transcript_text(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            output = root / "nested" / "report.json"
            source.write_text(json.dumps({
                "human_gold_windows": self._gold(),
                "engine_outputs": {
                    "engine_a": self._engine(["復次文殊師利", "台語交替測事"]),
                },
            }, ensure_ascii=False), encoding="utf-8")
            stdout = StringIO()
            with patch.object(sys, "argv", ["golden_v2_benchmark", "--input", str(source),
                                             "--output", str(output)]), redirect_stdout(stdout):
                self.assertEqual(benchmark_cli_main(), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["window_count"], 2)
            self.assertNotIn("復次文殊師利", stdout.getvalue())
            with self.assertRaises(SystemExit) as error:
                with patch.object(sys, "argv", ["golden_v2_benchmark", "--input", str(source),
                                                 "--output", str(output)]), \
                        redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    benchmark_cli_main()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
