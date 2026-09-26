# Runbook

## Safety rules

- Never print, copy, or commit service-account JSON, rclone configuration, OAuth tokens, Cloudflare tunnel tokens, or secret environment values.
- Keep AI account profiles under `/opt/course-transcript/secrets/ai-accounts`
  and the active runtime under `/opt/course-transcript/secrets/ai-runtime`,
  both outside Git. The API may write only these dedicated state paths; the
  pipeline worker mounts the active runtime read-only.
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

## Automatic start after non-paid preflight

New transcription jobs still perform the complete local, non-paid preflight:
Drive copy, FFprobe duration/audio validation, checksum, and cost estimation.
Ordinary jobs then enter the paid queue automatically when both safety
guardrails pass. The operator no longer confirms every estimate manually.

The server-side controls are:

```dotenv
COURSE_TRANSCRIPT_AUTO_AUTHORIZE_COSTS=true
COURSE_TRANSCRIPT_AUTO_AUTHORIZE_MAX_USD=10
COURSE_TRANSCRIPT_COST_LIMIT_USD=200
```

`COURSE_TRANSCRIPT_AUTO_AUTHORIZE_MAX_USD` is the maximum estimate that may
start without another click. The existing project cost limit remains a hard
cap. A job or whole batch outside either guardrail remains
`awaiting_confirmation`; unknown/invalid estimates never enter the paid
pipeline. Disabling `COURSE_TRANSCRIPT_AUTO_AUTHORIZE_COSTS` restores the
manual-confirmation behavior.

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
CHIRP_RECOVERY_POLL_SECONDS=60
CHIRP_MAX_PARALLEL_CHUNKS_LIMIT=8
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

The model is `gemini-3.7-flash`. Every paid response is stored under a prompt-version, source-digest, and attempt-unique audit filename. If a structured response is malformed, the parent response must be persisted before the window is split.

### Dacheng Golden Corpus / Error Memory

For `content_mode=dacheng_buddhist`, correction may read human-reviewed historical evidence from `/app/data/canonical/golden-corpus.jsonl` and deterministic typo evidence from `/app/data/canonical/golden-error-memory.json`. These sources are reference-only: current audio/ASR and current canonical scripture context have higher priority, and retrieval must never add, delete, split, merge, reorder, or retime segments.

Rebuild without provider calls:

```bash
PYTHONPATH=. python scripts/build_golden_corpus.py \
  --input-dir /path/to/reviewed-srts \
  --data-dir /opt/course-transcript-source/data
```

The correction cache includes the corpus digest. `app.canonical.omission_detection` is report-only and screens both `human_missing` and `source_missing` candidates; it never edits raw ASR or human gold.

Severe deletion, addition, repetition, or likely semantic rewrite triggers fallback to immutable Chirp text. The fallback reason must remain visible in corrected subtitle evidence and `content-qa.json`.

## AI account profiles and controlled switching

The owner-only `/review-admin/ai-accounts` screen stores named service-account
profiles without exposing private-key contents after submission. Every profile
must pass the read-only preflight checks for project visibility, Cloud Billing,
Speech-to-Text API and Vertex AI API before it can become active. The store
keeps immutable revision metadata, an audit trail, and the previous activation
for rollback.

Switching is a controlled deployment operation. It is blocked while any job is
using an active processing state or while the job ledger cannot be inspected.
After a successful switch, recreate the `api` and `pipeline-worker` services
from the same release generation and verify the health endpoint plus runtime
generation before starting paid work. The API never performs a model
generation during preflight and never returns credential contents.

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

Production services must never be recreated from the base Compose file with
`:local` images. Use the immutable release path only. After any production
cutover or controlled service recreation, run:

```bash
python3 scripts/runtime_revision_guard.py --expected-sha <40-char-release-sha>
```

The guard fails closed if any API/frontend/worker container is unlabelled,
stopped, on a different revision, or does not match the expected release SHA.
The formal release script runs this guard again before declaring cutover
complete.

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
一併保存為不可變背景，用於 Gemini 3.7 Flash 的純文字校正，不會更動原始 Chirp
結果、時間碼或分段。

咒語的講師／大眾重複只會在輸出層做去重，且必須偵測到兩輪完整、連續、順序正確
的咒語；未達條件只會保留原字幕並標示複核，絕不以空白 cue 覆寫原始內容。

`retention-monitor` 每日產生唯讀的 `retention-report.json`，列出可處理的 GCS
孤兒與 Drive backup。它不會自行刪除；仍需人工檢視報告後，以明確的 `--apply`
執行清理。

## 大成佛經本堂經文上下文

`dacheng_buddhist` 任務在 Gemini 文字校正前，會先以 raw Chirp 文字對
`dacheng_scripture` active canonical 做保守的 ordered exact-anchor 定位。
只有達到最小錨點、匹配字數與來源覆蓋率門檻時，才會產生
`lesson-scripture-context.json`。內容包含 canonical version/checksum/source、
本堂實際誦讀的 canonical line range、alignment confidence、lesson vocabulary
與 evidence。定位失敗時不套佛經 bias，只留下 review reason。

後續每個 Gemini correction window 會在本堂已定位的經文範圍內追蹤最可能正在
逐句解釋的 canonical line，並只把該句附近經文與本堂詞彙當作「拼寫／術語
優先參考」。它不得把講師的白話解釋、譬喻、例子或改述強制替換成經文原句。
每個 correction audit JSON 會保存 lesson context digest 與該 window 的
scripture hint，方便追溯為何某個佛教專有詞被優先採用。

歷史《大成佛經》課程可先執行唯讀 dry-run：

    python scripts/reprocess_dacheng_history.py --data-dir /app/data --output /app/data/dacheng-history-audit.json

此工具不修改 raw Chirp、provider response、時間碼、既有輸出或 Drive。
若要讓舊課程真正受益於新的「本堂詞彙＋逐句追蹤」文字校正，必須另外逐案
估算 Gemini 成本並依既有 paid-provider gate 明確核准後再執行。

## Chirp 3 / ChatGPT Handoff 工作模式

建立任務時可選三種 workflow_mode：

- FULL_AUTO：既有完整流程。Chirp 3 後由已選定的 server-side correction provider（Gemini / approved router）完成文字校正，再執行 cleanup、QA 與輸出。
- CHATGPT_HANDOFF：Chirp 3、merge、固定字幕後先執行 Chirp 完整性 Gate。Gate PASS 才建立 jobs/<job-id>/chatgpt-handoff/ 交接包；Gate BLOCKED 時停在 awaiting_review，禁止進入 ChatGPT 校稿。Server-side LLM correction 強制關閉，preflight 的 Gemini token / cost 估算為 0。
- CHIRP_ONLY：只使用 Chirp 3 文字與時間軸；不執行 LLM correction。deterministic export 仍會依使用者選定格式產生輸出。

CHATGPT_HANDOFF 交接包至少包含：chirp-raw.srt、segments.json、merged-words.json、chirp-completeness.json、raw-transcript.txt、golden-rules.json、canonical-context.json、INSTRUCTIONS.txt、handoff-manifest.json。完整性 Gate 會檢查 course-relative chunk density、15 分鐘密度、可疑有聲字幕空窗、尾端覆蓋與時間軸結構。high-confidence targeted patch 會依既有 budget gate 自動執行；若重建後仍有可修復 residual，系統會自動 recheck 並進入下一輪 repair，預設最多 3 輪（CHIRP_COMPLETENESS_AUTO_REPAIR_MAX_ROUNDS），每輪仍受 patch 數量、總秒數與 reserved budget 限制。尾端 repair 以真實音檔終點為界，不再被最後 base chunk 截斷。只有超過安全上限、budget gate 拒絕或結構性不可自動修復問題才保留 review 阻擋。

Gate BLOCKED 後若已完成局部 ASR 修復，可呼叫 POST /api/v1/jobs/{job_id}/chirp-completeness/recheck，帶最新 expected_revision。此 API 本身不送付費 provider；它只把任務重新排入字幕重建與完整性檢查，既有可重用 Chirp／patch evidence 仍由 worker 保留。

dacheng_buddhist 任務會把 active canonical 經文／咒語版本與 Golden Rules 版本寫入交接包。使用者另外提供的參考逐字稿只可作為拼字與上下文證據，不可用來補寫 Gate 尚未確認的漏辨識。Chirp word timestamps 與 source segment timing 是不可變 ASR 證據；最終顯示 cue 的合併／拆分屬於獨立 deterministic rendering concern，不可採用 ChatGPT 自行發明的時間碼。

ChatGPT 完成校稿後，以 owner-only endpoint POST /api/v1/jobs/{job_id}/chatgpt-handoff/import 回灌。首選 payload 是最新 expected_revision 加完整 segment_edits，每筆只含 segment_id 與 corrected_text，不含時間碼；必須完整覆蓋原始 segment IDs、順序一致、不得缺漏或重複。Legacy srt_text 仍相容，但 cue 數量與每個 start/end timestamp 必須完全相同。通過後任務只續跑 deterministic cleanup、Golden Rules audit、export、QA 與 validation，不會重新呼叫 Gemini / M3。若 revision race 發生，回灌 artifacts 會回復到操作前狀態。

### Chirp throughput and targeted-repair policy

Production chooses Chirp submission parallelism from the normalized audio duration:

- up to 30 minutes: 3 concurrent chunks
- over 30 and up to 90 minutes: 5 concurrent chunks
- over 90 minutes: 8 concurrent chunks

The server limit remains authoritative and caps the automatic value.  Targeted
repair patches are latency-sensitive completeness work and therefore always use
standard BatchRecognize, never Dynamic Batching.  Asynchronous recovery polls at
a 60-second baseline; retryable provider throttling (including quota/resource
exhaustion) uses the existing bounded exponential backoff rather than tight
polling.
