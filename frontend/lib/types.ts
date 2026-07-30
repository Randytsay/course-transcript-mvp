export type JobStatus =
  | "queued"
  | "downloading"
  | "normalizing"
  | "transcribing"
  | "correcting"
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
}
