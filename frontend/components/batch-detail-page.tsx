"use client";

import AppShell from "./app-shell";
import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Coins,
  FileAudio2,
  LoaderCircle,
  ShieldAlert,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { approveBatch, getBatch } from "@/lib/api-client";
import type { BatchDetail } from "@/lib/types";
import StatusBadge from "./status-badge";

const IDLE_BATCH_STATUSES = new Set([
  "awaiting_confirmation",
  "awaiting_review",
  "completed",
  "failed",
]);

function duration(seconds: number) {
  const total = Math.round(seconds);
  return `${Math.floor(total / 3600)} 小時 ${Math.floor((total % 3600) / 60)} 分`;
}

export default function BatchDetailPage({ batchId }: { batchId: string }) {
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBatch(await getBatch(batchId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法讀取批次");
    }
  }, [batchId]);

  useEffect(() => {
    if (!batch) void load();
    if (batch && IDLE_BATCH_STATUSES.has(batch.status)) return;

    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [batch, load]);

  const preflightDone = useMemo(
    () => batch?.jobs.filter((job) => ["awaiting_confirmation", "failed", "queued"].includes(job.status)).length ?? 0,
    [batch],
  );

  async function approve() {
    if (!batch?.estimatedCostUsd || !confirmed) return;
    setApproving(true);
    setError(null);
    try {
      await approveBatch(batch.id, batch.revision, batch.estimatedCostUsd);
      setConfirmed(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法確認批次費用");
    } finally {
      setApproving(false);
    }
  }

  return (
    <AppShell
      title={batch?.name ?? "批次任務"}
      description="逐檔本機檢查、整批費用確認、依序轉錄。"
      actions={<Link className="button button--secondary" href="/jobs/new"><ArrowLeft size={15} />返回選檔</Link>}
    >
      {error && <div className="form-error"><TriangleAlert size={17} /><span>{error}</span></div>}
      {!batch && !error && <div className="batch-loading"><LoaderCircle className="spin" size={20} />正在讀取批次…</div>}
      {batch && (
        <>
          <section className="batch-overview-grid">
            <article className="metric-card"><div className="metric-icon metric-icon--blue"><FileAudio2 size={20} /></div><div className="metric-copy"><span>影音檔案</span><strong>{batch.itemCount}</strong><small>Preflight {preflightDone} / {batch.itemCount}</small></div></article>
            <article className="metric-card"><div className="metric-icon metric-icon--violet"><Clock3 size={20} /></div><div className="metric-copy"><span>總音訊時長</span><strong>{batch.totalDurationSeconds ? duration(batch.totalDurationSeconds) : "檢查中"}</strong><small>由 FFprobe 逐檔確認</small></div></article>
            <article className="metric-card"><div className="metric-icon metric-icon--amber"><Coins size={20} /></div><div className="metric-copy"><span>整批估計費用</span><strong>{batch.estimatedCostUsd ? `US$${batch.estimatedCostUsd}` : "計算中"}</strong><small>非 Cloud Billing 實際帳務</small></div></article>
          </section>

          <section className="batch-detail-grid">
            <div className="panel batch-job-panel">
              <div className="panel-header"><div><h2>檔案佇列</h2><p>同時間只會有一個來源檔進入實際處理。</p></div><span className="batch-state">{batch.status}</span></div>
              <div className="batch-job-list">
                {batch.jobs.map((job, index) => (
                  <div className="batch-job-row" key={job.id}>
                    <span className="batch-position">{String(index + 1).padStart(2, "0")}</span>
                    <div className="file-icon"><FileAudio2 size={18} /></div>
                    <div><Link href={`/jobs/${job.id}`}>{job.filename}</Link><span>{job.duration} · {job.sourcePath}</span></div>
                    <StatusBadge status={job.status} />
                  </div>
                ))}
              </div>
            </div>

            <aside className="panel batch-approval-card">
              <div className="approval-icon"><ShieldAlert size={22} /></div>
              <h2>付費操作確認</h2>
              {batch.status === "preflight" && <p>Worker 正在逐檔檢查格式與時長。完成前不會呼叫 Chirp 或 Gemini。</p>}
              {batch.status === "awaiting_confirmation" && batch.estimatedCostUsd && (
                <>
                  <p>確認後，這 {batch.itemCount} 個檔案將依序進入付費辨識。預估總額為 <strong>US${batch.estimatedCostUsd}</strong>。</p>
                  <label className="approval-check">
                    <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                    <span>我確認這是估計費用，並授權此批次進入付費處理佇列。</span>
                  </label>
                  <button className="button button--primary button--full button--large" disabled={!confirmed || approving} onClick={() => void approve()}>
                    {approving ? <LoaderCircle className="spin" size={16} /> : <CheckCircle2 size={16} />}
                    確認並排入處理
                  </button>
                </>
              )}
              {batch.status === "queued" && <p className="approval-success"><CheckCircle2 size={17} />費用已由你確認，檔案正在依序等待 Worker。</p>}
              {batch.failedCount > 0 && <p className="approval-warning"><TriangleAlert size={17} />有 {batch.failedCount} 個檔案未通過本機媒體檢查，不會產生辨識費用。</p>}
              <small>程式上限 US$200；Cloud Billing 才是實際帳務依據。</small>
            </aside>
          </section>
        </>
      )}
    </AppShell>
  );
}
