"use client";

import AppShell from "./app-shell";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FileAudio,
  Folder,
  FolderOpen,
  HardDrive,
  Info,
  Layers3,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Square,
  SquareCheckBig,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  browseDrive,
  createBatch,
  getCosts,
  previewBatch,
} from "@/lib/api-client";
import type {
  BatchPreview,
  CostSummary,
  CreatedBatch,
  DriveDirectory,
  DriveEntry,
} from "@/lib/types";

type SelectionMode = "files" | "folder";

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function displayPath(path: string) {
  return path.replace(/^gdrive:/, "我的雲端硬碟 / ").replaceAll("/", " / ");
}

export default function NewJobPage() {
  const [directory, setDirectory] = useState<DriveDirectory | null>(null);
  const [pathInput, setPathInput] = useState("gdrive:");
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("files");
  const [selected, setSelected] = useState<Map<string, DriveEntry>>(new Map());
  const [preview, setPreview] = useState<BatchPreview | null>(null);
  const [created, setCreated] = useState<CreatedBatch | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [busy, setBusy] = useState<"browse" | "preview" | "create" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chirpMaxParallelChunks, setChirpMaxParallelChunks] = useState(3);

  const selectedEntries = useMemo(() => Array.from(selected.values()), [selected]);
  const selectedSize = selectedEntries.reduce((sum, item) => sum + item.sizeBytes, 0);
  const folderReady = selectionMode === "folder" && Boolean(directory) && directory?.currentPath !== "gdrive:";
  const canPreview = selectionMode === "files" ? selected.size > 0 : folderReady;

  async function openDirectory(path: string) {
    setBusy("browse");
    setError(null);
    setPreview(null);
    setCreated(null);
    try {
      const result = await browseDrive(path);
      setDirectory(result);
      setPathInput(result.currentPath);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法讀取此 Drive 資料夾");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void openDirectory("gdrive:");
    getCosts().then(setCosts).catch(() => null);
  }, []);

  function toggleFile(entry: DriveEntry) {
    setPreview(null);
    setCreated(null);
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(entry.sourcePath)) next.delete(entry.sourcePath);
      else next.set(entry.sourcePath, entry);
      return next;
    });
  }

  async function inspectSelection() {
    if (!directory || !canPreview) return;
    setBusy("preview");
    setError(null);
    setPreview(null);
    setCreated(null);
    try {
      const paths = selectionMode === "folder"
        ? [directory.currentPath]
        : selectedEntries.map((entry) => entry.sourcePath);
      setPreview(await previewBatch(selectionMode, paths));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法建立批次預覽");
    } finally {
      setBusy(null);
    }
  }

  async function createPreflightBatch() {
    if (!preview) return;
    setBusy("create");
    setError(null);
    try {
      setCreated(await createBatch(preview.batchPreviewId, chirpMaxParallelChunks));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法建立批次");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell
      title="新增轉錄任務"
      description="從私人 Google Drive 選取一個、多個或整個資料夾的影音檔。"
    >
      <div className="new-job-layout">
        <section className="form-panel">
          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div>
                <h2>選擇批次方式</h2>
                <p>所有來源保持唯讀；資料夾只在你操作時列舉，不會啟用排程掃描。</p>
              </div>
            </div>
            <div className="selection-mode-grid">
              <button
                type="button"
                className={`selection-mode-card ${selectionMode === "files" ? "selection-mode-card--active" : ""}`}
                onClick={() => { setSelectionMode("files"); setPreview(null); setCreated(null); }}
              >
                <SquareCheckBig size={21} />
                <span><strong>選取一個或多個檔案</strong><small>可跨資料夾保留已勾選檔案</small></span>
                {selectionMode === "files" && <Check size={17} />}
              </button>
              <button
                type="button"
                className={`selection-mode-card ${selectionMode === "folder" ? "selection-mode-card--active" : ""}`}
                onClick={() => { setSelectionMode("folder"); setPreview(null); setCreated(null); }}
              >
                <FolderOpen size={21} />
                <span><strong>目前整個資料夾</strong><small>遞迴包含子資料夾內影音檔</small></span>
                {selectionMode === "folder" && <Check size={17} />}
              </button>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div>
                <h2>瀏覽 Google Drive</h2>
                <p>只顯示資料夾與支援的影音格式；其他檔案不會加入批次。</p>
              </div>
            </div>
            <form
              className="drive-path-bar"
              onSubmit={(event) => { event.preventDefault(); void openDirectory(pathInput); }}
            >
              <HardDrive size={17} />
              <input
                aria-label="rclone Drive 資料夾路徑"
                value={pathInput}
                onChange={(event) => setPathInput(event.target.value)}
                spellCheck={false}
              />
              <button className="button button--secondary button--small" disabled={busy !== null}>
                {busy === "browse" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={14} />}
                讀取
              </button>
            </form>

            <div className="drive-browser">
              <div className="drive-browser__toolbar">
                <button
                  className="button button--ghost button--small"
                  type="button"
                  disabled={!directory?.parentPath || busy !== null}
                  onClick={() => directory?.parentPath && void openDirectory(directory.parentPath)}
                >
                  <ArrowLeft size={14} />上一層
                </button>
                <span>{directory ? displayPath(directory.currentPath) : "正在連線…"}</span>
                {selectionMode === "folder" && directory && (
                  <span className={`folder-choice-pill ${folderReady ? "" : "folder-choice-pill--disabled"}`}>
                    <Folder size={13} />{folderReady ? "將選取此資料夾" : "請先進入課程資料夾"}
                  </span>
                )}
              </div>
              <div className="drive-browser__header">
                <span>{selectionMode === "files" ? "選取" : "類型"}</span>
                <span>名稱</span>
                <span>大小</span>
                <span>修改時間</span>
              </div>
              <div className="drive-browser__list">
                {busy === "browse" && <div className="drive-browser__empty"><LoaderCircle className="spin" size={18} />正在讀取資料夾…</div>}
                {!busy && directory?.entries.length === 0 && <div className="drive-browser__empty">這個資料夾沒有可顯示的項目。</div>}
                {!busy && directory?.entries.map((entry) => {
                  const checked = selected.has(entry.sourcePath);
                  return (
                    <div className={`drive-entry ${checked ? "drive-entry--selected" : ""}`} key={entry.sourcePath}>
                      <span>
                        {entry.isDir
                          ? <Folder size={17} />
                          : selectionMode === "files" && entry.supportedMedia
                            ? <button type="button" className="file-check" onClick={() => toggleFile(entry)} aria-label={`選取 ${entry.name}`}>{checked ? <SquareCheckBig size={18} /> : <Square size={18} />}</button>
                            : <FileAudio size={17} />}
                      </span>
                      <button
                        type="button"
                        className="drive-entry__name"
                        disabled={!entry.isDir}
                        onClick={() => entry.isDir && void openDirectory(entry.sourcePath)}
                      >
                        {entry.name}{entry.isDir && <ChevronRight size={14} />}
                      </button>
                      <span>{entry.isDir ? "資料夾" : formatBytes(entry.sizeBytes)}</span>
                      <span>{entry.modifiedAt ? new Date(entry.modifiedAt).toLocaleDateString("zh-TW") : "—"}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {selectionMode === "files" && selectedEntries.length > 0 && (
              <div className="selected-files-strip">
                <strong>已選 {selectedEntries.length} 個影音檔</strong>
                <span>{formatBytes(selectedSize)}</span>
                <button type="button" className="button button--ghost button--small" onClick={() => { setSelected(new Map()); setPreview(null); }}>清除</button>
              </div>
            )}
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">3</span>
              <div>
                <h2>高準確度管線</h2>
                <p>每個檔案會依序執行；同時間只處理一個來源，避免磁碟與成本失控。</p>
              </div>
            </div>
            <div className="workflow-choice workflow-choice--selected">
              <div className="workflow-choice__check"><Check size={13} /></div>
              <div className="workflow-choice__icon"><Layers3 size={22} /></div>
              <div>
                <strong>Chirp 3 時間軸 + Gemini 3.6 Flash 固定段落校正</strong>
                <span>輸出原始稿、校正版、字幕與 QA；Gemini 不改動字幕時間。</span>
              </div>
              <span className="recommended-tag">固定模式</span>
            </div>

            <div style={{ marginTop: "16px", padding: "16px", border: "1px solid var(--border)", borderRadius: "10px", background: "#f8fafc" }}>
              <label style={{ display: "block", marginBottom: "8px", fontWeight: 700, fontSize: "14px", color: "#334155" }}>
                Chirp 同時辨識分段數
              </label>
              <select 
                value={chirpMaxParallelChunks} 
                onChange={(e) => setChirpMaxParallelChunks(parseInt(e.target.value, 10))}
                style={{ width: "100%", padding: "10px", borderRadius: "8px", border: "1px solid var(--border-strong)", fontSize: "15px", background: "#fff" }}
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3（建議）</option>
                <option value={4}>4</option>
                <option value={5}>5</option>
              </select>
              <p style={{ marginTop: "8px", fontSize: "13px", color: "#64748b", lineHeight: 1.5 }}>
                第一段會先單獨驗證。第一段成功後，其餘音訊分段最多同時執行此數量。提高數量可能縮短等待時間，但會增加 API 併發與配額壓力。<br/>
                <strong style={{ color: "#475569" }}>註：這是「同一支音檔內的分段併發數」，不同來源音檔仍然一次只處理一支。</strong>
              </p>
            </div>

            {preview && (
              <div className="batch-preview">
                <div className="batch-preview__heading">
                  <div><ShieldCheck size={19} /><span><strong>唯讀預覽完成</strong><small>尚未開始任何付費辨識</small></span></div>
                  <strong>{preview.itemCount} 個檔案 · {formatBytes(preview.totalSizeBytes)}</strong>
                </div>
                <div className="batch-preview__items">
                  {preview.items.map((item) => (
                    <div key={item.previewId}><FileAudio size={14} /><span>{item.name}</span><small>{formatBytes(item.sizeBytes)}</small></div>
                  ))}
                </div>
              </div>
            )}

            {error && <div className="form-error"><TriangleAlert size={17} /><span>{error}</span></div>}
            {created && (
              <div className="success-banner">
                <ShieldCheck size={20} />
                <div>
                  <strong>已建立 {created.itemCount} 個 preflight 任務</strong>
                  <span>批次 {created.batchId} 尚未產生 Chirp 或 Gemini 費用。</span>
                </div>
                <Link href={`/batches/${created.batchId}`}>查看批次 <ChevronRight size={14} /></Link>
              </div>
            )}
          </div>

          <div className="form-actions">
            <span><Info size={16} />建立批次只做本機媒體檢查；取得總預估費用後仍需你再次確認。</span>
            {!preview ? (
              <button className="button button--primary button--large" type="button" disabled={!canPreview || busy !== null} onClick={() => void inspectSelection()}>
                {busy === "preview" && <LoaderCircle className="spin" size={16} />}
                預覽批次
              </button>
            ) : (
              <button className="button button--primary button--large" type="button" disabled={busy !== null || Boolean(created)} onClick={() => void createPreflightBatch()}>
                {busy === "create" && <LoaderCircle className="spin" size={16} />}
                建立 Preflight 批次
              </button>
            )}
          </div>
        </section>

        <aside className="panel sticky-card">
          <h2>批次摘要</h2>
          <div className="summary-list">
            <div><span>選取模式</span><strong>{selectionMode === "files" ? "一個或多個檔案" : "整個資料夾"}</strong></div>
            <div><span>目前選取</span><strong>{preview?.itemCount ?? (selectionMode === "files" ? selected.size : folderReady ? "1 個資料夾" : "尚未選取")}</strong></div>
            <div><span>處理方式</span><strong>依序，一次一檔</strong></div>
            <div><span>預估成本上限</span><strong>US${costs?.projectLimitUsd ?? "200"}</strong></div>
            <div><span>剩餘預估額度</span><strong>US${costs?.remainingEstimatedBudgetUsd ?? "—"}</strong></div>
          </div>
          <div className="cost-note">
            <Info size={17} />
            <p>檔案時長必須先由 VPS 本機 FFprobe 檢查，才會顯示本批次預估費用。Cloud Billing 才是實際帳務依據。</p>
          </div>
          <div className="privacy-note">
            <ShieldCheck size={17} />
            <div><strong>私人來源不公開</strong><span>瀏覽器不會取得 rclone、GCP 或 Drive 憑證。</span></div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
