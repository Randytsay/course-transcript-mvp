from __future__ import annotations

import unittest

from app.pipeline.worker import _command_failure_message


class PipelineErrorTests(unittest.TestCase):
    def test_command_error_retains_stdout_and_stderr(self) -> None:
        message = _command_failure_message(
            1,
            "BUILD=FAIL invalid fixed segments\n",
            "jieba startup note\n",
        )
        self.assertIn("command exited 1", message)
        self.assertIn("BUILD=FAIL invalid fixed segments", message)
        self.assertIn("jieba startup note", message)


if __name__ == "__main__":
    unittest.main()
