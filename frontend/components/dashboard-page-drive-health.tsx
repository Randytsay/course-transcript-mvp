"use client";

import { useEffect, useState } from "react";
import { HardDrive, LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react";
import DashboardPage from "./dashboard-page";
import { DriveHealth, getDriveHealth } from "@/lib/drive-browser-client";

function statusCopy(health: DriveHealth | null, loading: boolean) {
  if (loading) return "檢查中";
  if (!health) return "無法連線";
  if (health.status === "ok") return "正常";
  if (health.status === "degraded") return "備援模式";
  return "異常";
}

export default function DashboardPageDriveHealth() {
  const [health, setHealth] = useState<DriveHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [toastOpen, setToastOpen] = useState(true);

  async function loadHealth() {
    setLoading(true);
    try {
      setHealth(await getDriveHealth());
    } catch {
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadHealth();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadHealth();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const abnormal = !loading && (!health || health.status === "error");

  return (
    <>
      {toastOpen && <div style={{ position: "fixed", zIndex: 40, right: 18, bottom: 18, maxWidth: 330, background: "var(--surface)", border: `1px solid ${abnormal ? "#ef4444" : "var(--border)"}`, borderRadius: 12, boxShadow: "0 12px 30px rgba(15,23,42,.15)", padding: "12px 14px", display: "flex", alignItems: "center", gap: 12 }}>
        <span className={`service-icon ${abnormal ? "service-icon--amber" : "service-icon--green"}`}>
          {loading ? <LoaderCircle className="spin" size={16} /> : abnormal ? <TriangleAlert size={16} /> : <HardDrive size={16} />}
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <strong style={{ display: "block", fontSize: 14 }}>Google Drive：{statusCopy(health, loading)}</strong>
          <small style={{ display: "block", color: "var(--muted)", marginTop: 2 }}>
            {health
              ? `瀏覽 ${health.provider === "google_api" ? "Drive API" : "rclone"}；傳輸 rclone${health.fallbackAvailable ? "；備援可用" : ""}`
              : "健康檢查未完成"}
          </small>
          {health?.warning && <small style={{ display: "block", color: "#9a3412", marginTop: 2 }}>{health.warning}</small>}
        </div>
        <button type="button" className="icon-button" aria-label="重新檢查 Google Drive" onClick={() => void loadHealth()} disabled={loading}>
          <RefreshCw size={16} className={loading ? "spin" : ""} />
        </button>
        {!abnormal && (
          <button type="button" className="icon-button" aria-label="隱藏狀態通知" onClick={() => setToastOpen(false)}>
            ✕
          </button>
        )}
      </div>}
      <DashboardPage />
    </>
  );
}
