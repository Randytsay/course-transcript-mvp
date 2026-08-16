"use client";

import AppShell from "./app-shell";
import styles from "./performance-page.module.css";
import Link from "next/link";
import { ArrowLeft, Clock3, Coins, Download, Gauge, RefreshCw, TimerReset, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { formatTwd } from "@/lib/currency";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const terminalStatuses = new Set(["awaiting_review", "review", "completed", "failed", "cancelled"]);

type StageAttempt = {
  stage: string;
  attemptNumber: number;
  status: string;
  startedAt?: string | null;
  completedAt?: string | null;
  activeDurationMs: number;
  observedActiveDurationMs?: number;
  reportingStatus?: string;
  excludedFromEffectiveDuration?: boolean;
  error?: string | null;
};

type ChunkMetric = {
  chunkIndex: number;
  startMs: number;
  endMs: number;
  audioDurationMs: number;
  status: string;
  attemptCount: number;
  submitLatencyMs: number;
  providerProcessingMs: number;
  recoveryDelayMs: number;
  totalWallMs: number;
  wordCount: number;
  billedAudioSeconds: number;
  estimatedCostUsd: string;
  estimatedCostTwd: string;
  errorCode?: string | null;
};

type ProviderCall = {
  callId: string;
  kind: string;
  model: string;
  provider?: string | null;
  billingMode?: string | null;
  sourceStartMs?: number | null;
  sourceEndMs?: number | null;
  latencyMs: number;
  attemptCount: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: string;
  estimatedCostTwd: string;
  cached: boolean;
  promptVersion?: string | null;
};

type PerformanceSummary = {
  jobId: string;
  jobStatus: string;
  audioDurationMs: number;
  totalElapsedMs: number;
  queueMs: number;
  pausedMs: number;
  wallProcessingMs: number;
  activeStageDurationMs: number;
  realTimeFactor?: number | null;
  activeRealTimeFactor?: number | null;
  estimatedAccruedCostUsd: string;
  estimatedAccruedCostTwd: string;
  estimatedCostPerAudioHourUsd: string;
  estimatedCostPerAudioHourTwd: string;
  stageAttempts: StageAttempt[];
  stageTotals: { stage: string; durationMs: number }[];
  chunks: ChunkMetric[];
  geminiCalls: ProviderCall[];
  providerCalls?: ProviderCall[];
  bottleneckSuggestions: string[];
  generatedAt: string;
  accountingNote: string;
};

function duration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function range(startMs?: number | null, endMs?: number | null): string {
  if (startMs == null || endMs == null) return "—";
  return `${duration(startMs)}–${duration(endMs)}`;
}

function providerLabel(item: ProviderCall): string {
  if (item.provider === "minimax") return "MiniMax M3";
  if (item.provider === "google-vertex-ai") return "Gemini / Vertex AI";
  return item.provider || item.model || "—";
}

function stageDurationLabel(item: StageAttempt): string {
  if (!item.excludedFromEffectiveDuration) return duration(item.activeDurationMs);
  return `已排除（原 ${duration(item.observedActiveDurationMs ?? 0)}）`;
}

async function fetchSummary(jobId: string, signal?: AbortSignal): Promise<PerformanceSummary> {
  const response = await fetch(`${apiBase}/jobs/${encodeURIComponent(jobId)}/performance`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal,
  });
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? `無法取得效能資料 (${response.status})`);
  return payload as PerformanceSummary;
}

export default function PerformancePage({ jobId }: { jobId: string }) {
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;

    async function load() {
      controller?.abort();
      controller = new AbortController();
      try {
        const next = await fetchSummary(jobId, controller.signal);
        if (disposed) return;
        setSummary(next);
        setError(null);
        if (!terminalStatuses.has(next.jobStatus) && document.visibilityState === "visible") {
          timer = window.setTimeout(() => void load(), 5000);
        }
      } catch (cause) {
        if (disposed || (cause as Error).name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "無法取得效能資料");
        timer = window.setTimeout(() => void load(), 15000);
      }
    }

    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (timer !== null) window.clearTimeout(timer);
        controller?.abort();
      } else {
        void load();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void load();
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [jobId, refreshKey]);

  const slowestStage = useMemo(() => summary?.stageTotals[0], [summary]);
  const providerCalls = useMemo(
    () => summary?.providerCalls ?? summary?.geminiCalls ?? [],
    [summary],
  );

  return (
    <AppShell
      title="效能與費用分析"
      description="拆解排隊、暫停、本機處理、Chirp 分段與 AI 校正呼叫，找出可量化的優化空間。"
      actions={
        <div className={styles.headerActions}>
          <button type="button" className="button button--secondary" onClick={() => setRefreshKey((value) => value + 1)}><RefreshCw size={17} />重新整理</button>
          <Link href={`/jobs/${jobId}`} className="button button--secondary"><ArrowLeft size={17} />返回任務</Link>
        </div>
      }
    >
      {error && <div className={styles.error} role="alert">{error}</div>}
      {!summary ? (
        <div className={styles.loading}>正在整理效能與費用證據…</div>
      ) : (
        <>
          <section className={styles.metricGrid} aria-label="效能摘要">
            <article className={styles.metric}><Clock3 size={22} /><span>音訊長度</span><strong>{duration(summary.audioDurationMs)}</strong><small>原始課程錄音時長</small></article>
            <article className={styles.metric}><TimerReset size={22} /><span>總經過時間</span><strong>{duration(summary.totalElapsedMs)}</strong><small>排隊 {duration(summary.queueMs)}｜暫停 {duration(summary.pausedMs)}</small></article>
            <article className={styles.metric}><Gauge size={22} /><span>即時倍率 RTF</span><strong>{summary.realTimeFactor == null ? "—" : summary.realTimeFactor.toFixed(3)}</strong><small>{summary.realTimeFactor != null && summary.realTimeFactor < 1 ? "快於即時播放" : "越低越快"}</small></article>
            <article className={styles.metric}><Coins size={22} /><span>目前預估費用</span><strong>{formatTwd(summary.estimatedAccruedCostTwd)}</strong><small>每音訊小時 {formatTwd(summary.estimatedCostPerAudioHourTwd)}</small></article>
          </section>

          <section className={styles.notice}>
            <TriangleAlert size={22} />
            <div><strong>帳務與效能定義</strong><p>{summary.accountingNote}　RTF 使用扣除排隊與暫停後的牆鐘處理時間計算。</p></div>
          </section>

          <section className={styles.panel}>
            <header><div><h2>瓶頸診斷建議</h2><p>目前最耗時階段：{slowestStage ? `${slowestStage.stage}（${duration(slowestStage.durationMs)}）` : "資料不足"}</p></div></header>
            <ol className={styles.suggestions}>{summary.bottleneckSuggestions.map((item) => <li key={item}>{item}</li>)}</ol>
          </section>

          <section className={styles.panel}>
            <header><div><h2>各階段嘗試</h2><p>重試保留獨立紀錄；被較新同階段嘗試取代的舊 orphan attempt 會保留原始觀測值，但不再污染有效時間。</p></div></header>
            <div className={styles.tableWrap}>
              <table><thead><tr><th>階段</th><th>嘗試</th><th>狀態</th><th>有效時間</th><th>錯誤</th></tr></thead><tbody>
                {summary.stageAttempts.map((item, index) => <tr key={`${item.stage}-${item.attemptNumber}-${index}`}><td>{item.stage}</td><td>{item.attemptNumber}</td><td>{item.reportingStatus ?? item.status}</td><td>{stageDurationLabel(item)}</td><td>{item.error ?? "—"}</td></tr>)}
                {summary.stageAttempts.length === 0 && <tr><td colSpan={5}>尚未開始付費處理階段。</td></tr>}
              </tbody></table>
            </div>
          </section>

          <section className={styles.panel}>
            <header><div><h2>Chirp 分段明細</h2><p>語音辨識沒有 LLM token；以音訊秒數、雲端處理時間、回收延遲與費用評估。</p></div></header>
            <div className={styles.tableWrap}>
              <table><thead><tr><th>分段</th><th>音訊範圍</th><th>狀態</th><th>雲端處理</th><th>回收延遲</th><th>總牆鐘</th><th>字數</th><th>嘗試</th><th>費用</th></tr></thead><tbody>
                {summary.chunks.map((item) => <tr key={item.chunkIndex}><td>第 {item.chunkIndex + 1} 段</td><td>{range(item.startMs, item.endMs)}</td><td>{item.status}</td><td>{duration(item.providerProcessingMs)}</td><td>{duration(item.recoveryDelayMs)}</td><td>{duration(item.totalWallMs)}</td><td>{item.wordCount.toLocaleString()}</td><td>{item.attemptCount}</td><td>{formatTwd(item.estimatedCostTwd)}</td></tr>)}
                {summary.chunks.length === 0 && <tr><td colSpan={9}>尚未建立 Chirp 分段計畫。</td></tr>}
              </tbody></table>
            </div>
          </section>

          <section className={styles.panel}>
            <header><div><h2>AI 校正呼叫明細</h2><p>依實際 provider 顯示 MiniMax M3 或 Gemini / Vertex AI；Token Plan 不誤標成 Gemini API 費用。</p></div></header>
            <div className={styles.tableWrap}>
              <table><thead><tr><th>Provider</th><th>模型</th><th>呼叫</th><th>類型</th><th>字幕範圍</th><th>延遲</th><th>輸入 token</th><th>輸出 token</th><th>嘗試</th><th>費用</th></tr></thead><tbody>
                {providerCalls.map((item) => <tr key={`${item.provider ?? item.model}-${item.kind}-${item.callId}`}><td>{providerLabel(item)}</td><td>{item.model}</td><td>{item.callId}</td><td>{item.kind}</td><td>{range(item.sourceStartMs, item.sourceEndMs)}</td><td>{duration(item.latencyMs)}</td><td>{item.inputTokens.toLocaleString()}</td><td>{item.outputTokens.toLocaleString()}</td><td>{item.attemptCount}</td><td>{item.billingMode === "token_plan" ? "Token Plan" : formatTwd(item.estimatedCostTwd)}</td></tr>)}
                {providerCalls.length === 0 && <tr><td colSpan={10}>尚無 AI 校正呼叫證據，或此任務未啟用校正。</td></tr>}
              </tbody></table>
            </div>
          </section>

          <section className={styles.downloadPanel}>
            <div><h2>下載效能報告</h2><p>可用於不同檔案、併發數及提示詞版本的基準比較。</p></div>
            <div className={styles.downloadActions}>
              {(["json", "csv", "html"] as const).map((format) => <a key={format} className="button button--secondary" href={`${apiBase}/jobs/${encodeURIComponent(jobId)}/performance-report.${format}`} target="_blank" rel="noreferrer"><Download size={17} />{format.toUpperCase()}</a>)}
            </div>
          </section>
        </>
      )}
    </AppShell>
  );
}
