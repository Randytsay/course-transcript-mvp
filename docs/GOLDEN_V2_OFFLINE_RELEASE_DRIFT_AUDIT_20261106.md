# Golden V2 release drift: provider-free predeployment audit

This is a **read-only, offline evidence collector**, not a deployment implementation, an owner approval, or a remedy for a mismatched runtime.

## Why it exists

The operator's 2026-11-06 read-only report places the running four containers at `/opt/course-transcript-v2-chat/releases/cdf9e73dccd3a2b2c28d43c158a04b6f1d814f76`, while the WebCodex controlled `runtime.json` and `/service` show `82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0`. The running Compose reportedly binds the release tree RW at `/app` and the OCI revision label is missing. The reconstructed PR #166 manifest is **not** proof of the original Git commit's ancestry.

## Safe offline invocation

In an authorized, isolated operator environment with read access to the **unchanged** Production release bytes, use:

```bash
python3 scripts/golden_v2_release_drift_audit.py \
  --release-dir /opt/course-transcript-v2-chat/releases/cdf9e73dccd3a2b2c28d43c158a04b6f1d814f76 \
  --manifest docs/GOLDEN_V2_CDF9E73_REVIEW_MANIFEST_20261102.json \
  --runtime-json /srv/ai-workspace/projects/golden-v2-controlled-commands/golden-v2-chat/runtime.json \
  --api-release-sha 82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0
```

This verifies the manifest's **17 named paths**, not every file in the live container image, and compares reported release identities. It never executes `docker`, `git`, a database write, a model call or a Drive operation. Optional `--docker-inspect-json` accepts a local JSON array from an authorized operator; only `Name`, `Config.Labels`, and `Mounts` are examined. **Never publish raw Docker inspect JSON, container environment values, tokens or secrets in GitHub.**

The tool returns `NO_GO` on a specific identity/file/mount blocker or `INCOMPLETE_EVIDENCE` when original commit provenance, restore or other observations remain unverified. **It never returns a deployment GO.**

## Separate required engineering (not delivered by this PR)

- Reconcile the *actual* running `docker-compose.yml`, host release contents and WebCodex `entry.py` /`runtime.json` path. Updating the already cleaner but unused `deploy/golden-v2-chat-compose.yml` alone does **not** repair Production.
- Build a **new immutable V2 image** with an independently verifiable Git SHA and OCI revision label; remove the live `/app` source bind mount. A `RELEASE_SHA` file inside a writable bind mount is **not** strong source attestation.
- Ensure the release API does not merely echo the environment release SHA when it differs from verified running code. Add fail-closed **mutation** guards and operator observability, with independently reviewed deployment/rollback tests.
- Exercise WAL-safe backup and **isolated cold restore**, then separately reconcile external provider charges and stale claims.
- Do not roll back live `cdf9e73` to `82e0cd45` on the basis of the current report, and do not touch Production before a scope-specific owner approval.

References: Issues #160, #170; PRs #166, #168, #171.
