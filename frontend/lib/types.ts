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
  | "awaiting_review"
  | "review"
  | "completed"
  | "failed";

export type PipelineStepStatus = "completed" | "running" | "pending" | "warning" | "failed";

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
  estimatedCostUsd?: string | null;
  chirpMaxParallelChunks?: number;
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

export interface BillingSummary {
  status: string;
  source?: string;
  targetProjectId?: string;
  billingCurrency?: string;
  projectGrossCost?: string;
  projectCredits?: string;
  projectNetCost?: string;
  projectGrossCostUsd?: string | null;
  accountPromotionCreditsUsed?: string;
  accountPromotionCreditsUsedUsd?: string | null;
  initialFreeTrialCreditUsd?: string;
  estimatedRemainingFreeTrialCreditUsd?: string | null;
  coverageStart?: string;
  coverageEnd?: string;
  lastBillingDataAt?: string | null;
  snapshotGeneratedAt?: string;
  dataAgeSeconds?: number;
  isEstimatedRemainingCredit?: boolean;
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
  jobs: TranscriptJob[];
}
