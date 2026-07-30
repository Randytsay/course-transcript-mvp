import type { Artifact, PipelineStep, ReviewTerm, TranscriptJob, TranscriptSegment } from "./types";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

type ApiJob = Omit<TranscriptJob, "sourcePath" | "durationSeconds" | "createdAt" | "updatedAt" | "reviewTerms"> & {
  source_path: string;
  duration_seconds: number;
  created_at: string;
  updated_at: string;
  review_terms: number;
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
  };
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 404 ? "找不到指定任務" : `API 回應 ${response.status}`);
  return response.json() as Promise<T>;
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
  const result = await fetchJson<{ review_terms: Array<{ id: string; heard: string; suggestion: string; timestamp: string; confidence: "low" | "medium"; status: "pending" | "confirmed" | "ignored" }> }>(`/jobs/${encodeURIComponent(id)}/review-terms`);
  return result.review_terms;
}

export async function getArtifacts(id: string): Promise<Artifact[]> {
  const result = await fetchJson<{ artifacts: Array<{ id: string; name: string; size_bytes: number; updated_at: string }> }>(`/jobs/${encodeURIComponent(id)}/artifacts`);
  return result.artifacts.map((item) => ({ id: item.id, name: item.name, sizeBytes: item.size_bytes, updatedAt: formatDate(item.updated_at) }));
}
