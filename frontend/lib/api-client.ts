import type {
  Artifact,
  BatchDetail,
  BatchPreview,
  CostSummary,
  CreatedBatch,
  DriveDirectory,
  PipelineStep,
  JobEvent,
  ReviewTerm,
  TranscriptJob,
  TranscriptSegment,
} from "./types";

// Production and local `next dev` both use the same-origin rewrite. Keeping the
// default relative is important because NEXT_PUBLIC_* values are compiled into
// browser bundles and a container runtime environment cannot safely repair an
// already-built absolute localhost URL.
const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

type ApiJob = Omit<TranscriptJob, "sourcePath" | "durationSeconds" | "createdAt" | "updatedAt" | "reviewTerms" | "batchId" | "estimatedCostUsd" | "chirpMaxParallelChunks"> & {
  source_path: string;
  duration_seconds: number;
  created_at: string;
  updated_at: string;
  review_terms: number;
  active_stage: string | null;
  stage_detail: string | null;
  error: string | null;
  revision: number;
  batch_id?: string | null;
  estimated_cost_usd?: string | null;
  chirp_max_parallel_chunks?: number;
  pipeline: Array<{ id: string; label: string; detail: string; status: PipelineStep["status"] }>;
};

function formatDate(iso: string) {
  return new Intl.DateTimeFormat("zh-TW", { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));
}

function mapJob(job: ApiJob): TranscriptJob {
  return {
    id: job.id,
    filename: job.filename,
    sourcePath: job.source_path,
    course: job.course,
    duration: job.duration,
    durationSeconds: job.duration_seconds,
    progress: job.progress,
    status: job.status,
    createdAt: formatDate(job.created_at),
    updatedAt: formatDate(job.updated_at),
    language: job.language,
    model: job.model,
    words: job.words,
    reviewTerms: job.review_terms,
    pipeline: job.pipeline,
    activeStage: job.active_stage,
    stageDetail: job.stage_detail,
    error: job.error,
    revision: job.revision,
    batchId: job.batch_id ?? null,
    estimatedCostUsd: job.estimated_cost_usd ?? null,
    chirpMaxParallelChunks: job.chirp_max_parallel_chunks ?? 3,
  };
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? (response.status === 404 ? "找不到指定資料" : `API 回應 ${response.status}`));
  }
  return response.json() as Promise<T>;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, { method: "POST", body: JSON.stringify(body) });
}

function patchJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export async function getJobs(): Promise<TranscriptJob[]> {
  const result = await fetchJson<{ jobs: ApiJob[] }>("/jobs");
  return result.jobs.map(mapJob);
}

export async function getJob(id: string): Promise<TranscriptJob> {
  return mapJob(await fetchJson<ApiJob>(`/jobs/${encodeURIComponent(id)}`));
}

export async function getSegments(id: string): Promise<TranscriptSegment[]> {
  const result = await fetchJson<{ segments: Array<{ segment_id: string; start_ms: number; end_ms: number; raw_text: string; corrected_text: string; uncertain_terms?: string[]; revision?: number }> }>(`/jobs/${encodeURIComponent(id)}/segments`);
  return result.segments.map((segment) => ({ id: segment.segment_id, startMs: segment.start_ms, endMs: segment.end_ms, rawText: segment.raw_text, correctedText: segment.corrected_text, uncertainTerms: segment.uncertain_terms ?? [], revision: segment.revision ?? 0 }));
}

export async function getReviewTerms(id: string): Promise<ReviewTerm[]> {
  const result = await fetchJson<{ review_terms: Array<{ id: string; heard: string; suggestion: string; timestamp: string; confidence: "low" | "medium"; status: "pending" | "confirmed" | "ignored"; scope?: "session" | "course" | "instructor" | "global"; approved_value?: string | null }> }>(`/jobs/${encodeURIComponent(id)}/review-terms`);
  return result.review_terms.map((term) => ({ ...term, approvedValue: term.approved_value }));
}

export async function decideReviewTerm(
  jobId: string,
  termId: string,
  action: "confirmed" | "ignored",
  approvedValue: string,
  scope: "session" | "course" | "instructor" | "global",
): Promise<ReviewTerm> {
  const result = await patchJson<{ term: { id: string; heard: string; suggestion: string; timestamp: string; confidence: "low" | "medium"; status: "pending" | "confirmed" | "ignored"; scope: "session" | "course" | "instructor" | "global"; approved_value?: string | null } }>(
    `/jobs/${encodeURIComponent(jobId)}/review-terms/${encodeURIComponent(termId)}`,
    { action, approved_value: approvedValue, scope },
  );
  return { ...result.term, approvedValue: result.term.approved_value };
}

export async function getArtifacts(id: string): Promise<Artifact[]> {
  const result = await fetchJson<{ artifacts: Array<{ id: string; name: string; size_bytes: number; updated_at: string }> }>(`/jobs/${encodeURIComponent(id)}/artifacts`);
  return result.artifacts.map((item) => ({ id: item.id, name: item.name, sizeBytes: item.size_bytes, updatedAt: formatDate(item.updated_at) }));
}

export async function getJobEvents(id: string): Promise<JobEvent[]> {
  const result = await fetchJson<{
    events: Array<{
      id: number;
      event_type: string;
      actor: string;
      payload: Record<string, unknown>;
      created_at: string;
    }>;
  }>(`/jobs/${encodeURIComponent(id)}/events`);
  return result.events.map((event) => ({
    id: event.id,
    eventType: event.event_type,
    actor: event.actor,
    payload: event.payload,
    createdAt: formatDate(event.created_at),
  }));
}

async function jobAction(
  id: string,
  action: "pause" | "resume",
  revision: number,
): Promise<TranscriptJob> {
  const result = await postJson<{ job: ApiJob }>(
    `/jobs/${encodeURIComponent(id)}/${action}`,
    { expected_revision: revision },
  );
  return mapJob(result.job);
}

export function pauseJob(id: string, revision: number): Promise<TranscriptJob> {
  return jobAction(id, "pause", revision);
}

export function resumeJob(id: string, revision: number): Promise<TranscriptJob> {
  return jobAction(id, "resume", revision);
}

export async function retryFailedStage(
  id: string,
  revision: number,
  stage: string,
): Promise<TranscriptJob> {
  const result = await postJson<{ job: ApiJob }>(
    `/jobs/${encodeURIComponent(id)}/retry-stage`,
    { expected_revision: revision, stage },
  );
  return mapJob(result.job);
}

export async function browseDrive(sourcePath: string): Promise<DriveDirectory> {
  const result = await postJson<{
    current_path: string;
    parent_path: string | null;
    entries: Array<{
      source_path: string;
      name: string;
      is_dir: boolean;
      size_bytes: number;
      modified_at: string | null;
      mime_type: string | null;
      supported_media: boolean;
    }>;
  }>("/drive/browse", { source_path: sourcePath });
  return {
    currentPath: result.current_path,
    parentPath: result.parent_path,
    entries: result.entries.map((entry) => ({
      sourcePath: entry.source_path,
      name: entry.name,
      isDir: entry.is_dir,
      sizeBytes: entry.size_bytes,
      modifiedAt: entry.modified_at,
      mimeType: entry.mime_type,
      supportedMedia: entry.supported_media,
    })),
  };
}

export async function previewBatch(
  selectionMode: "files" | "folder",
  sourcePaths: string[],
): Promise<BatchPreview> {
  const result = await postJson<{
    batch_preview_id: string;
    selection_mode: "files" | "folder";
    source_root: string | null;
    item_count: number;
    total_size_bytes: number;
    expires_at: string;
    paid_operation_started: false;
    items: Array<{
      preview_id: string;
      source_path: string;
      name: string;
      size_bytes: number;
      modified_at: string | null;
    }>;
  }>("/drive/preview-batch", {
    selection_mode: selectionMode,
    source_paths: sourcePaths,
  });
  return {
    batchPreviewId: result.batch_preview_id,
    selectionMode: result.selection_mode,
    sourceRoot: result.source_root,
    itemCount: result.item_count,
    totalSizeBytes: result.total_size_bytes,
    expiresAt: result.expires_at,
    paidOperationStarted: result.paid_operation_started,
    items: result.items.map((item) => ({
      previewId: item.preview_id,
      sourcePath: item.source_path,
      name: item.name,
      sizeBytes: item.size_bytes,
      modifiedAt: item.modified_at,
    })),
  };
}

export async function createBatch(batchPreviewId: string, chirpMaxParallelChunks: number = 3): Promise<CreatedBatch> {
  const result = await postJson<{
    batch_id: string;
    status: string;
    item_count: number;
    job_ids: string[];
    created_at: string;
    paid_operation_started: false;
    next_action: string;
  }>("/batches", {
    batch_preview_id: batchPreviewId,
    language_code: "cmn-Hant-TW",
    profile: "highest_accuracy",
    enable_gemini_correction: true,
    enable_subtitles: true,
    require_human_review: true,
    chirp_max_parallel_chunks: chirpMaxParallelChunks,
  });
  return {
    batchId: result.batch_id,
    status: result.status,
    itemCount: result.item_count,
    jobIds: result.job_ids,
    createdAt: result.created_at,
    paidOperationStarted: result.paid_operation_started,
    nextAction: result.next_action,
  };
}

export async function getCosts(): Promise<CostSummary> {
  const result = await fetchJson<{
    project_limit_usd: string;
    committed_estimated_cost_usd: string;
    recorded_actual_cost_usd: string;
    remaining_estimated_budget_usd: string;
    warning_thresholds_usd: string[];
    pricing_version: string;
    accounting_note: string;
  }>("/costs");
  return {
    projectLimitUsd: result.project_limit_usd,
    committedEstimatedCostUsd: result.committed_estimated_cost_usd,
    recordedActualCostUsd: result.recorded_actual_cost_usd,
    remainingEstimatedBudgetUsd: result.remaining_estimated_budget_usd,
    warningThresholdsUsd: result.warning_thresholds_usd,
    pricingVersion: result.pricing_version,
    accountingNote: result.accounting_note,
  };
}

export async function getBatch(id: string): Promise<BatchDetail> {
  const result = await fetchJson<{
    id: string;
    name: string;
    status: string;
    selection_mode: "files" | "folder";
    source_root: string | null;
    item_count: number;
    completed_count: number;
    failed_count: number;
    estimated_cost_usd: string | null;
    reserved_cost_usd: string;
    actual_cost_usd: string;
    total_duration_seconds: number;
    created_at: string;
    updated_at: string;
    revision: number;
    jobs: ApiJob[];
  }>(`/batches/${encodeURIComponent(id)}`);
  return {
    id: result.id,
    name: result.name,
    status: result.status,
    selectionMode: result.selection_mode,
    sourceRoot: result.source_root,
    itemCount: result.item_count,
    completedCount: result.completed_count,
    failedCount: result.failed_count,
    estimatedCostUsd: result.estimated_cost_usd,
    reservedCostUsd: result.reserved_cost_usd,
    actualCostUsd: result.actual_cost_usd,
    totalDurationSeconds: result.total_duration_seconds,
    createdAt: formatDate(result.created_at),
    updatedAt: formatDate(result.updated_at),
    revision: result.revision,
    jobs: result.jobs.map(mapJob),
  };
}

export async function approveBatch(
  id: string,
  revision: number,
  estimatedCostUsd: string,
): Promise<{ status: string; reservedCostUsd: string }> {
  const result = await postJson<{
    status: string;
    reserved_cost_usd: string;
  }>(`/batches/${encodeURIComponent(id)}/approve`, {
    expected_revision: revision,
    confirmed_estimated_cost_usd: estimatedCostUsd,
  });
  return {
    status: result.status,
    reservedCostUsd: result.reserved_cost_usd,
  };
}
