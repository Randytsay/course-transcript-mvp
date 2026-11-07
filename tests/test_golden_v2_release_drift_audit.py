"""Provider-free, DB-free tests for Golden V2 release drift evidence."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.golden_v2_release_drift_audit import audit_release


class GoldenV2ReleaseDriftAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release_sha = "c" * 40
        self.release = self.root / self.release_sha
        (self.release / "app").mkdir(parents=True)
        self.source = self.release / "app" / "worker.py"
        self.source.write_bytes(b"immutable test worker")
        self.manifest = {
            "observed_release_directory_sha": self.release_sha,
            "is_original_git_commit": False,
            "files": {"app/worker.py": hashlib.sha256(self.source.read_bytes()).hexdigest()},
        }
        self.runtime = {
            "release_sha": self.release_sha, "release_dir": str(self.release),
            "ignored_secret": "NEVER_SHOW_THIS_VALUE",
        }
        self.inspect = [{
            "Name": "/golden-v2-chat-api-1",
            "Image": "sha256:fakeimage",
            "Config": {
                "Labels": {"org.opencontainers.image.revision": self.release_sha},
                "Env": ["TOKEN=NEVER_SHOW_THIS_VALUE"],
            },
            "Mounts": [{"Destination": "/data", "Type": "bind", "RW": True}],
        }]

    def audit(self, **changes):
        values = dict(release_dir=self.release, manifest=self.manifest,
                      runtime=self.runtime, inspect=self.inspect,
                      api_release_sha=self.release_sha)
        values.update(changes)
        return audit_release(**values)

    def test_consistent_evidence_does_not_auto_approve_release(self):
        result = self.audit()
        self.assertEqual(result["files_checked"], 1)
        self.assertEqual(result["files_matched"], 1)
        self.assertEqual(result["status"], "INCOMPLETE_EVIDENCE")
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(any(x["severity"] == "BLOCKER" for x in result["findings"]))
        self.assertIn("ORIGINAL_GIT_PROVENANCE_NOT_ATTESTED",
                      {x["code"] for x in result["findings"]})
        self.assertNotIn("NEVER_SHOW_THIS_VALUE", repr(result))

    def test_report_detects_control_plane_and_image_split_brain(self):
        self.runtime["release_sha"] = "8" * 40
        self.runtime["release_dir"] = str(self.root / ("8" * 40))
        self.inspect[0]["Config"]["Labels"] = {}
        self.inspect[0]["Mounts"].append({
            "Destination": "/app", "Type": "bind", "RW": True,
        })
        result = self.audit(api_release_sha="8" * 40)
        self.assertEqual(result["status"], "NO_GO")
        codes = {x["code"] for x in result["findings"]}
        self.assertTrue({
            "CONTROLLED_RUNTIME_RELEASE_SHA_DRIFT",
            "CONTROLLED_RUNTIME_RELEASE_PATH_DRIFT",
            "API_REPORTED_RELEASE_SHA_DRIFT",
            "IMAGE_OCI_REVISION_MISMATCH_OR_MISSING",
            "APPLICATION_SOURCE_BIND_MOUNT",
        }.issubset(codes))

    def test_ro_source_bind_is_blocked_even_if_image_revision_matches(self):
        self.inspect[0]["Mounts"].append({
            "Destination": "/app", "Type": "bind", "RW": False,
        })
        result = self.audit()
        self.assertEqual(result["status"], "NO_GO")
        codes = {entry["code"] for entry in result["findings"]}
        self.assertIn("APPLICATION_SOURCE_BIND_MOUNT", codes)

    def test_non_object_manifest_fails_closed(self):
        result = self.audit(manifest=["untrusted"])
        self.assertEqual(result["status"], "NO_GO")
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["production_modified"])
        self.assertIn("INVALID_RECONSTRUCTION_MANIFEST",
                      {entry["code"] for entry in result["findings"]})

    def test_content_tamper_and_symlink_are_rejected(self):
        self.source.write_bytes(b"changed")
        result = self.audit()
        self.assertEqual(result["status"], "NO_GO")
        self.assertIn("RELEASE_SOURCE_HASH_MISMATCH",
                      {x["code"] for x in result["findings"]})
        self.source.unlink()
        external = self.root / "external"
        external.write_bytes(b"immutable test worker")
        self.source.symlink_to(external)
        result = self.audit()
        self.assertIn("MISSING_OR_UNSAFE_RELEASE_FILE",
                      {x["code"] for x in result["findings"]})

    def test_missing_evidence_and_invalid_manifest_are_not_silent_pass(self):
        result = self.audit(runtime=None, inspect=None, api_release_sha=None)
        self.assertEqual(result["status"], "INCOMPLETE_EVIDENCE")
        codes = {x["code"] for x in result["findings"]}
        self.assertIn("CONTROLLED_RUNTIME_CONFIG_NOT_CHECKED", codes)
        self.assertIn("CONTAINER_IMAGE_AND_MOUNTS_NOT_CHECKED", codes)
        self.assertIn("API_REPORTED_RELEASE_NOT_CHECKED", codes)
        manifest = {"observed_release_directory_sha": self.release_sha,
                    "files": {"../../outside": "0" * 64}}
        result = self.audit(manifest=manifest)
        self.assertEqual(result["status"], "NO_GO")
        self.assertIn("INVALID_MANIFEST_FILE_ENTRY",
                      {x["code"] for x in result["findings"]})


if __name__ == "__main__":
    unittest.main()
