import unittest

from app.providers.run_chirp_pipeline import _diagnostic_excerpt


class ChirpDiagnosticTests(unittest.TestCase):
    def test_provider_diagnostic_excerpt_keeps_terminal_google_root_cause(self) -> None:
        prefix = "Traceback context\n" + ("stack frame\n" * 300)
        root = (
            "google.api_core.exceptions.PermissionDenied: 403 "
            "Permission 'speech.recognizers.recognize' denied "
            '[reason: "IAM_PERMISSION_DENIED"]'
        )
        excerpt = _diagnostic_excerpt(prefix + root, limit=600)

        self.assertIn("Traceback context", excerpt)
        self.assertIn("speech.recognizers.recognize", excerpt)
        self.assertIn("IAM_PERMISSION_DENIED", excerpt)
        self.assertIn("provider diagnostic truncated", excerpt)


if __name__ == "__main__":
    unittest.main()
