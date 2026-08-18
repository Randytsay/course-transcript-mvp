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

export default function ReviewPortalPage(){
 const [providers,setProviders]=useState<ProvidersResponse["providers"]|null>(null);const [me,setMe]=useState<MeResponse|null>(null);const [loading,setLoading]=useState(true);const [busy,setBusy]=useState<ProviderName|"logout"|null>(null);const [message,setMessage]=useState<string|null>(null);
 const refresh=useCallback(async()=>{setLoading(true);try{const [pr,mr]=await Promise.all([fetch("/api/v1/review/auth/providers",{cache:"no-store"}),fetch("/api/v1/review/auth/me",{cache:"no-store",credentials:"same-origin"})]);if(pr.ok)setProviders((await pr.json()).providers);if(mr.ok)setMe(await mr.json());else if(mr.status===401)setMe(null)}catch{setMessage("目前無法連線到登入服務，請稍後重新整理。")}finally{setLoading(false)}},[]);
 useEffect(()=>{void refresh()},[refresh]);
 async function startAuth(provider:ProviderName,action:"login"|"link"){setBusy(provider);setMessage(null);try{const headers:Record<string,string>={"Content-Type":"application/json"};if(action==="link"&&me?.csrf_token)headers["X-Review-CSRF"]=me.csrf_token;const r=await fetch(`/api/v1/review/auth/${provider}/start`,{method:"POST",credentials:"same-origin",headers,body:JSON.stringify({action,return_to:"/review"})});const body=await r.json().catch(()=>({}));if(!r.ok||typeof body.authorization_url!=="string")throw new Error(body.detail||"無法啟動登入流程");window.location.assign(body.authorization_url)}catch(e){setMessage(e instanceof Error?e.message:"無法啟動登入流程");setBusy(null)}}
 async function logout(){if(!me?.csrf_token)return;setBusy("logout");setMessage(null);try{const r=await fetch("/api/v1/review/auth/logout",{method:"POST",credentials:"same-origin",headers:{"X-Review-CSRF":me.csrf_token}});if(!r.ok){const body=await r.json().catch(()=>({}));throw new Error(body.detail||"登出失敗")}setMe(null)}catch(e){setMessage(e instanceof Error?e.message:"登出失敗")}finally{setBusy(null)}}
 const linkedProviders=new Set(me?.identities.map(identity=>identity.provider)??[]);
 return <main className={styles.page}><section className={styles.shell} aria-labelledby="review-title">
  <div className={styles.brandMark} aria-hidden="true">學</div><p className={styles.eyebrow}>佛學共學平台</p><h1 id="review-title">看課、複習、共修，都在同一個地方接著走。</h1>
  <p className={styles.lead}>系統會記住你看到哪裡、哪些課已學完、什麼時候該複習；字幕共修完成後，也能把核准內容整理成有影片出處的 AI 筆記、重點、問答與複習卡。</p>
  <section className={styles.howItWorks} aria-label="使用方式"><div><b>1</b><strong>接著上次學習</strong><span>影片會記住你的觀看位置</span></div><div><b>2</b><strong>用重點快速複習</strong><span>AI 筆記可回到影片時間核對</span></div><div><b>3</b><strong>一起把字幕校準</strong><span>發現錯字時再送出修改建議</span></div></section>
  <p className={styles.reassurance}>觀看進度、學習完成、複習與字幕共修分開記錄。AI 正式學習整理只採用已核准的字幕版本，不會因你的學習操作直接修改 YouTube。</p>
  <p className={styles.reassurance}>想參與字幕共修時也很簡單：選一支影片、邊看邊校對、送出修改建議；不用調整時間碼，也不會直接改到 YouTube，正式字幕仍由管理員統一審核。</p>
  {loading?<div className={styles.statusCard}>正在確認登入狀態…</div>:me?<div className={styles.accountCard}>
    <div className={styles.identityRow}>{me.user.avatar_url?/* eslint-disable-next-line @next/next/no-img-element */<img src={me.user.avatar_url} alt="" className={styles.avatar}/>:<div className={styles.avatarFallback} aria-hidden="true">{me.user.display_name.slice(0,1)}</div>}<div><p className={styles.signedIn}>歡迎回來</p><h2>{me.user.display_name}</h2></div></div>
    <a className={styles.enterReviewButton} href="/review/learn"><span>我的學習中心</span><strong>繼續觀看、查看進度、開始複習 →</strong></a>
    <div className={styles.quickLinks}><a href="/review/videos">字幕共修</a><a href="/review/learn/review">複習中心</a><a href="/review/learn/search">知識搜尋</a><a href="/review/contributions">我的共修紀錄</a></div>
    <div className={styles.linkedSection}><p className={styles.sectionLabel}>登入方式</p><div className={styles.providerChips}>{me.identities.map(identity=><span className={styles.providerChip} key={identity.provider}><span className={styles.check}>✓</span>{providerLabels[identity.provider]}{identity.email?<small>{identity.email}</small>:null}</span>)}</div></div>
    <div className={styles.actions}>{(["google","line"] as ProviderName[]).map(provider=>{const configured=providers?.[provider]?.configured??false;const linked=linkedProviders.has(provider);if(linked)return null;return <button className={styles.secondaryButton} disabled={!configured||busy!==null} key={provider} onClick={()=>void startAuth(provider,"link")} type="button">{busy===provider?"連結中…":`加綁 ${providerLabels[provider]} 登入`}</button>})}<button className={styles.textButton} disabled={busy!==null} onClick={()=>void logout()} type="button">{busy==="logout"?"登出中…":"登出"}</button></div>
  </div>:<div className={styles.loginCard}><p className={styles.sectionLabel}>登入後即可保存自己的學習歷程</p><div className={styles.loginButtons}><button className={styles.googleButton} disabled={!providers?.google?.configured||busy!==null} onClick={()=>void startAuth("google","login")} type="button"><span className={styles.providerInitial}>G</span>{busy==="google"?"前往 Google…":"使用 Google 登入"}</button><button className={styles.lineButton} disabled={!providers?.line?.configured||busy!==null} onClick={()=>void startAuth("line","login")} type="button"><span className={styles.providerInitial}>L</span>{busy==="line"?"前往 LINE…":"使用 LINE 登入"}</button></div><p className={styles.privacyNote}>系統只使用登入身分保存你的學習、筆記與共修紀錄；Google 或 LINE 的登入權杖不會存放在你的瀏覽器裡。</p></div>}
  {message?<p className={styles.errorMessage} role="alert">{message}</p>:null}
  <footer className={styles.footer}><span>跨裝置續學</span><span>AI 筆記可追溯</span><span>個人筆記保存</span><span>字幕修改先審核</span></footer>
 </section></main>
}
