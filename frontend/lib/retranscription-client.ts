const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

export type AsrSeverity = "normal" | "low" | "medium" | "high";

export interface AsrQualityChunk {
  chunk_index: number;
  status: string;
  role: string;
  metrics: Record<string, number | string | null>;
  quality: {
    score: number;
    severity: AsrSeverity;
    suspicious: boolean;
    reasons: string[];
  };
}

export interface AsrQualityReport {
  schema_version: string;
  job_id: string;
  paid_provider_calls: number;
  retranscription_enabled: boolean;
  baseline: {
    eligible_chunk_count: number;
    median_density_chars_per_min: number;
    median_char_count: number;
  };
  summary: {
    chunk_count: number;
    suspicious_chunk_count: number;
    high_count: number;
    medium_count: number;
  };
  chunks: AsrQualityChunk[];
}

export interface RetranscriptionCandidate {
  id: string;
  job_id: string;
  source_revision: number;
  chunk_index: number;
  recognizer: string;
  language_code: string;
  processing_strategy: string;
  estimated_cost_usd: string;
  confirmed_cost_usd: string;
  pricing_version: string;
  status: "queued" | "submitted" | "processing" | "completed" | "failed" | "rejected" | "applied" | "stale";
  requested_by: string;
  requested_at: string;
  updated_at: string;
  submitted_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  rejected_at: string | null;
  error_kind: string | null;
  error_safe_message: string | null;
}

export interface RetranscriptionPreview {
  job_id: string;
  job_revision: number;
  chunk_index: number;
  source_start_ms: number;
  source_end_ms: number;
  quality: {
    severity: AsrSeverity;
    score: number;
    reasons: string[];
    metrics: Record<string, number | string | null>;
  };
  recommended_for_retranscription: boolean;
  estimate: {
    duration_ms: number;
    billable_minutes: string;
    processing_strategy: string;
    chirp_usd_per_minute: string;
    estimated_cost_usd: string;
    estimated_cost_twd: string;
    pricing_version: string;
    project_limit_usd: string;
  };
  budget: {
    project_limit_usd: string;
    committed_before_request_usd?: string;
    committed_after_request_usd?: string;
    remaining_after_request_usd?: string;
  };
  existing_candidate: RetranscriptionCandidate | null;
  new_cost_reservation_required: boolean;
  retranscription_enabled: boolean;
  paid_operation_started: false;
}

export interface CandidateComparison {
  schema_version: string;
  chunk_index: number;
  source_evidence: {
    unchanged: boolean;
  };
  original: {
    raw_text: string;
    word_count: number;
    metrics: Record<string, number | string | null>;
  };
  candidate: {
    raw_text: string;
    word_count: number;
    metrics: Record<string, number | string | null>;
  };
  comparison: {
    text_similarity_ratio: number;
    text_changed: boolean;
    metric_deltas: Record<string, number>;
    signals: string[];
  };
  decision: "operator_review_required";
  auto_apply: false;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) {
    throw new Error(payload?.detail ?? `API 回應 ${response.status}`);
  }
  return payload as T;
}

const jobPath = (jobId: string) => `/jobs/${encodeURIComponent(jobId)}`;

export function getAsrQuality(jobId: string): Promise<AsrQualityReport> {
  return requestJson<AsrQualityReport>(`${jobPath(jobId)}/asr-quality`);
}

export function previewRetranscription(
  jobId: string,
  expectedRevision: number,
  chunkIndex: number,
): Promise<RetranscriptionPreview> {
  return requestJson<RetranscriptionPreview>(
    `${jobPath(jobId)}/retranscription-candidates/preview`,
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        chunk_index: chunkIndex,
      }),
    },
  );
}

export function createRetranscription(
  jobId: string,
  preview: RetranscriptionPreview,
): Promise<{ candidate: RetranscriptionCandidate; created: boolean; paid_operation_started: false }> {
  return requestJson(
    `${jobPath(jobId)}/retranscription-candidates`,
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: preview.job_revision,
        chunk_index: preview.chunk_index,
        confirmed_estimated_cost_usd: preview.estimate.estimated_cost_usd,
        force: false,
      }),
    },
  );
}

export async function listRetranscriptions(jobId: string): Promise<RetranscriptionCandidate[]> {
  const payload = await requestJson<{ candidates: RetranscriptionCandidate[] }>(
    `${jobPath(jobId)}/retranscription-candidates`,
  );
  return payload.candidates;
}

export function getRetranscription(
  jobId: string,
  candidateId: string,
): Promise<{ candidate: RetranscriptionCandidate; comparison: CandidateComparison | null }> {
  return requestJson(
    `${jobPath(jobId)}/retranscription-candidates/${encodeURIComponent(candidateId)}`,
  );
}

export function rejectRetranscription(
  jobId: string,
  candidateId: string,
  expectedRevision: number,
): Promise<{ candidate: RetranscriptionCandidate; accepted_artifacts_mutated: false }> {
  return requestJson(
    `${jobPath(jobId)}/retranscription-candidates/${encodeURIComponent(candidateId)}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
  );
}
