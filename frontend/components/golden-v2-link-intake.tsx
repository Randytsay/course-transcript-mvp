"use client";

import { useEffect, useState, type FormEvent } from "react";
import { GOLDEN_V2_STAGES, parseLinkBatch, parseLinkPreview, type GoldenV2Mode } from "@/lib/golden-v2-admin-contract";
import styles from "./golden-v2-admin-page.module.css";

type FilePreview = { file_id: string; name: string; source_path: string; version: string; size_bytes: number };
type Preview = { preview_sha256: string; files: FilePreview[]; total_bytes: number; skipped_count: number };
type Batch = { batch_id: string; status: string; items: (FilePreview & {
  intake_state: string; job_id: string | null; job_status: string | null; error_code: string | null;
})[] };
const labels = ["Gemini 主稿", "Chirp 時碼", "差異比對", "第三模型", "裁決", "Golden QA"];
const intakeLabels: Record<string, string> = { PENDING: "等待來源預檢", PROCESSING: "下載與預檢中", COMPLETE: "來源預檢完成", FAILED: "來源預檢失敗" };

async function call(path: string, body?: unknown) {
  const response = await fetch(`/api/golden-v2/${path}`, {
    ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof payload?.detail === "string" ? payload.detail : `服務回應 ${response.status}`);
  return payload;
}

export default function GoldenV2LinkIntake() {
  const [link, setLink] = useState("");
  const [recursive, setRecursive] = useState(false);
  const [mode, setMode] = useState<GoldenV2Mode>("FLASH_CHIRP");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [costs, setCosts] = useState<Record<string, Record<string, string>>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const [batchId, setBatchId] = useState("");

  useEffect(() => {
    try { setBatchId(localStorage.getItem("golden-v2-last-batch") ?? ""); } catch { /* storage optional */ }
  }, []);
  useEffect(() => {
    if (!/^v2b_[0-9a-f]{32}$/.test(batchId)) return;
    let active = true;
    const refresh = async () => {
      try {
        const payload = await call(`link-batches/${batchId}`);
        if (active) { setBatch(payload); setError(""); }
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "無法讀取批次"); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 20000);
    return () => { active = false; window.clearInterval(timer); };
  }, [batchId]);

  function resetPreview() { setPreview(null); setConfirmed(false); setCosts({}); }
  async function discover(event: FormEvent) {
    event.preventDefault();
    const body = parseLinkPreview({ link, recursive });
    if (!body) { setError("請貼上一般 Google Drive 檔案或資料夾連結。"); return; }
    resetPreview(); setBusy(true); setError("");
    try { setPreview(await call("link-preview", body)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Drive 預覽失敗"); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!preview) return;
    const estimates = Object.fromEntries(preview.files.map((file) => [file.file_id,
      Object.fromEntries(GOLDEN_V2_STAGES.map((stage) => [stage,
        costs[file.file_id]?.[stage]?.trim() ? Number(costs[file.file_id][stage]) : NaN])),
    ]));
    const body = parseLinkBatch({ link, recursive, mode, preview_sha256: preview.preview_sha256,
      estimated_stage_cents_by_file: estimates, confirm: confirmed });
    if (!body) { setError("請填妥每檔各階段估價並確認來源。未知費用請保留空白，待查核後再建立。"); return; }
    setBusy(true); setError("");
    try {
      const payload: Batch = await call("link-batches", body);
      setBatch(payload); setBatchId(payload.batch_id); setConfirmed(false);
      try { localStorage.setItem("golden-v2-last-batch", payload.batch_id); } catch { /* storage optional */ }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "批次建立失敗"); }
    finally { setBusy(false); }
  }

  return <section className={styles.sourceIntake} aria-labelledby="link-intake-title">
    <div className={styles.sourceHeading}><h2 id="link-intake-title">貼上 Drive 連結</h2></div>
    <p className={styles.sourceNote}>先唯讀列出影音檔，再建立來源預檢批次。預覽不下載音檔；預檢批次由持續執行的服務處理。辨識費用與交付各自核准。</p>
    <form className={styles.sourceForm} onSubmit={(event) => void discover(event)}>
      <label><span>檔案或資料夾連結</span><input type="url" required maxLength={2048} value={link} disabled={busy}
        placeholder="https://drive.google.com/drive/folders/…"
        onChange={(event) => { setLink(event.target.value); resetPreview(); }} /></label>
      <label className={styles.confirmRow}><input type="checkbox" checked={recursive} disabled={busy}
        onChange={(event) => { setRecursive(event.target.checked); resetPreview(); }} /><span>包含子資料夾（最多五層）</span></label>
      <button className={styles.refreshButton} disabled={busy}>{busy ? "處理中…" : "唯讀預覽影音檔"}</button>
    </form>
    {error && <p className={styles.feedbackError} role="alert">{error}</p>}
    {preview && <form className={styles.sourceForm} onSubmit={(event) => void submit(event)}>
      <p role="status">{preview.files.length} 個影音檔，合計 {(preview.total_bytes / 1024 ** 2).toFixed(1)} MiB；略過 {preview.skipped_count} 個項目。音檔時長與可信估費尚待預檢。</p>
      <label><span>辨識流程</span><select value={mode} disabled={busy} onChange={(event) => { setMode(event.target.value as GoldenV2Mode); setConfirmed(false); }}>
        <option value="FLASH_CHIRP">雙引擎</option><option value="SMART_2PLUS">雙引擎，必要時第三模型</option><option value="QUALITY_3WAY">三引擎</option>
      </select></label>
      {preview.files.map((file) => <details key={file.file_id} className={styles.evidenceDetails}>
        <summary>{file.name} · {(file.size_bytes / 1024 ** 2).toFixed(1)} MiB · 版本 {file.version}</summary>
        <p>{file.source_path}</p>
        <fieldset className={styles.estimateFieldset}><legend>人工預檢估價（每階段美分；並非即時報價或費用授權）</legend>
          <div className={styles.estimateGrid}>{GOLDEN_V2_STAGES.map((stage, i) => <label key={stage}>
            <span>{labels[i]}</span><input type="number" min={0} step={1} disabled={busy}
              value={costs[file.file_id]?.[stage] ?? ""} placeholder="待查核"
              onChange={(event) => { const value = event.target.value; setCosts((current) => ({ ...current,
                [file.file_id]: { ...current[file.file_id], [stage]: value } })); setConfirmed(false); }} />
          </label>)}</div>
        </fieldset>
      </details>)}
      <label className={styles.confirmRow}><input type="checkbox" checked={confirmed} disabled={busy}
        onChange={(event) => setConfirmed(event.target.checked)} /><span>已核對所有來源與人工估價，同意唯讀下載並建立待核准任務。</span></label>
      <button className={styles.approveButton} disabled={busy || !confirmed || preview.files.length === 0}>建立持續預檢批次</button>
    </form>}
    <label><span>查詢批次 ID</span><input value={batchId} maxLength={36} onChange={(event) => { setBatchId(event.target.value); setBatch(null); }} placeholder="v2b_…" /></label>
    {batch && <div role="status"><p>批次 {batch.batch_id} · {batch.status === "INTAKE_COMPLETE" ? "來源預檢完成" : batch.status === "INTAKE_REVIEW_REQUIRED" ? "部分來源需要檢查" : "來源預檢排隊／執行中"}</p>
      <p>可關閉頁面，之後以批次 ID 查詢。辨識任務完成預檢後會出現在下方帳本。</p>
      <ul>{batch.items.map((item) => <li key={item.file_id}>{item.name}：{intakeLabels[item.intake_state] ?? item.intake_state}
        {item.job_id && ` · ${item.job_id} · ${item.job_status}`}{item.error_code && ` · ${item.error_code}`}</li>)}</ul>
    </div>}
  </section>;
}
