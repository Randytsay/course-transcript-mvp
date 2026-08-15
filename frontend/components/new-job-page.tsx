"use client";

import AppShell from "./app-shell";
import {
  ArrowLeft,
  Check,
  ChevronDown,
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
  SlidersHorizontal,
  Sparkles,
  Square,
  SquareCheckBig,
  TriangleAlert,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  browseDrive,
  getCosts,
  previewBatch,
} from "@/lib/api-client";
import {
  createBatchWithPolicy,
  getCorrectionProviderStatus,
  type CorrectionPolicy,
} from "@/lib/correction-policy-client";
import type {
  CostSummary,
  DriveDirectory,
  DriveEntry,
  OutputFormat,
  ProcessingStrategy,
} from "@/lib/types";

type SelectionMode = "files" | "folder";

type SavedDefaults = {
  processingStrategy?: ProcessingStrategy;
  correctionPolicy?: CorrectionPolicy;
  outputFormats?: OutputFormat[];
  chirpMaxParallelChunks?: number;
};

const DEFAULTS_KEY = "course-transcript-new-job-defaults-v2";
const DEFAULT_OUTPUT_FORMATS: OutputFormat[] = ["srt", "txt", "csv"];
const ALL_OUTPUT_FORMATS: Array<{ value: OutputFormat; label: string; detail: string }> = [
  { value: "srt", label: ".srt", detail: "通用字幕" },
  { value: "txt", label: ".txt", detail: "可讀逐字稿" },
  { value: "csv", label: ".csv", detail: "校正與詞彙資料" },
  { value: "json", label: ".json", detail: "Chirp 原始時間軸" },
  { value: "vtt", label: ".vtt", detail: "網頁字幕" },
  { value: "ass", label: ".ass", detail: "樣式字幕" },
  { value: "docx", label: ".docx", detail: "Word 文件" },
  { value: "pdf", label: ".pdf", detail: "閱讀版文件" },
];

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function displayPath(path: string) {
  return path.replace(/^gdrive:/, "我的雲端硬碟 / ").replaceAll("/", " / ");
}

function readSavedDefaults(): SavedDefaults {
  try {
    const raw = window.localStorage.getItem(DEFAULTS_KEY);
    return raw ? JSON.parse(raw) as SavedDefaults : {};
  } catch {
    return {};
  }
}

export default function NewJobPage() {
  const router = useRouter();
  const [directory, setDirectory] = useState<DriveDirectory | null>(null);
  const [pathInput, setPathInput] = useState("gdrive:");
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("files");
  const [selected, setSelected] = useState<Map<string, DriveEntry>>(new Map());
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [busy, setBusy] = useState<"browse" | "prepare" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [defaultsLoaded, setDefaultsLoaded] = useState(false);
  const [chirpMaxParallelChunks, setChirpMaxParallelChunks] = useState(3);
  const [processingStrategy, setProcessingStrategy] = useState<ProcessingStrategy>("DYNAMIC_BATCHING");
  const [correctionPolicy, setCorrectionPolicy] = useState<CorrectionPolicy>("GEMINI_FIRST");
  const [outputFormats, setOutputFormats] = useState<OutputFormat[]>(DEFAULT_OUTPUT_FORMATS);
  const [m3Enabled, setM3Enabled] = useState(false);

  const selectedEntries = useMemo(() => Array.from(selected.values()), [selected]);
  const selectedSize = selectedEntries.reduce((sum, item) => sum + item.sizeBytes, 0);
  const folderReady = selectionMode === "folder" && Boolean(directory) && directory?.currentPath !== "gdrive:";
  const canPrepare = selectionMode === "files" ? selected.size > 0 : folderReady;

  async function openDirectory(path: string) {
    setBusy("browse");
    setError(null);
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
    const saved = readSavedDefaults();
    if (saved.processingStrategy) setProcessingStrategy(saved.processingStrategy);
    if (saved.correctionPolicy) setCorrectionPolicy(saved.correctionPolicy);
    if (Array.isArray(saved.outputFormats) && saved.outputFormats.length > 0) {
      setOutputFormats(saved.outputFormats);
    }
    if (Number.isInteger(saved.chirpMaxParallelChunks)) {
      setChirpMaxParallelChunks(Math.min(5, Math.max(1, saved.chirpMaxParallelChunks ?? 3)));
    }
    setDefaultsLoaded(true);
    void openDirectory("gdrive:");
    getCosts().then(setCosts).catch(() => null);
    getCorrectionProviderStatus()
      .then((status) => {
        setM3Enabled(status.m3Enabled);
        if (!status.m3Enabled && saved.correctionPolicy === "M3_FIRST") {
          setCorrectionPolicy("GEMINI_FIRST");
        }
      })
      .catch(() => setM3Enabled(false));
    // Initial page setup runs once; openDirectory intentionally stays local.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!defaultsLoaded) return;
    const saved: SavedDefaults = {
      processingStrategy,
      correctionPolicy,
      outputFormats,
      chirpMaxParallelChunks,
    };
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(saved));
  }, [defaultsLoaded, processingStrategy, correctionPolicy, outputFormats, chirpMaxParallelChunks]);

  function resetPreparedState() {
    setError(null);
  }

  function toggleFile(entry: DriveEntry) {
    resetPreparedState();
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

  async function prepareAndCreateBatch() {
    if (!directory || !canPrepare) return;
    setBusy("prepare");
    setError(null);
    try {
      const paths = selectionMode === "folder"
        ? [directory.currentPath]
        : selectedEntries.map((entry) => entry.sourcePath);
      const preview = await previewBatch(selectionMode, paths);
      const created = await createBatchWithPolicy(
        preview.batchPreviewId,
        correctionPolicy,
        chirpMaxParallelChunks,
        outputFormats,
        processingStrategy,
      );
      router.push(`/batches/${created.batchId}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法檢查檔案並建立批次");
      setBusy(null);
    }
  }

  return (
    <AppShell
      title="新增轉錄任務"
      description="選檔後一次完成唯讀檢查與 Preflight；取得估價後只需一次付費確認。"
    >
      <div className="new-job-layout">
        <section className="form-panel">
          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div>
                <h2>選擇影音</h2>
                <p>可選一個、多個檔案，或直接處理目前整個資料夾。</p>
              </div>
            </div>
            <div className="selection-mode-grid">
              <button
                type="button"
                className={`selection-mode-card ${selectionMode === "files" ? "selection-mode-card--active" : ""}`}
                onClick={() => { setSelectionMode("files"); resetPreparedState(); }}
              >
                <SquareCheckBig size={21} />
                <span><strong>選取檔案</strong><small>可跨資料夾保留已勾選檔案</small></span>
                {selectionMode === "files" && <Check size={17} />}
              </button>
              <button
                type="button"
                className={`selection-mode-card ${selectionMode === "folder" ? "selection-mode-card--active" : ""}`}
                onClick={() => { setSelectionMode("folder"); resetPreparedState(); }}
              >
                <FolderOpen size={21} />
                <span><strong>目前整個資料夾</strong><small>遞迴包含子資料夾內影音檔</small></span>
                {selectionMode === "folder" && <Check size={17} />}
              </button>
            </div>

            <form
              className="drive-path-bar"
              onSubmit={(event) => { event.preventDefault(); void openDirectory(pathInput); }}
              style={{ marginTop: 16 }}
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
                    <Folder size={13} />{folderReady ? "將處理此資料夾" : "請先進入課程資料夾"}
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
                <button type="button" className="button button--ghost button--small" onClick={() => setSelected(new Map())}>清除</button>
              </div>
            )}
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div>
                <h2>處理方式</h2>
                <p>日常只需決定速度與文字校正模型；其他技術參數使用已保存的預設值。</p>
              </div>
            </div>

            <label style={{ display: "block", marginBottom: 8, fontWeight: 700 }}>辨識速度</label>
            <div className="selection-mode-grid">
              <button type="button" className={`selection-mode-card ${processingStrategy === "DYNAMIC_BATCHING" ? "selection-mode-card--active" : ""}`} onClick={() => setProcessingStrategy("DYNAMIC_BATCHING")}>
                <Layers3 size={21} />
                <span><strong>經濟模式</strong><small>Dynamic Batch；較省費用，可能等待較久</small></span>
                {processingStrategy === "DYNAMIC_BATCHING" && <Check size={17} />}
              </button>
              <button type="button" className={`selection-mode-card ${processingStrategy === "STANDARD_BATCH" ? "selection-mode-card--active" : ""}`} onClick={() => setProcessingStrategy("STANDARD_BATCH")}>
                <LoaderCircle size={21} />
                <span><strong>快速模式</strong><small>Standard Batch；較快完成，費用較高</small></span>
                {processingStrategy === "STANDARD_BATCH" && <Check size={17} />}
              </button>
            </div>

            <label style={{ display: "block", margin: "18px 0 8px", fontWeight: 700 }}>AI 文字校正</label>
            <div className="selection-mode-grid">
              <button
                type="button"
                className={`selection-mode-card ${correctionPolicy === "GEMINI_FIRST" ? "selection-mode-card--active" : ""}`}
                onClick={() => setCorrectionPolicy("GEMINI_FIRST")}
              >
                <ShieldCheck size={21} />
                <span><strong>Gemini 3.7 優先</strong><small>正式品質基準；全程優先使用 Gemini 3.7 Flash</small></span>
                {correctionPolicy === "GEMINI_FIRST" && <Check size={17} />}
              </button>
              <button
                type="button"
                disabled={!m3Enabled}
                className={`selection-mode-card ${correctionPolicy === "M3_FIRST" ? "selection-mode-card--active" : ""}`}
                onClick={() => m3Enabled && setCorrectionPolicy("M3_FIRST")}
                title={!m3Enabled ? "MiniMax M3 尚未完成正式環境驗證" : undefined}
              >
                <Sparkles size={21} />
                <span>
                  <strong>M3 優先{!m3Enabled ? "（待啟用）" : ""}</strong>
                  <small>額度可用時先用 M3；額度不足自動轉 Gemini 3.7</small>
                </span>
                {correctionPolicy === "M3_FIRST" && <Check size={17} />}
              </button>
            </div>

            <button
              type="button"
              className="button button--ghost"
              style={{ marginTop: 18 }}
              onClick={() => setAdvanced((value) => !value)}
            >
              <SlidersHorizontal size={15} />進階設定<ChevronDown size={15} />
            </button>

            {advanced && (
              <div style={{ marginTop: 12, padding: 16, border: "1px solid var(--border)", borderRadius: 10, background: "#f8fafc" }}>
                <label style={{ display: "block", marginBottom: 8, fontWeight: 700 }}>Chirp 同時辨識分段數</label>
                <select
                  value={chirpMaxParallelChunks}
                  onChange={(event) => setChirpMaxParallelChunks(parseInt(event.target.value, 10))}
                  style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid var(--border-strong)", fontSize: 15, background: "#fff" }}
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                  <option value={3}>3（建議）</option>
                  <option value={4}>4</option>
                  <option value={5}>5</option>
                </select>

                <div className="output-format-section" style={{ marginTop: 18 }}>
                  <strong>輸出附件</strong>
                  <div className="output-format-grid" role="group" aria-label="輸出附件格式" style={{ marginTop: 8 }}>
                    {ALL_OUTPUT_FORMATS.map((format) => {
                      const checked = outputFormats.includes(format.value);
                      return (
                        <label className={`output-format-option ${checked ? "output-format-option--selected" : ""}`} key={format.value}>
                          <input type="checkbox" checked={checked} onChange={() => toggleOutputFormat(format.value)} />
                          <span><strong>{format.label}</strong><small>{format.detail}</small></span>
                        </label>
                      );
                    })}
                  </div>
                </div>
                <p className="output-format-note"><Info size={15} />這些設定會保存在此瀏覽器，下次自動沿用。</p>
              </div>
            )}
          </div>

          {error && <div className="form-error"><TriangleAlert size={17} /><span>{error}</span></div>}

          <div className="form-actions">
            <span><Info size={16} />按下後會自動完成唯讀預覽與 Preflight，不會開始付費辨識；估價完成後下一頁只需一次確認。</span>
            <button
              className="button button--primary button--large"
              type="button"
              disabled={!canPrepare || busy !== null}
              onClick={() => void prepareAndCreateBatch()}
            >
              {busy === "prepare" && <LoaderCircle className="spin" size={16} />}
              檢查檔案與估價
            </button>
          </div>
        </section>

        <aside className="panel sticky-card">
          <h2>本次設定</h2>
          <div className="summary-list">
            <div><span>目前選取</span><strong>{selectionMode === "files" ? `${selected.size} 個檔案` : folderReady ? "目前整個資料夾" : "尚未選取"}</strong></div>
            <div><span>辨識速度</span><strong>{processingStrategy === "DYNAMIC_BATCHING" ? "經濟" : "快速"}</strong></div>
            <div><span>文字校正</span><strong>{correctionPolicy === "M3_FIRST" ? "M3 → Gemini 3.7" : "Gemini 3.7"}</strong></div>
            <div><span>輸出</span><strong>{outputFormats.map((format) => `.${format}`).join("、")}</strong></div>
            <div><span>Chirp 併發</span><strong>自動 / {chirpMaxParallelChunks}</strong></div>
            <div><span>預估成本上限</span><strong>US${costs?.projectLimitUsd ?? "200"}</strong></div>
          </div>
          <div className="cost-note">
            <Info size={17} />
            <p>FFprobe 完成後才會產生本批次預估費用。只有你在下一頁確認金額後，Worker 才能進入付費處理。</p>
          </div>
          <div className="privacy-note">
            <ShieldCheck size={17} />
            <div><strong>私人來源不公開</strong><span>瀏覽器不會取得 rclone、GCP 或模型憑證。</span></div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
