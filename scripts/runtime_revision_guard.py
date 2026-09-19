#!/usr/bin/env python3
"""Fail closed when the live production services do not share one Git revision."""
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SERVICES = {
    "api": "course-transcript-source-api-1",
    "worker": "course-transcript-source-worker-1",
    "pipeline-worker": "course-transcript-source-pipeline-worker-1",
    "delivery-worker": "course-transcript-source-delivery-worker-1",
    "health-monitor": "course-transcript-source-health-monitor-1",
    "retention-monitor": "course-transcript-source-retention-monitor-1",
    "frontend": "course-transcript-source-frontend-1",
}


@dataclass(frozen=True)
class RuntimeRevision:
    service: str
    container: str
    state: str
    revision: str


def validate_runtime_revisions(
    rows: list[RuntimeRevision],
    *,
    expected_sha: str | None = None,
) -> str:
    if not rows:
        raise ValueError("no runtime services were inspected")
    if expected_sha is not None and not SHA_RE.fullmatch(expected_sha):
        raise ValueError("expected SHA must be exactly 40 lowercase hexadecimal characters")

    expected_services = set(SERVICES)
    actual_services = {row.service for row in rows}
    missing = sorted(expected_services - actual_services)
    extra = sorted(actual_services - expected_services)
    if missing or extra:
        raise ValueError(f"runtime service set mismatch: missing={missing} extra={extra}")

    bad_states = [row.service for row in rows if row.state != "running"]
    if bad_states:
        raise ValueError("services are not running: " + ",".join(sorted(bad_states)))

    invalid = [
        f"{row.service}={row.revision or '<missing>'}"
        for row in rows
        if not SHA_RE.fullmatch(row.revision)
    ]
    if invalid:
        raise ValueError("invalid or unlabelled runtime revisions: " + ",".join(invalid))

    revisions = {row.revision for row in rows}
    if len(revisions) != 1:
        details = ",".join(f"{row.service}={row.revision}" for row in rows)
        raise ValueError("mixed runtime revisions: " + details)

    revision = next(iter(revisions))
    if expected_sha is not None and revision != expected_sha:
        raise ValueError(f"runtime revision {revision} does not match expected {expected_sha}")
    return revision


def inspect_runtime() -> list[RuntimeRevision]:
    rows: list[RuntimeRevision] = []
    for service, container in SERVICES.items():
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{.State.Status}}|{{index .Config.Labels "org.opencontainers.image.revision"}}',
                container,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cannot inspect runtime container for {service}")
        state, separator, revision = result.stdout.strip().partition("|")
        if not separator:
            raise RuntimeError(f"invalid docker inspect output for {service}")
        rows.append(
            RuntimeRevision(
                service=service,
                container=container,
                state=state.strip(),
                revision=revision.strip(),
            )
        )
    return rows


def self_test() -> None:
    sha = "a" * 40
    healthy = [
        RuntimeRevision(name, container, "running", sha)
        for name, container in SERVICES.items()
    ]
    assert validate_runtime_revisions(healthy) == sha
    assert validate_runtime_revisions(healthy, expected_sha=sha) == sha

    mixed = list(healthy)
    mixed[-1] = RuntimeRevision(
        mixed[-1].service, mixed[-1].container, "running", "b" * 40
    )
    try:
        validate_runtime_revisions(mixed)
    except ValueError as exc:
        assert "mixed runtime revisions" in str(exc)
    else:
        raise AssertionError("mixed runtime was accepted")

    unlabelled = list(healthy)
    unlabelled[0] = RuntimeRevision(
        unlabelled[0].service, unlabelled[0].container, "running", "unknown"
    )
    try:
        validate_runtime_revisions(unlabelled)
    except ValueError as exc:
        assert "invalid or unlabelled" in str(exc)
    else:
        raise AssertionError("unlabelled runtime was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("RUNTIME_REVISION_GUARD_SELF_TEST=PASS")
        return 0

    try:
        revision = validate_runtime_revisions(
            inspect_runtime(), expected_sha=args.expected_sha
        )
    except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"RUNTIME_REVISION_GUARD=FAIL reason={exc}")
        return 1

    print(f"RUNTIME_REVISION_GUARD=PASS revision={revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
