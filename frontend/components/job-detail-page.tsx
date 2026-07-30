"use client";

import AppShell from "./app-shell";
import { getJob, reviewTerms as initialReviewTerms, transcriptSegments } from "@/lib/mock-data";
import type { ReviewTerm } from "@/lib/types";
import { AlertTriangle, ArrowLeft, Check, CheckCircle2, ChevronDown, Circle, Clock3, Download, FileJson, FileText, FolderUp, Gauge, Headphones, MoreHorizontal, Pause, Play, RefreshCcw, Save, Search, TriangleAlert, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import StatusBadge from "./status-badge";

function formatTime(ms: number) {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export default function JobDetailPage({ jobId }: { jobId: string }) {
  const job = getJob(jobId);
  const [playing, setPlaying] = useState(false);
  const [currentMs, setCurrentMs] = useState(758920);
  const [activeSegmentId, setActiveSegmentId] = useState(128);
  const [reviewTerms, setReviewTerms] = useState<ReviewTerm[]>(initialReviewTerms);
  const [tab, setTab] = useState<"transcript" | "qa" | "files">("transcript");
  const pendingCount = useMemo(() => reviewTerms.filter((term) => term.status === "pending").length, [reviewTerms]);

  function updateTerm(id: string, status: ReviewTerm["status"]) {
    setReviewTerms((terms) => terms.map((term) => (term.id === id ? { ...term, status } : term)));
  }

  return (
    <AppShell
      title={job.course}
      description={`${job.filename} · ${job.duration} · ${job.language}`}
      actions={<><Link href="/" className="button button--ghost"><ArrowLeft size={17} />返回</Link><button className="button button--secondary"><Download size={17} />下載結果</button><button className="button button--primary" disabled={pendingCount > 0}><FolderUp size={17} />上傳 Drive</button></>}
    >
      <section className="job-overview-bar">
        <div className="job-overview-main"><StatusBadge status={job.status} /><span className="job-id">Job ID: {job.id}</span><span className="job-meta"><Clock3 size={15} />更新於 {job.updatedAt}</span></div>
        <div className="job-overview-stats"><div><span>字詞數</span><strong>{job.words.toLocaleString()}</strong></div><div><span>待確認</span><strong className="text-warning">{pendingCount}</strong></div><div><span>時間軸</span><strong className="text-success">正常</strong></div></div>
      </section>

      <section className="pipeline-strip" aria-label="處理管線">
        {job.pipeline.map((step, index) => <div className={`pipeline-step pipeline-step--${step.status}`} key={step.id}><div className="pipeline-step__node">{step.status === "completed" ? <Check size={15} /> : step.status === "warning" ? <TriangleAlert size={15} /> : <Circle size={13} />}</div><div><strong>{step.label}</strong><span>{step.detail}</span></div>{index < job.pipeline.length - 1 && <span className="pipeline-step__line" />}</div>)}
      </section>

      <section className="review-workspace">
        <div className="review-main">
          <div className="audio-console">
            <div className="audio-console__top"><div className="audio-title"><Headphones size={19} /><div><strong>音訊審查</strong><span>點擊字幕可跳到對應時間</span></div></div><button className="icon-button" aria-label="更多音訊選項"><MoreHorizontal size={19} /></button></div>
            <div className="waveform" aria-label="音訊波形示意">{Array.from({ length: 96 }).map((_, index) => <span key={index} className={index < 38 ? "wave-bar wave-bar--active" : "wave-bar"} style={{ height: 18 + ((index * 29) % 48) }} />)}<span className="waveform-marker" style={{ left: "39.5%" }} /></div>
            <div className="audio-controls"><button className="play-button" onClick={() => setPlaying((value) => !value)} aria-label={playing ? "暫停" : "播放"}>{playing ? <Pause size={19} fill="currentColor" /> : <Play size={19} fill="currentColor" />}</button><span className="time-readout">{formatTime(currentMs)}</span><div className="audio-scrubber"><span style={{ width: "39.5%" }} /></div><span className="time-readout time-readout--muted">{job.duration}</span><button className="speed-button">1.0× <ChevronDown size={14} /></button></div>
          </div>

          <div className="workspace-panel">
            <div className="workspace-tabs"><button className={tab === "transcript" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("transcript")}>逐字稿</button><button className={tab === "qa" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("qa")}>QA 報告 <span className="tab-count">3</span></button><button className={tab === "files" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("files")}>輸出檔案</button><div className="workspace-search"><Search size={15} /><input placeholder="搜尋逐字稿" /></div></div>

            {tab === "transcript" && <div className="transcript-list">{transcriptSegments.map((segment) => <button className={`transcript-segment ${activeSegmentId === segment.id ? "transcript-segment--active" : ""}`} key={segment.id} onClick={() => { setActiveSegmentId(segment.id); setCurrentMs(segment.startMs); }}><span className="segment-time">{formatTime(segment.startMs)}</span><span className="segment-copy"><span className="segment-corrected">{segment.correctedText}</span><span className="segment-raw">Chirp 原文：{segment.rawText}</span></span>{segment.uncertainTerms?.length ? <span className="segment-warning"><AlertTriangle size={15} />需確認</span> : <span className="segment-ok"><CheckCircle2 size={16} /></span>}</button>)}</div>}

            {tab === "qa" && <div className="qa-grid"><article className="qa-card"><Gauge size={20} /><div><span>字幕閱讀速度</span><strong>18 段偏快</strong><small>建議人工抽查高密度片段</small></div></article><article className="qa-card"><CheckCircle2 size={20} /><div><span>時間碼倒退</span><strong className="text-success">0</strong><small>全域時間軸通過驗證</small></div></article><article className="qa-card"><RefreshCcw size={20} /><div><span>接點 QA</span><strong>3 / 3</strong><small>重疊邊界均已保留檢查片段</small></div></article><div className="qa-notice"><TriangleAlert size={19} /><div><strong>狀態：NEEDS_REVIEW</strong><p>格式與時間軸已通過，但仍有 {pendingCount} 個詞彙需要人工確認。</p></div></div></div>}

            {tab === "files" && <div className="file-output-list">{([{ name: "voice_11386603-seg1.corrected.txt", label: "校正後全文", size: "86 KB", icon: FileText }, { name: "voice_11386603-seg1.srt", label: "可讀版字幕", size: "104 KB", icon: FileText }, { name: "voice_11386603-seg1.vtt", label: "WebVTT 字幕", size: "109 KB", icon: FileText }, { name: "voice_11386603-seg1.qa-report.json", label: "QA 機器報告", size: "18 KB", icon: FileJson }] as const).map(({ name, label, size, icon: FileIcon }) => <div className="output-file" key={name}><span className="output-file__icon"><FileIcon size={19} /></span><div><strong>{name}</strong><span>{label} · {size}</span></div><button className="icon-button" aria-label={`下載 ${name}`}><Download size={17} /></button></div>)}</div>}
          </div>
        </div>

        <aside className="review-sidebar">
          <div className="review-sidebar__header"><div><h2>待確認詞彙</h2><p>確認後才可通過人工審查閘門。</p></div><span className="review-count">{pendingCount}</span></div>
          <div className="term-list">{reviewTerms.map((term) => <article className={`term-card term-card--${term.status}`} key={term.id}><div className="term-card__top"><button className="term-time" onClick={() => setCurrentMs(Number(term.timestamp.split(":")[0]) * 60000 + Number(term.timestamp.split(":")[1]) * 1000)}><Play size={12} fill="currentColor" />{term.timestamp}</button>{term.status === "confirmed" && <span className="term-resolved"><Check size={13} />已確認</span>}{term.status === "ignored" && <span className="term-ignored">已忽略</span>}</div><div className="term-comparison"><div><span>辨識內容</span><strong>{term.heard}</strong></div><div className="term-arrow">→</div><div><span>建議修正</span><strong>{term.suggestion}</strong></div></div>{term.status === "pending" && <div className="term-actions"><button className="button button--confirm button--small" onClick={() => updateTerm(term.id, "confirmed")}><Check size={15} />確認</button><button className="button button--ghost button--small"><Save size={15} />修改</button><button className="icon-button" onClick={() => updateTerm(term.id, "ignored")} aria-label="忽略"><X size={16} /></button></div>}</article>)}</div>
          <div className="review-footer"><div className="review-progress"><span>審查進度</span><strong>{reviewTerms.length - pendingCount} / {reviewTerms.length}</strong></div><div className="review-progress-bar"><span style={{ width: `${((reviewTerms.length - pendingCount) / reviewTerms.length) * 100}%` }} /></div><button className="button button--primary button--full" disabled={pendingCount > 0}><CheckCircle2 size={17} />完成審查</button></div>
        </aside>
      </section>
    </AppShell>
  );
}
