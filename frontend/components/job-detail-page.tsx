"use client";

import AppShell from "./app-shell";
import { getArtifacts, getJob, getReviewTerms, getSegments } from "@/lib/api-client";
import type { Artifact, ReviewTerm, TranscriptJob, TranscriptSegment } from "@/lib/types";
import { AlertTriangle, ArrowLeft, Check, CheckCircle2, Circle, Clock3, ExternalLink, FileJson, FileText, FolderUp, Gauge, Headphones, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import StatusBadge from "./status-badge";

function formatTime(ms: number) {
  const seconds = Math.floor(ms / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function size(bytes: number) { return `${Math.max(1, Math.round(bytes / 1024))} KB`; }

export default function JobDetailPage({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<TranscriptJob | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [reviewTerms, setReviewTerms] = useState<ReviewTerm[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [tab, setTab] = useState<"transcript" | "qa" | "files">("transcript");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getJob(jobId), getSegments(jobId), getReviewTerms(jobId), getArtifacts(jobId)])
      .then(([nextJob, nextSegments, nextTerms, nextArtifacts]) => {
        setJob(nextJob); setSegments(nextSegments); setReviewTerms(nextTerms); setArtifacts(nextArtifacts); setActiveSegmentId(nextSegments[0]?.id ?? null);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "無法讀取任務"));
  }, [jobId]);

  const pendingCount = useMemo(() => reviewTerms.filter((term) => term.status === "pending").length, [reviewTerms]);
  if (error) return <AppShell title="任務不可用" description={error}><div className="empty-state empty-state--error">此任務不存在，或後端 API 暫時無法讀取。</div></AppShell>;
  if (!job) return <AppShell title="載入任務"><div className="empty-state">正在取得真實任務資料…</div></AppShell>;
  const qaStep = job.pipeline.find((step) => step.id === "qa");

  return <AppShell title={job.course} description={`${job.filename} · ${job.duration} · ${job.language}`} actions={<><Link href="/" className="button button--ghost"><ArrowLeft size={17} />返回</Link><button className="button button--primary" disabled title="第一個 API 里程碑不提供 Drive 發布"><FolderUp size={17} />等待人工確認後發布</button></>}>
    <section className="job-overview-bar">
      <div className="job-overview-main"><StatusBadge status={job.status} /><span className="job-id">Job ID: {job.id}</span><span className="job-meta"><Clock3 size={15} />更新於 {job.updatedAt}</span></div>
      <div className="job-overview-stats"><div><span>字詞數</span><strong>{job.words.toLocaleString()}</strong></div><div><span>待確認</span><strong className="text-warning">{pendingCount}</strong></div><div><span>時間軸</span><strong className={qaStep?.status === "warning" ? "text-warning" : "text-success"}>{qaStep?.status === "warning" ? "需修正" : "待 QA"}</strong></div></div>
    </section>
    <section className="pipeline-strip" aria-label="處理管線">
      {job.pipeline.map((step, index) => <div className={`pipeline-step pipeline-step--${step.status}`} key={step.id}><div className="pipeline-step__node">{step.status === "completed" ? <Check size={15} /> : step.status === "warning" ? <TriangleAlert size={15} /> : <Circle size={13} />}</div><div><strong>{step.label}</strong><span>{step.detail}</span></div>{index < job.pipeline.length - 1 && <span className="pipeline-step__line" />}</div>)}
    </section>
    <section className="review-workspace"><div className="review-main">
      <div className="audio-console"><div className="audio-console__top"><div className="audio-title"><Headphones size={19} /><div><strong>音訊審查</strong><span>安全的音訊串流 API 尚未啟用</span></div></div></div><div className="empty-state">此階段不向瀏覽器暴露 Drive 或 GCS 音訊。完成安全 range streaming 後，才能在此播放與定位。</div></div>
      <div className="workspace-panel"><div className="workspace-tabs"><button className={tab === "transcript" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("transcript")}>逐字稿</button><button className={tab === "qa" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("qa")}>QA 報告</button><button className={tab === "files" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("files")}>輸出檔案</button></div>
        {tab === "transcript" && <div className="transcript-list">{segments.length === 0 ? <div className="empty-state">尚未產生固定字幕段。</div> : segments.map((segment) => <button className={`transcript-segment ${activeSegmentId === segment.id ? "transcript-segment--active" : ""}`} key={segment.id} onClick={() => setActiveSegmentId(segment.id)}><span className="segment-time">{formatTime(segment.startMs)}</span><span className="segment-copy"><span className="segment-corrected">{segment.correctedText}</span><span className="segment-raw">Chirp 原文：{segment.rawText}</span></span>{segment.uncertainTerms?.length ? <span className="segment-warning"><AlertTriangle size={15} />需確認</span> : <span className="segment-ok"><CheckCircle2 size={16} /></span>}</button>)}</div>}
        {tab === "qa" && <div className="qa-grid"><article className="qa-card"><Gauge size={20} /><div><span>目前 QA 狀態</span><strong className={qaStep?.status === "warning" ? "text-warning" : "text-success"}>{qaStep?.detail ?? "尚未產生"}</strong><small>以後端實際 QA 報告為準，不由前端推測。</small></div></article><div className="qa-notice"><TriangleAlert size={19} /><div><strong>Drive 發布仍鎖定</strong><p>需完成時間軸與人工詞彙審查，且由你明確確認後才會啟用。</p></div></div></div>}
        {tab === "files" && <div className="file-output-list">{artifacts.length === 0 ? <div className="empty-state">尚未產生可展示的產物。</div> : artifacts.map((artifact) => <div className="output-file" key={artifact.id}><span className="output-file__icon">{artifact.name.endsWith(".json") ? <FileJson size={19} /> : <FileText size={19} />}</span><div><strong>{artifact.name}</strong><span>{size(artifact.sizeBytes)} · {artifact.updatedAt}</span></div><a className="button button--ghost" href={`/api/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.id)}`} target="_blank" rel="noreferrer" aria-label={`開啟 ${artifact.name}`}><ExternalLink size={16} />開啟</a></div>)}</div>}
      </div></div>
      <aside className="review-sidebar"><div className="review-sidebar__header"><div><h2>待確認詞彙</h2><p>第一個 API 里程碑為唯讀。</p></div><span className="review-count">{pendingCount}</span></div><div className="term-list">{reviewTerms.length === 0 ? <div className="empty-state">尚無已匯出的術語候選。</div> : reviewTerms.map((term) => <article className={`term-card term-card--${term.status}`} key={term.id}><div className="term-card__top"><span className="term-time">{term.timestamp}</span></div><div className="term-comparison"><div><span>辨識內容</span><strong>{term.heard}</strong></div><div className="term-arrow">→</div><div><span>建議修正</span><strong>{term.suggestion}</strong></div></div></article>)}</div></aside>
    </section>
  </AppShell>;
}
