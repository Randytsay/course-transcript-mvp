"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./review.module.css";

type ProviderName = "google" | "line";
type ProviderStatus = { configured: boolean };
type ProvidersResponse = { providers: Record<ProviderName, ProviderStatus> };
type MeResponse = {
  user: { id: string; display_name: string; avatar_url: string | null; role: "owner" | "reviewer" };
  identities: Array<{ provider: ProviderName; email: string | null; created_at: string; last_login_at: string }>;
  csrf_token: string;
  session_expires_at: string;
};
const providerLabels: Record<ProviderName,string>={google:"Google",line:"LINE"};

function ProviderIcon({ provider }: { provider: ProviderName }) {
 if(provider==="google")return <svg aria-hidden="true" className={styles.providerLogo} focusable="false" viewBox="0 0 24 24"><path fill="#4285F4" d="M21.35 12.27c0-.79-.07-1.55-.23-2.27H12v4.3h5.24a4.48 4.48 0 0 1-1.94 2.94v2.45h3.14c1.84-1.7 2.91-4.2 2.91-7.42Z"/><path fill="#34A853" d="M12 21.5c2.63 0 4.84-.87 6.45-2.36l-3.14-2.45c-.87.58-1.98.92-3.31.92-2.54 0-4.69-1.72-5.46-4.03H3.3v2.53A9.74 9.74 0 0 0 12 21.5Z"/><path fill="#FBBC05" d="M6.54 13.58A5.85 5.85 0 0 1 6.23 12c0-.55.11-1.08.31-1.58V7.89H3.3A9.5 9.5 0 0 0 2.25 12c0 1.48.35 2.88 1.05 4.11l3.24-2.53Z"/><path fill="#EA4335" d="M12 6.39c1.43 0 2.71.49 3.72 1.45l2.79-2.79C16.84 3.47 14.63 2.5 12 2.5a9.74 9.74 0 0 0-8.7 5.39l3.24 2.53c.77-2.31 2.92-4.03 5.46-4.03Z"/></svg>;
 return <svg aria-hidden="true" className={styles.providerLogo} focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M20.1 10.2c0-3.2-3.6-5.8-8.1-5.8S3.9 7 3.9 10.2c0 2.9 2.8 5.3 6.6 5.7l-.4 1.4c-.1.4.3.7.7.5 1.8-.8 3.5-1.9 4.8-3.1 2.7-1 4.5-2.6 4.5-4.5Zm-10.9.7H8v-2h1.2v2Zm2.1 0h-1.2v-2h1.2v2Zm2.1 0h-1.2v-2h1.2v2Z"/></svg>;
}

export default function ReviewPortalPage(){
 const [providers,setProviders]=useState<ProvidersResponse["providers"]|null>(null);const [me,setMe]=useState<MeResponse|null>(null);const [loading,setLoading]=useState(true);const [busy,setBusy]=useState<ProviderName|"logout"|null>(null);const [message,setMessage]=useState<string|null>(null);
 const refresh=useCallback(async()=>{setLoading(true);try{const [pr,mr]=await Promise.all([fetch("/api/v1/review/auth/providers",{cache:"no-store"}),fetch("/api/v1/review/auth/me",{cache:"no-store",credentials:"same-origin"})]);if(pr.ok)setProviders((await pr.json()).providers);if(mr.ok)setMe(await mr.json());else if(mr.status===401)setMe(null)}catch{setMessage("目前無法連線到登入服務，請稍後重新整理。")}finally{setLoading(false)}},[]);
 useEffect(()=>{void refresh()},[refresh]);
 async function startAuth(provider:ProviderName,action:"login"|"link"){setBusy(provider);setMessage(null);try{const headers:Record<string,string>={"Content-Type":"application/json"};if(action==="link"&&me?.csrf_token)headers["X-Review-CSRF"]=me.csrf_token;const r=await fetch(`/api/v1/review/auth/${provider}/start`,{method:"POST",credentials:"same-origin",headers,body:JSON.stringify({action,return_to:"/review"})});const body=await r.json().catch(()=>({}));if(!r.ok||typeof body.authorization_url!=="string")throw new Error(body.detail||"無法啟動登入流程");window.location.assign(body.authorization_url)}catch(e){setMessage(e instanceof Error?e.message:"無法啟動登入流程");setBusy(null)}}
 async function logout(){if(!me?.csrf_token)return;setBusy("logout");setMessage(null);try{const r=await fetch("/api/v1/review/auth/logout",{method:"POST",credentials:"same-origin",headers:{"X-Review-CSRF":me.csrf_token}});if(!r.ok){const body=await r.json().catch(()=>({}));throw new Error(body.detail||"登出失敗")}setMe(null)}catch(e){setMessage(e instanceof Error?e.message:"登出失敗")}finally{setBusy(null)}}
 const linkedProviders=new Set(me?.identities.map(identity=>identity.provider)??[]);
 return <main className={styles.page}><section className={styles.shell} aria-labelledby="review-title">
  <div className={styles.brandMark} aria-hidden="true">慈</div><p className={styles.eyebrow}>慈聖佛堂・佛學共學平台</p><h1 id="review-title">歡迎加入慈聖大成佛經校稿志工行列</h1>
  <p className={styles.lead}>一起讓佛法影片的字幕更準確、更好讀。登入後即可參與字幕校訂，也能保存自己的學習進度、筆記與複習紀錄。</p>
  <section className={styles.howItWorks} aria-label="使用方式"><div><b>1</b><strong>接著上次學習</strong><span>影片會記住你的觀看位置</span></div><div><b>2</b><strong>用重點快速複習</strong><span>AI 筆記可回到影片時間核對</span></div><div><b>3</b><strong>一起把字幕校準</strong><span>發現錯字時再送出修改建議</span></div></section>
  <p className={styles.reassurance}>觀看進度、學習完成、複習與字幕共修分開記錄。AI 正式學習整理只採用已核准的字幕版本，不會因你的學習操作直接修改 YouTube。</p>
  <p className={styles.reassurance}>想參與字幕共修時也很簡單：選一支影片、邊看邊校對、送出修改建議；不用調整時間碼，也不會直接改到 YouTube，正式字幕仍由管理員統一審核。</p>
  <p className={styles.reassurance}><a href="/review/help" style={{color:"inherit",fontWeight:800}}>第一次使用？查看完整使用說明 →</a></p>
  {loading?<div className={styles.statusCard}>正在確認登入狀態…</div>:me?<div className={styles.accountCard}>
    <div className={styles.identityRow}>{me.user.avatar_url?/* eslint-disable-next-line @next/next/no-img-element */<img src={me.user.avatar_url} alt="" className={styles.avatar}/>:<div className={styles.avatarFallback} aria-hidden="true">{me.user.display_name.slice(0,1)}</div>}<div><p className={styles.signedIn}>歡迎回來</p><h2>{me.user.display_name}</h2></div></div>
    <a className={styles.enterReviewButton} href="/review/learn"><span>我的學習中心</span><strong>繼續觀看、查看進度、開始複習 →</strong></a>
    <div className={styles.quickLinks}><a href="/review/learn/notes">我的筆記與書籤</a><a href="/review/videos">字幕共修</a><a href="/review/learn/review">複習中心</a><a href="/review/learn/search">知識搜尋</a><a href="/review/contributions">我的共修紀錄</a><a href="/review/help">使用說明</a></div>
    <div className={styles.linkedSection}><p className={styles.sectionLabel}>登入方式</p><div className={styles.providerChips}>{me.identities.map(identity=><span className={styles.providerChip} key={identity.provider}><span className={styles.check}>✓</span>{providerLabels[identity.provider]}{identity.email?<small>{identity.email}</small>:null}</span>)}</div></div>
    <div className={styles.actions}>{(["google","line"] as ProviderName[]).map(provider=>{const configured=providers?.[provider]?.configured??false;const linked=linkedProviders.has(provider);if(linked)return null;return <button className={styles.secondaryButton} disabled={!configured||busy!==null} key={provider} onClick={()=>void startAuth(provider,"link")} type="button">{busy===provider?"連結中…":`加綁 ${providerLabels[provider]} 登入`}</button>})}<button className={styles.textButton} disabled={busy!==null} onClick={()=>void logout()} type="button">{busy==="logout"?"登出中…":"登出"}</button></div>
  </div>:<div className={styles.loginCard}><p className={styles.sectionLabel}>登入後即可加入校稿，保存自己的學習歷程</p><div className={styles.loginButtons}><button className={styles.googleButton} disabled={!providers?.google?.configured||busy!==null} onClick={()=>void startAuth("google","login")} type="button"><ProviderIcon provider="google" />{busy==="google"?"前往 Google…":"使用 Google 登入"}</button><button className={styles.lineButton} disabled={!providers?.line?.configured||busy!==null} onClick={()=>void startAuth("line","login")} type="button"><ProviderIcon provider="line" />{busy==="line"?"前往 LINE…":"使用 LINE 登入"}</button></div><p className={styles.privacyNote}>系統只使用登入身分保存你的學習、筆記與共修紀錄；Google 或 LINE 的登入權杖不會存放在你的瀏覽器裡。</p></div>}
  {message?<p className={styles.errorMessage} role="alert">{message}</p>:null}
  <footer className={styles.footer}><span>跨裝置續學</span><span>AI 筆記可追溯</span><span>個人筆記保存</span><span>字幕修改先審核</span></footer>
 </section></main>
}
