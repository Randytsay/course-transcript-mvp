# Golden V2 release drift: provider-free predeployment audit

This is a **read-only, offline evidence collector**, not a deployment implementation, an owner approval, or a remedy for a mismatched runtime.

## Why it exists

Independent read-only Docker inspection and AGY-LOCAL-001 acceptance established a **split runtime**: the API container uses a baked `82e0cd45...` image (no `/app` bind) on `127.0.0.1:8816`; Intake, Worker and Delivery use a different image with OCI revision `a18fd131...` and a **read-only (`RW=false`) `/app` bind** from the host `cdf9e73...` release. The API and background services were created from different Compose configurations even though they share a Compose project. The controlled `runtime.json` and API health report the old `82e0cd45...` version. Earlier statements that `/app` is writable or the OCI revision is missing were disproven and must not be repeated. The reconstructed PR #166 17-file manifest verifies file bytes only, **not** the original Git commit ancestry, whole image identity, or deploy authorization.

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

The tool returns `NO_GO` on a specific identity/file/mount blocker or `INCOMPLETE_EVIDENCE` when original commit provenance, restore or other observations remain unverified. A read-only source bind is a blocker too. A manifest that is not a JSON object is rejected. The tool never repairs the observed drift. **It never returns a deployment GO. **Its CLI exits with status 2 for BOTH `NO_GO` and `INCOMPLETE_EVIDENCE`**, so automated release pipelines cannot interpret incomplete evidence as a successful deployment gate; machine-readable JSON distinguishes the reason.**

## Separate required engineering (not delivered by this PR)

- Reconcile the *actual* running `docker-compose.yml`, host release contents and WebCodex `entry.py` /`runtime.json` path. Updating the already cleaner but unused `deploy/golden-v2-chat-compose.yml` alone does **not** repair Production.
- Build a **new immutable V2 image** with an independently verifiable Git SHA and OCI revision label; remove the live `/app` source bind mount. A `RELEASE_SHA` file inside a writable bind mount is **not** strong source attestation.
- Ensure the release API does not merely echo the environment release SHA when it differs from verified running code. Add fail-closed **mutation** guards and operator observability, with independently reviewed deployment/rollback tests.
- Exercise WAL-safe backup and **isolated cold restore**, then separately reconcile external provider charges and stale claims.
- Do not roll back live `cdf9e73` to `82e0cd45` on the basis of the current report, and do not touch Production before a scope-specific owner approval.

References: Issues #160, #170; PRs #166, #168, #171.

## Release extraction scope (2026-11-07)

This script, its synthetic tests, the existing 17-file manifest and this runbook were **extracted alone** from the heavily divergent Draft PR #172 onto current `main`. This is a developer-only reviewable slice; it does **not** include the large PR stack's paid-worker implementation, V2 Ledger migrations, API images or Compose deployment. Production cannot be promoted using this tool. The true current image/source/port/mount topology must be independently reobserved before any release authorization. See [go-live master plan](GOLDEN_V2_GO_LIVE_MASTER_PLAN.md) and [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173).
