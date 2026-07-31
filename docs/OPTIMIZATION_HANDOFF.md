# Optimization handoff

Branch: `optimize/accessibility-drive-performance`

This change set improves Drive browsing latency, frontend accessibility, runtime resilience, and automated validation without changing the transcription provider contract or enabling Drive publishing.

## Included changes

### Google Drive browsing

- Adds a process-local directory listing cache with a default 60-second TTL.
- Cache is bounded to 128 folders and never stores credentials.
- Performance logs use a non-reversible path hash, item count, cache status, and elapsed milliseconds; folder names are not logged.
- Multi-file batch preview groups selected files by parent folder and reuses one directory listing instead of issuing one `rclone --stat` request per file.
- Exact `--stat` remains the fallback when cached/listed metadata is missing or stale.
- Optional `COURSE_TRANSCRIPT_RCLONE_FAST_LIST=true` is available for large recursive folder previews, but it remains disabled by default because it can increase memory use.

### Accessibility and presbyopia support

- Large typography is applied on first paint, before React hydration.
- Desktop retains Standard, Large, and X-Large controls with `aria-pressed` state.
- Mobile gains a compact `Aa` control instead of losing font-size selection.
- Muted text contrast is increased.
- Keyboard focus rings and reduced-motion handling are added.
- Long filenames and X-Large mobile layouts receive overflow protection.
- Static status wording no longer claims that Chirp, Gemini, or GCS are healthy without backend evidence.

### Runtime and build reliability

- Frontend Docker builds now require `npm ci`; lockfile problems fail instead of silently falling back to `npm install`.
- API and frontend health checks are added.
- Long-running web services use `restart: unless-stopped` and explicit grace periods.
- Batch polling pauses when the browser tab is hidden and stops while waiting for human confirmation or after terminal states.
- GitHub Actions validates Python, Next.js, npm production audit, and Docker Compose configuration.

## Required Codex/VPS validation

Do not deploy directly over the running service before the following branch checks pass.

```bash
cd /opt/course-transcript
git fetch origin
git checkout optimize/accessibility-drive-performance

python3 -m compileall -q app tests
python3 -m unittest discover -s tests -v

npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend audit --omit=dev

docker compose --profile web config --quiet
sudo docker compose --profile web build
```

Before restarting production, preserve the existing SQLite data and confirm there is no active paid source job.

Start the reviewed build with the existing Cloudflare overlay and secret environment file. Do not recreate the tunnel, Access application, DNS, IAM, firewall, or billing configuration.

After restart, verify:

```bash
sudo docker compose --profile web ps
curl -fsS http://127.0.0.1:3300/api/v1/health
```

Then validate in the authenticated browser:

1. Standard, Large, and X-Large typography on desktop.
2. `Aa` font cycling on mobile width.
3. Keyboard focus visibility.
4. Drive root browse, enter a folder, return to the same folder, and compare latency.
5. Select several files from the same folder and confirm preview does not perform one stat request per file.
6. Keep Drive publishing disabled.

## Drive performance observation

The API log now emits entries shaped like:

```text
drive_browse cache_hit=false path_id=0123456789ab items=42 duration_ms=1850
drive_browse cache_hit=true path_id=0123456789ab items=42 duration_ms=1
```

Record p50 and p95 for real folders before changing the 60-second TTL or enabling fast-list.

Suggested targets:

- cached browse: under 300 ms
- normal one-level browse p50: under 2 seconds
- normal one-level browse p95: under 5 seconds
- recursive preview up to 100 media files: under 15 seconds

## Rollback

This branch does not migrate the SQLite schema. Rollback is therefore source-and-container only: restore the previous reviewed commit and rebuild, while preserving `data/`, `logs/`, `tmp/`, and all files under `/opt/course-transcript/secrets/`.
