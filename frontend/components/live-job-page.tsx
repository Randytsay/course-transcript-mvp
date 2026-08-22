"use client";

import AppShell from "./app-shell";
import styles from "./live-job-page.module.css";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Copy,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Sigma,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatTwd } from "@/lib/currency";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const terminalJobStatuses = new Set([
  "awaiting_review",
  "review",
  "completed",
  "failed",
  "paused",
]);

type JobSummary = {
  id: string;
  filename: string;
  status: string;
  progress: number;
  activeStage?: string | null;
  stageDetail?: string | null;
  batchId?: string | null;
  parallelism: number;
  geminiEnabled: boolean;
  revision?: number;
};

type ChunkItem = {
  chunkIndex: number;
  startMs: number;
  endMs: number;
  durationMs: number;
  status: string;
  wordCount: number;
  hasTranscript: boolean;
  updatedAt?: string | null;
  error?: string | null;
};

type ChunkResponse = {
  jobId: string;
  jobStatus: string;
  completedCount: number;
  totalCount: number;
  parallelism: number;
  canaryCompleted: boolean;
  updatedAt?: string | null;
  chunks: ChunkItem[];
};

type LiveCost = {
  estimatedTotalTwd: string;
  estimatedAccruedTwd: string;
  estimatedRemainingTwd: string;
  chirpEstimatedTwd: string;
  geminiEstimatedTwd: string;
 estimatedTotalUsd: string;
  estimatedAccruedUsd: string;
  estimatedRemainingUsd: string;
  chirpEstimatedUsd: string;
  geminiEstimatedUsd: string;
  submittedChunkCount: number;
  completedChunkCount: number;
  warning: string;
};

type ChunkTranscript = {
  chunkIndex: number;
  startMs: number;
  endMs: number;
  status: string;
  wordCount: number;
  rawText: string;
  completedAt?: string | null;
  warning: string;
};

type FormalSegment = {
  id: string | number;
  startMs: number;
  endMs: number;
  text: string;
};

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    method: "GET",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function mapJob(raw: Record<string, unknown>): JobSummary {
  return {
    id: String(raw.id ?? ""),
    filename: String(raw.filename ?? raw.source_name ?? raw.id ?? "轉錄任務"),
    status: String(raw.status ?? "queued"),
    progress: Number(raw.progress ?? 0),
    activeStage: raw.active_stage ? String(raw.active_stage) : null,
    stageDetail: raw.stage_detail ? String(raw.stage_detail) : null,
    batchId: raw.batch_id ? String(raw.batch_id) : null,
    parallelism: Number(raw.chirp_max_parallel_chunks ?? 3),
    geminiEnabled: raw.enable_gemini_correction !== false,
    revision: Number(raw.revision ?? 1),
  };
}

function formatTime(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function statusLabel(status: string): string {
  return {
    WAITING: "等待中",
    PLANNED: "等待中",
    SUBMITTED: "辨識中",
    RUNNING: "辨識中",
    RECOVERING: "恢復中",
    SUCCEEDED: "完成",
    EMPTY_SILENCE: "完成（無語音）",
    FAILED: "失敗",
  }[status] ?? status;
}

function statusClass(status: string): string {
  if (status === "FAILED") return `${styles.status} ${styles.statusFailed}`;
  if (status === "SUCCEEDED" || status === "EMPTY_SILENCE") return `${styles.status} ${styles.statusSuccess}`;
  if (["SUBMITTED", "RUNNING", "RECOVERING"].includes(status)) return `${styles.status} ${styles.statusRunning}`;
  return `${styles.status} ${styles.statusWaiting}`;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "FAILED") return <AlertTriangle size={17} aria-hidden="true" />;
  if (status === "SUCCEEDED" || status === "EMPTY_SILENCE") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (["SUBMITTED", "RUNNING", "RECOVERING"].includes(status)) return <LoaderCircle size={17} aria-hidden="true" />;
  return <Circle size={17} aria-hidden="true" />;
}

function mapSegments(payload: unknown): FormalSegment[] {
  const rawSegments = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object" && Array.isArray((payload as { segments?: unknown[] }).segments)
      ? (payload as { segments: unknown[] }).segments
      : [];
  return rawSegments.flatMap((value, index) => {
    if (!value || typeof value !== "object") return [];
    const item = value as Record<string, unknown>;
    const text = String(
      item.corrected_text ?? item.correctedText ?? item.raw_text ?? item.rawText ?? item.text ?? "",
    ).trim();
    if (!text) return [];
    return [{
      id: String(item.id ?? item.segment_id ?? index),
      startMs: Number(item.start_ms ?? item.startMs ?? 0),
      endMs: Number(item.end_ms ?? item.endMs ?? 0),
      text,
    }];
  });
}

export default function LiveJobPage({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobSummary | null>(null);
  const [chunks, setChunks] = useState<ChunkResponse | null>(null);
  const [cost, setCost] = useState<LiveCost | null>(null);
  const [formalSegments, setFormalSegments] = useState<FormalSegment[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [transcripts, setTranscripts] = useState<Record<number, ChunkTranscript>>({});
  const [loadingTranscript, setLoadingTranscript] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [manualRefresh, setManualRefresh] = useState(0);
  const [busyRetry, setBusyRetry] = useState(false);
  const [retryingChunk, setRetryingChunk] = useState<number | null>(null);
  const [recalculatingChunk, setRecalculatingChunk] = useState<number | null>(null);
  const [actionFeedback, setActionFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const formalLoadedRef = useRef(false);

  async function handleRetryStage() {
    if (!job) return;
    setBusyRetry(true);
    setError(null);
    setActionFeedback(null);
    try {
      const response = await fetch(`${apiBase}/jobs/${encodeURIComponent(jobId)}/retry-stage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: job.revision ?? 1, stage: job.activeStage ?? "chirp", force: true }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "重試階段失敗");
      }
      setActionFeedback({
        type: "success",
        message: `✅ 已成功送出重試請求 (${job.activeStage ?? "chirp"})！系統正在重新處理中...`,
      });
      setManualRefresh((value) => value + 1);
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "重試階段失敗";
      setError(msg);
      setActionFeedback({ type: "error", message: `❌ 重試階段失敗：${msg}` });
    } finally {
      setBusyRetry(false);
    }
  }

  async function retryChunk(chunkIndex: number) {
    setRetryingChunk(chunkIndex);
    setError(null);
    setActionFeedback(null);
    try {
      const response = await fetch(`${apiBase}/jobs/${encodeURIComponent(jobId)}/retry-stage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: job?.revision ?? 1,
          stage: job?.activeStage ?? "chirp",
          chunk_index: chunkIndex,
          force: true,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "重試此分段失敗");
      }
      setActionFeedback({
        type: "success",
        message: `✅ 第 ${chunkIndex + 1} 段已排入重新辨識；待取得新的 Chirp operation 後才會更新文字。`,
      });
      setManualRefresh((value) => value + 1);
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "無法重試此分段";
      setError(msg);
      setActionFeedback({ type: "error", message: `❌ 重試第 ${chunkIndex + 1} 段失敗：${msg}` });
    } finally {
      setRetryingChunk(null);
    }
  }

  async function handleRecalculate(chunkIndex: number) {
    if (!job || recalculatingChunk !== null) return;
    setRecalculatingChunk(chunkIndex);
    setError(null);
    setActionFeedback(null);
    try {
      const response = await fetch(`${apiBase}/jobs/${encodeURIComponent(jobId)}/recalculate/${chunkIndex}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: job.revision ?? 1 }),
      });
      const payload = await response.json().catch(() => null) as
        | { detail?: string; wordCount?: number; message?: string }
        | null;
      if (!response.ok) {
        throw new Error(payload?.detail ?? "無法計算字數");
      }
      setActionFeedback({
        type: "success",
        message: payload?.message ?? `✅ 第 ${chunkIndex + 1} 段字詞統計已重算（${payload?.wordCount ?? "?"} 字詞）。`,
      });
      setManualRefresh((value) => value + 1);
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "無法計算字數";
      setError(msg);
      setActionFeedback({ type: "error", message: `❌ 重算第 ${chunkIndex + 1} 段失敗：${msg}` });
    } finally {
      setRecalculatingChunk(null);
    }
  }

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;
    let inFlight = false;
    let retryDelay = 3000;

    const clearTimer = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };

    const loadFormal = async (signal: AbortSignal) => {
      if (formalLoadedRef.current) return;
      try {
        const payload = await getJson<unknown>(`/jobs/${encodeURIComponent(jobId)}/segments`, signal);
        if (!disposed) {
          setFormalSegments(mapSegments(payload));
          formalLoadedRef.current = true;
        }
      } catch (cause) {
        if ((cause as Error).name !== "AbortError") {
          // Formal output may legitimately not exist yet; the live poll continues.
          formalLoadedRef.current = false;
        }
      }
    };

    const schedule = (delay: number, terminal: boolean) => {
      clearTimer();
      if (!disposed && !terminal && document.visibilityState === "visible") {
        timer = window.setTimeout(() => void refresh(), delay);
      }
    };

    const refresh = async () => {
      if (disposed || inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      controller?.abort();
      controller = new AbortController();
      try {
        const [rawJob, nextChunks, nextCost] = await Promise.all([
          getJson<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId)}`, controller.signal),
          getJson<ChunkResponse>(`/jobs/${encodeURIComponent(jobId)}/chunks`, controller.signal),
          getJson<LiveCost>(`/jobs/${encodeURIComponent(jobId)}/live-cost`, controller.signal),
        ]);
        const nextJob = mapJob(rawJob);
        if (disposed) return;
        setJob(nextJob);
        setChunks(nextChunks);
        setCost(nextCost);
        setLastUpdated(new Date());
        setError(null);
        retryDelay = 3000;
        const terminal = terminalJobStatuses.has(nextJob.status);
        if (terminal || nextJob.progress >= 72) await loadFormal(controller.signal);
        schedule(retryDelay, terminal);
      } catch (cause) {
        if (disposed || (cause as Error).name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "無法更新任務狀態");
        retryDelay = Math.min(30000, retryDelay * 2);
        schedule(retryDelay, false);
      } finally {
        inFlight = false;
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        clearTimer();
        controller?.abort();
      } else {
        void refresh();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    void refresh();
    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [jobId, manualRefresh]);

  const formalReady = useMemo(
    () => formalSegments.length > 0 && Boolean(job && (terminalJobStatuses.has(job.status) || job.progress >= 72)),
    [formalSegments.length, job],
  );

  async function toggleTranscript(chunk: ChunkItem) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(chunk.chunkIndex)) next.delete(chunk.chunkIndex);
      else next.add(chunk.chunkIndex);
      return next;
    });
    if (transcripts[chunk.chunkIndex] || !chunk.hasTranscript) return;
    setLoadingTranscript(chunk.chunkIndex);
    try {
      const transcript = await getJson<ChunkTranscript>(
        `/jobs/${encodeURIComponent(jobId)}/chunks/${chunk.chunkIndex}/transcript`,
      );
      setTranscripts((current) => ({ ...current, [chunk.chunkIndex]: transcript }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法載入分段原始稿");
    } finally {
      setLoadingTranscript(null);
    }
  }

  async function copyTranscript(chunkIndex: number) {
    const text = transcripts[chunkIndex]?.rawText;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      setError("瀏覽器未允許複製文字");
    }
  }

  return (
    <AppShell
      title={job?.filename ?? "轉錄任務"}
      description="查看分段進度、即時 Chirp 原始稿與完成校正後的正式逐字稿。"
      actions={<Link href="/" className="button button--secondary"><ArrowLeft size={17} />返回儀表板</Link>}
    >
      {error && <div className={styles.error} role="alert">{error}</div>}

      <section className={styles.summaryGrid} aria-label="任務即時摘要">
        <article className={styles.card}>
          <span className={styles.cardLabel}>任務狀態</span>
          <strong className={styles.cardValue}>{job?.status ?? "讀取中"}</strong>
          <span className={styles.cardDetail}>{job?.stageDetail ?? "正在取得最新狀態"}</span>
        </article>
        <article className={styles.card}>
          <span className={styles.cardLabel}>Chirp 分段併發</span>
          <strong className={styles.cardValue}>{chunks?.parallelism ?? job?.parallelism ?? 3}</strong>
          <span className={styles.cardDetail}>第一段 Canary；來源檔仍一次處理一支</span>
        </article>
        <article className={styles.card}>
          <span className={styles.cardLabel}>本任務即時預估費用</span>
          <strong className={styles.cardValue}>{formatTwd(cost?.estimatedAccruedTwd)}</strong>
          <span className={styles.cardDetail}>完整預估 {formatTwd(cost?.estimatedTotalTwd)}；剩餘 {formatTwd(cost?.estimatedRemainingTwd)}</span>
        </article>
        <article className={styles.card}>
          <span className={styles.cardLabel}>最後畫面更新</span>
          <strong className={styles.cardValue}>{lastUpdated ? lastUpdated.toLocaleTimeString("zh-TW") : "—"}</strong>
          <span className={styles.cardDetail}>前景處理中約每 3 秒更新；錯誤時最長退避 30 秒</span>
        </article>
      </section>

      {job?.status === "awaiting_confirmation" && job.batchId && (
        <section className={styles.section}>
          <div className={styles.sectionBody}>
            <strong>此任務尚未授權付費處理。</strong>
            <p className={styles.meta}>先確認整批預估費用，才會送出 Chirp 或 Gemini 請求。</p>
            <div className={styles.inlineActions}>
              <Link className={styles.primaryLink} href={`/batches/${job.batchId}`}>前往確認費用</Link>
            </div>
          </div>
        </section>
      )}

      <section className={styles.section} aria-labelledby="chunk-progress-heading">
        <header className={styles.sectionHeader}>
          <div>
            <h2 id="chunk-progress-heading">第一層｜分段進度</h2>
            <p>每段顯示等待中、辨識中、恢復中、完成或失敗。</p>
          </div>
          <div className={styles.headerControls}>
            {job?.status === "failed" && (
              <button
                type="button"
                className={styles.headerRetryButton}
                disabled={busyRetry}
                onClick={() => void handleRetryStage()}
              >
                {busyRetry ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />}
                重試失敗階段{job.activeStage ? ` (${job.activeStage})` : ""}
              </button>
            )}
            <span className={styles.progressText}>{chunks?.completedCount ?? 0} / {chunks?.totalCount ?? 0} 分段完成</span>
          </div>
        </header>
        {actionFeedback && (
          <div
            className={actionFeedback.type === "success" ? styles.feedbackSuccess : styles.feedbackError}
            role="status"
          >
            {actionFeedback.message}
          </div>
        )}
        {!chunks || chunks.chunks.length === 0 ? (
          <div className={styles.sectionBody}>
            <p className={styles.empty}>尚未建立分段計畫。音訊正規化完成後會自動顯示全部分段。</p>
          </div>
        ) : (
          <div className={styles.chunkList}>
            {chunks.chunks.map((chunk) => (
              <article className={styles.chunkItem} key={chunk.chunkIndex}>
                <div className={styles.chunkTop}>
                  <div className={styles.chunkTitle}>第 {chunk.chunkIndex + 1} 段</div>
                  <div className={styles.chunkMeta}>{formatTime(chunk.startMs)}–{formatTime(chunk.endMs)} · {chunk.wordCount.toLocaleString("zh-TW")} 字詞</div>
                  <span className={statusClass(chunk.status)}><StatusIcon status={chunk.status} />{statusLabel(chunk.status)}</span>
                  <div className={styles.chunkActions}>
                    <button
                      type="button"
                      className={chunk.status === "FAILED" ? styles.retryChunkButton : styles.actionButton}
                      disabled={retryingChunk === chunk.chunkIndex}
                      onClick={() => void retryChunk(chunk.chunkIndex)}
                      title="重新觸發此分段的 Chirp 語音辨識"
                    >
                      {retryingChunk === chunk.chunkIndex ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}
                      {chunk.status === "FAILED" ? "重試此分段" : "重新辨識"}
                    </button>
                    <button
                      type="button"
                      className={styles.actionButton}
                      disabled={recalculatingChunk === chunk.chunkIndex}
                      onClick={() => void handleRecalculate(chunk.chunkIndex)}
                      title="從已完成的辨識結果本地重算此分段字詞統計（不會重新呼叫 Chirp，零費用）"
                    >
                      {recalculatingChunk === chunk.chunkIndex ? <LoaderCircle className="spin" size={15} /> : <Sigma size={15} />}
                      重算字數
                    </button>
                    <button
                      type="button"
                      className={styles.actionButton}
                      disabled={!chunk.hasTranscript || loadingTranscript === chunk.chunkIndex}
                      onClick={() => void toggleTranscript(chunk)}
                      aria-expanded={expanded.has(chunk.chunkIndex)}
                    >
                      {loadingTranscript === chunk.chunkIndex ? <LoaderCircle size={17} /> : expanded.has(chunk.chunkIndex) ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
                      {expanded.has(chunk.chunkIndex) ? "收合原始稿" : "展開原始稿"}
                    </button>
                  </div>
                </div>
                {chunk.error && <div className={styles.error}>{chunk.error}</div>}
                {expanded.has(chunk.chunkIndex) && transcripts[chunk.chunkIndex] && (
                  <div className={styles.transcriptBox}>
                    <div className={styles.transcriptToolbar}>
                      <span>完成時間：{transcripts[chunk.chunkIndex].completedAt ? new Date(transcripts[chunk.chunkIndex].completedAt as string).toLocaleString("zh-TW") : "未知"}</span>
                      <button type="button" className={styles.actionButton} onClick={() => void copyTranscript(chunk.chunkIndex)}><Copy size={16} />複製</button>
                    </div>
                    <p>{transcripts[chunk.chunkIndex].rawText || "此段判定為無語音。"}</p>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className={styles.section} aria-labelledby="live-transcript-heading">
        <header className={styles.sectionHeader}>
          <div>
            <h2 id="live-transcript-heading">第二層｜即時 Chirp 原始稿</h2>
            <p>完成一段即可在上方展開；每段獨立顯示，避免 10 秒重疊內容被誤認為正式稿。</p>
          </div>
        </header>
        <div className={styles.notice}>尚未完成跨段接合、術語校正與最終 QA。這裡的文字僅供提前閱讀，不會取代正式逐字稿或字幕。</div>
      </section>

      <section className={styles.section} aria-labelledby="formal-transcript-heading">
        <header className={styles.sectionHeader}>
          <div>
            <h2 id="formal-transcript-heading">第三層｜正式逐字稿</h2>
            <p>全部 Chirp 分段接合，並依任務設定完成 Gemini 校正與 QA 後才顯示。</p>
          </div>
          <button type="button" className={styles.actionButton} onClick={() => setManualRefresh((value) => value + 1)}><RefreshCw size={16} />重新整理</button>
        </header>
        {!formalReady ? (
          <div className={styles.sectionBody}>
            <p className={styles.empty}>正式逐字稿尚未完成。目前可先查看已完成分段的 Chirp 原始稿。</p>
          </div>
        ) : (
          <div className={styles.formalList}>
            {formalSegments.map((segment) => (
              <article className={styles.formalSegment} key={segment.id}>
                <div className={styles.formalTime}><Clock3 size={15} /> {formatTime(segment.startMs)}–{formatTime(segment.endMs)}</div>
                <div className={styles.formalText}>{segment.text}</div>
              </article>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
