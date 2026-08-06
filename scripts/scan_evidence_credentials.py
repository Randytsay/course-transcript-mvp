#!/usr/bin/env python3
"""Count raw credential markers in a deployment evidence directory.

The scanner never prints matched content. It follows no symlinks and returns a
non-zero exit code when any regular file cannot be read.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(rb'"refresh_token"\s*:', re.IGNORECASE),
    re.compile(rb'"client_secret"\s*:', re.IGNORECASE),
    re.compile(rb"Authorization:\s*Bearer", re.IGNORECASE),
    re.compile(rb"Cf-Access-Jwt-Assertion", re.IGNORECASE),
)


def scan(root: Path) -> int:
    if not root.is_dir():
        raise ValueError(f"evidence root is not a directory: {root}")

    marker_count = 0
    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if not (current / name).is_symlink()
        ]

        for name in file_names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue

            try:
                payload = path.read_bytes()
            except OSError as exc:
                print(
                    f"SCAN_ERROR path={path} error={exc.__class__.__name__}",
                    file=sys.stderr,
                )
                raise

            marker_count += sum(
                len(pattern.findall(payload))
                for pattern in PATTERNS
            )

    return marker_count


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: scan_evidence_credentials.py <evidence-root>",
            file=sys.stderr,
        )
        return 2

    try:
        count = scan(Path(argv[1]))
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            print(f"SCAN_ERROR {exc}", file=sys.stderr)
        return 2

    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
