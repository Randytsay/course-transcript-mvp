# Phase 2D final evidence scan incident

## Incident

The 2026-08-06 Phase 2D production cutover replaced and validated all five exact-SHA services successfully. The final credential-evidence scan then ran GNU grep with an extended-regex pattern beginning with `-----BEGIN` but without an option terminator. GNU grep parsed the pattern as a command-line option, returned exit code 2, and the deployment safety trap rolled every service back successfully.

No provider calls, Drive mutations, production data changes, or cloudflared changes occurred. The rollback restored all five services and preserved the database backup, rollback image tags, and evidence directory.

## Root cause

The vulnerable form was equivalent to:

```bash
grep -ERic '-----BEGIN ...' "$EVIDENCE_ROOT"
```

The correct GNU grep form is:

```bash
grep -ERic -- '-----BEGIN ...' "$EVIDENCE_ROOT"
```

The existing `|| true` also collapses grep exit statuses. A permanent implementation should explicitly distinguish:

- exit 0: one or more credential markers found;
- exit 1: scan completed with no matches;
- exit 2 or other: scanner execution failure.

Scanner failure must remain a deployment failure, but its reason must be recorded as `credential_scan_error`, not as credential leakage.

## Safe retry entrypoint

Until the inline scanner block is replaced, invoke Phase 2D through:

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

The wrapper prepends `--` only for the exact Phase 2D credential scan invocation. Every other grep invocation is passed through unchanged. It does not weaken the evidence scan, bypass rollback, alter containers, or suppress a genuine credential match.

## Required pre-retry gates

1. Confirm all five current services are running on the restored rollback images.
2. Confirm active jobs and live leases are both zero.
3. Preserve the failed evidence directory and rollback artifacts.
4. Run `bash tests/test_deploy_release_safe.sh`.
5. Run the full Phase 2D dry-run through `deploy_release_safe.sh`.
6. Review the new evidence directory and confirm the credential marker count is numeric zero.
7. Do not clean old images, rollback tags, backups, or prior evidence until observation is complete.

## Permanent follow-up

Replace the inline command substitution in `scripts/deploy_release.sh` with a function that captures grep output and exit status separately. Add CI coverage for clean evidence, real markers, unreadable evidence, and dash-prefixed patterns. Once that implementation is merged and deployed, retire the compatibility wrapper.
