# Private ChatGPT skill entry

The first operational ChatGPT capability is deliberately the non-paid V2
source intake/status service. It is independent of the V1 production Compose
project and of earlier four-minute paid smoke ledgers. Gemini 3.8 primary and
Chirp timing remain the intended later paid pipeline; this service has no
provider client, credential mount, approval endpoint or Drive publisher.

## Deployed contract

A private plugin provides the `golden-subtitles-v2` skill and depends on the
owner's existing WebCodex VPS App. It reuses that connection. The skill runs a
VPS-owned bridge with WebCodex's structured `run_process` tool, preserving URL
and JSON argv/stdin rather than constructing shell commands. Plugin packages
contain no tokens, Drive OAuth files or local secrets.

The runtime is `/opt/course-transcript-v2-chat/releases/<exact-sha>` with a
separate `/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite`. The
independent `golden-v2-chat` Compose project serves only loopback
`127.0.0.1:8816`. It exposes authenticated status, Drive GET-only preview and
confirmed batch source intake, with no approval/paid/publication routes. Shared
Drive OAuth is mounted read-only; model credentials are not mounted. Source
intake persists through an independent restartable daemon.

Chat operations:

- `service-status`: actual release, capabilities and observed intake heartbeat.
- `drive-health`: authenticated, read-only Drive connectivity without account
  contents or credential values.
- `preview <Drive URL> [--recursive]`: bounded source listing, identity and
  version-bound preview SHA, with unknown costs explicitly reported.
- `create-batch --request-stdin`: exact confirmed preview/mode/per-file estimates
  from a bounded JSON stdin body. This creates non-paid preflight only.
- `batch-status <batch_id>`, `jobs`, `job-status <job_id>`: the same durable V2
  ledger used by the service and worker.

Estimates remain operator supplied; do not invent prices, enter zero for an
unknown paid estimate, or infer approval from a request to install the skill.
New source jobs remain `AWAITING_APPROVAL`. Complete autonomous paid execution,
Golden audio review and safe Drive delivery remain later integration work.

## Operator installation

Place a reviewed Git archive in the immutable release directory. Build the
ARM64 image with exact OCI revision labels and package all daemon/client
scripts. `deploy/bootstrap_golden_v2_chat.py` creates strong server/client
scoped tokens locally and never prints them. Its client directory must be the
intended WebCodex project child `golden-v2-chat`; that owner-only directory holds
`entry.py`, a non-secret revision binding and private client configuration.
The Skill invokes only `entry.py`; it never reads the config file.

Initialize the dedicated V2 SQLite ledger through a network-disabled one-shot
container. Start only the independent API and source intake services from
`deploy/golden-v2-chat-compose.yml`. Do not replace or restart V1 containers.
Require exact image/runtime SHA, loopback bind, shared V2 data mount, healthy API,
actual worker heartbeat, scoped auth rejection and successful calls through the
real WebCodex App. A private plugin save is separate from user installation;
verify its declared App binding and report any remaining install action.

Retain a deployment evidence report with release SHA, container/image identity,
client/Drive connectivity results, ledger integrity and unchanged V1 labels.
Do not record credential values or access-bearing URLs in reports or Git.
