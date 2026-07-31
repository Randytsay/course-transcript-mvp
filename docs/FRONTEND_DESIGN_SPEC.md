# Frontend Design Specification

## Product objective

Provide a clear operational interface for long-form transcription jobs without exposing infrastructure complexity or cloud credentials to the browser.

The interface should make five questions immediately answerable:

1. Which recording is being processed?
2. Which pipeline stage is active?
3. Has the timeline and output passed automated QA?
4. Which terms still require human review?
5. Is the job safe to publish back to Google Drive?

## Information architecture

### Dashboard `/`

- Current workload metrics
- Recent transcription jobs
- Pipeline service health
- Primary action: create a transcription job

### New job `/jobs/new`

- Google Drive source path
- Safe file metadata preview
- Language and accuracy profile
- Output and review options
- Read-only summary before submission

### Job detail `/jobs/[id]`

- Job metadata and aggregate state
- Six-stage pipeline strip
- Audio review controls and waveform
- Fixed transcript segments
- Uncertain-term review queue
- QA report and output artifacts
- Drive publishing gated by review completion

## Design principles

- **Operational clarity:** status and next action take priority over decoration.
- **Progressive disclosure:** diagnostics belong on the job page, not the dashboard.
- **Human review first:** uncertain terminology remains visible until confirmed or explicitly ignored.
- **No false completion:** `completed`, `review`, and `failed` are separate states.
- **Responsive by default:** desktop supports simultaneous audio and transcript review; mobile stacks the same workflow without hiding core actions.

## Visual tokens

| Token | Value | Usage |
|---|---:|---|
| Brand | `#4f46e5` | primary action, active navigation, waveform progress |
| Success | `#15803d` | completed stages and verified results |
| Warning | `#b45309` | human review and uncertain terminology |
| Danger | `#b91c1c` | failed jobs and destructive states |
| Background | `#f5f7fb` | application canvas |
| Surface | `#ffffff` | cards, panels, review workspace |
| Text | `#172033` | primary copy |
| Muted text | `#7b899c` | secondary metadata |
| Border | `#e2e8f0` | panel and field boundaries |

## Status model

Frontend labels must map to backend enums rather than infer state from progress percentage.

| Backend status | UI label | Color family |
|---|---|---|
| `queued` | 排隊中 | slate |
| `downloading` | 下載中 | blue |
| `normalizing` | 音訊處理 | blue |
| `transcribing` | Chirp 辨識 | blue |
| `correcting` | Gemini 校正 | blue/violet |
| `review` | 待審查 | amber |
| `completed` | 已完成 | green |
| `failed` | 失敗 | red |

## Transcript editing contract

Gemini correction must not modify timestamps. The frontend edits a stable segment object:

```json
{
  "id": 128,
  "start_ms": 758920,
  "end_ms": 764300,
  "raw_text": "接下來介紹的是美安的 OPC 三還有相關的抗氧化營養",
  "corrected_text": "接下來介紹的是美安的 OPC-3，以及相關的抗氧化營養。",
  "uncertain_terms": ["OPC-3"],
  "revision": 3
}
```

Segment ID, ordering, `start_ms`, and `end_ms` remain immutable during text correction.

## Responsive behavior

### Desktop ≥ 1180 px

- Fixed 258 px sidebar
- Dashboard table plus service-health rail
- Job workspace uses transcript plus a 315 px term-review rail

### Tablet 681–1179 px

- Sidebar becomes a drawer below 900 px
- Auxiliary cards stack or use two columns
- Job review rail narrows, then stacks below the transcript

### Mobile ≤ 680 px

- Page actions become full-width controls
- Tables become task cards
- Audio controls simplify
- Transcript review state appears below corrected text
- Search and keyboard hints are hidden

## Accessibility requirements

- Icon-only controls require `aria-label`.
- Status is never communicated by color alone.
- Keyboard users must be able to select transcript segments and review actions.
- Focus states must remain visible after later component-library adoption.
- A real waveform component must provide text-based current time and duration.
