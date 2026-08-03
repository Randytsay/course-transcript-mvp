"use client";

import { Ban, LoaderCircle, Pause, Play, RotateCcw, X } from "lucide-react";
import { useEffect, useState } from "react";
import styles from "./job-controls.module.css";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const pausable = new Set([
  "queued",
  "downloading",
  "normalizing",
  "transcribing",
  "merging",
  "segmenting",
  "correcting",
  "exporting",
  "quality_check",
]);
const cancellable = new Set([
  "preflight",
  "awaiting_confirmation",
  "queued",
  "downloading",
  "normalizing",
  "transcribing",
  "merging",
  "segmenting",
  "correcting",
  "exporting",
  "quality_check",
  "paused",
  "failed",
]);

type JobState = {
  status: string;
  revision: number;
  activeStage?: string | null;
  stageDetail?: string | null;
};

function mapJob(value: unknown): JobState | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const nested = payload.job && typeof payload.job === "object"
    ? payload.job as Record<string, unknown>
    : payload;
  return {
    status: String(nested.status ?? payload.status ?? ""),
    revision: Number(nested.revision ?? payload.revision ?? 0),
    activeStage: nested.active_stage
      ? String(nested.active_stage)
      : nested.activeStage
        ? String(nested.activeStage)
        : null,
    stageDetail: nested.stage_detail
      ? String(nested.stage_detail)
      : nested.stageDetail
        ? String(nested.stageDetail)
        : payload.stageDetail
          ? String(payload.stageDetail)
          : null,
  };
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${apiBase}${path}`, {
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
    ...init,
  });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? `操作失敗 (${response.status})`);
  return payload;
}

export default function JobControls({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobState | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reason, setReason] = useState("來源檔或設定需要重新確認");
  const [cleanupTemporary, setCleanupTemporary] = useState(false);

  async function refresh() {
    try {
      setJob(mapJob(await requestJson(`/jobs/${encodeURIComponent(jobId)}`)));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取得任務狀態");
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [jobId]);

  async function action(kind: "pause" | "resume") {
    if (!job || job.revision < 1) return;
    setBusy(kind);
    setError(null);
    try {
      const payload = await requestJson(
        `/jobs/${encodeURIComponent(jobId)}/${kind}`,
        {
          method: "POST",
          body: JSON.stringify({ expected_revision: job.revision }),
        },
      );
      setJob(mapJob(payload));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任務操作失敗");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function retryJob() {
    if (!job || job.revision < 1) return;
    setBusy("retry");
    setError(null);
    try {
      const stage = job.activeStage ?? "chirp";
      const payload = await requestJson(
        `/jobs/${encodeURIComponent(jobId)}/retry-stage`,
        {
          method: "POST",
          body: JSON.stringify({ expected_revision: job.revision, stage }),
        },
      );
      setJob(mapJob(payload));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重試失敗");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function cancelJob() {
    if (!job || job.revision < 1 || !reason.trim()) return;
    setBusy("cancel");
    setError(null);
    try {
      const payload = await requestJson(
        `/jobs/${encodeURIComponent(jobId)}/cancel`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: job.revision,
            reason: reason.trim(),
            cleanup_mode: cleanupTemporary ? "temporary" : "preserve",
          }),
        },
      );
      setJob(mapJob(payload));
      setDialogOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取消任務");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  if (!job) return null;

  return (
    <div className={styles.wrapper} aria-live="polite">
      <div className={styles.statusCopy}>
        <strong>任務控制</strong>
        <span>{job.status}{job.stageDetail ? `｜${job.stageDetail}` : ""}</span>
        {error && <span className={styles.error}>{error}</span>}
      </div>
      <div className={styles.actions}>
        {pausable.has(job.status) && (
          <button
            type="button"
            className="button button--secondary"
            disabled={busy !== null}
            onClick={() => void action("pause")}
          >
            {busy === "pause" ? <LoaderCircle className="spin" size={18} /> : <Pause size={18} />}
            暫停
          </button>
        )}
        {job.status === "paused" && (
          <button
            type="button"
            className="button button--primary"
            disabled={busy !== null}
            onClick={() => void action("resume")}
          >
            {busy === "resume" ? <LoaderCircle className="spin" size={18} /> : <Play size={18} />}
            繼續
          </button>
        )}
        {job.status === "failed" && (
          <button
            type="button"
            className="button button--primary"
            disabled={busy !== null}
            onClick={() => void retryJob()}
            title={`重試失敗的 ${job.activeStage ?? "chirp"} 階段`}
          >
            {busy === "retry" ? <LoaderCircle className="spin" size={18} /> : <RotateCcw size={18} />}
            重試失敗階段
          </button>
        )}
        {cancellable.has(job.status) && (
          <button
            type="button"
            className={styles.cancelButton}
            disabled={busy !== null}
            onClick={() => setDialogOpen(true)}
          >
            <Ban size={18} />取消任務
          </button>
        )}
        {job.status === "cancelling" && (
          <span className={styles.cancelling}><LoaderCircle className="spin" size={18} />正在安全停止</span>
        )}
        {job.status === "cancelled" && <span className={styles.cancelled}>任務已取消</span>}
      </div>

      {dialogOpen && (
        <div className={styles.scrim} role="presentation">
          <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="cancel-title">
            <button className={styles.close} type="button" onClick={() => setDialogOpen(false)} aria-label="關閉取消視窗"><X size={20} /></button>
            <h2 id="cancel-title">永久取消這個任務？</h2>
            <p>系統會停止本機程序，並盡力取消已送出的 Chirp 雲端操作。雲端取消不保證成功，已產生的費用不會退回。</p>
            <label className={styles.field}>
              <span>取消原因</span>
              <textarea value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} />
            </label>
            <label className={styles.checkbox}>
              <input type="checkbox" checked={cleanupTemporary} onChange={(event) => setCleanupTemporary(event.target.checked)} />
              <span>取消後清理大型暫存音檔；保留 manifest、原始辨識結果與稽核證據</span>
            </label>
            <div className={styles.dialogActions}>
              <button type="button" className="button button--secondary" onClick={() => setDialogOpen(false)}>返回</button>
              <button type="button" className={styles.confirmCancel} disabled={busy !== null || !reason.trim()} onClick={() => void cancelJob()}>
                {busy === "cancel" ? <LoaderCircle className="spin" size={18} /> : <Ban size={18} />}
                確認永久取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
