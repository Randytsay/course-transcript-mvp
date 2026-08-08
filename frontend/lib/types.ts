export type JobStatus =
  | "preflight"
  | "awaiting_confirmation"
  | "queued"
  | "downloading"
  | "normalizing"
  | "transcribing"
  | "merging"
  | "segmenting"
  | "correcting"
  | "exporting"
  | "quality_check"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "awaiting_review"
  | "review"
  | "completed"
  | "failed";

export type PipelineStepStatus = "completed" | "running" | "pending" | "warning" | "failed";
export type OutputFormat = "srt" | "txt" | "csv" | "vtt" | "ass" | "docx" | "pdf";
export type ProcessingStrategy = "DYNAMIC_BATCHING" | "STANDARD_BATCH";

export interface PipelineStep {
  id: string;
  label: string;
  detail: string;
  status: PipelineStepStatus;
}

export interface TranscriptSegment {
  id: string;
  startMs: number;
  endMs: number;
  rawText: string;
  correctedText: string;
  uncertainTerms?: string[];
  revision: number;
}

export interface Artifact {
  id: string;
  name: string;
  sizeBytes: number;
  updatedAt: string;
}

export interface ReviewTerm {
  id: string;
  heard: string;
  suggestion: string;
  timestamp: string;
  confidence: "low" | "medium";
  status: "pending" | "confirmed" | "ignored";
  scope?: "session" | "course" | "instructor" | "global";
  approvedValue?: string | null;
}

export interface TranscriptJob {
  id: string;
  filename: string;
  sourcePath: string;
  course: string;
  duration: string;
  durationSeconds: number;
  progress: number;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  language: string;
  model: string;
  words: number;
  reviewTerms: number;
  pipeline: PipelineStep[];
  activeStage?: string | null;
  stageDetail?: string | null;
  error?: string | null;
  revision: number;
  batchId?: string | null;
  processingStrategy: ProcessingStrategy;
  estimatedCostUsd?: string | null;
  chirpMaxParallelChunks?: number;
  outputFormats: OutputFormat[];
  drivePublished?: boolean;
  drivePublicationStatus?: string | null;
}

export interface JobEvent {
  id: number;
  eventType: string;
  actor: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface DriveEntry {
  sourcePath: string;
  name: string;
  isDir: boolean;
  sizeBytes: number;
  modifiedAt: string | null;
  mimeType: string | null;
  supportedMedia: boolean;
}

export interface DriveDirectory {
  currentPath: string;
  parentPath: string | null;
  entries: DriveEntry[];
}

export interface BatchPreviewItem {
  previewId: string;
  sourcePath: string;
  name: string;
  sizeBytes: number;
  modifiedAt: string | null;
}

export interface BatchPreview {
  batchPreviewId: string;
  selectionMode: "files" | "folder";
  sourceRoot: string | null;
  itemCount: number;
  totalSizeBytes: number;
  expiresAt: string;
  items: BatchPreviewItem[];
  paidOperationStarted: false;
}

export interface CreatedBatch {
  batchId: string;
  status: string;
  itemCount: number;
  jobIds: string[];
  createdAt: string;
  paidOperationStarted: false;
  nextAction: string;
  processingStrategy: ProcessingStrategy;
}

export interface CostSummary {
  projectLimitUsd: string;
  committedEstimatedCostUsd: string;
  recordedActualCostUsd: string;
  remainingEstimatedBudgetUsd: string;
  warningThresholdsUsd: string[];
  pricingVersion: string;
  accountingNote: string;
}

export interface ChunkProgress {
  chunkIndex: number;
  startMs: number;
  endMs: number;
  durationMs: number;
  status: string;
  wordCount: number;
  hasTranscript: boolean;
  updatedAt?: string | null;
  error?: string | null;
}

export interface ChunkProgressResponse {
  jobId: string;
  jobStatus: string;
  completedCount: number;
  totalCount: number;
  parallelism: number;
  canaryCompleted: boolean;
  updatedAt?: string | null;
  chunks: ChunkProgress[];
}

export interface ChunkTranscript {
  chunkIndex: number;
  startMs: number;
  endMs: number;
  status: string;
  wordCount: number;
  rawText: string;
  completedAt?: string | null;
  isFinal: boolean;
  warning: string;
}

export interface LiveCost {
  estimatedTotalUsd: string;
  estimatedAccruedUsd: string;
  estimatedRemainingUsd: string;
  chirpEstimatedUsd: string;
  geminiEstimatedUsd: string;
  submittedChunkCount: number;
  completedChunkCount: number;
  isEstimate: boolean;
  warning?: string;
}

export interface BillingSummary {
  status: string;
  source?: string;
  targetProjectId?: string;
  billingCurrency?: string | null;
  currencyCount?: number;
  projectGrossCost?: string | null;
  projectCredits?: string | null;
  projectNetCost?: string | null;
  projectGrossCostUsd?: string | null;
  projectCreditsUsd?: string | null;
  projectNetCostUsd?: string | null;
  accountPromotionCreditsUsed?: string | null;
  accountPromotionCreditsUsedUsd?: string | null;
  freeTrialPromotionCreditsUsedUsd?: string | null;
  initialFreeTrialCreditUsd?: string;
  estimatedRemainingFreeTrialCreditUsd?: string | null;
  coverageStart?: string;
  coverageEnd?: string | null;
  lastBillingDataAt?: string | null;
  snapshotGeneratedAt?: string | null;
  dataAgeSeconds?: number | null;
  isEstimatedRemainingCredit?: boolean;
  conversionComplete?: boolean;
  warning?: string;
}

export interface BatchDetail {
  id: string;
  name: string;
  status: string;
  selectionMode: "files" | "folder";
  sourceRoot: string | null;
  itemCount: number;
  completedCount: number;
  failedCount: number;
  estimatedCostUsd: string | null;
  reservedCostUsd: string;
  actualCostUsd: string;
  totalDurationSeconds: number;
  createdAt: string;
  updatedAt: string;
  revision: number;
  processingStrategy: ProcessingStrategy;
  jobs: TranscriptJob[];
}
