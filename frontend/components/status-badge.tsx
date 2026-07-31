import type { JobStatus } from "@/lib/types";

const statusLabels: Record<JobStatus, string> = {
  preflight: "媒體檢查",
  awaiting_confirmation: "待確認費用",
  queued: "排隊中",
  downloading: "下載中",
  normalizing: "音訊處理",
  transcribing: "Chirp 辨識",
  correcting: "Gemini 校正",
  review: "待審查",
  completed: "已完成",
  failed: "失敗"
};

export default function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`status-badge status-badge--${status}`}>{statusLabels[status]}</span>;
}
