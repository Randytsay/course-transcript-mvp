"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, LockKeyhole, RefreshCcw, ShieldCheck, AlertTriangle } from "lucide-react";
import styles from "./golden-v2-paid-console.module.css";

type Job = {
  job_id: string; name: string; duration_seconds: number;
  estimated_cents: number; quote_sha256: string; source_sha256: string;
  status: string; revision: number; approved_cents: number | null;
  eligible: boolean; can_approve: boolean;
};
type Data = {
  schema: string; min_duration_seconds: number; batch_cap_cents: number;
  reserved_exposure_cents: number; remaining_cents: number;
  scope_prefix: string; invoice_verified: boolean;
  summary: { eligible: number; excluded_short: number; pending_quote: number;
    awaiting_approval: number; approved_or_active: number; failed_quote: number };
  jobs: Job[];
};

function usd(cents: number) {
  return new Intl.NumberFormat("en-US", { style:"currency", currency:"USD" })
    .format(cents / 100);
}
function duration(seconds: number) {
  return Math.floor(seconds / 3600) + " 小時 " +
    Math.floor((seconds % 3600) / 60) + " 分";
}

export default function GoldenV2PaidConsole() {
  const [data, setData] = useState<Data | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("Golden V2 owner-approved batch, >=3600s; new charges <= US$100");
  const [tab, setTab] = useState<"eligible" | "excluded" | "active">("eligible");

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/golden-v2/paid-console", { cache:"no-store" });
      const value = await response.json().catch(() => null);
      if (!response.ok || value?.schema !== "golden-v2-paid-console-v1"
          || !Array.isArray(value.jobs)) {
        throw new Error(value?.detail || "無法讀取付費核准資料");
      }
      setData(value);
      setSelected((prior) => prior.filter((id) =>
        value.jobs.some((job: Job) => job.job_id === id && job.can_approve)));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "查詢失敗");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => { if (!busy) void refresh(); }, 30000);
    return () => clearInterval(interval);
  }, [refresh,busy]);

  const selectedJobs = useMemo(() =>
    (data?.jobs ?? []).filter((job) => selected.includes(job.job_id) && job.can_approve),
  [data,selected]);
  const selectedCents = selectedJobs.reduce((sum, job) => sum + job.estimated_cents, 0);
  const ready = !busy && selectedJobs.length > 0 && selectedJobs.length <= 5
    && selectedCents <= (data?.remaining_cents ?? 0)
    && confirmed && typed.trim() === "核准轉換"
    && reason.trim().length >= 10;

  function toggle(id: string) {
    setSelected((previous) => previous.includes(id)
      ? previous.filter((x) => x !== id)
      : previous.length < 5 ? [...previous,id] : previous);
    setConfirmed(false); setTyped(""); setNotice("");
  }

  async function approveSelected() {
    if (!ready || !data) return;
    setBusy(true); setError(""); setNotice("");
    let succeeded = 0;
    for (const job of selectedJobs) {
      try {
        const response = await fetch("/api/golden-v2/paid-console/authorize", {
          method: "POST", headers: { "Content-Type":"application/json" },
          body: JSON.stringify({
            job_id:job.job_id, revision:job.revision,
            quote_sha256:job.quote_sha256, source_sha256:job.source_sha256,
            approved_cents:job.estimated_cents, reference:reason.trim(), confirm:true,
          }),
        });
        const result = await response.json().catch(() => null);
        if (!response.ok || result?.status !== "QUEUED") {
          throw new Error(result?.detail || "核准結果不明；必須先檢查狀態");
        }
        succeeded += 1;
      } catch (cause) {
        setError("已核准 " + succeeded + " 筆；下一筆停止：" +
          (cause instanceof Error ? cause.message : "核准結果不明") +
          "。先重新整理查證，不要直接重送。");
        break;
      }
    }
    if (succeeded > 0) setNotice("已確認 " + succeeded +
      " 筆進入付費佇列。辨識與品質驗收仍需另行確認。");
    setBusy(false); setConfirmed(false); setTyped(""); setSelected([]);
    await refresh();
  }

  const filteredJobs = (data?.jobs ?? []).filter((job) =>
    tab === "excluded" ? !job.eligible
    : tab === "active" ? job.eligible && !job.can_approve
    : job.can_approve);

  return <div className={styles.wrap}>
    <div className={styles.hero}>
      <div>
        <p className={styles.eyebrow}><LockKeyhole size={15}/> OWNER CONTROL · SIGNED GRANTS</p>
        <h2>付費核准管理台</h2>
        <p>先確認音檔達 1 小時、來源未變更及累計預算，再由授權管理員核准。這裡不會自動扣款。</p>
      </div>
      <button className={styles.refresh} onClick={() => void refresh()} disabled={busy}>
        <RefreshCcw size={16}/>重新整理
      </button>
    </div>

    {error && <div className={styles.error} role="alert"><AlertTriangle size={17}/>{error}</div>}
    {notice && <div className={styles.notice} role="status"><CheckCircle2 size={17}/>{notice}</div>}
    {loading && <p>正在讀取可信報價與任務狀態⋯</p>}
    {data && <>
      <div className={styles.stats}>
        <article><small>本批累計上限</small><strong>{usd(data.batch_cap_cents)}</strong></article>
        <article><small>已核准保守預留</small><strong>{usd(data.reserved_exposure_cents)}</strong></article>
        <article><small>可繼續核准額度</small><strong>{usd(data.remaining_cents)}</strong></article>
        <article><small>已驗證達一小時</small><strong>{data.summary.eligible} <em>筆</em></strong></article>
      </div>
      <div className={styles.security}>
        <ShieldCheck size={20}/>
        <p><strong>後端強制檢查：</strong>單檔 ≥3,600 秒；原始 Drive 版本、SHA 及報價一致；
          累計預留不超過 US$100。已排除短音檔 {data.summary.excluded_short} 筆。
          另外 {data.summary.pending_quote} 筆等待取得完整報價。費用是保守預留，非雲端帳單最終金額。</p>
      </div>
      <div className={styles.tabs}>
        <button className={tab === "eligible" ? styles.active : ""} onClick={() => setTab("eligible")}>
          可核准 ({data.summary.awaiting_approval})
        </button>
        <button className={tab === "active" ? styles.active : ""} onClick={() => setTab("active")}>
          已核准／覆核中
        </button>
        <button className={tab === "excluded" ? styles.active : ""} onClick={() => setTab("excluded")}>
          未滿 1 小時（排除）
        </button>
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th>選擇</th><th>來源音檔</th><th>長度</th><th>保守費用</th><th>狀態</th></tr></thead>
          <tbody>{filteredJobs.map((job) => <tr key={job.job_id}>
            <td>{job.can_approve
              ? <input type="checkbox" aria-label={"選擇 " + job.name}
                  checked={selected.includes(job.job_id)}
                  disabled={busy || (selected.length >= 5 && !selected.includes(job.job_id))}
                  onChange={() => toggle(job.job_id)}/>
              : <span className={styles.disabled}>—</span>}</td>
            <td><strong title={job.name}>{job.name}</strong>
              <small>{job.job_id}</small></td>
            <td><span className={styles.duration}><Clock3 size={14}/>{duration(job.duration_seconds)}</span></td>
            <td>{usd(job.estimated_cents)}</td>
            <td><span className={job.can_approve ? styles.ready : styles.held}>
              {job.can_approve ? "待核准"
              : !job.eligible ? "不足 1 小時" : job.status}</span></td>
          </tr>)}</tbody>
        </table>
        {filteredJobs.length === 0 && <p className={styles.empty}>目前沒有符合這個分類的任務。</p>}
      </div>
      <div className={styles.approval}>
        <div className={styles.approvalTitle}><ShieldCheck size={21}/>
          <div><h3>核准本批（每次最多 5 筆）</h3>
            <p>已選 {selectedJobs.length} 筆｜預留 {usd(selectedCents)}｜餘額 {usd(data.remaining_cents)}</p>
          </div>
        </div>
        <label className={styles.field}>核准依據／審計備註
          <textarea rows={2} maxLength={256} value={reason}
            onChange={(e) => {setReason(e.target.value);setConfirmed(false);}}/>
        </label>
        <label className={styles.confirm}>
          <input type="checkbox" checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}/>
          我已核對選取的音檔長度、費用及本批 US$100 上限，確認現在可以開始付費。
        </label>
        <label className={styles.field}>請輸入「核准轉換」以確認
          <input type="text" autoComplete="off" value={typed}
            placeholder="核准轉換" onChange={(e) => setTyped(e.target.value)}/>
        </label>
        <div className={styles.submit}>
          <button disabled={!ready} onClick={() => void approveSelected()}>
            {busy ? "正在核對核准結果⋯" : "簽章核准並排入辨識"}
          </button>
          <small>逐筆確認成功才繼續；若回應不明立即停止，避免重複付費。</small>
        </div>
      </div>
      <p className={styles.foot}>此管理台僅顯示已進入可信報價帳本的音檔；它不是整個 Drive 的檔案清單。
        已交付的 AI 字幕初稿不等於 Golden QA 通過。</p>
    </>}
  </div>;
}
