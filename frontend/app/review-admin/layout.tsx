import type { ReactNode } from "react";

export default function ReviewAdminLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <div style={{background:"#203f34",color:"#fff",padding:"10px 18px",display:"flex",gap:10,alignItems:"center",justifyContent:"center",flexWrap:"wrap",fontSize:14}}>
        <strong style={{marginRight:8}}>佛學共學管理</strong>
        <a href="/review-admin" style={{color:"#fff",textDecoration:"none",padding:"6px 10px",borderRadius:999,background:"rgba(255,255,255,.12)"}}>字幕共修管理</a>
        <a href="/review-admin/learning" style={{color:"#fff",textDecoration:"none",padding:"6px 10px",borderRadius:999,background:"rgba(255,255,255,.12)"}}>AI 學習內容</a>
        <a href="/review-admin/ai-accounts" style={{color:"#fff",textDecoration:"none",padding:"6px 10px",borderRadius:999,background:"rgba(255,255,255,.12)"}}>AI 帳戶管理</a>
        <a href="/review-admin/ai-providers" style={{color:"#fff",textDecoration:"none",padding:"6px 10px",borderRadius:999,background:"rgba(255,255,255,.12)"}}>AI 模型供應商</a>
        <a href="/review-admin/help" style={{color:"#fff",textDecoration:"none",padding:"6px 10px",borderRadius:999,background:"rgba(255,255,255,.12)"}}>管理員說明</a>
        <a href="/review/videos" style={{color:"#d9e9e1",textDecoration:"none",padding:"6px 10px"}}>進入校訂入口 ↗</a>
      </div>
      {children}
    </>
  );
}
