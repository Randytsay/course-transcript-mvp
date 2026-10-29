"""Zero-network tests for Owner CLI + private bridge diagnostic routing."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from scripts import golden_v2_owner_client as owner
from scripts import golden_v2_chat_client as bridge

JOB = "v2p_" + "f" * 32


class ChirpPreviewOwnerBridgeTests(unittest.TestCase):
    def test_authorized_owner_cli_reads_preview_using_status_token(self):
        request_context = MagicMock()
        request_context.__enter__.return_value.read.return_value = json.dumps(
            {"read_only": True, "live_gcp_provider_status_verified": False}
        ).encode()
        opener = MagicMock()
        opener.open.return_value = request_context
        env = dict(COURSE_TRANSCRIPT_V2_OWNER_API_URL="http://127.0.0.1:8000",
                   COURSE_TRANSCRIPT_V2_STATUS_TOKEN="s" * 40,
                   COURSE_TRANSCRIPT_V2_WRITE_TOKEN="w" * 40)
        with patch.dict(os.environ, env), patch.object(
                sys, "argv", ["owner", "chirp-reconciliation-preview", JOB]), patch(
                "scripts.golden_v2_owner_client.build_opener", return_value=opener
            ), patch("builtins.print"):
            self.assertEqual(owner.main(), 0)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data)
        self.assertEqual(
            req.full_url,
            f"http://127.0.0.1:8000/api/v1/golden-v2/jobs/{JOB}/"
            "chirp-reconciliation-preview")
        self.assertEqual(req.get_header("Authorization"), "Bearer " + "s" * 40)

    def test_unknown_job_does_not_call_remote_api(self):
        env = dict(COURSE_TRANSCRIPT_V2_OWNER_API_URL="http://127.0.0.1:8000",
                   COURSE_TRANSCRIPT_V2_STATUS_TOKEN="s" * 40,
                   COURSE_TRANSCRIPT_V2_WRITE_TOKEN="w" * 40)
        with patch.dict(os.environ, env), patch.object(
                sys, "argv", ["owner", "chirp-reconciliation-preview", "fake"]), patch(
                "scripts.golden_v2_owner_client.build_opener") as remote, patch(
                "sys.stderr"):
            with self.assertRaises(SystemExit):
                owner.main()
            remote.assert_not_called()

    def test_chat_bridge_is_explicitly_allowlisted(self):
        settings = dict(COURSE_TRANSCRIPT_V2_OWNER_API_URL="http://127.0.0.1:8000",
                        COURSE_TRANSCRIPT_V2_STATUS_TOKEN="s" * 40,
                        COURSE_TRANSCRIPT_V2_WRITE_TOKEN="w" * 40)
        with patch.dict(os.environ, {}, clear=False), patch.object(
                sys, "argv", ["entry", "chirp-reconciliation-preview", JOB]), patch(
                "scripts.golden_v2_chat_client.load_private_config",
                return_value=settings), patch(
                "scripts.golden_v2_chat_client.owner_main", return_value=0) as delegate:
            self.assertEqual(bridge.main(), 0)
            self.assertEqual(
                sys.argv, ["entry", "chirp-reconciliation-preview", JOB])
            delegate.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
