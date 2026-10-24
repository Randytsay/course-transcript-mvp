"""VPS-side chat bridge. A private local config supplies scoped API tokens.

Call using WebCodex run_process with literal argv. Do not read/print the config
in chat. The private API exposes only non-paid source intake and status.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.golden_v2_owner_client import main as owner_main

_ALLOWED = {'service-status', 'drive-health', 'preview', 'create-batch', 'batch-status', 'jobs', 'job-status', 'paid-readiness', 'quote', 'quote-status', 'authorize-paid', 'review','review-overlaps','review-adjudication','authorize-publication','attest-ai-review','authorize-ai-publication','prepare-ai-review','prepare-ai-draft'}
_KEYS = {'COURSE_TRANSCRIPT_V2_OWNER_API_URL', 'COURSE_TRANSCRIPT_V2_STATUS_TOKEN', 'COURSE_TRANSCRIPT_V2_WRITE_TOKEN'}


def load_private_config(path: Path) -> dict[str, str]:
    if path.is_symlink(): raise ValueError('private regular configuration required')
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
        raise ValueError('private owner-only configuration required')
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'): continue
        key, separator, value = line.partition('=')
        if separator != '=' or key not in _KEYS or key in values or not value:
            raise ValueError('invalid private configuration')
        values[key] = value
    if set(values) != _KEYS: raise ValueError('incomplete private configuration')
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=Path(__file__).resolve().parent / 'private-client.env')
    parser.add_argument('action', choices=sorted(_ALLOWED))
    args, remaining = parser.parse_known_args()
    try:
        values = load_private_config(args.config)
    except (OSError, ValueError):
        parser.error('V2 chat connection is not configured; do not expose credential files')
    os.environ.update(values)
    sys.argv = [sys.argv[0], args.action, *remaining]
    return owner_main()


if __name__ == '__main__':
    raise SystemExit(main())
