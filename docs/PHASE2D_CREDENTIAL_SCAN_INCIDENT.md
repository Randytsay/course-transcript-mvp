# Phase 2D final evidence scan incident

## Incident

The 2026-08-06 Phase 2D production cutover replaced and validated all five exact-SHA services successfully. The final credential-evidence scan then ran GNU grep with an extended-regex pattern beginning with `-----BEGIN` but without an option terminator. GNU grep parsed the pattern as a command-line option, returned exit code 2, and the deployment safety trap rolled every service back successfully.

No provider calls, Drive mutations, production data changes, or cloudflared changes occurred. The rollback restored all five services and preserved the database backup, rollback image tags, and evidence directory.

## Root causes

The vulnerable form was equivalent to:

```bash
grep -ERic '-----BEGIN ...' "$EVIDENCE_ROOT"
```

Two independent defects existed:

1. a pattern beginning with `-` requires an option terminator such as `--`;
2. recursive `grep -c` against a directory emits one `path:count` record per file, not a single numeric total.

Therefore, adding only `--` prevents the option-parsing failure but still leaves output such as `/path/to/file:0`, which cannot safely be compared with the string `0`.

The original `|| true` also collapsed grep exit statuses and could not distinguish:

- a completed scan with no matches;
- a completed scan with credential markers;
- a scanner execution failure.

## Corrected safe retry entrypoint

The reviewed retry entrypoint is:

```bash
sudo bash scripts/deploy_release_safe.sh \
  --release-sha <approved-40-character-sha> \
  --dry-run
```

After a new dry-run passes, execute only with the same approved SHA and explicit confirmation:

```bash
sudo bash scripts/deploy_release_safe.sh \
  --release-sha <approved-40-character-sha> \
  --execute \
  --confirm-sha <approved-40-character-sha>
```

The safe entrypoint does not shim or weaken grep. It creates a temporary copy of the deployment script and refuses to continue unless the expected allowlist and vulnerable scanner blocks each occur exactly once. It then:

- expands the allowlist only to reviewed deployment-tool, test, CI, and incident-documentation paths;
- replaces the final recursive grep block with `scan_evidence_credentials.py`;
- syntax-checks the patched copy before execution;
- leaves the repository, live working tree, release source, containers, data, and credentials untouched.

The scanner follows no symlinks, prints only a numeric marker count, never prints matching content, and fails closed if its evidence root is missing or a regular file cannot be read.

## Dirty live working tree

The existing seven modified files under `/opt/course-transcript-source` were previously backed up and classified. They are not used to build or run the immutable release. A clean-working-tree requirement is therefore not a valid Phase 2D gate.

The correct policy is:

- record the working-tree status as evidence;
- do not checkout, reset, stash, clean, commit, or modify it;
- export deployment tools from `origin/main` into a separate tools directory;
- deploy only the Phase 2C exact-SHA images and release directory.

## Required pre-retry gates

1. Confirm all five current services are running on the restored rollback images.
2. Confirm active jobs and live leases are both zero.
3. Preserve all failed evidence directories and rollback artifacts.
4. Run `bash tests/test_deploy_release_safe.sh`.
5. Export all four reviewed tool files: the base script, library, safe entrypoint, and Python scanner.
6. Run a new full dry-run through `deploy_release_safe.sh`.
7. Confirm the new evidence reports a numeric credential marker count of zero.
8. Do not clean old images, rollback tags, backups, the live working tree, or prior evidence until observation is complete.

## Permanent follow-up

A future application release should incorporate the deterministic scanner directly into `scripts/deploy_release.sh`. At that point the exact compatibility patch in `deploy_release_safe.sh` should intentionally fail its occurrence checks and the wrapper can be retired in the same reviewed change.
