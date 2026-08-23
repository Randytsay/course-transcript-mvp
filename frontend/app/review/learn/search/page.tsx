"use client";

import { FormEvent, useState } from "react";
import styles from "../learning.module.css";
import ReviewNav from "../../review-nav";

type SubtitleResult={segment_id:number;youtube_video_id:string;segment_index:number;start_ms:number;end_ms:number;text:string;video_title:string};
type ArtifactResult={id:string;artifact_id:string;youtube_video_id:string;video_title:string;title:string;artifact_type:string;section:string;snippet:string;start_ms:number;end_ms:number;source_segment_index:number;generated_at:string};
type SearchResponse={query:string;subtitle_results:SubtitleResult[];artifact_results:ArtifactResult[]};
const examples=["彌勒","龍華三會","兜率天","發菩提心"];
function stamp(ms:number){const total=Math.max(0,Math.floor(ms/1000));const h=Math.floor(total/3600);const m=Math.floor((total%3600)/60);const s=total%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
function sectionLabel(value:string){const labels:Record<string,string>={overview:"課程總覽",detailed_notes:"詳細筆記",quick_review_10m:"10 分鐘複習",quick_review_3m:"3 分鐘複習",key_points:"重點",qa:"問答",flashcards:"複習卡",quiz:"自我測驗",glossary:"名詞整理"};return labels[value]||"AI 學習整理"}
export default function LearningSearchPage(){
 const [query,setQuery]=useState("");const [data,setData]=useState<SearchResponse|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState<string|null>(null);
 async function runSearch(raw:string){const text=raw.trim();if(text.length<2){setError("請至少輸入兩個字");return}setLoading(true);setError(null);try{const r=await fetch(`/api/v1/review/learning/search?q=${encodeURIComponent(text)}`,{cache:"no-store",credentials:"same-origin"});if(r.status===401){window.location.assign("/review");return}if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||"搜尋失敗，請稍後再試")}setData(await r.json())}catch(e){setError(e instanceof Error?e.message:"搜尋失敗，請稍後再試")}finally{setLoading(false)}}
 async function submit(event:FormEvent){event.preventDefault();await runSearch(query)}
 const total=(data?.subtitle_results.length||0)+(data?.artifact_results.length||0);
 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.topbar}><a className={styles.brand} href="/review/learn"><span className={styles.brandMark}>學</span><span className={styles.brandText}><strong>佛學共學平台</strong><span>知識搜尋</span></span></a><ReviewNav active="search" /></header>
  <section className={styles.heroMain}><p className={styles.eyebrow}>知識搜尋</p><h1>忘了在哪一堂聽過？從課程字幕與目前有效的 AI 學習整理找回來。</h1><p>搜尋結果以可追溯來源為主；點時間就回到影片原段落。AI 整理如果所依據的字幕已更新，就不會被當成目前有效知識顯示。</p><form className={styles.searchBar} onSubmit={submit}><input aria-label="搜尋課程內容" autoComplete="off" placeholder="例如：龍華三會、兜率天、發菩提心…" value={query} onChange={e=>setQuery(e.target.value)}/><button disabled={loading} type="submit">{loading?"搜尋中…":"搜尋"}</button></form><div className={styles.toolbar} style={{marginTop:12,marginBottom:0}} aria-label="搜尋範例">{examples.map(example=><button disabled={loading} key={example} onClick={()=>{setQuery(example);void runSearch(example)}} type="button">{example}</button>)}</div>{error?<p className={styles.error} role="alert">{error}</p>:null}</section>
  {data?<><div className={styles.sectionHeader}><div><h2>「{data.query}」的結果</h2><p>{total?`找到 ${total} 筆可追溯內容。點時間即可回到影片核對。`:"目前沒有找到相符內容，可以換一個名詞或較短的片語再試。"}</p></div></div>
   {total?<div className={styles.twoColumn}>
    <section className={styles.panel}><h2>課程字幕</h2><p className={styles.muted}>來自目前平台上的課程字幕；若內容仍在共修修正中，請以影片原音與管理員最終核定版本為準。</p>{data.subtitle_results.length?data.subtitle_results.map(item=><article className={styles.result} key={`${item.youtube_video_id}-${item.segment_id}`}><h3>{item.video_title}</h3><p>{item.text}</p><a aria-label={`${item.video_title} ${stamp(item.start_ms)} 回到影片`} href={`/review/learn/${item.youtube_video_id}?t=${Math.floor(item.start_ms/1000)}`}>▶ {stamp(item.start_ms)} 回到影片</a></article>):<div className={styles.empty}>課程字幕裡沒有找到相符內容。</div>}</section>
    <section className={styles.panel}><h2>AI 學習整理</h2><p className={styles.muted}>只顯示目前有效、可追溯到正式字幕來源的 AI 學習內容；時間點取自實際命中的那一則內容。</p>{data.artifact_results.length?data.artifact_results.map(item=><article className={styles.result} key={item.id}><div className={styles.metaRow}><span className={styles.badge}>{sectionLabel(item.section)}</span><span className={styles.badge}>字幕第 {item.source_segment_index} 段</span></div><h3>{item.video_title}</h3><p>{item.snippet}</p><a aria-label={`${item.video_title} ${stamp(item.start_ms)} 查看 AI 內容來源`} href={`/review/learn/${item.youtube_video_id}?t=${Math.floor(item.start_ms/1000)}&tab=notes`}>▶ {stamp(item.start_ms)} 查看來源</a></article>):<div className={styles.empty}>目前有效的 AI 學習整理裡沒有找到相符內容。</div>}</section>
   </div>:<section className={styles.panel}><div className={styles.empty}>沒有找到「{data.query}」。可以試試經名、人物、專有名詞，或你記得的兩三個關鍵字。</div></section>}
  </>:<div className={styles.sectionHeader}><div><h2>可以怎麼找</h2><p>輸入經名、人物、名詞、修行概念或你記得的一小段話；這裡先幫你找到「在哪裡講過」，不是讓 AI 憑記憶自行回答。</p></div></div>}
 </div></main>
}
