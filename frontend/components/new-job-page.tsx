"use client";
import AppShell from "./app-shell";
import { ArrowLeft, FileAudio, Info, Layers3, ShieldCheck } from "lucide-react";
import Link from "next/link";

export default function NewJobPage() {
  return <AppShell title="新增轉錄任務" description="建立任務的後端佇列與成本保護尚未啟用。"><div className="new-job-layout"><section className="form-panel"><div className="form-section"><div className="section-heading"><span className="step-number">1</span><div><h2>目前安全範圍：唯讀檢視</h2><p>這個頁面保留既有工作流程設計，但第一個整合里程碑不允許瀏覽器建立付費任務。</p></div></div><div className="file-preview-card"><div className="file-preview-card__icon"><FileAudio size={25} /></div><div className="file-preview-card__copy"><strong>來源檔案由 VPS worker 管理</strong><span>不會向瀏覽器傳送 rclone 設定、Drive 存取權杖或 GCP 憑證。</span></div><span className="verified-pill"><ShieldCheck size={14} />安全</span></div></div><div className="form-section"><div className="section-heading"><span className="step-number">2</span><div><h2>下一個里程碑</h2><p>新增 POST /jobs 前，會先加入來源檢查、唯一任務鎖、費用上限與明確人工確認。</p></div></div><div className="workflow-choice workflow-choice--selected"><div className="workflow-choice__icon"><Layers3 size={22} /></div><div><strong>Chirp 3 時間軸 + Gemini 3.6 Flash 校正</strong><span>維持固定字幕 ID 和時間碼；不會由 Gemini 重新切分字幕。</span></div></div></div><div className="form-actions"><span><Info size={16} />目前不會建立 GCP、Drive 或 worker 請求。</span><Link href="/" className="button button--primary"><ArrowLeft size={17} />返回查看真實任務</Link></div></section></div></AppShell>;
}
