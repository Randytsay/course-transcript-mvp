"""Batch completed count must exclude jobs awaiting human/Golden QA review."""
import sqlite3
import unittest

from app.jobs.completion import refresh_batch_state


class BatchReviewCountTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript("""
            CREATE TABLE batches (
                id TEXT PRIMARY KEY, status TEXT, completed_count INTEGER,
                failed_count INTEGER, updated_at TEXT, revision INTEGER DEFAULT 0
            );
            CREATE TABLE jobs (
                id TEXT, batch_id TEXT, status TEXT, queue_position INTEGER
            );
            INSERT INTO batches (id, status, completed_count, failed_count)
            VALUES ('b', 'processing', 9, 0);
            INSERT INTO jobs VALUES ('1', 'b', 'awaiting_review', 0);
            INSERT INTO jobs VALUES ('2', 'b', 'awaiting_review', 1);
        """)

    def tearDown(self):
        self.con.close()

    def test_awaiting_review_never_counts_as_completed(self):
        refresh_batch_state(self.con, "b", "2026-10-10T12:00:00Z")
        status, count = self.con.execute("SELECT status, completed_count FROM batches WHERE id='b'").fetchone()
        self.assertEqual((status, count), ("awaiting_review", 0))

    def test_only_explicitly_completed_jobs_count(self):
        self.con.execute("UPDATE jobs SET status='completed' WHERE id='1'")
        refresh_batch_state(self.con, "b", "2026-10-10T12:00:01Z")
        status, count = self.con.execute("SELECT status, completed_count FROM batches WHERE id='b'").fetchone()
        self.assertEqual((status, count), ("awaiting_review", 1))

    def test_all_completed_is_complete(self):
        self.con.execute("UPDATE jobs SET status='completed'")
        refresh_batch_state(self.con, "b", "2026-10-10T12:00:02Z")
        status, count = self.con.execute("SELECT status, completed_count FROM batches WHERE id='b'").fetchone()
        self.assertEqual((status, count), ("completed", 2))


if __name__ == "__main__":
    unittest.main()