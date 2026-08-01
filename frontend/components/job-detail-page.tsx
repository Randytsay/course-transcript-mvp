"use client";

import AppShell from "./app-shell";
import { approveBatch, decideReviewTerm, getArtifacts, getBatch, getJob, getJobEvents, getReviewTerms, getSegments, pauseJob, resumeJob, retryFailedStage, getJobChunks, getJobChunkTranscript } from "@/lib/api-client";
import type { Artifact, JobEvent, ReviewTerm, TranscriptJob, TranscriptSegment, ChunkProgressResponse } from "@/lib/types";
import { AlertTriangle, ArrowLeft, Check, CheckCircle2, Circle, Clock3, ExternalLink, FileJson, FileText, FolderUp, Gauge, Headphones, LoaderCircle, Pause, Play, RotateCcw, TriangleAlert, ChevronRight, ChevronDown, Activity } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState, useCallback, useRef } from "react";
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
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [tab, setTab] = useState<"transcript" | "qa" | "files">("transcript");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [termDrafts, setTermDrafts] = useState<Record<string, { value: string; scope: "session" | "course" | "instructor" | "global" }>>({});
  const [decidingTerm, setDecidingTerm] = useState<string | null>(null);
  const [jobAction, setJobAction] = useState<"pause" | "resume" | "retry" | null>(null);
  const [confirmedCost, setConfirmedCost] = useState(false);
  const [approvingCost, setApprovingCost] = useState(false);

  const [chunksRes, setChunksRes] = useState<ChunkProgressResponse | null>(null);
  const [expandedChunks, setExpandedChunks] = useState<Record<number, string>>({});
  const [loadingChunkText, setLoadingChunkText] = useState<number | null>(null);

  const pollController = useRef<AbortController | null>(null);

  const loadData = useCallback(async (isPolling = false) => {
    if (pollController.current) {
      pollController.current.abort();
    }
    pollController.current = new AbortController();
    try {
      const [nextJob, nextSegments, nextTerms, nextArtifacts, nextEvents, nextChunks] = await Promise.all([
        getJob(jobId), getSegments(jobId), getReviewTerms(jobId), getArtifacts(jobId), getJobEvents(jobId),
        getJobChunks(jobId).catch(() => null)
      ]);
      setJob(nextJob); setSegments(nextSegments); setReviewTerms(nextTerms); setArtifacts(nextArtifacts); setEvents(nextEvents); 
      if (!isPolling && !activeSegmentId) setActiveSegmentId(nextSegments[0]?.id ?? null);
      if (!isPolling) setTermDrafts(Object.fromEntries(nextTerms.map((term) => [term.id, { value: term.approvedValue ?? term.suggestion, scope: term.scope ?? "session" }])));
      setChunksRes(nextChunks);
      setError(null);
    } catch (cause: unknown) {
      if ((cause as Error).name !== 'AbortError') {
        setError(cause instanceof Error ? cause.message : "無法讀取任務");
      }
    }
  }, [jobId, activeSegmentId]);

  useEffect(() => {
    void loadData(false);
    let timer: number | null = null;
    
    function startPolling() {
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") {
          setJob((currentJob) => {
            if (currentJob && ["awaiting_review", "completed", "failed", "paused"].includes(currentJob.status)) {
               // keep polling for now
            }
            void loadData(true);
            return currentJob;
          });
        }
      }, 3000);
    }
    
    startPolling();
    
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void loadData(true);
        if (!timer) startPolling();
      } else {
        if (timer) {
          window.clearInterval(timer);
          timer = null;
        }
      }
    };
    
    document.addEventListener("visibilitychange", handleVisibility);
    
    return () => {
      if (timer) window.clearInterval(timer);
      if (pollController.current) pollController.current.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadData]);

  async function toggleChunkText(chunkIndex: number) {
    if (expandedChunks[chunkIndex]) {
      const next = { ...expandedChunks };
      delete next[chunkIndex];
      setExpandedChunks(next);
      return;
    }
    setLoadingChunkText(chunkIndex);
    try {
      const transcript = await getJobChunkTranscript(jobId, chunkIndex);
      setExpandedChunks(prev => ({ ...prev, [chunkIndex]: transcript.rawText }));
    } catch (e) {
      alert("無法載入此分段的原始稿");
    } finally {
      setLoadingChunkText(null);
    }
  }

  const pendingCount = useMemo(() => reviewTerms.filter((term) => term.status === "pending").length, [reviewTerms]);

  async function approveJobCost() {
    if (!job?.batchId || !confirmedCost) return;
    setApprovingCost(true);
    setActionError(null);
    try {
      const batch = await getBatch(job.batchId);
      if (batch.estimatedCostUsd) {
        await approveBatch(batch.id, batch.revision, batch.estimatedCostUsd);
        const updated = await getJob(job.id);
        setJob(updated);
      }
    } catch (cause: unknown) {
      setActionError(cause instanceof Error ? cause.message : "費用確認授權失敗");
    } finally {
      setApprovingCost(false);
    }
  }
  async function control(action: "pause" | "resume" | "retry") {
    if (!job) return;
    setJobAction(action);
    setActionError(null);
    try {
      const updated = action === "pause"
        ? await pauseJob(job.id, job.revision)
        : action === "resume"
          ? await resumeJob(job.id, job.revision)
          : await retryFailedStage(job.id, job.revision, job.activeStage ?? "");
      setJob(updated);
      setEvents(await getJobEvents(job.id));
    } catch (cause: unknown) {
      setActionError(cause instanceof Error ? cause.message : "任務操作失敗");
    } finally {
      setJobAction(null);
    }
  }
  async function decide(term: ReviewTerm, action: "confirmed" | "ignored") {
    const draft = termDrafts[term.id] ?? { value: term.suggestion, scope: "session" as const };
    setDecidingTerm(term.id);
    setActionError(null);
    try {
      const updated = await decideReviewTerm(jobId, term.id, action, draft.value, draft.scope);
      setReviewTerms((current) => current.map((item) => item.id === term.id ? updated : item));
    } catch (cause: unknown) {
      setActionError(cause instanceof Error ? cause.message : "無法儲存詞彙決定");
    } finally {
      setDecidingTerm(null);
    }
  }
  if (error) return <AppShell title="任務不可用" description={error}><div className="empty-state empty-state--error">此任務不存在，或後端 API 暫時無法讀取。</div></AppShell>;
  if (!job) return <AppShell title="載入任務"><div className="empty-state">正在取得真實任務資料…</div></AppShell>;
  const qaStep = job.pipeline.find((step) => step.id === "qa");

  const pausable = ["queued", "downloading", "normalizing", "transcribing", "merging", "segmenting", "correcting", "exporting", "quality_check"].includes(job.status);
  return <AppShell title={job.course} description={`${job.filename} · ${job.duration} · ${job.language}`} actions={<><Link href="/" className="button button--ghost"><ArrowLeft size={17} />返回</Link>{pausable && <button className="button button--ghost" disabled={jobAction !== null} onClick={() => void control("pause")}><Pause size={16} />暫停</button>}{job.status === "paused" && <button className="button button--primary" disabled={jobAction !== null} onClick={() => void control("resume")}><Play size={16} />繼續</button>}{job.status === "failed" && job.activeStage && <button className="button button--primary" disabled={jobAction !== null} onClick={() => void control("retry")}><RotateCcw size={16} />重試 {job.activeStage}</button>}<button className="button button--primary" disabled title="Drive 上傳仍需另一次明確授權"><FolderUp size={17} />等待人工確認後發布</button></>}>
    <section className="job-overview-bar">
      <div className="job-overview-main"><StatusBadge status={job.status} /><span className="job-id">Job ID: {job.id}</span><span className="job-meta"><Clock3 size={15} />更新於 {job.updatedAt}</span></div>
      <div className="job-overview-stats"><div><span>字詞數</span><strong>{job.words.toLocaleString()}</strong></div><div><span>待確認</span><strong className="text-warning">{pendingCount}</strong></div><div><span>時間軸</span><strong className={qaStep?.status === "warning" ? "text-warning" : "text-success"}>{qaStep?.status === "warning" ? "需修正" : "待 QA"}</strong></div></div>
    </section>

    {job.status === "awaiting_confirmation" && (
      <section className="qa-notice" style={{ marginBottom: "16px", padding: "18px", background: "#fff7ed", border: "2px solid #f59e0b", borderRadius: "12px" }}>
        <TriangleAlert size={28} className="text-warning" style={{ flexShrink: 0, marginTop: "2px" }} />
        <div style={{ flex: 1 }}>
          <strong style={{ fontSize: "16px", color: "#9a3412" }}>預估辨識費用待確認：US$ {job.estimatedCostUsd ?? "2.68"}</strong>
          <p style={{ fontSize: "14px", color: "#c2410c", marginTop: "4px" }}>
            本機 Preflight 格式與時長檢查已完成（影音時長 {job.duration}）。確認授權後，系統將自動排入付費轉錄佇列 (Chirp 3 + Gemini 3.6 Flash)。
          </p>
          <div style={{ marginTop: "12px", display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "14px", fontWeight: 600, color: "#7c2d12", cursor: "pointer" }}>
              <input type="checkbox" checked={confirmedCost} onChange={(e) => setConfirmedCost(e.target.checked)} style={{ width: "18px", height: "18px", accentColor: "var(--brand)" }} />
              我確認預估費用 US$ {job.estimatedCostUsd ?? "2.68"}，並授權排入處理
            </label>
            <button className="button button--primary button--large" disabled={!confirmedCost || approvingCost} onClick={() => void approveJobCost()}>
              {approvingCost ? <LoaderCircle className="spin" size={18} /> : <CheckCircle2 size={18} />}
              確認費用並排入處理
            </button>
            {job.batchId && (
              <Link href={`/batches/${job.batchId}`} className="button button--secondary button--large">
                查看完整批次 (Batch)
              </Link>
            )}
          </div>
        </div>
      </section>
    )}
    <section className="pipeline-strip" aria-label="處理管線">
      {job.pipeline.map((step, index) => <div className={`pipeline-step pipeline-step--${step.status}`} key={step.id}><div className="pipeline-step__node">{step.status === "completed" ? <Check size={15} /> : step.status === "warning" ? <TriangleAlert size={15} /> : <Circle size={13} />}</div><div><strong>{step.label}</strong><span>{step.detail}</span></div>{index < job.pipeline.length - 1 && <span className="pipeline-step__line" />}</div>)}
    </section>
    <section className="review-workspace"><div className="review-main">
      <div className="audio-console"><div className="audio-console__top"><div className="audio-title"><Headphones size={19} /><div><strong>音訊審查</strong><span>安全的音訊串流 API 尚未啟用</span></div></div></div><div className="empty-state">此階段不向瀏覽器暴露 Drive 或 GCS 音訊。完成安全 range streaming 後，才能在此播放與定位。</div></div>

      {chunksRes && chunksRes.chunks.length > 0 && (
        <div style={{ padding: "16px", border: "1px solid var(--border)", borderRadius: "12px", background: "var(--surface)", boxShadow: "var(--shadow-sm)" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Activity size={18} className="text-brand" />
              <h3 style={{ fontSize: "15px", margin: 0 }}>Chirp 時間軸</h3>
              <span className="status-badge status-badge--preflight" style={{ marginLeft: "8px" }}>
                {chunksRes.completedCount} / {chunksRes.totalCount} 分段完成
              </span>
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              併發數: {chunksRes.parallelism} | 第一段 Canary: 啟用
            </div>
          </div>
          
          <div style={{ display: "grid", gap: "8px" }}>
            {chunksRes.chunks.map(chunk => (
              <div key={chunk.chunkIndex} style={{ border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
                <div style={{ display: "flex", alignItems: "center", padding: "10px 14px", background: "#f8fafc", gap: "12px" }}>
                  <div style={{ width: "20px", display: "grid", placeItems: "center" }}>
                    {chunk.status === "完成" || chunk.status === "完成（無語音）" ? <CheckCircle2 size={16} className="text-success" /> : 
                     chunk.status === "辨識中" ? <LoaderCircle size={16} className="spin text-brand" /> :
                     chunk.status === "失敗" ? <TriangleAlert size={16} className="text-danger" /> :
                     <Circle size={16} className="text-muted" />}
                  </div>
                  <div>
                    <strong style={{ fontSize: "14px", display: "block" }}>第 {chunk.chunkIndex + 1} 段</strong>
                    <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{formatTime(chunk.startMs)} – {formatTime(chunk.endMs)}</span>
                  </div>
                  <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "12px" }}>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: chunk.status === "失敗" ? "var(--danger)" : "var(--text-soft)" }}>{chunk.status}</span>
                    {chunk.error && <span style={{ fontSize: "12px", color: "var(--danger)", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{chunk.error}</span>}
                    {chunk.hasTranscript && (
                      <button className="button button--secondary button--small" onClick={() => void toggleChunkText(chunk.chunkIndex)} disabled={loadingChunkText === chunk.chunkIndex}>
                        {loadingChunkText === chunk.chunkIndex ? <LoaderCircle size={14} className="spin" /> : expandedChunks[chunk.chunkIndex] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        展開原始稿
                      </button>
                    )}
                  </div>
                </div>
                {expandedChunks[chunk.chunkIndex] && (
                  <div style={{ padding: "14px", background: "#fff", borderTop: "1px solid var(--border)" }}>
                    <div style={{ marginBottom: "10px", padding: "8px", background: "#fff7ed", color: "#9a3412", border: "1px solid #fed7aa", borderRadius: "6px", fontSize: "12px", fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
                      <TriangleAlert size={14} /> 即時 Chirp 原始稿 (尚未完成跨段接合、術語校正與最終QA)
                    </div>
                    <p style={{ fontSize: "15px", lineHeight: 1.6, color: "var(--text)", margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {expandedChunks[chunk.chunkIndex]}
                    </p>
                    <div style={{ marginTop: "12px", fontSize: "12px", color: "var(--text-muted)" }}>字詞數: {chunk.wordCount}</div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="workspace-panel"><div className="workspace-tabs"><button className={tab === "transcript" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("transcript")}>正式逐字稿</button><button className={tab === "qa" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("qa")}>QA 報告</button><button className={tab === "files" ? "workspace-tab workspace-tab--active" : "workspace-tab"} onClick={() => setTab("files")}>輸出檔案</button></div>
        {tab === "transcript" && <div className="transcript-list">
          {segments.length === 0 ? (
            <div className="empty-state">
              <strong style={{ display: "block", fontSize: "16px", marginBottom: "8px" }}>正式逐字稿尚未完成</strong>
              目前可先查看上方已完成分段的「Chirp 即時原始稿」。
            </div>
          ) : segments.map((segment) => <button className={`transcript-segment ${activeSegmentId === segment.id ? "transcript-segment--active" : ""}`} key={segment.id} onClick={() => setActiveSegmentId(segment.id)}><span className="segment-time">{formatTime(segment.startMs)}</span><span className="segment-copy"><span className="segment-corrected">{segment.correctedText}</span><span className="segment-raw">Chirp 原文：{segment.rawText}</span></span>{segment.uncertainTerms?.length ? <span className="segment-warning"><AlertTriangle size={15} />需確認</span> : <span className="segment-ok"><CheckCircle2 size={16} /></span>}</button>)}</div>}
        {tab === "qa" && <div className="qa-grid"><article className="qa-card"><Gauge size={20} /><div><span>目前 QA 狀態</span><strong className={qaStep?.status === "warning" ? "text-warning" : "text-success"}>{qaStep?.detail ?? "尚未產生"}</strong><small>以後端實際 QA 報告為準，不由前端推測。</small></div></article><div className="qa-notice"><TriangleAlert size={19} /><div><strong>Drive 發布仍鎖定</strong><p>需完成時間軸與人工詞彙審查，且由你明確確認後才會啟用。</p></div></div><article className="qa-card"><Clock3 size={20} /><div><span>階段與錯誤紀錄</span><strong>{events.length} 筆事件</strong><small>{events.slice(0, 8).map((event) => `${event.createdAt} · ${event.eventType}`).join("｜") || "尚無事件"}</small></div></article></div>}
        {tab === "files" && <div className="file-output-list">{artifacts.length === 0 ? <div className="empty-state">尚未產生可展示的產物。</div> : artifacts.map((artifact) => <div className="output-file" key={artifact.id}><span className="output-file__icon">{artifact.name.endsWith(".json") ? <FileJson size={19} /> : <FileText size={19} />}</span><div><strong>{artifact.name}</strong><span>{size(artifact.sizeBytes)} · {artifact.updatedAt}</span></div><a className="button button--ghost" href={`/api/v1/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.id)}`} target="_blank" rel="noreferrer" aria-label={`開啟 ${artifact.name}`}><ExternalLink size={16} />開啟</a></div>)}</div>}
      </div></div>
      <aside className="review-sidebar"><div className="review-sidebar__header"><div><h2>待確認詞彙</h2><p>決定只記錄於審查資料，不覆蓋原始稿。</p></div><span className="review-count">{pendingCount}</span></div>{actionError && <div className="qa-notice"><TriangleAlert size={17} /><div><strong>詞彙決定未儲存</strong><p>{actionError}</p></div></div>}<div className="term-list">{reviewTerms.length === 0 ? <div className="empty-state">尚無已匯出的術語候選。</div> : reviewTerms.map((term) => { const draft = termDrafts[term.id] ?? { value: term.suggestion, scope: "session" as const }; return <article className={`term-card term-card--${term.status}`} key={term.id}><div className="term-card__top"><span className="term-time">{term.timestamp}</span><span>{term.status === "pending" ? "待確認" : term.status === "confirmed" ? "已確認" : "已忽略"}</span></div><div className="term-comparison"><div><span>辨識內容</span><strong>{term.heard}</strong></div><div className="term-arrow">→</div><div><span>建議修正</span><strong>{term.suggestion}</strong></div></div>{term.status === "pending" && <div className="term-decision"><input aria-label={`${term.heard} 核准寫法`} value={draft.value} onChange={(event) => setTermDrafts((current) => ({ ...current, [term.id]: { ...draft, value: event.target.value } }))} /><select aria-label={`${term.heard} 套用範圍`} value={draft.scope} onChange={(event) => setTermDrafts((current) => ({ ...current, [term.id]: { ...draft, scope: event.target.value as typeof draft.scope } }))}><option value="session">只限本堂</option><option value="course">同課程</option><option value="instructor">同講師</option><option value="global">全域候選</option></select><div><button className="button button--primary" disabled={decidingTerm === term.id || !draft.value.trim()} onClick={() => decide(term, "confirmed")}>確認</button><button className="button button--ghost" disabled={decidingTerm === term.id} onClick={() => decide(term, "ignored")}>忽略</button></div></div>}</article>; })}</div></aside>
    </section>
  </AppShell>;
}
