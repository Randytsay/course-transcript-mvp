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
import { formatTwd } from "@/lib/currency";
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
  const failedCounts = useMemo(() => {
    const jobs = batch?.jobs ?? [];
    const preflight = jobs.filter((job) => job.status === "failed" && job.activeStage === "preflight").length;
    const download = jobs.filter((job) => job.status === "failed" && ["download", "normalize"].includes(job.activeStage ?? "")).length;
    return {
      preflight,
      download,
      other: Math.max(0, (batch?.failedCount ?? 0) - preflight - download),
    };
  }, [batch]);

  async function approve() {
    if (!batch?.estimatedCostUsd) return;
    setApproving(true);
    setError(null);
    try {
      await approveBatch(batch.id, batch.revision, batch.estimatedCostUsd);
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
      description="系統會先安全檢查檔案，再依序開始辨識；只有超出成本安全門檻時才需要你確認。"
      actions={<Link className="button button--secondary" href="/jobs/new"><ArrowLeft size={15} />返回選檔</Link>}
    >
      {error && <div className="form-error"><TriangleAlert size={17} /><span>{error}</span></div>}
      {!batch && !error && <div className="batch-loading"><LoaderCircle className="spin" size={20} />正在讀取批次…</div>}
      {batch && (
        <>
          <section className="batch-overview-grid">
            <article className="metric-card"><div className="metric-icon metric-icon--blue"><FileAudio2 size={20} /></div><div className="metric-copy"><span>影音檔案</span><strong>{batch.itemCount}</strong><small>Preflight {preflightDone} / {batch.itemCount}</small></div></article>
            <article className="metric-card"><div className="metric-icon metric-icon--violet"><Clock3 size={20} /></div><div className="metric-copy"><span>總音訊時長</span><strong>{batch.totalDurationSeconds ? duration(batch.totalDurationSeconds) : "檢查中"}</strong><small>由 FFprobe 逐檔確認</small></div></article>
            <article className="metric-card"><div className="metric-icon metric-icon--amber"><Coins size={20} /></div><div className="metric-copy"><span>整批估計費用</span><strong>{batch.estimatedCostTwd ? formatTwd(batch.estimatedCostTwd) : "計算中"}</strong><small>非 Cloud Billing 實際帳務</small></div></article>
            <article className="metric-card"><div className="metric-icon metric-icon--violet"><Clock3 size={20} /></div><div className="metric-copy"><span>辨識模式</span><strong>{batch.processingStrategy === "DYNAMIC_BATCHING" ? "經濟模式" : "快速模式"}</strong><small>{batch.processingStrategy === "DYNAMIC_BATCHING" ? "Google 離峰批次，最多約 24 小時" : "Standard Batch，較快但費用較高"}</small></div></article>
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
              <h2>開始狀態</h2>
              {batch.status === "preflight" && <p>正在檢查檔案格式、時長與預估費用。這個階段不會產生辨識費用；符合安全門檻後會自動開始。</p>}
              {batch.status === "awaiting_confirmation" && batch.estimatedCostUsd && (
                <>
                  <p>
                    這批任務的預估費用 <strong>{formatTwd(batch.estimatedCostTwd)}</strong> 超出目前自動安全門檻，或受專案預算限制，因此沒有自動開始。
                  </p>
                  <button className="button button--primary button--full button--large" disabled={approving} onClick={() => void approve()}>
                    {approving ? <LoaderCircle className="spin" size={16} /> : <CheckCircle2 size={16} />}
                    確認費用並開始處理
                  </button>
                  <small>一般任務會自動開始；只有超出安全門檻的批次才會看到這個確認動作。</small>
                </>
              )}
              {batch.status === "queued" && <p className="approval-success"><CheckCircle2 size={17} />安全檢查已通過，檔案正在依序等待辨識。</p>}
              {failedCounts.preflight > 0 && <p className="approval-warning"><TriangleAlert size={17} />有 {failedCounts.preflight} 個檔案未通過本機媒體檢查，不會產生辨識費用。</p>}
              {failedCounts.download > 0 && <p className="approval-warning"><TriangleAlert size={17} />有 {failedCounts.download} 個檔案在來源下載或前處理階段失敗，尚未進入付費辨識，不會產生辨識費用。</p>}
              {failedCounts.other > 0 && <p className="approval-warning"><TriangleAlert size={17} />有 {failedCounts.other} 個檔案在後續處理階段失敗，請查看檔案明細與實際成本紀錄。</p>}
              <small>程式預算以台幣估算；Cloud Billing 才是實際帳務依據。</small>
            </aside>
          </section>
        </>
      )}
    </AppShell>
  );
}
