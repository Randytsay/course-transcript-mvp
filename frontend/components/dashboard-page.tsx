"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Clock3, FileAudio2, MoreHorizontal, Plus, ShieldCheck, Sparkles, TimerReset, TriangleAlert } from "lucide-react";
import { jobs } from "@/lib/mock-data";
import ProgressRing from "./progress-ring";
import StatusBadge from "./status-badge";

const metrics = [
  { label: "處理中任務", value: "3", detail: "1 個正在校正", icon: Activity, tone: "blue" },
  { label: "待人工審查", value: "1", detail: "15 個詞彙待確認", icon: TriangleAlert, tone: "amber" },
  { label: "本月完成", value: "18.6h", detail: "共 27 支錄音", icon: CheckCircle2, tone: "green" },
  { label: "平均處理時間", value: "0.42×", detail: "相較原始音訊長度", icon: TimerReset, tone: "violet" }
];

export default function DashboardPage() {
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
            <div className="panel-header panel-header--compact"><div><h2>管線健康度</h2><p>最近 24 小時</p></div><span className="health-score">98%</span></div>
            <div className="health-list">
              <div><span className="service-icon service-icon--green"><ShieldCheck size={16} /></span><div><strong>Google Drive</strong><small>rclone read-only</small></div><span className="service-state">正常</span></div>
              <div><span className="service-icon service-icon--blue"><FileAudio2 size={16} /></span><div><strong>Chirp 3</strong><small>us endpoint</small></div><span className="service-state">正常</span></div>
              <div><span className="service-icon service-icon--violet"><Sparkles size={16} /></span><div><strong>Gemini 3.6 Flash</strong><small>global endpoint</small></div><span className="service-state">正常</span></div>
            </div>
          </div>
          <div className="panel quick-panel"><div className="quick-panel__icon"><Sparkles size={21} /></div><h2>最高精準度模式</h2><p>Chirp 建立時間軸，Gemini 依固定字幕段落進行繁體與術語校正。</p><Link href="/jobs/new" className="button button--secondary">建立精準轉錄 <ArrowRight size={16} /></Link></div>
        </aside>
      </section>
    </AppShell>
  );
}
