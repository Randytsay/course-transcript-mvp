"""Offline delivery re-entry gate: old owner grants must not bypass QA."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.jobs.golden_v2_ledger import V2Conflict
from scripts.golden_v2_delivery_daemon import publication_gate, read_publication_state


class DeliveryDaemonGateTests(unittest.TestCase):
    def test_readonly_precheck_does_not_take_worker_write_lock(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "ledger.sqlite"
            job_id = "v2p_" + "a" * 32
            with sqlite3.connect(database) as db:
                db.executescript(
                    "CREATE TABLE v2_jobs(id TEXT PRIMARY KEY,status TEXT,revision INTEGER);"
                    "CREATE TABLE v2_stages(job_id TEXT,name TEXT,state TEXT);"
                )
                db.execute("INSERT INTO v2_jobs VALUES (?,?,?)",
                           (job_id, "AWAITING_FINAL_REVIEW", 7))
                db.execute("INSERT INTO v2_stages VALUES (?,?,?)",
                           (job_id, "golden_qa", "COMPLETE"))
            writer = sqlite3.connect(database, isolation_level=None, timeout=0.2)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE v2_jobs SET revision=revision+1 WHERE id=?", (job_id,))
                # Reads must not conflict with the worker's reserved writer lock.
                state = read_publication_state(database, job_id)
                self.assertEqual(state["revision"], 7)
                self.assertIsNone(publication_gate(state))
            finally:
                writer.rollback()
                writer.close()

    def test_missing_job_is_not_implicitly_created(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "ledger.sqlite"
            with sqlite3.connect(database) as db:
                db.executescript(
                    "CREATE TABLE v2_jobs(id TEXT PRIMARY KEY,status TEXT,revision INTEGER);"
                    "CREATE TABLE v2_stages(job_id TEXT,name TEXT,state TEXT);"
                )
            with self.assertRaisesRegex(V2Conflict, "publication job not found"):
                read_publication_state(database, "v2p_" + "f" * 32)

    def test_needs_review_and_pending_qa_defer_without_drive_call(self):
        state = {'status': 'NEEDS_REVIEW', 'stages': [
            {'name': 'golden_qa', 'state': 'PENDING'}]}
        self.assertEqual(publication_gate(state), 'GOLDEN_QA_NOT_READY')

    def test_only_final_review_with_complete_qa_can_attempt_publish(self):
        self.assertEqual(publication_gate({'status': 'AWAITING_FINAL_REVIEW',
            'stages': [{'name': 'golden_qa', 'state': 'COMPLETE'}]}), None)
        self.assertEqual(publication_gate({'status': 'PROCESSING',
            'stages': [{'name': 'golden_qa', 'state': 'COMPLETE'}]}), 'GOLDEN_QA_NOT_READY')

    def test_missing_or_malformed_qa_stage_fails_closed(self):
        self.assertEqual(publication_gate({'status': 'AWAITING_FINAL_REVIEW', 'stages': []}),
                         'GOLDEN_QA_NOT_READY')


if __name__ == '__main__':
    unittest.main()
