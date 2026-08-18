"use client";

import { useEffect, useState } from "react";
import styles from "../learning.module.css";

type ReviewItem={youtube_video_id:string;title:string;duration_ms:number|null;last_playback_ms:number|null;next_due_at:string;stage:number;completed_cycles:number;artifact_id:string|null};
function stamp(ms:number|null|undefined){const total=Math.max(0,Math.floor((ms||0)/1000));const h=Math.floor(total/3600);const m=Math.floor((total%3600)/60);const s=total%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
const cadence=["第 1 天","第 3 天","第 7 天","第 14 天","第 30 天"];
export default function ReviewCenterPage(){
 const [items,setItems]=useState<ReviewItem[]|null>(null);const [error,setError]=useState<string|null>(null);
 useEffect(()=>{let cancelled=false;async function load(){try{const r=await fetch("/api/v1/review/learning/review-queue",{cache:"no-store",credentials:"same-origin"});if(r.status===401){window.location.assign("/review");return}if(!r.ok)throw new Error("目前無法讀取複習清單");const body=await r.json();if(!cancelled)setItems(body.items||[])}catch(e){if(!cancelled)setError(e instanceof Error?e.message:"載入失敗")}}void load();return()=>{cancelled=true}},[]);
 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.topbar}><a className={styles.brand} href="/review/learn"><span className={styles.brandMark}>學</span><span className={styles.brandText}><strong>佛學共學平台</strong><span>複習中心</span></span></a><nav className={styles.nav}><a href="/review/learn">學習中心</a><a href="/review/videos">字幕共修</a><a href="/review/learn/search">知識搜尋</a></nav></header>
  <section className={styles.heroMain}><p className={styles.eyebrow}>我的複習中心</p><h1>不是看完就算了，把重要內容在對的時間重新想起來。</h1><p>完成一堂課後，系統依 1、3、7、14、30 天節奏提醒複習。每次複習可以用 3 分鐘重點、10 分鐘摘要、Flashcards 或自我測驗，不會影響你的字幕共修進度。</p></section>
  <div className={styles.sectionHeader}><div><h2>今天到期</h2><p>{items?`共有 ${items.length} 堂等待複習。`:"正在整理…"}</p></div></div>
  {error?<p className={styles.error}>{error}</p>:items===null?<p className={styles.status}>正在整理複習清單…</p>:items.length?<section className={styles.lessonGrid}>{items.map(item=><article className={styles.lessonCard} key={item.youtube_video_id}>
    {/* eslint-disable-next-line @next/next/no-img-element */}<img className={styles.thumb} src={`https://i.ytimg.com/vi/${item.youtube_video_id}/hqdefault.jpg`} alt=""/>
    <div className={styles.lessonBody}><h3 className={styles.lessonTitle}>{item.title}</h3><div className={styles.metaRow}><span className={`${styles.badge} ${styles.badgeDue}`}>今天要複習</span><span className={styles.badge}>{cadence[Math.min(item.stage||0,cadence.length-1)]}</span>{item.artifact_id?<span className={styles.badge}>AI 複習內容可用</span>:null}</div><p className={styles.muted}>上次影片位置 {stamp(item.last_playback_ms)}{item.completed_cycles?` ・ 已完成 ${item.completed_cycles} 輪複習`:""}</p><div className={styles.cardActions}><a className={styles.openButton} href={`/review/learn/${item.youtube_video_id}?tab=review`}>開始快速複習</a><a className={styles.reviewButton} href={`/review/learn/${item.youtube_video_id}?t=${Math.floor((item.last_playback_ms||0)/1000)}`}>回看影片</a></div></div>
  </article>)}</section>:<div className={styles.empty}>今天沒有到期的複習。可以回到學習中心繼續下一堂。</div>}
  <div className={styles.sectionHeader}><div><h2>複習節奏</h2><p>預設使用簡單、可預期的間隔複習，不把學習變成壓力。</p></div></div>
  <section className={styles.panel}><div className={styles.stats}>{["1 天後","3 天後","7 天後","14 天後","30 天後"].map((label,i)=><div className={styles.statCard} key={label}><strong>{i+1}</strong><span>{label}</span></div>)}</div><p className={styles.muted}>完成一次複習後，系統才安排下一次。Flashcards 會依「再來一次／有點難／記得／很熟」另外調整單張卡片的下次時間。</p></section>
 </div></main>
}
