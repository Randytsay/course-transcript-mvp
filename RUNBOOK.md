# Runbook

## Safety rules

- Never print, copy, or commit service-account JSON, rclone configuration, OAuth tokens, Cloudflare tunnel tokens, or secret environment values.
- Keep `/opt/course-transcript/secrets/gcp-sa.json` outside Git and mount it read-only.
- Keep `/opt/course-transcript/secrets/cloudflare-tunnel.env` owned by root with mode `600`.
- Never overwrite or rename the Drive source media.
- Derived Drive sidecars may be published only after local QA passes and only through the resumable safe-publish implementation.
- Do not start a real Chirp, Gemini, or Drive mutation test without explicit approval for the exact source and estimated cost.
- Do not delete provider manifests, raw responses, operation names, usage evidence, or Drive transaction state during recovery or rollback.

## Production entry points

- API: `app.api_hardened:app`
- preflight worker: `app.jobs.preflight_observed`
- paid pipeline worker: `app.pipeline.dynamic_worker_production`
- delayed Drive delivery worker: `app.jobs.delivery_worker`
- billing worker: `app.billing.worker`

Successful hardened jobs finish as `completed`. The legacy `awaiting_review` status remains readable only so older jobs can be delivered or edited safely.

## Shared-data and locking requirement

`app.jobs.drive_lock` uses Linux `fcntl.flock` on a file below `/app/data`. Cross-container locking is valid only when every process uses the same underlying host directory.

The production Compose file bind-mounts the repository's same `./data` directory to `/app/data` for `api`, `pipeline-worker`, and `delivery-worker`. Before every deployment, verify the rendered Compose configuration preserves that exact shared source path. Do not substitute a different named volume or container-local directory for any of those services.

## Cloudflare Tunnel connector

The connector is defined in `docker-compose.cloudflare.yml`. It has no host port, no GCP credential mount, and reaches only `http://frontend:3000` through the Docker network. Never route the public hostname directly to `api:8000`.

Inspect it without printing the token:

```bash
sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  --profile tunnel ps cloudflared

sudo docker compose --env-file /opt/course-transcript/secrets/cloudflare-tunnel.env \
  -f docker-compose.yml -f docker-compose.cloudflare.yml \
  logs --tail=50 cloudflared
```

## Chirp dynamic-batch operation

The production path uses Speech-to-Text V2 Chirp 3 with dynamic batching by default.

Important defaults:

```dotenv
CHIRP_DYNAMIC_BATCHING=true
CHIRP_DYNAMIC_MAX_INFLIGHT_JOBS=5
CHIRP_RECOVERY_POLL_SECONDS=120
CHIRP_PROVIDER_DEADLINE_SECONDS=90000
CHIRP_OUTPUT_PROPAGATION_GRACE_SECONDS=300
CHIRP_GCS_CLEANUP_AFTER_RECOVERY=true
CHIRP_MAX_PARALLEL_CHUNKS=3
CHIRP_MAX_PARALLEL_RECOVERY=3
```

`CHIRP_PROVIDER_DEADLINE_SECONDS` is the implemented 25-hour deadline. The obsolete name `CHIRP_RECOVERY_DEADLINE_SECONDS` is not read by the application.

The current planner derives chunk windows from the audio and can retain an older compatible standard-batch plan. Do not assume all chunks are always 15 minutes. Each retained chunk must match its saved source offsets and processing strategy before reuse.

Each new provider attempt uses an isolated GCS prefix. A submitted operation is durable evidence and must not be re-submitted merely because output is not yet visible.

Recovery outcomes:

- exit `75`: provider work is still pending;
- exit `76`: transient provider/GCS error; schedule exponential backoff in one-pass dynamic recovery;
- exit `78`: terminal condition requiring job failure or operator intervention.

The recovery worker checks the saved long-running operation, waits only the bounded output-propagation grace after completion, and terminates work that exceeds the configured provider deadline.

## Merge rule

Use word midpoint ownership, not text similarity. Boundaries are derived from actual adjacent Chirp coverage. Earlier chunk keeps midpoint `< boundary`; later chunk keeps midpoint `>= boundary`.

Do not treat silence as transcription failure. A zero-word chunk is terminal only after a separate speech/VAD check confirms audible speech.

## Gemini correction

Gemini is text-only and must not change segment IDs or timestamps.

Production defaults:

```dotenv
GEMINI_CORRECTION_WINDOW_MS=60000
GEMINI_MAX_PARALLEL_WINDOWS=2
```

The model remains `gemini-3.6-flash`. Every paid response is stored under a prompt-version, source-digest, and attempt-unique audit filename. If a structured response is malformed, the parent response must be persisted before the window is split.

Severe deletion, addition, repetition, or likely semantic rewrite triggers fallback to immutable Chirp text. The fallback reason must remain visible in corrected subtitle evidence and `content-qa.json`.

## Subtitle segmentation and imports

Run `build_srt` only after merging the Chirp word timeline. It uses local lexical segmentation and immutable timings.

External SRT import is all-or-nothing. Reject the complete import when any cue is malformed, empty, non-positive, overlapping, out of order, or has minute/second components outside `00..59`. Never silently drop invalid cues.

## Automatic subtitle cleanup and review

After optional Gemini correction, `app.providers.subtitle_cleanup` creates a
separate cleaned text layer. It may remove only high-confidence boundary
fillers and obvious triple stutters. Raw Chirp/Gemini JSON, segment IDs and
timestamps are preserved. It always writes `subtitles-cleaned.json`,
`subtitles-cleaned.srt`, `subtitles-cleaned.vtt`, `transcript-cleaned.txt`,
and `cleanup-review.json`. Inner fillers, possible double stutters, duplicate
cues, suspected audio interruptions, long cues and invalid timing are listed
for review instead of being silently rewritten. User-facing SRT/TXT/ASS
exports prefer this cleaned layer; the raw `chirp.json` sidecar remains raw.

## Drive publication

Automatic post-QA publication is controlled by:

```dotenv
COURSE_TRANSCRIPT_AUTO_PUBLISH_TO_SOURCE=true
DRIVE_GLOBAL_MIN_INTERVAL_SECONDS=1.0
```

The publisher performs a resumable transaction for each selected sidecar:

1. upload a pending file;
2. verify the pending size;
3. rename the existing final file to a timestamped backup;
4. promote the pending file;
5. verify the final size;
6. persist every phase in `drive-publish-state.json`.

A completed file is request-free on resume. Drive failure must not repeat Chirp or Gemini.

`delivery-worker` retries only existing local artifacts for both `completed` jobs and compatible legacy `awaiting_review` jobs. It must recheck editor ownership while holding the same global Drive lock before publishing or writing a failure state.

## Subtitle editor publication

The editor stores overlay state separately from provider artifacts. A publish request must:

1. snapshot the requested revision;
2. acquire the global Drive lock;
3. revalidate the current revision and prior editor marker;
4. persist `editor_publish_in_progress` before any remote mutation;
5. publish the isolated revision directory;
6. mark the pipeline delivery as superseded;
7. record the successful revision in job state and history.

A delayed pipeline retry or an older editor request must never overwrite a newer manual revision.

## Local validation

Before deployment or merge:

```bash
python -m compileall -q app tests
python -m unittest discover -s tests -v
python -c "from app.api_hardened import app; assert app.title"
python -c "import app.pipeline.dynamic_worker_production"
python -c "import app.jobs.delivery_worker"
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev --audit-level=high
docker compose --profile web config --quiet
docker compose --profile billing config --quiet
```

No paid provider call is required for these checks.

## VPS deployment and acceptance

Follow `docs/VPS_DEPLOY_GATE.md`. The required order is:

1. confirm no active paid job or non-expired relevant lease;
2. create verified backups of `data/course-transcript.db` and `data/jobs/`;
3. pull the exact approved `main` commit;
4. verify shared `/app/data` bind mounts;
5. build ARM64 images;
6. run non-paid tests and import checks;
7. restart services and verify health/restart persistence;
8. stop and request approval before real provider or Drive mutation tests.

Do not treat container health alone as acceptance. Verify database state, artifacts, manifest hashes, event history, and logs.

## Pause, retry, and rollback

Pause is observed at worker heartbeat boundaries. Completed evidence remains intact. Retry resumes only the failed stage and downstream stages; output failure never invalidates completed ASR or Gemini evidence.

Rollback:

1. stop `pipeline-worker` and `delivery-worker`;
2. confirm no active paid operation is being newly submitted;
3. preserve all manifests and Drive transaction state;
4. restore the pre-deployment source archive and database backup when necessary;
5. set `CHIRP_DYNAMIC_BATCHING=false` only as an explicit rollback decision;
6. rebuild and restart the prior worker entry point after verifying the database lease state.

Already submitted dynamic operations remain billable and may complete after rollback. Recover them from saved operation/GCS evidence rather than re-submitting audio.
# 暫存清理與健康監控（新增）

正常完成的 pipeline 會在 `audio-cleanup.json` 留下稽核紀錄，並只清除
`normalized.flac`／chunk FLAC；raw provider evidence、字幕、逐字稿與 manifest
不會刪除。取消或失敗任務仍保留診斷用暫存。

先以 dry-run 檢查 GCS 孤兒與 Drive backup：

```bash
python -m app.operations.retention_cleanup --data-dir /app/data
```

確認 `retention-report.json` 後才可明確加 `--apply`。VPS 的
`health-monitor` 每 15 分鐘產生 `production-health.json`，會檢查過期 lease、
heartbeat、Dynamic Batch 逾時與 Drive delivery retry。

## 文件內容模式與提示詞

建立任務時，先選擇每份文件的內容模式：`一般文件` 是預設值，不會帶入佛經
或咒語偏置；`大成佛經` 才會使用固定咒語拼寫。選填的「補充說明」會與該工作
一併保存為不可變背景，用於 Gemini 3.6 Flash 的純文字校正，不會更動原始 Chirp
結果、時間碼或分段。

咒語的講師／大眾重複只會在輸出層做去重，且必須偵測到兩輪完整、連續、順序正確
的咒語；未達條件只會保留原字幕並標示複核，絕不以空白 cue 覆寫原始內容。

`retention-monitor` 每日產生唯讀的 `retention-report.json`，列出可處理的 GCS
孤兒與 Drive backup。它不會自行刪除；仍需人工檢視報告後，以明確的 `--apply`
執行清理。
