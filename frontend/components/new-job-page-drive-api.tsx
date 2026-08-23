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
  Layers3,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  SquareCheckBig,
  TriangleAlert,
  Zap,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { getCosts, previewBatch } from "@/lib/api-client";
import {
  browseDrivePage,
  DriveDirectoryPage,
  searchDrivePage,
} from "@/lib/drive-browser-client";
import {
  createBatchWithPolicy,
  type AIProviderId,
  getCorrectionProviderStatus,
  type CorrectionPolicy,
} from "@/lib/correction-policy-client";
import type {
  BatchPreview,
  ContentMode,
  CostSummary,
  CreatedBatch,
  DriveEntry,
  OutputFormat,
  ProcessingStrategy,
} from "@/lib/types";
import { formatTwd } from "@/lib/currency";

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
  const router = useRouter();
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
  const [processingStrategy, setProcessingStrategy] = useState<ProcessingStrategy>("DYNAMIC_BATCHING");
  const [correctionPolicy, setCorrectionPolicy] = useState<CorrectionPolicy>("GEMINI_FIRST");
  const [m3Enabled, setM3Enabled] = useState(false);
  const [m3Configured, setM3Configured] = useState(false);
  const [m3StatusLoaded, setM3StatusLoaded] = useState(false);
  const [m3Model, setM3Model] = useState("MiniMax-M3");
  const [m3QuotaLiveCheck, setM3QuotaLiveCheck] = useState(false);
  const [m3QuotaState, setM3QuotaState] = useState<"available" | "unavailable" | "unknown">("unknown");
  const [outputFormats, setOutputFormats] = useState<OutputFormat[]>(DEFAULT_OUTPUT_FORMATS);
  const [contentMode, setContentMode] = useState<ContentMode>("general");
  const [documentContext, setDocumentContext] = useState("");

  // Provider router per-job selection
  type ProviderProfileLite = { id: string; name?: string; provider?: string; default_model?: string };
  type ProviderModelLite = { id: string; name?: string };
  const [providerId, setProviderId] = useState<AIProviderId>("vertex");
  const [providerProfiles, setProviderProfiles] = useState<ProviderProfileLite[]>([]);
  const [providerProfileId, setProviderProfileId] = useState("");
  const [providerModels, setProviderModels] = useState<ProviderModelLite[]>([]);
  const [providerModel, setProviderModel] = useState("gemini-3.7-flash");
  const [executionMode, setExecutionMode] = useState<"REALTIME" | "BATCH">("REALTIME");
  const [fallbackPolicy, setFallbackPolicy] = useState<"RAW_CHIRP_FALLBACK">("RAW_CHIRP_FALLBACK");

  const DEFAULT_MODELS: Record<AIProviderId, string> = {
    vertex: "gemini-3.7-flash",
    openrouter: "google/gemini-3.7-flash",
    minimax: "MiniMax-M3",
  };

  // Load registered provider profiles for profile/model selectors.
  useEffect(() => {
    let alive = true;
    fetch("/api/v1/review-admin/ai-providers", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { profiles: [] }))
      .then((body: { profiles?: ProviderProfileLite[] }) => {
        if (!alive) return;
        const profiles = body.profiles ?? [];
        setProviderProfiles(profiles);
        if (profiles.length && !profiles.some((p) => p.id === providerProfileId)) {
          setProviderProfileId(profiles[0].id);
        }
      })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When switching provider: default model, clear model list (vertex/minimax
  // have fixed defaults; openrouter loads from its models endpoint).
  useEffect(() => {
    setExecutionMode("REALTIME");
    setProviderModel(DEFAULT_MODELS[providerId]);
    setProviderModels([]);
    if (providerId !== "openrouter" || !providerProfileId) return;
    let alive = true;
    fetch("/api/v1/review-admin/ai-providers/openrouter-models", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { models: [] }))
      .then((body: { models?: ProviderModelLite[] }) => {
        if (alive) setProviderModels(body.models ?? []);
      })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, providerProfileId]);

  // BATCH availability is server-confirmed per model — never trusted from UI.
  const [batchAvailable, setBatchAvailable] = useState(false);
  const [batchUnavailableReason, setBatchUnavailableReason] =
    useState<string>("此供應商／模型未確認支援批次");
  useEffect(() => {
    let alive = true;
    if (executionMode === "BATCH") return;
    fetch("/api/v1/review-admin/ai-providers/batch-capability?" +
          new URLSearchParams({ provider: providerId, model: providerModel }),
          { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { supported: false, reason: "無法確認批次支援" }))
      .then((body: { supported: boolean; reason?: string }) => {
        if (!alive) return;
        setBatchAvailable(body.supported === true);
        if (!body.supported && body.reason) setBatchUnavailableReason(body.reason);
      })
      .catch(() => { if (alive) setBatchAvailable(false); });
    return () => { alive = false; };
  }, [providerId, providerModel, executionMode]);

  // Cost preview only when pricing is known; never fabricate $0.
  const [costPreviewKnown, setCostPreviewKnown] = useState(false);
  const [costPreviewUsd, setCostPreviewUsd] = useState<number | null>(null);
  const [realtimeCostUsd, setRealtimeCostUsd] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/v1/review-admin/ai-providers/cost-preview?" +
          new URLSearchParams({ provider: providerId, model: providerModel,
                                execution_mode: executionMode }),
          { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { known: false }))
      .then((body: { known: boolean; estimated_cost_usd?: number | null;
                      realtime_cost_usd?: number | null }) => {
        if (!alive) return;
        setCostPreviewKnown(body.known === true && typeof body.estimated_cost_usd === "number");
        setCostPreviewUsd(body.estimated_cost_usd ?? null);
        setRealtimeCostUsd(body.realtime_cost_usd ?? null);
      })
      .catch(() => { if (alive) setCostPreviewKnown(false); });
    return () => { alive = false; };
  }, [providerId, providerModel, executionMode]);

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
    getCorrectionProviderStatus()
      .then((status) => {
        setM3Enabled(status.m3Enabled);
        setM3Configured(status.minimaxConfigured);
        setM3Model(status.m3Model);
        setM3QuotaLiveCheck(status.quotaLiveCheck);
        setM3QuotaState(status.quotaState);
        if (!status.m3Enabled || !status.minimaxConfigured) setCorrectionPolicy("GEMINI_FIRST");
      })
      .finally(() => setM3StatusLoaded(true));
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

  const m3SelectionAvailable = m3StatusLoaded && m3Enabled && m3Configured && m3QuotaLiveCheck;

  function describeM3Status() {
    if (!m3StatusLoaded) return "正在確認 M3 服務與 quota 狀態…";
    if (!m3Enabled) return "伺服器尚未開放 M3；目前所有任務仍會使用 Gemini 3.7。";
    if (!m3Configured) return "MiniMax key 尚未掛載；開啟前請先完成服務設定。";
    if (!m3QuotaLiveCheck) return "M3 quota 檢查尚未開啟；為安全起見會使用 Gemini 3.7。";
    if (m3QuotaState === "available") return "可手動啟用；目前 quota 可用，異常時本課程會單向轉 Gemini 3.7。";
    if (m3QuotaState === "unavailable") return "目前 quota 不可用；即使選取 M3，本課程也會安全從 Gemini 3.7 開始。";
    return "可手動啟用；quota 尚未確認時會安全從 Gemini 3.7 開始。";
  }

  function setM3Selection(enabled: boolean) {
    if (enabled && !m3SelectionAvailable) return;
    setCorrectionPolicy(enabled ? "M3_FIRST" : "GEMINI_FIRST");
  }

  async function prepareAndCreateBatch() {
    if (!directory || !canPreview) return;
    setBusy("preview");
    setError(null);
    setPreview(null);
    setCreated(null);
    try {
      const paths = selectionMode === "folder" ? [directory.currentPath] : selectedEntries.map((entry) => entry.sourcePath);
      const nextPreview = await previewBatch(selectionMode, paths);
      setPreview(nextPreview);
      setBusy("create");
      const nextBatch = await createBatchWithPolicy(nextPreview.batchPreviewId, correctionPolicy, chirpMaxParallelChunks, outputFormats, processingStrategy, contentMode, documentContext, {
        provider: providerId,
        provider_profile_id: providerProfileId,
        model: providerModel,
        execution_mode: executionMode,
        fallback_policy: fallbackPolicy,
      });
      setCreated(nextBatch);
      router.push(`/batches/${nextBatch.batchId}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法檢查檔案並建立 preflight 工作");
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

          <div className="form-section document-context-section">
            <div className="section-heading"><span className="step-number">3</span><div><h2>文件方向與辨識背景</h2><p>設定會隨每個工作保存；不會套用到之後的新文件。</p></div></div>
            <div className="context-mode-grid">
              <button type="button" className={`context-mode-card ${contentMode === "general" ? "context-mode-card--active" : ""}`} onClick={() => setContentMode("general")}>
                <span><strong>一般文件</strong><small>預設。課程、會議、訪談或其他非特定宗教內容。</small></span>{contentMode === "general" && <Check size={17} />}
              </button>
              <button type="button" className={`context-mode-card ${contentMode === "dacheng_buddhist" ? "context-mode-card--active" : ""}`} onClick={() => setContentMode("dacheng_buddhist")}>
                <span><strong>大成佛經</strong><small>使用固定咒語拼寫；講師與大眾完整重複時只在輸出字幕保留一次。</small></span>{contentMode === "dacheng_buddhist" && <Check size={17} />}
              </button>
            </div>
            <label className="context-textarea-label" htmlFor="document-context">補充說明（選填）</label>
            <textarea id="document-context" className="context-textarea" value={documentContext} maxLength={2400} onChange={(event) => setDocumentContext(event.target.value)} placeholder="例如：能源績效量測驗證課程；講者林佑璇；常見術語包含 M&V、基準線、節能量。" />
            <div className="context-note"><span>此背景只作校正參考，不會改變原始 Chirp 結果、時間碼或分段。</span><span>{documentContext.length}/2400</span></div>
            {selectionMode === "files" && selectedEntries.length > 1 && <p className="context-batch-note">目前會將這個設定套用到已選的 {selectedEntries.length} 個檔案；不同主題請分批建立。</p>}
          </div>

          <div className="form-section">
            <div className="section-heading"><span className="step-number">4</span><div><h2>AI 文字校正模型</h2><p>這是本批次的選擇，不會改變全域預設；原始 Chirp 文字、時間碼與分段永遠保留。</p></div></div>
            <div className={`model-route-card ${correctionPolicy === "M3_FIRST" ? "model-route-card--m3" : ""}`}>
              <div className="model-route-card__icon" aria-hidden="true">
                {correctionPolicy === "M3_FIRST" ? <Sparkles size={21} /> : <ShieldCheck size={21} />}
              </div>
              <div className="model-route-card__copy">
                <div className="model-route-card__heading"><strong>{correctionPolicy === "M3_FIRST" ? `${m3Model} 優先` : "Gemini 3.7 優先"}</strong><span className="model-route-card__badge">{correctionPolicy === "M3_FIRST" ? "人工抽查模式" : "安全預設"}</span></div>
                <p>{correctionPolicy === "M3_FIRST" ? `${m3Model} 先處理；quota、回應格式或服務異常時，本批次只會轉到 Gemini 3.7。` : "全程優先使用 Google Vertex AI Gemini 3.7 Flash。"}</p>
                <small className="model-route-card__status"><Zap size={14} />{describeM3Status()}</small>
              </div>
              <div className="model-route-card__control">
                <span className="model-route-card__control-label">
                  <strong>{correctionPolicy === "M3_FIRST" ? `目前：${m3Model}` : `可切換：${m3Model}`}</strong>
                  <small>{m3SelectionAvailable ? "只套用本批次" : "目前不可用"}</small>
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={correctionPolicy === "M3_FIRST"}
                  aria-label={`切換 ${m3Model} 人工抽查模式`}
                  className={`model-toggle ${correctionPolicy === "M3_FIRST" ? "model-toggle--on" : ""}`}
                  disabled={!m3StatusLoaded || !m3SelectionAvailable}
                  onClick={() => setM3Selection(correctionPolicy !== "M3_FIRST")}
                  title={!m3StatusLoaded ? "正在確認 MiniMax M3 狀態" : !m3SelectionAvailable ? describeM3Status() : undefined}
                >
                  <span className="model-toggle__thumb" />
                  <span className="sr-only">{correctionPolicy === "M3_FIRST" ? "已開啟" : "未開啟"}</span>
                </button>
              </div>
            </div>
            <div className="model-route-note"><ShieldCheck size={15} /><span>開關可選 MiniMax M3 人工抽查模式；關閉就是 Gemini 3.7。設定只影響這一批任務，建立後會把實際請求路由與 fallback 記錄在任務稽核檔。</span></div>

            <div className="model-route-card" data-testid="ai-provider-router">
              <div className="model-route-card__copy" style={{ flex: 1 }}>
                <div className="model-route-card__heading"><strong>AI 文字校正（Provider Router）</strong><span className="model-route-card__badge">進階</span></div>
                <div style={{ display: "grid", gap: 10, gridTemplateColumns: "1fr", marginTop: 10 }}>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontWeight: 700 }}>供應商</span>
                    <select value={providerId} onChange={(e) => {
                      const next = e.target.value as AIProviderId;
                      setProviderId(next);
                      setExecutionMode("REALTIME");
                    }}>
                      <option value="vertex">Google Vertex AI</option>
                      <option value="openrouter">OpenRouter</option>
                      <option value="minimax">MiniMax</option>
                    </select>
                  </label>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontWeight: 700 }}>帳號／設定檔</span>
                    {providerProfiles.length ? (
                      <select value={providerProfileId} onChange={(e) => setProviderProfileId(e.target.value)}>
                        {providerProfiles.map((p) => (
                          <option key={p.id} value={p.id}>{p.name || p.id}</option>
                        ))}
                      </select>
                    ) : (
                      <input value={providerProfileId} onChange={(e) => setProviderProfileId(e.target.value)}
                             placeholder="尚未登記設定檔；請到 AI 模型供應商新增" disabled />
                    )}
                  </label>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontWeight: 700 }}>模型</span>
                    {providerModels.length ? (
                      <select value={providerModel} onChange={(e) => { setProviderModel(e.target.value); setExecutionMode("REALTIME"); }}>
                        {!providerModels.some((m) => m.id === providerModel) && (
                          <option value={providerModel}>{providerModel}（未驗證 model）</option>
                        )}
                        {providerModels.map((m) => (
                          <option key={m.id} value={m.id}>{m.name ? `${m.name}（${m.id}）` : m.id}</option>
                        ))}
                      </select>
                    ) : (
                      <input value={providerModel} onChange={(e) => setProviderModel(e.target.value)}
                             placeholder={providerId === "minimax" ? "MiniMax-M3" : providerId === "vertex" ? "gemini-3.7-flash" : "openrouter model slug"} />
                    )}
                  </label>
                  <div style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontWeight: 700 }}>執行模式</span>
                    <div role="radiogroup" aria-label="AI 文字校正處理模式" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button type="button" role="radio" aria-checked={executionMode === "REALTIME"}
                              className={`selection-mode-card ${executionMode === "REALTIME" ? "selection-mode-card--active" : ""}`}
                              onClick={() => setExecutionMode("REALTIME")}>
                        <Zap size={17} /><span><strong>即時 REALTIME</strong><small>速度較快、標準價格</small></span>
                      </button>
                      <button type="button" role="radio" aria-checked={executionMode === "BATCH"}
                              className={`selection-mode-card ${executionMode === "BATCH" ? "selection-mode-card--active" : ""}`}
                              disabled={!batchAvailable}
                              title={!batchAvailable ? batchUnavailableReason ?? undefined : undefined}
                              onClick={() => batchAvailable && setExecutionMode("BATCH")}>
                        <Layers3 size={17} /><span><strong>經濟 BATCH</strong><small>{batchAvailable ? "非即時、較便宜、可能等待數小時" : batchUnavailableReason}</small></span>
                      </button>
                    </div>
                  </div>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontWeight: 700 }}>失敗時</span>
                    <select value={fallbackPolicy} onChange={(e) => setFallbackPolicy(e.target.value as typeof fallbackPolicy)}>
                      <option value="RAW_CHIRP_FALLBACK">保留 Chirp 原文（預設，零額外 AI 費用）</option>
                    </select>
                  </label>
                  {costPreviewKnown ? (
                    <p style={{ margin: 0 }}><small>預估 AI 成本：USD {costPreviewUsd?.toFixed(4)}{executionMode === "BATCH" && realtimeCostUsd != null && realtimeCostUsd > 0 ? `（相較即時約省 ${Math.round((1 - costPreviewUsd! / realtimeCostUsd) * 100)}%）` : ""}</small></p>
                  ) : (
                    <p style={{ margin: 0 }}><small>預估 AI 成本：價格未知（不顯示估算數字）</small></p>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading"><span className="step-number">5</span><div><h2>輸出與併發</h2><p>建立工作前只做唯讀預覽；付費辨識仍需另行確認費用。</p></div></div>
            <label style={{ display: "block", fontWeight: 700, marginBottom: 8 }}>辨識處理模式</label>
            <div className="selection-mode-grid">
              <button type="button" className={`selection-mode-card ${processingStrategy === "DYNAMIC_BATCHING" ? "selection-mode-card--active" : ""}`} onClick={() => setProcessingStrategy("DYNAMIC_BATCHING")}>
                <Layers3 size={21} /><span><strong>經濟模式（Dynamic Batch）</strong><small>Google 離峰處理，最多等待約 24 小時；費用較低</small></span>{processingStrategy === "DYNAMIC_BATCHING" && <Check size={17} />}
              </button>
              <button type="button" className={`selection-mode-card ${processingStrategy === "STANDARD_BATCH" ? "selection-mode-card--active" : ""}`} onClick={() => setProcessingStrategy("STANDARD_BATCH")}>
                <LoaderCircle size={21} /><span><strong>快速模式（Standard Batch）</strong><small>優先較快完成；費用較高，適合急件</small></span>{processingStrategy === "STANDARD_BATCH" && <Check size={17} />}
              </button>
            </div>
            <p style={{ margin: "8px 0 16px", fontSize: 13, color: "#64748b" }}>模式會寫入每個任務並反映到 preflight 預估費用；付費提交後不能切換。</p>
            <label style={{ display: "block", fontWeight: 700, marginBottom: 8 }}>Chirp 同時辨識分段數</label>
            <select value={chirpMaxParallelChunks} onChange={(event) => setChirpMaxParallelChunks(Number(event.target.value))} style={{ width: "100%", padding: 10, borderRadius: 8, border: "1px solid var(--border-strong)" }}>
              {[1, 2, 3, 4, 5].map((value) => <option value={value} key={value}>{value}{value === 3 ? "（建議）" : ""}</option>)}
            </select>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16 }}>
              {(["srt", "txt", "csv", "json", "vtt", "ass", "docx", "pdf"] as OutputFormat[]).map((format) => (
                <button type="button" key={format} className={`button ${outputFormats.includes(format) ? "button--primary" : "button--ghost"}`} onClick={() => toggleOutputFormat(format)}>.{format}</button>
              ))}
            </div>
          </div>

          <div className="form-section">
            <button type="button" className="button button--primary button--large" disabled={!canPreview || busy !== null} onClick={() => void prepareAndCreateBatch()}>
              {busy === "preview" || busy === "create" ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}
              {busy === "preview" ? "檢查檔案中…" : busy === "create" ? "建立 preflight 中…" : "檢查檔案與估價"}
            </button>
            {preview && busy === "create" && <div className="empty-state" style={{ marginTop: 14 }}>已檢查 {preview.itemCount} 個檔案，共 {formatBytes(preview.totalSizeBytes)}；正在建立 preflight 工作。</div>}
            {created && <div className="empty-state" style={{ marginTop: 14 }}>批次已建立：{created.batchId}。模型：{correctionPolicy === "M3_FIRST" ? `${m3Model} → Gemini 3.7` : "Gemini 3.7"}；模式：{created.processingStrategy === "DYNAMIC_BATCHING" ? "經濟 Dynamic Batch" : "快速 Standard Batch"}；尚未啟動付費辨識。</div>}
          </div>
        </section>

        <aside className="dashboard-side">
          <div className="panel quick-panel"><h2>Drive 混合架構</h2><p>目錄瀏覽、搜尋與健康檢查使用 Google Drive API；下載、成果上傳、備份與升版仍使用 rclone。</p><small>校正模型：{correctionPolicy === "M3_FIRST" ? `${m3Model} → Gemini 3.7` : "Gemini 3.7"}</small><small>剩餘預估額度：{costs ? formatTwd(costs.remainingEstimatedBudgetTwd) : "讀取中"}</small></div>
        </aside>
      </div>
    </AppShell>
  );
}
