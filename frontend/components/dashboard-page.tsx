"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Clock3, FileAudio2, MoreHorizontal, Plus, TriangleAlert } from "lucide-react";
import { getJobs } from "@/lib/api-client";
import type { TranscriptJob } from "@/lib/types";
import { formatTwd } from "@/lib/currency";
import ProgressRing from "./progress-ring";
import StatusBadge from "./status-badge";
import { useCallback, useEffect, useMemo, useState } from "react";

export default function DashboardPage() {
  const [jobs, setJobs] = useState<TranscriptJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setJobs(await getJobs());
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "無法讀取任務");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search).get("q") ?? "";
    setSearchQuery(query);
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [load]);

  const reviewing = jobs.filter((job) => ["review", "awaiting_review"].includes(job.status)).length;
  const active = jobs.filter((job) => ["preflight", "queued", "downloading", "normalizing", "transcribing", "merging", "segmenting", "correcting", "exporting", "quality_check"].includes(job.status)).length;
  const awaitingConfirmation = jobs.filter((job) => job.status === "awaiting_confirmation").length;
  const firstAwaitingJob = jobs.find((job) => job.status === "awaiting_confirmation");
  const completed = jobs.filter((job) => job.status === "completed").length;
  const filteredJobs = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    if (!query) return jobs;
    return jobs.filter((job) => [job.id, job.filename, job.course].some((value) => value.toLocaleLowerCase().includes(query)));
  }, [jobs, searchQuery]);
  const visibleJobs = useMemo(() => showAll ? filteredJobs : filteredJobs.slice(0, 4), [filteredJobs, showAll]);
  function handleSearch(query: string) {
    setSearchQuery(query);
    window.history.replaceState(null, "", `/?q=${encodeURIComponent(query)}#jobs`);
    window.requestAnimationFrame(() => document.getElementById("jobs")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
  const metrics = [
    { label: "處理中", value: String(active), detail: active ? "辨識工作正在進行" : "目前沒有處理中的任務", icon: Activity, tone: "blue" },
    { label: "需要處理", value: String(awaitingConfirmation), detail: awaitingConfirmation ? "超出自動安全門檻，需人工確認" : "目前沒有需要介入的任務", icon: TriangleAlert, tone: "amber" },
    { label: "待審查", value: String(reviewing), detail: reviewing ? "已有文字結果可開始審查" : "目前沒有待審查內容", icon: FileAudio2, tone: "violet" },
    { label: "已完成", value: String(completed), detail: "已完成並可使用的轉錄成果", icon: CheckCircle2, tone: "green" },
  ];

  return (
    <AppShell title="轉錄工作台" description="選檔、開始辨識，然後在這裡追蹤進度與結果。" onSearch={handleSearch} actions={<Link href="/jobs/new" className="button button--primary"><Plus size={17} />開始新的辨識</Link>}>
      {firstAwaitingJob && (
        <div style={{ marginBottom: "20px", padding: "16px 20px", background: "#fff7ed", border: "2px solid #f59e0b", borderRadius: "12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <TriangleAlert size={26} className="text-warning" style={{ flexShrink: 0 }} />
            <div>
              <strong style={{ fontSize: "var(--font-md)", color: "#8a4b13" }}>有 {awaitingConfirmation} 個任務需要你確認</strong>
              <p style={{ margin: "4px 0 0", fontSize: "var(--font-sm)", color: "#9a3412", lineHeight: 1.6 }}>
                一般任務會自動開始。<strong>{firstAwaitingJob.filename}</strong> 因成本超出自動安全門檻或預算限制而停下，預估 {firstAwaitingJob.estimatedCostTwd ? formatTwd(firstAwaitingJob.estimatedCostTwd) : "仍在計算"}。
              </p>
            </div>
          </div>
          <Link href={firstAwaitingJob.batchId ? `/batches/${firstAwaitingJob.batchId}` : `/jobs/${firstAwaitingJob.id}`} className="button button--primary button--large">
            <CheckCircle2 size={18} />查看並處理
          </Link>
        </div>
      )}

      <section className="metric-grid" aria-label="轉錄統計">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return <article className="metric-card" key={metric.label}><div className={`metric-icon metric-icon--${metric.tone}`}><Icon size={20} /></div><div className="metric-copy"><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.detail}</small></div></article>;
        })}
      </section>

      <section className="dashboard-grid dashboard-grid--single">
        <div style={{ display: "grid", gap: "18px" }}>
          <div className="panel panel--jobs" id="jobs">
            <div className="panel-header"><div><h2>最近任務</h2><p>{searchQuery ? `搜尋「${searchQuery}」找到 ${filteredJobs.length} 筆。` : showAll ? `目前顯示全部 ${jobs.length} 筆工作。` : `目前顯示最近 ${Math.min(4, jobs.length)} 筆工作。`}</p></div>{filteredJobs.length > 4 && <button type="button" className="button button--ghost" onClick={() => setShowAll((current) => !current)}>{showAll ? "收合" : "查看全部"} <ArrowRight size={16} /></button>}</div>
            <div className="jobs-table" role="table" aria-label="最近轉錄任務">
              <div className="jobs-table__header" role="row"><span>檔案與課程</span><span>處理進度</span><span>狀態</span><span>更新時間</span><span /></div>
              {loading && <div className="empty-state">正在讀取後端任務資料…</div>}
              {error && <div className="empty-state empty-state--error">後端目前無法連線：{error}</div>}
              {!loading && !error && jobs.length === 0 && <div className="empty-state">尚無已登記的本機任務。</div>}
              {!loading && !error && jobs.length > 0 && filteredJobs.length === 0 && <div className="empty-state">找不到符合的檔名、課程或任務編號。</div>}
              {visibleJobs.map((job) => (
                <div className="jobs-table__row" role="row" key={job.id}>
                  <div className="job-file-cell"><div className="file-icon"><FileAudio2 size={20} /></div><div><Link href={`/jobs/${job.id}`} className="job-name">{job.filename}</Link><span>{job.course} · {job.duration}</span></div></div>
                  <div className="job-progress-cell"><ProgressRing value={job.progress} /><span>{job.progress === 100 ? "處理完成" : `${job.progress}%`}</span></div>
                  <div>
                    {job.status === "awaiting_confirmation" ? (
                      <Link href={job.batchId ? `/batches/${job.batchId}` : `/jobs/${job.id}`} className="button button--confirm button--small">
                        <CheckCircle2 size={14} />{job.estimatedCostTwd ? `確認費用 ${formatTwd(job.estimatedCostTwd)}` : "確認費用"}
                      </Link>
                    ) : (
                      <StatusBadge status={job.status} />
                    )}
                  </div>
                  <div className="updated-cell"><Clock3 size={15} />{job.updatedAt}</div>
                  <div className="row-action"><Link className="icon-button" href={`/jobs/${job.id}`} aria-label={`查看 ${job.filename}`}><MoreHorizontal size={19} /></Link></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </AppShell>
  );
}
