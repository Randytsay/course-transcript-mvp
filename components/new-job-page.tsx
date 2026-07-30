"use client";

import AppShell from "./app-shell";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FileAudio,
  FolderSearch,
  Info,
  Layers3,
  LoaderCircle,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import { FormEvent, useState } from "react";

const defaultDrivePath = "gdrive:01 美安/01 態度與知識/01 GMTSS課程/08 產品課/20251207 楊筑雅-女性保健/voice_11386603-seg1.mp3";

export default function NewJobPage() {
  const [drivePath, setDrivePath] = useState(defaultDrivePath);
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    window.setTimeout(() => {
      setIsSubmitting(false);
      setSubmitted(true);
    }, 850);
  }

  return (
    <AppShell
      title="新增轉錄任務"
      description="從 Google Drive 選擇一支課程錄音，建立可續跑的精準轉錄工作。"
      actions={<Link href="/" className="button button--ghost"><ArrowLeft size={17} />返回儀表板</Link>}
    >
      <div className="new-job-layout">
        <form className="form-panel" onSubmit={handleSubmit}>
          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">1</span>
              <div><h2>選擇來源檔案</h2><p>系統僅讀取指定檔案，不會修改原始 Drive 內容。</p></div>
            </div>

            <label className="field-label" htmlFor="drive-path">Google Drive 路徑</label>
            <div className="path-input-wrap">
              <FolderSearch size={19} />
              <input id="drive-path" value={drivePath} onChange={(event) => setDrivePath(event.target.value)} />
              <button type="button" className="button button--secondary button--small">瀏覽</button>
            </div>

            <div className="file-preview-card">
              <div className="file-preview-card__icon"><FileAudio size={25} /></div>
              <div className="file-preview-card__copy">
                <strong>voice_11386603-seg1.mp3</strong>
                <span>MP3 · 53.6 MB · 55:49 · 雙聲道 48 kHz</span>
              </div>
              <span className="verified-pill"><Check size={14} />已驗證</span>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">2</span>
              <div><h2>辨識設定</h2><p>第一版固定使用經驗證的 Chirp + Gemini 雙模型工作流。</p></div>
            </div>

            <div className="form-grid form-grid--two">
              <label className="form-field">
                <span>語言</span>
                <select defaultValue="cmn-Hant-TW">
                  <option value="cmn-Hant-TW">繁體中文（台灣）</option>
                  <option value="en-US">English (US)</option>
                </select>
              </label>
              <label className="form-field">
                <span>處理模式</span>
                <select defaultValue="accuracy">
                  <option value="accuracy">最高精準度</option>
                  <option value="fast">快速草稿</option>
                </select>
              </label>
            </div>

            <div className="workflow-choice workflow-choice--selected">
              <div className="workflow-choice__check"><Check size={16} /></div>
              <div className="workflow-choice__icon"><Layers3 size={22} /></div>
              <div>
                <strong>Chirp 3 時間軸 + Gemini 3.6 Flash 校正</strong>
                <span>15 分鐘分段、10 秒重疊、字詞時間碼、固定字幕段落校正。</span>
              </div>
              <span className="recommended-tag">建議</span>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="step-number">3</span>
              <div><h2>輸出與審查</h2><p>先產出本機結果並停在人工 QA，不自動覆蓋或上傳原檔。</p></div>
            </div>

            <div className="toggle-list">
              <label><input type="checkbox" defaultChecked /><span className="toggle-ui" /><div><strong>Gemini 術語校正</strong><small>依全域術語表修正產品名、縮寫與繁體用字。</small></div></label>
              <label><input type="checkbox" defaultChecked /><span className="toggle-ui" /><div><strong>產生 SRT / VTT</strong><small>保留 Chirp 時間軸，輸出可讀版字幕。</small></div></label>
              <label><input type="checkbox" defaultChecked /><span className="toggle-ui" /><div><strong>人工審查閘門</strong><small>QA 通過後仍需人工確認，才可上傳 Drive。</small></div></label>
            </div>
          </div>

          {submitted && (
            <div className="success-banner" role="status">
              <ShieldCheck size={20} />
              <div><strong>前端原型已建立模擬任務</strong><span>串接後端後，此處會顯示真實 job_id 與排隊狀態。</span></div>
              <Link href="/jobs/voice-11386603-seg1">查看任務 <ChevronRight size={16} /></Link>
            </div>
          )}

          <div className="form-actions">
            <span><Info size={16} />目前為假資料原型，不會真的送出 GCP 請求。</span>
            <button className="button button--primary button--large" type="submit" disabled={isSubmitting}>
              {isSubmitting ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />}
              {isSubmitting ? "建立中…" : "建立轉錄任務"}
            </button>
          </div>
        </form>

        <aside className="setup-summary">
          <div className="panel sticky-card">
            <h2>本次工作摘要</h2>
            <div className="summary-list">
              <div><span>音訊長度</span><strong>55:49</strong></div>
              <div><span>Chirp 分段</span><strong>4 段</strong></div>
              <div><span>分段重疊</span><strong>10 秒</strong></div>
              <div><span>字幕語言</span><strong>繁體中文</strong></div>
              <div><span>預計輸出</span><strong>8 種檔案</strong></div>
            </div>
            <div className="cost-note">
              <Info size={17} />
              <p>正式串接後，後端應回傳實際模型、區域、預估成本與是否需要人工審查。</p>
            </div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
