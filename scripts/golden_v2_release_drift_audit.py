"""Offline/read-only Golden V2 release identity audit.

This tool only reads source bytes and pre-existing, *redacted* runtime evidence.
It never calls Docker, cloud APIs, SQLite, Drive, or modifies Production.
Matching identifiers are NOT proof of an authorized or deployable release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_release(
    *, release_dir: Path, manifest: dict, runtime: dict | None = None,
    inspect: list[dict] | None = None, api_release_sha: str | None = None,
) -> dict:
    """Return deterministic findings, without trusting labels as provenance."""
    root = Path(release_dir)
    findings: list[dict] = []

    def finding(code: str, *, severity: str = "BLOCKER", detail: str = "") -> None:
        findings.append({"code": code, "severity": severity, "detail": detail})

    observed_sha = root.name
    if not isinstance(manifest, dict):
        finding("INVALID_RECONSTRUCTION_MANIFEST")
        return {"status": "NO_GO", "observed_release_dir_sha": observed_sha,
                "files_checked": 0, "files_matched": 0,
                "findings": findings, "deployment_authorized": False,
                "production_modified": False}
    if not _SHA.fullmatch(observed_sha):
        finding("INVALID_RELEASE_DIRECTORY_SHA")
    if not root.is_dir() or root.is_symlink():
        finding("RELEASE_DIRECTORY_MISSING_OR_SYMLINK")
        return {"status": "NO_GO", "observed_release_dir_sha": observed_sha,
                "files_checked": 0, "files_matched": 0, "findings": findings,
                "deployment_authorized": False, "production_modified": False}

    expected_sha = manifest.get("observed_release_directory_sha")
    if expected_sha != observed_sha:
        finding("RECONSTRUCTION_MANIFEST_RELEASE_ID_MISMATCH")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        finding("MISSING_RECONSTRUCTION_FILE_HASHES")
        expected = {}

    files_checked = 0
    files_matched = 0
    for relative, digest in sorted(expected.items()):
        if (not isinstance(relative, str) or not relative
                or Path(relative).is_absolute() or ".." in Path(relative).parts
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            finding("INVALID_MANIFEST_FILE_ENTRY", detail=str(relative)[:160])
            continue
        path = root / relative
        if (not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or any(node.is_symlink() for node in path.parents if node != root and
                       node.is_relative_to(root))):
            finding("MISSING_OR_UNSAFE_RELEASE_FILE", detail=relative)
            continue
        files_checked += 1
        if _sha256(path) != digest:
            finding("RELEASE_SOURCE_HASH_MISMATCH", detail=relative)
        else:
            files_matched += 1

    if runtime is None:
        finding("CONTROLLED_RUNTIME_CONFIG_NOT_CHECKED", severity="UNKNOWN")
    elif not isinstance(runtime, dict):
        finding("INVALID_CONTROLLED_RUNTIME_CONFIG")
    else:
        config_sha = runtime.get("release_sha")
        config_dir = runtime.get("release_dir")
        if config_sha != observed_sha:
            finding("CONTROLLED_RUNTIME_RELEASE_SHA_DRIFT")
        if (not isinstance(config_dir, str)
                or Path(config_dir).resolve() != root.resolve()):
            finding("CONTROLLED_RUNTIME_RELEASE_PATH_DRIFT")

    if api_release_sha is None:
        finding("API_REPORTED_RELEASE_NOT_CHECKED", severity="UNKNOWN")
    elif api_release_sha != observed_sha:
        finding("API_REPORTED_RELEASE_SHA_DRIFT")

    if inspect is None:
        finding("CONTAINER_IMAGE_AND_MOUNTS_NOT_CHECKED", severity="UNKNOWN")
    elif not isinstance(inspect, list) or not inspect:
        finding("INVALID_DOCKER_INSPECT_EVIDENCE")
    else:
        for obj in inspect:
            if not isinstance(obj, dict):
                finding("INVALID_DOCKER_INSPECT_ENTRY")
                continue
            name = str(obj.get("Name", ""))[:100]
            config = obj.get("Config") or {}
            labels = config.get("Labels") or {} if isinstance(config, dict) else {}
            revision = labels.get("org.opencontainers.image.revision") if isinstance(labels, dict) else None
            if revision != observed_sha:
                finding("IMAGE_OCI_REVISION_MISMATCH_OR_MISSING", detail=name)
            mounts = obj.get("Mounts")
            if not isinstance(mounts, list):
                finding("CONTAINER_MOUNT_EVIDENCE_MISSING", detail=name)
                continue
            if any(isinstance(m, dict) and m.get("Destination") == "/app"
                   and m.get("Type") == "bind" for m in mounts):
                finding("APPLICATION_SOURCE_BIND_MOUNT", detail=name)
            elif any(isinstance(m, dict) and m.get("Destination") == "/app"
                     and m.get("RW") is True for m in mounts):
                finding("APPLICATION_SOURCE_WRITABLE_MOUNT", detail=name)

    # An orphan directory name or a reconstructed commit does not prove
    # original Git ancestry or operator approval, even if every file matches.
    finding("ORIGINAL_GIT_PROVENANCE_NOT_ATTESTED", severity="UNKNOWN")
    finding("COLD_RESTORE_AND_ROLLBACK_NOT_ATTESTED", severity="UNKNOWN")
    blocker = any(x["severity"] == "BLOCKER" for x in findings)
    unknown = any(x["severity"] == "UNKNOWN" for x in findings)
    return {
        "schema": "golden-v2-release-drift-offline-v1",
        "status": "NO_GO" if blocker else "INCOMPLETE_EVIDENCE" if unknown
                  else "REVIEW_ONLY_NOT_RELEASE_APPROVAL",
        "observed_release_dir_sha": observed_sha,
        "files_checked": files_checked,
        "files_matched": files_matched,
        "findings": findings,
        "deployment_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-json", type=Path)
    parser.add_argument("--docker-inspect-json", type=Path,
                        help="Optional operator-supplied inspect JSON; do not commit raw inspect files")
    parser.add_argument("--api-release-sha", help="Exact observed SHA from authorized read-only status")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        runtime = json.loads(args.runtime_json.read_text(encoding="utf-8")) if args.runtime_json else None
        inspect = json.loads(args.docker_inspect_json.read_text(encoding="utf-8")) if args.docker_inspect_json else None
        result = audit_release(release_dir=args.release_dir, manifest=manifest,
                               runtime=runtime, inspect=inspect,
                               api_release_sha=args.api_release_sha)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        # Never print environment, tokens, or entire untrusted JSON/paths.
        print(json.dumps({"status": "NO_GO", "error_type": type(exc).__name__,
                          "deployment_authorized": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    # A diagnostic report is never an affirmative deployment gate. Even when
    # source bytes match, original Git provenance and cold restore remain UNKNOWN.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
