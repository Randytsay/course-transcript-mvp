"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Clock3, FileAudio2, MoreHorizontal, Plus, ShieldCheck, Sparkles, TimerReset, TriangleAlert } from "lucide-react";
import { getCosts, getJobs } from "@/lib/api-client";
import type { CostSummary, TranscriptJob } from "@/lib/types";
import ProgressRing from "./progress-ring";
import StatusBadge from "./status-badge";
import { useEffect, useState } from "react";

export default function DashboardPage() {
  const [jobs, setJobs] = useState<TranscriptJob[]>([]);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    Promise.all([getJobs(), getCosts()])
      .then(([nextJobs, nextCosts]) => { setJobs(nextJobs); setCosts(nextCosts); })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "無法讀取任務"))
      .finally(() => setLoading(false));
  }, []);
  const reviewing = jobs.filter((job) => ["review", "awaiting_review"].includes(job.status)).length;
  const active = jobs.filter((job) => ["preflight", "queued", "downloading", "normalizing", "transcribing", "merging", "segmenting", "correcting", "exporting", "quality_check"].includes(job.status)).length;
  const awaitingConfirmation = jobs.filter((job) => job.status === "awaiting_confirmation").length;
  const completed = jobs.filter((job) => ["completed", "review", "awaiting_review"].includes(job.status)).length;
  const metrics = [
    { label: "處理中任務", value: String(active), detail: active ? "後端目前正在處理" : "目前沒有處理中的任務", icon: Activity, tone: "blue" },
    { label: "待人工確認", value: String(reviewing + awaitingConfirmation), detail: awaitingConfirmation ? `${awaitingConfirmation} 個待確認費用` : reviewing ? `${reviewing} 個待內容審查` : "目前沒有待確認項目", icon: TriangleAlert, tone: "amber" },
    { label: "已有輸出", value: String(completed), detail: "包含待審查與已完成任務", icon: CheckCircle2, tone: "green" },
    { label: "剩餘預估額度", value: costs ? `US$${costs.remainingEstimatedBudgetUsd}` : "—", detail: "上限 US$200；非實際帳務", icon: TimerReset, tone: "violet" },
  ];
  return (
    <AppShell title="轉錄儀表板" description="掌握長檔辨識進度、人工審查與輸出狀態。" actions={<Link href="/jobs/new" className="button button--primary"><Plus size={17} />新增轉錄任務</Link>}>
      <section className="metric-grid" aria-label="轉錄統計">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return <article className="metric-card" key={metric.label}><div className={`metric-icon metric-icon--${metric.tone}`}><Icon size={20} /></div><div className="metric-copy"><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.detail}</small></div></article>;
        })}
      </section>

      <section className="dashboard-grid">
        <div className="panel panel--jobs" id="jobs">
          <div className="panel-header"><div><h2>最近任務</h2><p>目前顯示最近更新的 4 筆工作。</p></div><button className="button button--ghost">查看全部 <ArrowRight size={16} /></button></div>
          <div className="jobs-table" role="table" aria-label="最近轉錄任務">
            <div className="jobs-table__header" role="row"><span>檔案與課程</span><span>處理進度</span><span>狀態</span><span>更新時間</span><span /></div>
            {loading && <div className="empty-state">正在讀取後端任務資料…</div>}
            {error && <div className="empty-state empty-state--error">後端目前無法連線：{error}</div>}
            {!loading && !error && jobs.length === 0 && <div className="empty-state">尚無已登記的本機任務。</div>}
            {jobs.map((job) => (
              <div className="jobs-table__row" role="row" key={job.id}>
                <div className="job-file-cell"><div className="file-icon"><FileAudio2 size={20} /></div><div><Link href={`/jobs/${job.id}`} className="job-name">{job.filename}</Link><span>{job.course} · {job.duration}</span></div></div>
                <div className="job-progress-cell"><ProgressRing value={job.progress} /><span>{job.progress === 100 ? "處理完成" : `${job.progress}%`}</span></div>
                <div><StatusBadge status={job.status} /></div>
                <div className="updated-cell"><Clock3 size={15} />{job.updatedAt}</div>
                <div className="row-action"><Link className="icon-button" href={`/jobs/${job.id}`} aria-label={`查看 ${job.filename}`}><MoreHorizontal size={19} /></Link></div>
              </div>
            ))}
          </div>
        </div>

        <aside className="dashboard-side">
          <div className="panel health-panel">
            <div className="panel-header panel-header--compact"><div><h2>管線連線狀態</h2><p>來自目前 API 回應</p></div><span className="health-score">{error ? "異常" : loading ? "檢查中" : "正常"}</span></div>
            <div className="health-list">
              <div><span className="service-icon service-icon--green"><ShieldCheck size={16} /></span><div><strong>Google Drive</strong><small>僅由 worker 讀取</small></div><span className="service-state">未由前端探測</span></div>
              <div><span className="service-icon service-icon--blue"><FileAudio2 size={16} /></span><div><strong>Chirp 3</strong><small>由任務記錄反映</small></div><span className="service-state">請查看任務</span></div>
              <div><span className="service-icon service-icon--violet"><Sparkles size={16} /></span><div><strong>Gemini 3.6 Flash</strong><small>由任務記錄反映</small></div><span className="service-state">請查看任務</span></div>
            </div>
          </div>
          <div className="panel quick-panel"><div className="quick-panel__icon"><Sparkles size={21} /></div><h2>最高精準度模式</h2><p>Chirp 建立時間軸，Gemini 依固定字幕段落進行繁體與術語校正。</p><Link href="/jobs/new" className="button button--secondary">建立精準轉錄 <ArrowRight size={16} /></Link></div>
        </aside>
      </section>
    </AppShell>
  );
}
