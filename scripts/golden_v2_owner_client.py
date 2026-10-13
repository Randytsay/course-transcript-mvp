"""Trusted operator client for the shared V2 API. Tokens stay in server env.

No provider/Drive publication commands or automatic retries. This can be called
by a configured chat tool or a terminal; it is not itself a ChatGPT connector.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward owner authorization to another origin.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preview = sub.add_parser("preview"); preview.add_argument("link"); preview.add_argument("--recursive", action="store_true")
    create = sub.add_parser("create-batch")
    create_input = create.add_mutually_exclusive_group(required=True)
    create_input.add_argument("--request-file", type=Path)
    create_input.add_argument("--request-stdin", action="store_true")
    status = sub.add_parser("batch-status"); status.add_argument("batch_id")
    sub.add_parser("jobs")
    sub.add_parser("service-status")
    sub.add_parser("drive-health")
    job = sub.add_parser("job-status"); job.add_argument("job_id")
    approve = sub.add_parser("approve-job"); approve.add_argument("job_id"); approve.add_argument("--request-file", type=Path, required=True)
    args = parser.parse_args()
    base = os.environ.get("COURSE_TRANSCRIPT_V2_OWNER_API_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        url = urlsplit(base)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1", "api"})):
            raise ValueError
    except ValueError:
        parser.error("Owner API requires an HTTPS origin or private loopback/container origin")
    write = args.action in {"create-batch", "approve-job"}
    token = os.environ.get("COURSE_TRANSCRIPT_V2_WRITE_TOKEN" if write else "COURSE_TRANSCRIPT_V2_STATUS_TOKEN", "")
    if len(token) < 32:
        parser.error("Required scoped owner token is not configured")
    body = None
    if args.action == "preview":
        route = "link-preview"; body = {"link": args.link, "recursive": args.recursive}
    elif args.action == "create-batch": route = "link-batches"
    elif args.action == "batch-status":
        if not re.fullmatch(r"v2b_[0-9a-f]{32}", args.batch_id): parser.error("Invalid batch ID")
        route = "link-batches/" + args.batch_id
    elif args.action == "jobs": route = "jobs"
    elif args.action == "service-status": route = "service"
    elif args.action == "drive-health": route = "drive-health"
    else:
        if not re.fullmatch(r"v2p?_[0-9a-f]{32}", args.job_id): parser.error("Invalid job ID")
        route = "jobs/" + args.job_id + ("/approval" if write else "")
    if write:
        try:
            if getattr(args, "request_stdin", False):
                raw = sys.stdin.buffer.read(65537)
                if len(raw) > 65536: raise ValueError
                body = json.loads(raw)
            else:
                if args.request_file.stat().st_size > 65536: raise ValueError
                body = json.loads(args.request_file.read_text())
            if not isinstance(body, dict) or body.get("confirm") is not True: raise ValueError
        except (OSError, ValueError): parser.error("Confirmed bounded JSON request file required")
    request = Request(base + "/api/v1/golden-v2/" + route,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                      method="POST" if body is not None else "GET")
    try:
        with build_opener(NoRedirect()).open(request, timeout=180) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024: raise ValueError("oversized response")
            payload = json.loads(raw)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except HTTPError as exc:
        print(json.dumps({"error": "OWNER_API_REJECTED", "status": exc.code,
                          "next_action": "QUERY_STATUS_BEFORE_RETRY"}), file=sys.stderr)
    except (URLError, OSError, ValueError):
        print(json.dumps({"error": "OWNER_API_UNAVAILABLE", "next_action": "QUERY_STATUS_BEFORE_RETRY"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
