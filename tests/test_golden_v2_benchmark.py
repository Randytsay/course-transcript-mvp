import unittest

from app.evaluation.golden_v2_benchmark import evaluate_character_errors


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


if __name__ == "__main__":
    unittest.main()
