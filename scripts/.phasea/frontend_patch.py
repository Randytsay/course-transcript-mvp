from pathlib import Path
p=Path('frontend/components/new-job-page-drive-api.tsx')
s=p.read_text()
def r(a,b):
    global s
    if a not in s: raise SystemExit('missing frontend pattern: '+a[:80])
    s=s.replace(a,b,1)
r('import { FormEvent, useEffect, useMemo, useState } from "react";\n','import { useRouter } from "next/navigation";\nimport { FormEvent, useEffect, useMemo, useState } from "react";\n')
r('export default function NewJobPageDriveApi() {\n  const [directory, setDirectory]','export default function NewJobPageDriveApi() {\n  const router = useRouter();\n  const [directory, setDirectory]')
r('  const [m3Enabled, setM3Enabled] = useState(false);\n  const [m3StatusLoaded','  const [m3Enabled, setM3Enabled] = useState(false);\n  const [m3Configured, setM3Configured] = useState(false);\n  const [m3StatusLoaded')
r('  const canPreview = selectionMode === "files" ? selected.size > 0 : folderReady;\n','  const canPreview = selectionMode === "files" ? selected.size > 0 : folderReady;\n  const m3Ready = m3Enabled && m3Configured;\n')
r('''        setM3Enabled(status.m3Enabled);\n        setM3Model(status.m3Model);\n        setM3QuotaState(status.quotaState);\n        if (!status.m3Enabled) setCorrectionPolicy("GEMINI_FIRST");''','''        setM3Enabled(status.m3Enabled);\n        setM3Configured(status.minimaxConfigured);\n        setM3Model(status.m3Model);\n        setM3QuotaState(status.quotaState);\n        if (!status.m3Enabled || !status.minimaxConfigured) setCorrectionPolicy("GEMINI_FIRST");''')
start=s.index('  async function inspectSelection() {')
end=s.index('\n\n  return (',start)
s=s[:start]+'''  async function prepareAndCreateBatch() {\n    if (!directory || !canPreview) return;\n    setBusy("preview");\n    setError(null);\n    setPreview(null);\n    setCreated(null);\n    try {\n      const paths = selectionMode === "folder" ? [directory.currentPath] : selectedEntries.map((entry) => entry.sourcePath);\n      const nextPreview = await previewBatch(selectionMode, paths);\n      setPreview(nextPreview);\n      setBusy("create");\n      const nextBatch = await createBatchWithPolicy(nextPreview.batchPreviewId, correctionPolicy, chirpMaxParallelChunks, outputFormats, processingStrategy, contentMode, documentContext);\n      setCreated(nextBatch);\n      router.push(`/batches/${nextBatch.batchId}`);\n    } catch (cause) {\n      setError(cause instanceof Error ? cause.message : "無法檢查檔案並建立 preflight 工作");\n      setBusy(null);\n    }\n  }'''+s[end:]
r('disabled={!m3StatusLoaded || !m3Enabled}','disabled={!m3StatusLoaded || !m3Ready}')
r('onClick={() => m3Enabled && setCorrectionPolicy("M3_FIRST")}','onClick={() => m3Ready && setCorrectionPolicy("M3_FIRST")}')
r('title={!m3StatusLoaded ? "正在確認 MiniMax M3 狀態" : !m3Enabled ? "MiniMax M3 尚未在 production 啟用" : undefined}','title={!m3StatusLoaded ? "正在確認 MiniMax M3 狀態" : !m3Configured ? "MiniMax 憑證尚未完成設定" : !m3Enabled ? "MiniMax M3 尚未在 production 啟用" : undefined}')
r('{!m3StatusLoaded ? "（檢查中）" : !m3Enabled ? "（未啟用）" : ""}','{!m3StatusLoaded ? "（檢查中）" : !m3Configured ? "（尚未設定）" : !m3Enabled ? "（未啟用）" : ""}')
old='''            <button type="button" className="button button--primary button--large" disabled={!canPreview || busy !== null} onClick={() => void inspectSelection()}>\n              {busy === "preview" ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}建立唯讀批次預覽\n            </button>\n            {preview && <div className="empty-state" style={{ marginTop: 14 }}>已預覽 {preview.itemCount} 個檔案，共 {formatBytes(preview.totalSizeBytes)}。<button type="button" className="button button--primary" disabled={busy !== null} onClick={() => void createPreflightBatch()}>{busy === "create" ? "建立中…" : "建立 preflight 工作"}</button></div>}'''
new='''            <button type="button" className="button button--primary button--large" disabled={!canPreview || busy !== null} onClick={() => void prepareAndCreateBatch()}>\n              {busy === "preview" || busy === "create" ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}\n              {busy === "preview" ? "檢查檔案中…" : busy === "create" ? "建立 preflight 中…" : "檢查檔案與估價"}\n            </button>\n            {preview && busy === "create" && <div className="empty-state" style={{ marginTop: 14 }}>已檢查 {preview.itemCount} 個檔案，共 {formatBytes(preview.totalSizeBytes)}；正在建立 preflight 工作。</div>}'''
r(old,new)
p.write_text(s)

q=Path('frontend/lib/correction-policy-client.ts'); t=q.read_text()
t=t.replace('  quotaState: "available" | "unavailable" | "unknown";\n}> {','  quotaState: "available" | "unavailable" | "unknown";\n  quotaCheckedAt: string | null;\n  quotaSourcePool: string | null;\n}> {',1)
t=t.replace('      quotaState: "unknown",\n    };','      quotaState: "unknown",\n      quotaCheckedAt: null,\n      quotaSourcePool: null,\n    };',1)
t=t.replace('    quota_state?: "available" | "unavailable" | "unknown";\n  };','    quota_state?: "available" | "unavailable" | "unknown";\n    quota_checked_at?: string | null;\n    quota_source_pool?: string | null;\n  };',1)
t=t.replace('    quotaState: payload.quota_state ?? "unknown",\n  };','    quotaState: payload.quota_state ?? "unknown",\n    quotaCheckedAt: payload.quota_checked_at ?? null,\n    quotaSourcePool: payload.quota_source_pool ?? null,\n  };',1)
q.write_text(t)
