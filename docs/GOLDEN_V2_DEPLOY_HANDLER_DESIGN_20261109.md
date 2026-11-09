# Golden V2 Governed Deployment & Rollback Handler Design

**Document ID:** `GOLDEN-V2-DEPLOY-HANDLER-DESIGN-20261109`  
**Target Release:** `961b03d92c6f5b4eea135e826a1286c0ed5e08a3`  
**Base Release:** `6999290b139c9230d9f877a2dead0cea78c8d5dd`  
**Target Project:** `golden-v2-chat` (Independent Golden V2 stack)  
**Governance Scope:** Stage-0 Deploy Preparation Only (Default-Deny)

---

## 1. Executive Summary & Design Scope

Following the Owner explicit authorization for **Stage-0 Preparation Only** on 2026-11-09 (Asia/Taipei), this design document establishes a hardened, root-governed deployment and failure-atomic rollback mechanism specifically tailored for the Golden V2 architecture.

This mechanism resolves the governance gap identified in AGY-LOCAL-009 where `/etc/webcodex-deploy/projects/golden-v2-chat.conf` lacked a registered handler (`HANDLER`).

### Core Safety Principles:
1. **Strict V1 Isolation & Prohibition of V1 Re-use:**
   - The production V1 project (`course-transcript-source` at `/opt/course-transcript-source`) is completely independent.
   - The V1 deployment handler (`/usr/local/sbin/course-transcript-deploy`) and project name `course-transcript` are strictly blocked from interacting with Golden V2.
   - Any attempt to target V1 paths (`/opt/course-transcript-source`, `/opt/course-transcript-releases`, `/opt/course-transcript-backups`) triggers an immediate fail-closed `GATE_STOP`.

2. **Stage-0 Default-Deny Governance:**
   - Mutating subcommands (`deploy`, `switch`, `restart`, `rollback-execute`, `paid`, `publish`) are fail-closed in Stage-0.
   - Any direct invocation from `webcodex-deploy` will perform read-only preflight and exit with code 1 (`STAGE0_DEFAULT_DENY`), requiring a separate explicit Owner Gate-A live deployment authorization.

3. **Preflight Gates & Drain Requirements:**
   - **Exact 40-character commit SHA:** Must be validated against `origin/main`.
   - **Root Disk Capacity Floor:** Strictly enforces `>= 8.0 GiB` available space at all times.
   - **Zero In-Flight Operations:** Queries live `/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite` (read-only) ensuring 0 active leases, 0 non-DONE Chirp operations, and 0 pending auto-publication fences.
   - **Small WAL-Safe Backup:** Performs transactional online SQLite backup via `sqlite3.Connection.backup()` prior to any staging.

4. **Failure-Atomic Rollback with Container Image Re-Pin:**
   - Switching the `/opt/course-transcript-v2-chat/current` symlink alone is insufficient because running container processes keep their existing image in memory.
   - The rollback procedure explicitly re-pins and restarts containers with the exact prior OCI image digests:
     - `course-transcript-v2-chat:6999290b139c9230d9f877a2dead0cea78c8d5dd`
     - `course-transcript-v2-runtime:6999290b139c9230d9f877a2dead0cea78c8d5dd`

---

## 2. Architecture & File Layout

### 2.1 File Placement
- **Production Staging & Code Repository:**
  - `deploy/golden_v2_chat_deploy.py`: Core logic with CLI options (`--subcommand plan|verify|rollback-plan|deploy`).
  - `deploy/golden-v2-chat-deploy`: Root-executable wrapper invoked by `/usr/local/sbin/webcodex-deploy`.
  - `tests/test_golden_v2_deploy_handler_negative.py`: Synthetic negative tests suite.
- **Host System Configuration:**
  - `/etc/webcodex-deploy/projects/golden-v2-chat.conf`:
    ```bash
    # Golden V2 Deployment Configuration
    LIVE_ROOT="/opt/course-transcript-v2-chat"
    RELEASES_ROOT="${LIVE_ROOT}/releases"
    DATA_ROOT="${LIVE_ROOT}/data"
    HANDLER="/usr/local/sbin/golden-v2-chat-deploy"
    ```
  - `/usr/local/sbin/golden-v2-chat-deploy`: Root-owned (`root:root`, mode `0755`) wrapper that defaults to deny for any mutation.

### 2.2 Container Allowlist
Only the five official Golden V2 Compose services are recognized and managed:
1. `golden-v2-chat-api-1` (`course-transcript-v2-chat:<SHA>`, port `8816`)
2. `golden-v2-chat-golden-v2-intake-1` (`course-transcript-v2-chat:<SHA>`)
3. `golden-v2-chat-golden-v2-worker-1` (`course-transcript-v2-chat:<SHA>`)
4. `golden-v2-chat-golden-v2-paid-1` (`course-transcript-v2-runtime:<SHA>`)
5. `golden-v2-chat-golden-v2-delivery-1` (`course-transcript-v2-chat:<SHA>`)

---

## 3. Predeployment Capacity Matrix

| Component | Estimated Size | Max Peak Headroom | Safe within 8 GiB Floor? |
| :--- | :---: | :---: | :---: |
| **Current Root Free Space** | **16.72 GiB** | — | **YES** (~8.72 GiB margin) |
| **Git Release Archive** (`.tar.gz`) | ~15 MiB | ~30 MiB | **YES** |
| **Release Directory Extracted** | ~75 MiB | ~150 MiB | **YES** |
| **WAL-Safe SQLite Snapshot** | 651 KiB | 2 MiB | **YES** |
| **OCI Chat Base Image Layers** | ~1.4 GiB | ~1.6 GiB | **YES** |
| **OCI Runtime Image Layers** | ~1.4 GiB (shared base) | ~0.5 GiB delta | **YES** |
| **Retained Prior Images** (`6999290b...`) | ~1.4 GiB | 0 (already present) | **YES** |
| **Jobs Data Archive Extraction** | **~12–14 GiB** | **UNSAFE ON ROOT** | **STRICTLY FORBIDDEN / BLOCKED** |

> [!IMPORTANT]
> Full historical jobs archive extraction (`pr159-20261029T071713Z/jobs.tar.gz`) is strictly prohibited on root disk. All recovery drills are restricted to isolated SQLite restore drills unless an off-root attached volume is provisioned.

---

## 4. Rollback Plan Specification

When a production rollout fails post-flight verification or healthchecks:
1. **Drain Assertion:** Verify no active in-flight locks were acquired.
2. **Symlink Reversion:** Atomically re-point `/opt/course-transcript-v2-chat/current` to `/opt/course-transcript-v2-chat/releases/6999290b139c9230d9f877a2dead0cea78c8d5dd`.
3. **Container Re-Pinning (Mandatory):** Re-launch containers explicitly referencing the prior image tags:
   ```bash
   docker compose -p golden-v2-chat \
     -f /opt/course-transcript-v2-chat/current/deploy/golden-v2-chat-compose.yml \
     -f /opt/course-transcript-v2-chat/current/deploy/golden-v2-paid-compose.yml \
     up -d --force-recreate
   ```
4. **Health Verification:** Validate `curl http://127.0.0.1:8816/health` returns `release_sha: 6999290b139c9230d9f877a2dead0cea78c8d5dd`.
5. **No Ledger Corruption:** Ensure live SQLite ledger was backed up prior to staging and remains at integrity `ok`.

---

## 5. Negative Test Suite & Verification Results

The test suite in `tests/test_golden_v2_deploy_handler_negative.py` was executed with 11 test cases covering all failure modes:
- **Wrong SHA Format:** Fails closed (exit 1).
- **V1 Project & Path Exclusion:** Fails closed on any reference to `course-transcript` or `/opt/course-transcript-source`.
- **Disk Floor Gate:** Fails closed when free disk is below 8.0 GiB.
- **Ledger Active Leases:** Fails closed if jobs are `IN_FLIGHT`, `RUNNING`, or `LOCKED`.
- **Ledger Unsafe Chirp:** Fails closed if non-DONE Chirp operations exist.
- **Stage-0 Default-Deny:** Fails closed on all mutating commands (`deploy`, `switch`, `restart`, `rollback-execute`, `paid`, `publish`).
- **Rollback Image Repin:** Validates rollback plan includes explicit OCI image repin.

**Test Run Result:**
```text
Ran 11 tests in 0.537s
OK (Ran with both unprivileged user and sudo root)
```
