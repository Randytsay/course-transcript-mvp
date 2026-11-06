"""No-provider/no-host deterministic tests for local Chirp Ledger comparison."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.golden_v2_chirp_ledger_snapshot_diff import (
    EvidenceUnavailable, compare_snapshots, inspect_snapshot,
)

JOB = 'v2p_' + 'c' * 32
SOURCE = 's' * 64


def make_db(path: Path, *, count: int = 16, cents: int = 78, revision: int = 5,
            source: str = SOURCE, unnamed: bool = False, extra_result: bool = False):
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE v2_jobs (
            id TEXT PRIMARY KEY, source_sha TEXT, revision INTEGER, status TEXT,
            approved_cents INTEGER);
          CREATE TABLE v2_stages (
            job_id TEXT, name TEXT, state TEXT, actual_cents INTEGER);
          CREATE TABLE v2_chirp_operations (
            job_id TEXT, window_id TEXT, source_sha TEXT, operation_name TEXT,
            status TEXT, actual_cents INTEGER);
        """)
        db.execute('INSERT INTO v2_jobs VALUES (?, ?, ?, ?, ?)',
                   (JOB, source, revision, 'NEEDS_REVIEW', 600))
        db.execute('INSERT INTO v2_stages VALUES (?, ?, ?, ?)',
                   (JOB, 'chirp_independent', 'REVIEW_REQUIRED', cents))
        amounts = [cents // count] * count
        for n in range(cents % count):
            amounts[n] += 1
        for n, amount in enumerate(amounts):
            db.execute('INSERT INTO v2_chirp_operations VALUES (?, ?, ?, ?, ?, ?)',
                       (JOB, f'window_{n}', source,
                        None if unnamed and n == count - 1 else f'provider_operation_{n}',
                        'DONE', amount))
        if extra_result:
            db.execute('INSERT INTO v2_chirp_operations VALUES (?, ?, ?, ?, ?, ?)',
                       (JOB, 'other_window', source, 'provider_operation_0', 'DONE', 0))


class ChirpLedgerSnapshotDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.current = self.root / 'current.sqlite'
        self.old = self.root / 'previous.sqlite'

    def test_16_vs_34_does_not_silently_reconcile(self):
        make_db(self.current, count=16, cents=78, revision=5)
        make_db(self.old, count=34, cents=136, revision=4)
        result = compare_snapshots(self.current, self.old, JOB)
        self.assertEqual(result['status'], 'LOCAL_EVIDENCE_DIFFERS')
        self.assertEqual(result['current']['operation_count'], 16)
        self.assertEqual(result['previous']['operation_count'], 34)
        self.assertEqual(result['comparison']['named_operations_common'], 16)
        self.assertEqual(result['comparison']['named_only_previous'], 18)
        self.assertEqual(result['current']['operation_cost_known_cents'], 78)
        self.assertEqual(result['previous']['operation_cost_known_cents'], 136)
        self.assertEqual(result['external_gcp_billing'], 'UNKNOWN')
        self.assertFalse(result['provider_resubmission_authorized'])
        self.assertNotIn('provider_operation_', repr(result))

    def test_identical_snapshots_do_not_claim_cloud_reconciliation(self):
        make_db(self.current)
        make_db(self.old)
        result = compare_snapshots(self.current, self.old, JOB)
        self.assertEqual(result['status'], 'LOCAL_EVIDENCE_MATCHES_NOT_CLOUD_VERIFIED')
        self.assertEqual(result['comparison']['differences'], [])
        self.assertFalse(result['comparison']['snapshots_share_one_transaction'])
        self.assertEqual(result['current']['snapshot_transaction'],
                         'EXPLICIT_READ_TRANSACTION')

    def test_source_drift_is_explicit(self):
        make_db(self.current)
        make_db(self.old, source='q' * 64)
        result = compare_snapshots(self.current, self.old, JOB)
        self.assertFalse(result['comparison']['same_source_sha'])
        self.assertIn('SOURCE_SHA_DIFFERS', result['comparison']['differences'])

    def test_missing_or_duplicate_names_are_not_proven_complete(self):
        make_db(self.current, unnamed=True)
        make_db(self.old, extra_result=True)
        result = compare_snapshots(self.current, self.old, JOB)
        self.assertFalse(result['comparison']['named_operation_coverage_complete'])
        self.assertIn('UNRESOLVED_OR_DUPLICATE_OPERATION_NAMES',
                      result['comparison']['differences'])

    def test_nonexistent_or_symlink_or_sidecar_rejected(self):
        make_db(self.current)
        make_db(self.old)
        with self.assertRaises(EvidenceUnavailable):
            inspect_snapshot(self.root / 'missing.sqlite', JOB, label='missing')
        symlink = self.root / 'symlink.sqlite'
        symlink.symlink_to(self.current)
        with self.assertRaises(EvidenceUnavailable):
            inspect_snapshot(symlink, JOB, label='symlink')
        (self.root / 'current.sqlite-wal').write_bytes(b'dummy')
        with self.assertRaises(EvidenceUnavailable):
            inspect_snapshot(self.current, JOB, label='sidecar')

    def test_same_db_cannot_be_used_as_its_own_historical_evidence(self):
        make_db(self.current)
        with self.assertRaises(EvidenceUnavailable):
            compare_snapshots(self.current, self.current, JOB)

    def test_job_id_invalid_refuses(self):
        make_db(self.current)
        with self.assertRaises(EvidenceUnavailable):
            inspect_snapshot(self.current, '../wrong', label='bad')


if __name__ == '__main__':
    unittest.main()
