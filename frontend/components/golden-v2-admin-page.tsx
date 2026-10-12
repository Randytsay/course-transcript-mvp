"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Check, CircleAlert, Clock3, FileAudio2, RefreshCw, ShieldCheck } from "lucide-react";

import {
  formatUsdCents,
  patchGoldenV2ApprovalForm,
  type GoldenV2ApprovalForm,
} from "@/lib/golden-v2-admin-contract";
import styles from "./golden-v2-admin-page.module.css";

type Stage = {
  ord: number;
  name: string;
  state: string;
  estimate_cents: number;
  actual_cents: number | null;
  evidence_sha: string | null;
};

type Snapshot = {
  job: {
    id: string;
    source_sha: string;
    mode: string;
    status: string;
    revision: number;
    estimated_cents: number;
    approved_cents: number | null;
    created_at: string;
  };
  stages: Stage[];
  actual_cents: number;
  golden_qa_complete: boolean;
};

const STAGE_LABEL: Record<string, string> = {
  gemini_primary: "Gemini 主稿",
  chirp_independent: "Chirp 時碼",
  compare: "差異比對",
  third_optional: "第三模型",
  adjudication: "人工裁決",
  golden_qa: "Golden QA",
};

const STATE_LABEL: Record<string, string> = {
  AWAITING_APPROVAL: "待核准",
  QUEUED: "排隊中",
  PROCESSING: "處理中",
  NEEDS_REVIEW: "等待覆核",
  COMPLETED: "已完成",
  GOLDEN_PASS: "Golden 通過",
  FAILED: "失敗",
};

const STAGE_STATE_LABEL: Record<string, string> = {
  PENDING: "尚未開始",
  RUNNING: "處理中",
  COMPLETE: "完成",
  REVIEW: "需要覆核",
  BLOCKED: "等待條件",
  OUTCOME_UNKNOWN: "結果待確認",
};

const MODE_LABEL: Record<string, string> = {
  QUALITY_3WAY: "三引擎品質模式",
  SMART_2PLUS: "智慧雙引擎模式",
  FLASH_CHIRP: "快速 Chirp 模式",
};

function statusLabel(value: string) {
  return STATE_LABEL[value] ?? value.replaceAll("_", " ");
}

function createdLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function errorDetail(payload: unknown, status: number) {
  if (payload && typeof payload === "object" && "detail" in payload
      && typeof payload.detail === "string") return payload.detail;
  return `服務回應 ${status}`;
}

export default function GoldenV2AdminPage() {
  const [jobs, setJobs] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [forms, setForms] = useState<Record<string, GoldenV2ApprovalForm>>({});
  const [submitting, setSubmitting] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    try {
      const response = await fetch("/api/golden-v2/jobs", { cache: "no-store" });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorDetail(payload, response.status));
      if (!Array.isArray(payload?.jobs)) throw new Error("V2 帳本回應格式無法辨識");
      setJobs(payload.jobs);
      setError("");
      if (!quiet) setNotice("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法讀取 Golden V2 任務");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 20000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  function formFor(job: Snapshot["job"]): GoldenV2ApprovalForm {
    return forms[job.id] ?? {
      amount: String(job.estimated_cents), reference: "", confirmed: false,
    };
  }

  function updateForm(jobId: string, patch: Partial<GoldenV2ApprovalForm>) {
    const snapshot = jobs.find((item) => item.job.id === jobId);
    if (!snapshot) return;
    setForms((previous) => ({
      ...previous,
      [jobId]: patchGoldenV2ApprovalForm(formFor(snapshot.job), patch),
    }));
  }

  async function approve(event: FormEvent<HTMLFormElement>, snapshot: Snapshot) {
    event.preventDefault();
    const form = formFor(snapshot.job);
    const amount = Number(form.amount);
    if (!Number.isSafeInteger(amount) || amount < snapshot.job.estimated_cents) {
      setError("核准金額需為整數美分，且不可低於估價。");
      return;
    }
    if (!form.reference.trim() || !form.confirmed) {
      setError("請填寫核准依據，並勾選已核對來源與預算。");
      return;
    }
    setSubmitting(snapshot.job.id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(
        `/api/golden-v2/jobs/${encodeURIComponent(snapshot.job.id)}/approval`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: snapshot.job.revision,
            approved_cents: amount,
            reference: form.reference.trim(),
            confirm: true,
          }),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorDetail(payload, response.status));
      setNotice(`任務 ${snapshot.job.id} 已核准並排入佇列。`);
      setForms((previous) => ({ ...previous, [snapshot.job.id]: {
        ...form, confirmed: false,
      } }));
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "核准未完成，請重新整理後確認狀態。");
    } finally {
      setSubmitting("");
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.hero} aria-labelledby="golden-v2-title">
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>GOLDEN TRANSCRIPT / EVIDENCE DESK</p>
          <h2 id="golden-v2-title">先對來源，<br />再讓模型開口。</h2>
          <p className={styles.heroNote}>
            每筆任務從音檔 SHA 開始。核准只會把工作排入佇列；任何模型呼叫都必須另有對應執行器與費用證據。
          </p>
        </div>
        <div className={styles.signal} aria-hidden="true">
          <span>SOURCE</span><i /><span>ASR</span><i /><span>REVIEW</span><i /><span>GOLD</span>
          <div className={styles.wave}>
            {[8, 15, 24, 12, 32, 19, 9, 27, 16, 36, 13, 23, 10, 30, 18, 8, 25].map((height, index) => (
              <b key={index} style={{ height }} />
            ))}
          </div>
          <small>SHA → WORD TIME → GOLDEN QA</small>
        </div>
      </section>

      <section className={styles.listSection} aria-labelledby="job-list-title">
        <div className={styles.listHeading}>
          <div>
            <p className={styles.sectionKicker}>
              任務帳本 / {error ? (jobs.length ? `${jobs.length}（暫存）` : "未知") : jobs.length.toString().padStart(2, "0")}
            </p>
            <h2 id="job-list-title">來源與處理進度</h2>
          </div>
          <button type="button" className={styles.refreshButton} onClick={() => void refresh(true)} disabled={refreshing}>
            <RefreshCw size={16} className={refreshing ? styles.spinning : ""} />
            更新狀態
          </button>
        </div>

        {error && <div className={styles.feedbackError} role="alert"><CircleAlert size={18} />{error}</div>}
        {notice && <div className={styles.feedbackSuccess} role="status"><Check size={18} />{notice}</div>}

        {loading ? (
          <div className={styles.empty}><Clock3 size={21} /><p>正在讀取 V2 任務帳本…</p></div>
        ) : error && jobs.length === 0 ? (
          <div className={styles.empty}>
            <CircleAlert size={23} />
            <h3>目前無法確認任務數量</h3>
            <p>管理頁暫時讀不到 V2 帳本。請檢查管理服務設定後再更新；這不代表目前沒有任務。</p>
          </div>
        ) : jobs.length === 0 ? (
          <div className={styles.empty}>
            <FileAudio2 size={23} />
            <h3>目前沒有 V2 任務</h3>
            <p>完成唯讀來源下載後，可用 source-preflight CLI 建立待核准任務；此頁不會自行取得 Drive 檔案。</p>
          </div>
        ) : (
          <div className={styles.jobs}>
            {jobs.map((snapshot) => {
              const { job, stages } = snapshot;
              const form = formFor(job);
              const canApprove = job.status === "AWAITING_APPROVAL";
              return (
                <article className={styles.job} key={job.id}>
                  <header className={styles.jobHeader}>
                    <div className={styles.jobIdentity}>
                      <div className={styles.audioMark}><FileAudio2 size={19} /></div>
                      <div className={styles.jobTitleBlock}>
                        <div className={styles.statusLine}>
                          <span className={`${styles.status} ${styles[`status_${job.status}`] ?? ""}`}>
                            {statusLabel(job.status)}
                          </span>
                          <span className={styles.mode}>{MODE_LABEL[job.mode] ?? job.mode}</span>
                        </div>
                        <h3>{job.id}</h3>
                        <code title={job.source_sha}>SOURCE SHA · {job.source_sha}</code>
                      </div>
                    </div>
                    <div className={styles.created}><Clock3 size={14} />{createdLabel(job.created_at)}</div>
                  </header>

                  <ol className={styles.stageRail} aria-label="辨識流程階段">
                    {stages.map((stage) => (
                      <li className={styles.stage} key={stage.name} data-state={stage.state}>
                        <span className={styles.stageDot} aria-hidden="true" />
                        <span className={styles.stageLabel}>{STAGE_LABEL[stage.name] ?? stage.name}</span>
                        <small>{STAGE_STATE_LABEL[stage.state] ?? stage.state}</small>
                      </li>
                    ))}
                  </ol>

                  <details className={styles.evidenceDetails}>
                    <summary>各階段費用與證據</summary>
                    <div className={styles.stageFacts}>
                      {stages.map((stage) => (
                        <section className={styles.stageFact} key={`${stage.name}-evidence`}>
                          <h4>{STAGE_LABEL[stage.name] ?? stage.name}</h4>
                          <p><span>預估</span><strong>{formatUsdCents(stage.estimate_cents)}</strong></p>
                          <p><span>實際</span><strong>{formatUsdCents(stage.actual_cents)}</strong></p>
                          {stage.evidence_sha && (
                            <code title={stage.evidence_sha}>證據 SHA · {stage.evidence_sha}</code>
                          )}
                        </section>
                      ))}
                    </div>
                  </details>

                  <div className={styles.facts}>
                    <div><span>預估上限</span><strong>{formatUsdCents(job.estimated_cents)}</strong></div>
                    <div><span>已核准</span><strong>{job.approved_cents === null ? "尚未核准" : formatUsdCents(job.approved_cents)}</strong></div>
                    <div><span>已記錄費用</span><strong>{formatUsdCents(snapshot.actual_cents)}</strong></div>
                    <div><span>Ledger revision</span><strong>r{job.revision}</strong></div>
                  </div>

                  {canApprove && (
                    <form className={styles.approval} onSubmit={(event) => void approve(event, snapshot)}>
                      <div className={styles.approvalHeading}>
                        <ShieldCheck size={19} />
                        <div><strong>核對來源與預算</strong><span>只有核准後才會進入佇列；預估金額不是供應商報價。</span></div>
                      </div>
                      <div className={styles.formGrid}>
                        <label>
                          <span>最高核准額（美分）</span>
                          <input
                            type="number" inputMode="numeric" min={job.estimated_cents}
                            step={1} required value={form.amount}
                            onChange={(event) => updateForm(job.id, { amount: event.target.value })}
                          />
                        </label>
                        <label>
                          <span>核准依據</span>
                          <input
                            type="text" maxLength={256} required value={form.reference}
                            placeholder="來源版本與預算的核對紀錄"
                            onChange={(event) => updateForm(job.id, { reference: event.target.value })}
                          />
                        </label>
                      </div>
                      <label className={styles.confirmRow}>
                        <input
                          type="checkbox" checked={form.confirmed}
                          onChange={(event) => updateForm(job.id, { confirmed: event.target.checked })}
                        />
                        <span>我已核對這筆來源 SHA 與預算，並同意最高 {formatUsdCents(Number(form.amount))} 的費用上限。</span>
                      </label>
                      <button className={styles.approveButton} type="submit"
                        disabled={!form.confirmed || submitting === job.id || submitting !== ""}>
                        {submitting === job.id ? "正在寫入核准…" : "核准預算並排入佇列"}
                      </button>
                    </form>
                  )}

                  {snapshot.golden_qa_complete && (
                    <p className={styles.qaMark}><Check size={15} />Golden QA 階段已完成；發布仍需獨立驗收。</p>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <footer className={styles.footerNote}>
        <span>私有任務帳本</span><span>狀態每 20 秒更新</span><span>估價 ≠ 報價 ≠ 核准</span>
      </footer>
    </div>
  );
}
