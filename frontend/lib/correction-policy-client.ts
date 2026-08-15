import type {
  ContentMode,
  CreatedBatch,
  OutputFormat,
  ProcessingStrategy,
} from "./types";

export type CorrectionPolicy = "GEMINI_FIRST" | "M3_FIRST";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API 回應 ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function createBatchWithPolicy(
  batchPreviewId: string,
  correctionPolicy: CorrectionPolicy,
  chirpMaxParallelChunks: number = 3,
  outputFormats: OutputFormat[] = ["srt", "txt", "csv"],
  processingStrategy: ProcessingStrategy = "DYNAMIC_BATCHING",
  contentMode: ContentMode = "general",
  documentContext: string = "",
): Promise<CreatedBatch & { correctionPolicy: CorrectionPolicy }> {
  const result = await postJson<{
    batch_id: string;
    status: string;
    item_count: number;
    job_ids: string[];
    created_at: string;
    paid_operation_started: false;
    next_action: string;
    processing_strategy: ProcessingStrategy;
    correction_policy: CorrectionPolicy;
  }>("/batches", {
    batch_preview_id: batchPreviewId,
    language_code: "cmn-Hant-TW",
    profile: "highest_accuracy",
    enable_gemini_correction: true,
    enable_subtitles: true,
    require_human_review: false,
    processing_strategy: processingStrategy,
    chirp_max_parallel_chunks: chirpMaxParallelChunks,
    output_formats: outputFormats,
    content_mode: contentMode,
    document_context: documentContext,
    correction_policy: correctionPolicy,
  });
  return {
    batchId: result.batch_id,
    status: result.status,
    itemCount: result.item_count,
    jobIds: result.job_ids,
    createdAt: result.created_at,
    paidOperationStarted: result.paid_operation_started,
    nextAction: result.next_action,
    processingStrategy: result.processing_strategy,
    correctionPolicy: result.correction_policy,
  };
}

export async function getCorrectionProviderStatus(): Promise<{
  defaultPolicy: CorrectionPolicy;
  m3Enabled: boolean;
  quotaLiveCheck: boolean;
}> {
  const response = await fetch(`${baseUrl}/correction/provider-status`, {
    cache: "no-store",
  });
  if (!response.ok) {
    return {
      defaultPolicy: "GEMINI_FIRST",
      m3Enabled: false,
      quotaLiveCheck: false,
    };
  }
  const payload = await response.json() as {
    default_policy?: CorrectionPolicy;
    m3_enabled?: boolean;
    quota_live_check?: boolean;
  };
  return {
    defaultPolicy: payload.default_policy ?? "GEMINI_FIRST",
    m3Enabled: Boolean(payload.m3_enabled),
    quotaLiveCheck: Boolean(payload.quota_live_check),
  };
}
