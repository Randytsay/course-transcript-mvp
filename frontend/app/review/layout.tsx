import type { ReactNode } from "react";

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {children}
      <a
        href="/review/help"
        aria-label="開啟佛學共學平台使用說明"
        style={{
          position: "fixed",
          right: 18,
          bottom: 18,
          zIndex: 80,
          minHeight: 44,
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          padding: "10px 14px",
          borderRadius: 999,
          border: "1px solid rgba(49,92,75,.2)",
          background: "rgba(255,255,255,.96)",
          color: "#315c4b",
          boxShadow: "0 8px 28px rgba(35,55,45,.16)",
          textDecoration: "none",
          fontSize: 14,
          fontWeight: 800,
          lineHeight: 1,
          WebkitBackdropFilter: "blur(8px)",
          backdropFilter: "blur(8px)",
        }}
      >
        <span aria-hidden="true" style={{fontSize:18}}>?</span>
        <span>使用說明</span>
      </a>
    </>
  );
}
