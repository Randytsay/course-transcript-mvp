"use client";

import AppShell from "./app-shell";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FileAudio,
  Folder,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  Search,
  Square,
  SquareCheckBig,
  TriangleAlert,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { createBatch, getCosts, previewBatch } from "@/lib/api-client";
import {
  browseDrivePage,
  DriveDirectoryPage,
  searchDrivePage,
} from "@/lib/drive-browser-client";
import type {
  BatchPreview,
  CostSummary,
  CreatedBatch,
  DriveEntry,
  OutputFormat,
} from "@/lib/types";

type SelectionMode = "files" | "folder";
type BusyState = "browse" | "search" | "more" | "preview" | "create" | null;

const DEFAULT_OUTPUT_FORMATS: OutputFormat[] = ["srt", "txt", "csv"];

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function displayPath(path: string) {
  return path.replace(/^gdrive:/, "我的雲端硬碟 / ").replaceAll("/", " / ");
}

function providerLabel(provider: DriveDirectoryPage["provider"] | null) {
  if (provider === "google_api") return "Google Drive API";
  if (provider === "rclone_fallback") return "rclone 備援";
  if (provider === "rclone") return "rclone";
  return "尚未連線";
}

export default function NewJobPageDriveApi() {
  const [directory, setDirectory] = useState<DriveDirectoryPage | null>(null);
  const [pathInput, setPathInput] = useState("gdrive:");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeSearch, setActiveSearch] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("files");
  const [selected, setSelected] = useState<Map<string, DriveEntry>>(new Map());
  const [preview, setPreview] = useState<BatchPreview | null>(null);
  const [created, setCreated] = useState<CreatedBatch | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [busy, setBusy] = useState<BusyState>(null);
  const [error, setError] = useState<string | null>(null);
  const [chirpMaxParallelChunks, setChirpMaxParallelChunks] = useState(3);
  const [outputFormats, setOutputFormats] = useState<OutputFormat[]>(DEFAULT_OUTPUT_FORMATS);

  const selectedEntries = useMemo(() => Array.from(selected.values()), [selected]);
  const selectedSize = selectedEntries.reduce((sum, item) => sum + item.sizeBytes, 0);
  const folderReady = selectionMode === "folder" && Boolean(directory) && directory?.currentPath !== "gdrive:" && !activeSearch;
  const canPreview = selectionMode === "files" ? selected.size > 0 : folderReady;

  async function openDirectory(path: string) {
    setBusy("browse");
    setError(null);
    setPreview(null);
    setCreated(null);
    setActiveSearch(null);
    try {
      const result = await browseDrivePage(path);
      setDirectory(result);
      setPathInput(result.currentPath);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法讀取此 Drive 資料夾");
    } finally {
      setBusy(null);
    }
  }

  async function runSearch(event?: FormEvent) {
    event?.preventDefault();
    const query = searchQuery.trim();
    if (query.length < 2) {
      setError("搜尋至少輸入 2 個字元");
      return;
    }
    setBusy("search");
    setError(null);
    setPreview(null);
    setCreated(null);
    try {
      const result = await searchDrivePage(query);
      setDirectory(result);
      setActiveSearch(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Drive 搜尋失敗");
    } finally {
      setBusy(null);
    }
  }

  async function loadMore() {
    if (!directory?.nextPageToken) return;
    setBusy("more");
    setError(null);
    try {
      const next = activeSearch
        ? await searchDrivePage(activeSearch, directory.nextPageToken)
        : await browseDrivePage(directory.currentPath, directory.nextPageToken);
      setDirectory({
        ...directory,
        entries: [...directory.entries, ...next.entries],
        nextPageToken: next.nextPageToken,
        provider: next.provider,
        warning: next.warning ?? directory.warning,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法載入下一頁");
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

  function toggleOutputFormat(format: OutputFormat) {
    setOutputFormats((current) => {
      if (current.includes(format)) {
        return current.length === 1 ? current : current.filter((item) => item !== format);
      }
      return [...current, format];
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
      setCreated(await createBatch(preview.batchPreviewId, chirpMaxParallelChunks, outputFormats));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法建立批次");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell title="新增轉錄任務" description="使用 Google Drive API 快速搜尋與瀏覽；檔案傳輸仍由 rclone 執行。">
      <div className="new-job-layout">
        <section className="form-panel">
          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div><h2>選擇批次方式</h2><p>可選取多個影音檔，或進入一個課程資料夾後選取整個資料夾。</p></div>
            </div>
            <div className="selection-mode-grid">
              <button type="button" className={`selection-mode-card ${selectionMode === "files" ? "selection-mode-card--active" : ""}`} onClick={() => setSelectionMode("files")}>
                <SquareCheckBig size={21} /><span><strong>選取檔案</strong><small>可跨資料夾保留勾選</small></span>{selectionMode === "files" && <Check size={17} />}
              </button>
              <button type="button" className={`selection-mode-card ${selectionMode === "folder" ? "selection-mode-card--active" : ""}`} onClick={() => setSelectionMode("folder")}>
                <Folder size={21} /><span><strong>目前整個資料夾</strong><small>建立預覽時由 rclone 遞迴列舉</small></span>{selectionMode === "folder" && <Check size={17} />}
              </button>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div><h2>瀏覽與搜尋 Google Drive</h2><p>單次前端請求最多等待 15 秒，支援分頁載入。</p></div>
            </div>

            <form className="drive-path-bar" onSubmit={(event) => { event.preventDefault(); void openDirectory(pathInput); }}>
              <HardDrive size={17} />
              <input aria-label="Google Drive 資料夾路徑" value={pathInput} onChange={(event) => setPathInput(event.target.value)} spellCheck={false} />
              <button className="button button--secondary button--small" disabled={busy !== null}>
                {busy === "browse" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={14} />}讀取
              </button>
            </form>

            <form className="drive-path-bar" onSubmit={runSearch} style={{ marginTop: 10 }}>
              <Search size={17} />
              <input aria-label="搜尋 Google Drive" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="輸入檔名或資料夾名稱" />
              <button className="button button--secondary button--small" disabled={busy !== null || searchQuery.trim().length < 2}>
                {busy === "search" ? <LoaderCircle className="spin" size={15} /> : <Search size={14} />}搜尋
              </button>
            </form>

            <div style={{ display: "flex", gap: 10, alignItems: "center", margin: "12px 0", flexWrap: "wrap" }}>
              <span className="status-badge status-badge--completed">瀏覽：{providerLabel(directory?.provider ?? null)}</span>
              <span style={{ fontSize: 13, color: "var(--muted)" }}>傳輸：rclone</span>
              {directory?.warning && <span style={{ fontSize: 13, color: "#9a3412" }}>備援原因：{directory.warning}</span>}
            </div>

            {error && <div className="empty-state empty-state--error"><TriangleAlert size={18} />{error}</div>}

            <div className="drive-browser">
              <div className="drive-browser__toolbar">
                <button className="button button--ghost button--small" type="button" disabled={(!directory?.parentPath && !activeSearch) || busy !== null} onClick={() => activeSearch ? void openDirectory("gdrive:") : directory?.parentPath && void openDirectory(directory.parentPath)}>
                  <ArrowLeft size={14} />{activeSearch ? "回到根目錄" : "上一層"}
                </button>
                <span>{activeSearch ? `搜尋：${activeSearch}` : directory ? displayPath(directory.currentPath) : "正在連線…"}</span>
              </div>
              <div className="drive-browser__header"><span>選取</span><span>名稱</span><span>大小</span><span>修改時間</span></div>
              <div className="drive-browser__list">
                {(busy === "browse" || busy === "search") && <div className="drive-browser__empty"><LoaderCircle className="spin" size={18} />正在讀取…</div>}
                {!busy && directory?.entries.length === 0 && <div className="drive-browser__empty">沒有可顯示的項目。</div>}
                {directory?.entries.map((entry) => {
                  const checked = selected.has(entry.sourcePath);
                  return (
                    <div className={`drive-entry ${checked ? "drive-entry--selected" : ""}`} key={`${entry.sourcePath}-${entry.modifiedAt ?? ""}`}>
                      <span>{entry.isDir ? <Folder size={17} /> : selectionMode === "files" && entry.supportedMedia ? <button type="button" className="file-check" onClick={() => toggleFile(entry)} aria-label={`選取 ${entry.name}`}>{checked ? <SquareCheckBig size={18} /> : <Square size={18} />}</button> : <FileAudio size={17} />}</span>
                      <button type="button" className="drive-entry__name" disabled={!entry.isDir} onClick={() => entry.isDir && void openDirectory(entry.sourcePath)}>
                        {entry.name}{entry.isDir && <ChevronRight size={14} />}
                      </button>
                      <span>{entry.isDir ? "資料夾" : formatBytes(entry.sizeBytes)}</span>
                      <span>{entry.modifiedAt ? new Date(entry.modifiedAt).toLocaleDateString("zh-TW") : "—"}</span>
                    </div>
                  );
                })}
                {directory?.nextPageToken && (
                  <div className="drive-browser__empty">
                    <button type="button" className="button button--secondary" disabled={busy !== null} onClick={() => void loadMore()}>
                      {busy === "more" ? <LoaderCircle className="spin" size={16} /> : <ChevronRight size={16} />}載入下一頁
                    </button>
                  </div>
                )}
              </div>
            </div>

            {selectionMode === "files" && selectedEntries.length > 0 && <div className="selected-files-strip"><strong>已選 {selectedEntries.length} 個影音檔</strong><span>{formatBytes(selectedSize)}</span><button type="button" className="button button--ghost button--small" onClick={() => setSelected(new Map())}>清除</button></div>}
          </div>

          <div className="form-section">
            <div className="section-heading"><span className="step-number">3</span><div><h2>輸出與併發</h2><p>建立工作前只做唯讀預覽；付費辨識仍需另行確認費用。</p></div></div>
            <label style={{ display: "block", fontWeight: 700, marginBottom: 8 }}>Chirp 同時辨識分段數</label>
            <select value={chirpMaxParallelChunks} onChange={(event) => setChirpMaxParallelChunks(Number(event.target.value))} style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid var(--border-strong)" }}>
              {[1, 2, 3, 4, 5].map((value) => <option value={value} key={value}>{value}{value === 3 ? "（建議）" : ""}</option>)}
            </select>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16 }}>
              {(["srt", "txt", "csv", "vtt", "ass", "docx", "pdf"] as OutputFormat[]).map((format) => (
                <button type="button" key={format} className={`button ${outputFormats.includes(format) ? "button--primary" : "button--ghost"}`} onClick={() => toggleOutputFormat(format)}>.{format}</button>
              ))}
            </div>
          </div>

          <div className="form-section">
            <button type="button" className="button button--primary button--large" disabled={!canPreview || busy !== null} onClick={() => void inspectSelection()}>
              {busy === "preview" ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}建立唯讀批次預覽
            </button>
            {preview && <div className="empty-state" style={{ marginTop: 14 }}>已預覽 {preview.itemCount} 個檔案，共 {formatBytes(preview.totalSizeBytes)}。<button type="button" className="button button--primary" disabled={busy !== null} onClick={() => void createPreflightBatch()}>{busy === "create" ? "建立中…" : "建立 preflight 工作"}</button></div>}
            {created && <div className="empty-state" style={{ marginTop: 14 }}>批次已建立：{created.batchId}。尚未啟動付費辨識。</div>}
          </div>
        </section>

        <aside className="dashboard-side">
          <div className="panel quick-panel"><h2>Drive 混合架構</h2><p>目錄瀏覽、搜尋與健康檢查使用 Google Drive API；下載、成果上傳、備份與升版仍使用 rclone。</p><small>剩餘預估額度：{costs ? `US$${costs.remainingEstimatedBudgetUsd}` : "讀取中"}</small></div>
        </aside>
      </div>
    </AppShell>
  );
}
