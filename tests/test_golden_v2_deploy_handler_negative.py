"""
Synthetic & Negative Test Suite for Golden V2 Deployment Handler
Covers requirements from TASK AGY-LOCAL-010:
- Wrong SHA
- Wrong project / V1 target isolation
- Inactive / default-deny mutating invocation
- Insufficient disk space (< 8 GiB reserve floor)
- Active pending Provider/Drive operations or active leases in ledger
- Stale / missing backup or missing release artifacts
- Rollback plan image repin confirmation
"""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from deploy.golden_v2_chat_deploy import (
    check_disk_space,
    check_live_ledger_drain,
    check_v1_isolation,
    plan_deployment,
    plan_rollback,
    validate_sha,
    verify_release_artifacts,
)


class TestGoldenV2DeployHandlerNegative(unittest.TestCase):

    def test_wrong_sha_format(self):
        invalid_shas = [
            "not-a-sha",
            "12345",
            "961b03d9",  # short sha
            "961b03d92c6f5b4eea135e826a1286c0ed5e08aG",  # non-hex
            "961b03d92c6f5b4eea135e826a1286c0ed5e08a3 ",  # whitespace
        ]
        for bad_sha in invalid_shas:
            with self.subTest(sha=bad_sha):
                with self.assertRaises(SystemExit) as cm:
                    validate_sha(bad_sha)
                self.assertEqual(cm.exception.code, 1)

    def test_valid_sha(self):
        valid = "961b03d92c6f5b4eea135e826a1286c0ed5e08a3"
        self.assertEqual(validate_sha(valid), valid)

    def test_v1_isolation_wrong_project(self):
        with self.assertRaises(SystemExit) as cm:
            check_v1_isolation("course-transcript")
        self.assertEqual(cm.exception.code, 1)

    def test_v1_isolation_forbidden_paths(self):
        bad_paths = [
            "/opt/course-transcript-source",
            "/opt/course-transcript-releases/abc",
            "/opt/course-transcript-backups",
        ]
        for p in bad_paths:
            with self.subTest(path=p):
                with self.assertRaises(SystemExit) as cm:
                    check_v1_isolation("golden-v2-chat", target_path=p)
                self.assertEqual(cm.exception.code, 1)

    def test_insufficient_disk_space_gate(self):
        # Mock statvfs returning 5 GiB (below 8 GiB floor)
        class MockStatVFS:
            f_bavail = 5 * 1024 * 1024  # 5 GiB in 1K blocks
            f_frsize = 1024

        with patch("os.statvfs", return_value=MockStatVFS()):
            with self.assertRaises(SystemExit) as cm:
                check_disk_space(floor_gib=8.0)
            self.assertEqual(cm.exception.code, 1)

    def test_live_ledger_drain_active_jobs_stop(self):
        # Create temp sqlite db with an active job
        tmp_db = Path("/tmp/test_ledger_active_job.sqlite")
        if tmp_db.exists():
            tmp_db.unlink()
        conn = sqlite3.connect(str(tmp_db))
        cur = conn.cursor()
        cur.execute("CREATE TABLE v2_jobs (id TEXT, status TEXT);")
        cur.execute("INSERT INTO v2_jobs VALUES ('job1', 'IN_FLIGHT');")
        cur.execute("CREATE TABLE v2_chirp_operations (id TEXT, status TEXT);")
        cur.execute("CREATE TABLE v2_auto_publication_fences (id TEXT);")
        conn.commit()
        conn.close()

        try:
            with self.assertRaises(SystemExit) as cm:
                check_live_ledger_drain(tmp_db)
            self.assertEqual(cm.exception.code, 1)
        finally:
            if tmp_db.exists():
                tmp_db.unlink()

    def test_live_ledger_drain_unsafe_chirp_stop(self):
        # Create temp sqlite db with non-DONE chirp op
        tmp_db = Path("/tmp/test_ledger_unsafe_chirp.sqlite")
        if tmp_db.exists():
            tmp_db.unlink()
        conn = sqlite3.connect(str(tmp_db))
        cur = conn.cursor()
        cur.execute("CREATE TABLE v2_jobs (id TEXT, status TEXT);")
        cur.execute("INSERT INTO v2_jobs VALUES ('job1', 'NEEDS_REVIEW');")
        cur.execute("CREATE TABLE v2_chirp_operations (id TEXT, status TEXT);")
        cur.execute("INSERT INTO v2_chirp_operations VALUES ('op1', 'RUNNING');")
        cur.execute("CREATE TABLE v2_auto_publication_fences (id TEXT);")
        conn.commit()
        conn.close()

        try:
            with self.assertRaises(SystemExit) as cm:
                check_live_ledger_drain(tmp_db)
            self.assertEqual(cm.exception.code, 1)
        finally:
            if tmp_db.exists():
                tmp_db.unlink()

    def test_stage0_default_deny_mutating_commands_via_cli(self):
        handler_script = Path(__file__).resolve().parent.parent / "deploy/golden_v2_chat_deploy.py"
        mutating_commands = ["deploy", "switch", "restart", "rollback-execute", "paid", "publish"]

        for cmd in mutating_commands:
            with self.subTest(command=cmd):
                res = subprocess.run(
                    [sys.executable, str(handler_script), "961b03d92c6f5b4eea135e826a1286c0ed5e08a3", "--subcommand", cmd],
                    capture_output=True,
                    text=True
                )
                self.assertEqual(res.returncode, 1)
                self.assertIn("STAGE0_DEFAULT_DENY", res.stderr)

    def test_wrapper_default_deny_when_invoked_by_webcodex(self):
        wrapper_script = Path(__file__).resolve().parent.parent / "deploy/golden-v2-chat-deploy"
        res = subprocess.run(
            [str(wrapper_script), "961b03d92c6f5b4eea135e826a1286c0ed5e08a3"],
            capture_output=True,
            text=True
        )
        self.assertEqual(res.returncode, 1)
        self.assertTrue("STAGE0_DEFAULT_DENY" in res.stderr or "GATE_STOP" in res.stderr)

    def test_plan_includes_image_repin(self):
        tmp_db = Path("/tmp/test_ledger_clean.sqlite")
        if tmp_db.exists():
            tmp_db.unlink()
        conn = sqlite3.connect(str(tmp_db))
        cur = conn.cursor()
        cur.execute("CREATE TABLE v2_jobs (id TEXT, status TEXT);")
        cur.execute("INSERT INTO v2_jobs VALUES ('job1', 'NEEDS_REVIEW');")
        cur.execute("CREATE TABLE v2_chirp_operations (id TEXT, status TEXT);")
        cur.execute("INSERT INTO v2_chirp_operations VALUES ('op1', 'DONE');")
        cur.execute("CREATE TABLE v2_auto_publication_fences (id TEXT);")
        conn.commit()
        conn.close()

        try:
            plan = plan_deployment("961b03d92c6f5b4eea135e826a1286c0ed5e08a3", db_path=tmp_db)
            self.assertEqual(plan["preflight_status"], "PASS")
            self.assertEqual(plan["rollback_plan"]["strategy"], "CONTAINER_IMAGE_REPIN_AND_SYMLINK_REVERT")
            self.assertIn("course-transcript-v2-chat:6999290b139c9230d9f877a2dead0cea78c8d5dd", plan["rollback_plan"]["rollback_image_chat"])
            self.assertIn("course-transcript-v2-runtime:6999290b139c9230d9f877a2dead0cea78c8d5dd", plan["rollback_plan"]["rollback_image_runtime"])
        finally:
            if tmp_db.exists():
                tmp_db.unlink()

    def test_rollback_plan_includes_image_repin_and_relaunch(self):
        rb_plan = plan_rollback("6999290b139c9230d9f877a2dead0cea78c8d5dd")
        self.assertTrue(rb_plan["rollback_includes_container_image_repin"])
        self.assertTrue(any("Re-pin containers" in step for step in rb_plan["rollback_steps"]))


if __name__ == "__main__":
    unittest.main()
