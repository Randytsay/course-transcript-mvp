"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Clock3, Coins, FileAudio2, MoreHorizontal, Plus, ShieldCheck, Sparkles, TimerReset, TriangleAlert } from "lucide-react";
import { getCosts, getJobs, getBillingSummary } from "@/lib/api-client";
import type { CostSummary, TranscriptJob, BillingSummary } from "@/lib/types";
import { formatTwd, formatTwdRate } from "@/lib/currency";
import ProgressRing from "./progress-ring";
import StatusBadge from "./status-badge";
import { useCallback, useEffect, useMemo, useState } from "react";

function localMoney(summary: BillingSummary, amount: string | null | undefined) {
  return summary.billingCurrency && amount != null
    ? `${summary.billingCurrency} ${amount}`
    : "暫無法顯示";
}

export default function DashboardPage() {
  const [jobs, setJobs] = useState<TranscriptJob[]>([]);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextJobs, nextCosts, nextBilling] = await Promise.all([
        getJobs(),
        getCosts(),
        getBillingSummary().catch(() => null),
      ]);
      setJobs(nextJobs);
      setCosts(nextCosts);
      setBilling(nextBilling);
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "無法讀取任務");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
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
  const completed = jobs.filter((job) => ["completed", "review", "awaiting_review"].includes(job.status)).length;
  const visibleJobs = useMemo(() => showAll ? jobs : jobs.slice(0, 4), [jobs, showAll]);
  const metrics = [
    { label: "處理中任務", value: String(active), detail: active ? "後端目前正在處理" : "目前沒有處理中的任務", icon: Activity, tone: "blue" },
    { label: "待人工確認", value: String(reviewing + awaitingConfirmation), detail: awaitingConfirmation ? `${awaitingConfirmation} 個待確認費用` : reviewing ? `${reviewing} 個待內容審查` : "目前沒有待確認項目", icon: TriangleAlert, tone: "amber" },
    { label: "已有輸出", value: String(completed), detail: "包含待審查與已完成任務", icon: CheckCircle2, tone: "green" },
    { label: "剩餘預估額度", value: costs ? formatTwd(costs.remainingEstimatedBudgetTwd) : "—", detail: costs ? `匯率 USD 1 = ${formatTwdRate(costs.usdToTwd)}${costs.fxRateDate ? `（${costs.fxRateDate}）` : ""}${costs.fxStale ? "；暫用快取" : ""}` : "非實際帳務", icon: TimerReset, tone: "violet" },
  ];

  return (
    <AppShell title="轉錄儀表板" description="掌握長檔辨識進度、人工審查與輸出狀態。" actions={<Link href="/jobs/new" className="button button--primary"><Plus size={17} />新增轉錄任務</Link>}>
      {firstAwaitingJob && (
        <div style={{ marginBottom: "20px", padding: "16px 20px", background: "#fff7ed", border: "2px solid #f59e0b", borderRadius: "12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <TriangleAlert size={26} className="text-warning" style={{ flexShrink: 0 }} />
            <div>
              <strong style={{ fontSize: "var(--font-md)", color: "#9a3412" }}>有 {awaitingConfirmation} 個任務正在等待費用確認授權</strong>
              <p style={{ margin: "4px 0 0", fontSize: "var(--font-sm)", color: "#9a3412", lineHeight: 1.6 }}>
                例如：<strong>{firstAwaitingJob.filename}</strong>（預估費用 {firstAwaitingJob.estimatedCostTwd ? formatTwd(firstAwaitingJob.estimatedCostTwd) : "仍在計算"}）。確認後才會開始付費轉錄。
              </p>
            </div>
          </div>
          <Link href={firstAwaitingJob.batchId ? `/batches/${firstAwaitingJob.batchId}` : `/jobs/${firstAwaitingJob.id}`} className="button button--primary button--large">
            <CheckCircle2 size={18} />前往確認費用
          </Link>
        </div>
      )}

      <section className="metric-grid" aria-label="轉錄統計">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return <article className="metric-card" key={metric.label}><div className={`metric-icon metric-icon--${metric.tone}`}><Icon size={20} /></div><div className="metric-copy"><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.detail}</small></div></article>;
        })}
      </section>

      <section className="dashboard-grid">
        <div style={{ display: "grid", gap: "18px" }}>
          <div className="panel">
            <div className="panel-header">
              <div><h2>Google Cloud 費用</h2><p>GCP 帳務資料可能延遲；官方剩餘抵免額請以 Billing Overview 為準。</p></div>
              {billing?.status === "stale" && <span className="status-badge status-badge--review">帳務資料已過期</span>}
            </div>
            {!billing || billing.status === "disabled" ? (
              <div className="empty-state">尚未設定 Cloud Billing BigQuery 匯出</div>
            ) : billing.status === "pending" ? (
              <div className="empty-state">帳務同步已啟用，正在等待第一份 BigQuery 匯出資料。</div>
            ) : billing.status === "error" && billing.projectGrossCost == null ? (
              <div className="empty-state empty-state--error">帳務摘要目前無法載入。{billing.warning ? ` ${billing.warning}` : ""}</div>
            ) : (
              <>
                <div className="metric-grid" style={{ marginBottom: 0, padding: "18px", borderTop: "none" }}>
                  <article className="metric-card">
                    <div className="metric-icon metric-icon--blue"><Coins size={20} /></div>
                    <div className="metric-copy">
                      <span>本專案 GCP 已回報使用費</span>
                      <strong>{localMoney(billing, billing.projectGrossCost)}</strong>
                      <small>{billing.projectGrossCostUsd != null ? `約 USD ${billing.projectGrossCostUsd}` : "USD 暫無法換算"}</small>
                    </div>
                  </article>
                  <article className="metric-card">
                    <div className="metric-icon metric-icon--violet"><Coins size={20} /></div>
                    <div className="metric-copy">
                      <span>帳單帳戶已使用促銷抵免</span>
                      <strong>{localMoney(billing, billing.accountPromotionCreditsUsed)}</strong>
                      <small>{billing.accountPromotionCreditsUsedUsd != null ? `約 USD ${billing.accountPromotionCreditsUsedUsd}` : "USD 暫無法換算"}</small>
                    </div>
                  </article>
                  <article className="metric-card">
                    <div className="metric-icon metric-icon--amber"><Coins size={20} /></div>
                    <div className="metric-copy">
                      <span>目前專案淨費用</span>
                      <strong>{localMoney(billing, billing.projectNetCost)}</strong>
                      <small>{billing.projectNetCostUsd != null ? `約 USD ${billing.projectNetCostUsd}` : "USD 暫無法換算"}</small>
                    </div>
                  </article>
                  <article className="metric-card">
                    <div className="metric-icon metric-icon--green"><Coins size={20} /></div>
                    <div className="metric-copy">
                      <span>預估剩餘免費抵免額</span>
                      <strong>{billing.estimatedRemainingFreeTrialCreditUsd != null ? `USD ${billing.estimatedRemainingFreeTrialCreditUsd}` : "暫無法計算"}</strong>
                      <small>最後帳務資料：{billing.lastBillingDataAt ? new Date(billing.lastBillingDataAt).toLocaleString("zh-TW") : "尚無資料"}</small>
                    </div>
                  </article>
                </div>
                {billing.warning && <div className="empty-state" style={{ borderTop: "1px solid var(--border)" }}>{billing.warning}</div>}
              </>
            )}
          </div>

          <div className="panel panel--jobs" id="jobs">
            <div className="panel-header"><div><h2>最近任務</h2><p>{showAll ? `目前顯示全部 ${jobs.length} 筆工作。` : `目前顯示最近 ${Math.min(4, jobs.length)} 筆工作。`}</p></div>{jobs.length > 4 && <button type="button" className="button button--ghost" onClick={() => setShowAll((current) => !current)}>{showAll ? "收合" : "查看全部"} <ArrowRight size={16} /></button>}</div>
            <div className="jobs-table" role="table" aria-label="最近轉錄任務">
              <div className="jobs-table__header" role="row"><span>檔案與課程</span><span>處理進度</span><span>狀態</span><span>更新時間</span><span /></div>
              {loading && <div className="empty-state">正在讀取後端任務資料…</div>}
              {error && <div className="empty-state empty-state--error">後端目前無法連線：{error}</div>}
              {!loading && !error && jobs.length === 0 && <div className="empty-state">尚無已登記的本機任務。</div>}
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

        <aside className="dashboard-side">
          <div className="panel health-panel">
            <div className="panel-header panel-header--compact"><div><h2>管線連線狀態</h2><p>來自目前 API 回應</p></div><span className="health-score">{error ? "異常" : loading ? "檢查中" : "正常"}</span></div>
            <div className="health-list">
              <div><span className="service-icon service-icon--green"><ShieldCheck size={16} /></span><div><strong>Google Drive</strong><small>僅由 worker 讀取</small></div><span className="service-state">未由前端探測</span></div>
              <div><span className="service-icon service-icon--blue"><FileAudio2 size={16} /></span><div><strong>Chirp 3</strong><small>由任務記錄反映</small></div><span className="service-state">請查看任務</span></div>
              <div><span className="service-icon service-icon--violet"><Sparkles size={16} /></span><div><strong>Gemini 3.7 Flash</strong><small>由任務記錄反映</small></div><span className="service-state">請查看任務</span></div>
            </div>
          </div>
          <div className="panel quick-panel"><div className="quick-panel__icon"><Sparkles size={21} /></div><h2>最高精準度模式</h2><p>Chirp 建立時間軸，Gemini 依固定字幕段落進行繁體與術語校正。</p><Link href="/jobs/new" className="button button--secondary">建立精準轉錄 <ArrowRight size={16} /></Link></div>
        </aside>
      </section>
    </AppShell>
  );
}
