"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "../learning.module.css";
import ReviewAdminLink from "../../review-admin-link";

type Segment = {
  id:number; segment_index:number; start_ms:number; end_ms:number; working_text:string;
  my_suggestion_id:string|null; my_suggested_text:string|null; my_suggestion_status:"pending"|"approved"|"rejected"|"withdrawn"|null; my_suggestion_withdrawn:number;
};
type Citation = { segment_index:number; start_ms:number; end_ms:number; text:string };
type StudyItem = Record<string, unknown> & { source_segment_indexes?: number[] };
type Artifact = {
  id:string; title:string; source_sha256:string; subtitle_version_id:string; prompt_version:string;
  model:string; generated_at:string; is_stale:boolean; latest_subtitle_version:number | null;
  citations:Citation[];
  content:{
    overview?: StudyItem;
    detailed_notes?: StudyItem[];
    quick_review_10m?: StudyItem[];
    quick_review_3m?: StudyItem[];
    key_points?: StudyItem[];
    qa?: StudyItem[];
    flashcards?: StudyItem[];
    quiz?: StudyItem[];
    glossary?: StudyItem[];
  };
};
type Lesson = {
  video:{youtube_video_id:string;title:string;duration_ms:number|null};
  progress:{last_playback_ms:number;reviewed_until_ms:number}|null;
  learning_state:{learning_status:"not_started"|"in_progress"|"completed";saved:number};
  segments:Segment[]; artifact:Artifact|null;
  notes:Array<{id:string;title:string|null;body:string;start_ms:number|null;updated_at:string}>;
  bookmarks:Array<{id:string;label:string|null;note:string|null;start_ms:number}>;
  review_schedule:{next_due_at:string|null;stage:number}|null;
  quiz_attempts:Array<{id:string;score:number;total:number;created_at:string}>;
};
type Me = {csrf_token:string; user:{display_name:string; role:"owner"|"reviewer"}};
type Player = { getCurrentTime:()=>number; getPlayerState?:()=>number; seekTo:(seconds:number,allow:boolean)=>void; pauseVideo?:()=>void; destroy:()=>void };
type EditLease = {lease_token:string; expires_at:string; heartbeat_after_seconds:number; max_editors:number};
type TabKey = "transcript"|"notes"|"review"|"qa"|"cards"|"quiz"|"personal";

const tabOptions:Array<[TabKey,string]> = [
  ["transcript","影片＋字幕"],
  ["notes","AI 筆記"],
  ["review","快速複習"],
  ["qa","問答"],
  ["cards","Flashcards"],
  ["quiz","自我測驗"],
  ["personal","我的筆記"],
];
let ytPromise:Promise<void>|null=null;
function loadYT(){if(typeof window==="undefined")return Promise.resolve();if(window.YT?.Player)return Promise.resolve();if(ytPromise)return ytPromise;ytPromise=new Promise<void>((resolve)=>{const prior=window.onYouTubeIframeAPIReady;window.onYouTubeIframeAPIReady=()=>{prior?.();resolve()};if(!document.getElementById("learning-youtube-api")){const s=document.createElement("script");s.id="learning-youtube-api";s.src="https://www.youtube.com/iframe_api";s.async=true;document.head.appendChild(s)}});return ytPromise}
function stamp(ms:number){const total=Math.max(0,Math.floor(ms/1000));const h=Math.floor(total/3600);const m=Math.floor((total%3600)/60);const s=total%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
function asText(value:unknown){return typeof value==="string"?value:""}
function asStrings(value:unknown){return Array.isArray(value)?value.map(String).filter(Boolean):[]}
function isTabKey(value:string|null): value is TabKey {return Boolean(value&&tabOptions.some(([key])=>key===value))}

export default function LearningLessonPage(){
  const params=useParams<{videoId:string}>();const router=useRouter();const videoId=params.videoId;
  const hostRef=useRef<HTMLDivElement|null>(null);const playerRef=useRef<Player|null>(null);
  const segmentRefs=useRef(new Map<number,HTMLElement>());const transcriptListRef=useRef<HTMLDivElement|null>(null);const autoScrollRef=useRef(false);
  const [me,setMe]=useState<Me|null>(null);const [lesson,setLesson]=useState<Lesson|null>(null);const [currentMs,setCurrentMs]=useState(0);
  const [tab,setTab]=useState<TabKey>("transcript");const [message,setMessage]=useState<string|null>(null);const [noteBody,setNoteBody]=useState("");const [noteTitle,setNoteTitle]=useState("");
  const [revealedCard,setRevealedCard]=useState<string|null>(null);const [answers,setAnswers]=useState<Record<string,number>>({});const [quizResult,setQuizResult]=useState<string|null>(null);
  const [courseInfoOpen,setCourseInfoOpen]=useState(false);const [followPlayback,setFollowPlayback]=useState(true);const [isPlaying,setIsPlaying]=useState(false);
  const [selectedSegmentId,setSelectedSegmentId]=useState<number|null>(null);const [editingSegmentId,setEditingSegmentId]=useState<number|null>(null);const [editDraft,setEditDraft]=useState("");const [editLease,setEditLease]=useState<EditLease|null>(null);const [editBusy,setEditBusy]=useState(false);
  const headers=useCallback(()=>({"Content-Type":"application/json",...(me?.csrf_token?{"X-Review-CSRF":me.csrf_token}:{})}),[me?.csrf_token]);
  const reload=useCallback(async()=>{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}`,{cache:"no-store",credentials:"same-origin"});if(r.status===401){router.replace("/review");return null}if(!r.ok)throw new Error("目前無法讀取這堂課");const body:Lesson=await r.json();setLesson(body);return body},[router,videoId]);
  const mutationError=useCallback(async(r:Response,fallback:string)=>{if(r.ok)return null;const body=await r.json().catch(()=>({}));return typeof body.detail==="string"&&body.detail.trim()?body.detail:fallback},[]);
  const activeSegment=useMemo(()=>lesson?.segments.find(s=>currentMs>=s.start_ms&&currentMs<s.end_ms)||null,[currentMs,lesson?.segments]);

  useEffect(()=>{let cancel=false;async function boot(){try{const mr=await fetch("/api/v1/review/auth/me",{cache:"no-store",credentials:"same-origin"});if(mr.status===401){router.replace("/review");return}if(!mr.ok)throw new Error("目前無法確認登入狀態");const m:Me=await mr.json();if(cancel)return;setMe(m);const body=await reload();if(!body||cancel)return;let start=body.progress?.last_playback_ms||0;const query=new URLSearchParams(window.location.search);const explicit=Number(query.get("t")||"");if(Number.isFinite(explicit)&&explicit>=0&&query.has("t"))start=explicit*1000;setCurrentMs(start);const requested=query.get("tab");if(isTabKey(requested))setTab(requested)}catch(e){if(!cancel)setMessage(e instanceof Error?e.message:"載入失敗")}}void boot();return()=>{cancel=true}},[reload,router]);

  useEffect(()=>{if(!lesson||!hostRef.current)return;let disposed=false;const youtubeVideoId=lesson.video.youtube_video_id;void loadYT().then(()=>{if(disposed||!hostRef.current||!window.YT?.Player)return;playerRef.current?.destroy();const node=document.createElement("div");hostRef.current.replaceChildren(node);playerRef.current=new window.YT.Player(node,{videoId:youtubeVideoId,playerVars:{rel:0,modestbranding:1,playsinline:1},events:{onReady:({target})=>{if(currentMs>0)target.seekTo(currentMs/1000,true)},onStateChange:({data})=>setIsPlaying(data===1)}})});return()=>{disposed=true;playerRef.current?.destroy();playerRef.current=null}},[lesson?.video.youtube_video_id]);
  useEffect(()=>{if(!lesson)return;const timer=window.setInterval(()=>{const p=playerRef.current;if(!p)return;setCurrentMs(Math.max(0,Math.floor(p.getCurrentTime()*1000)))},1000);return()=>window.clearInterval(timer)},[lesson]);
  useEffect(()=>{if(!lesson||!me)return;const timer=window.setInterval(()=>{const p=playerRef.current;if(!p)return;const value=Math.max(0,Math.floor(p.getCurrentTime()*1000));void fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/watch`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({last_playback_ms:value})})},10000);return()=>window.clearInterval(timer)},[headers,lesson,me,videoId]);
  const scrollActiveSegmentToTop=useCallback((behavior:ScrollBehavior="smooth")=>{
    const list=transcriptListRef.current;
    const node=activeSegment?segmentRefs.current.get(activeSegment.id):null;
    if(!list||!node)return false;
    const listRect=list.getBoundingClientRect();
    const nodeRect=node.getBoundingClientRect();
    const targetTop=Math.max(0,list.scrollTop+nodeRect.top-listRect.top-4);
    autoScrollRef.current=true;
    list.scrollTo({top:targetTop,behavior});
    window.setTimeout(()=>{autoScrollRef.current=false},behavior==="smooth"?800:0);
    return true;
  },[activeSegment?.id]);
  useEffect(()=>{if(!activeSegment||tab!=="transcript"||!followPlayback)return;scrollActiveSegmentToTop("smooth")},[activeSegment?.id,followPlayback,scrollActiveSegmentToTop,tab]);
  useEffect(()=>{if(!isPlaying||tab!=="transcript")return;const list=transcriptListRef.current;if(!list)return;const onScroll=()=>{if(!autoScrollRef.current)setFollowPlayback(false)};list.addEventListener("scroll",onScroll,{passive:true});return()=>list.removeEventListener("scroll",onScroll)},[isPlaying,tab]);
  useEffect(()=>{if(!editLease||!me)return;const timer=window.setInterval(async()=>{const response=await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease/heartbeat`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({lease_token:editLease.lease_token})});if(!response.ok){setEditLease(null);setEditingSegmentId(null);setMessage("字幕編輯權限已暫停，仍可繼續觀看；請重新按編輯再試。");return}const heartbeat=await response.json();setEditLease(current=>current?{...current,...heartbeat}:current)},Math.max(1000,editLease.heartbeat_after_seconds*1000));return()=>window.clearInterval(timer)},[editLease?.heartbeat_after_seconds,editLease?.lease_token,headers,me,videoId]);

  const citations=useMemo(()=>new Map((lesson?.artifact?.citations||[]).map(c=>[c.segment_index,c])),[lesson?.artifact?.citations]);
  const seek=(ms:number)=>{playerRef.current?.seekTo(ms/1000,true);setCurrentMs(ms);setFollowPlayback(false)};
  const sourceButtons=(item:StudyItem)=>{const indexes=Array.isArray(item.source_segment_indexes)?item.source_segment_indexes:[];return <span>{indexes.slice(0,6).map(index=>{const c=citations.get(Number(index));return c?<button aria-label={`回到影片 ${stamp(c.start_ms)} 查看來源`} className={styles.citation} key={Number(index)} onClick={()=>seek(c.start_ms)} type="button">▶ {stamp(c.start_ms)}</button>:null})}</span>};
  async function acquireEditLease(){if(editLease)return editLease;if(!me)return null;setMessage(null);const response=await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease`,{method:"POST",credentials:"same-origin",headers:headers()});const body=await response.json().catch(()=>({}));if(!response.ok){setMessage(response.status===409?`目前已有 ${body.max_editors||2} 位師兄姐正在校訂這支影片，稍後再試即可。`:body.detail||"目前暫時無法開啟字幕編輯");return null}const lease:EditLease=body;setEditLease(lease);return lease}
  function selectSegment(segment:Segment){setSelectedSegmentId(segment.id);setFollowPlayback(false);seek(segment.start_ms)}
  async function startSegmentEdit(segment:Segment){const lease=await acquireEditLease();if(!lease)return;const source=segment.my_suggestion_status==="pending"&&segment.my_suggested_text?segment.my_suggested_text:segment.working_text;setSelectedSegmentId(segment.id);setEditingSegmentId(segment.id);setEditDraft(source);setFollowPlayback(false);playerRef.current?.pauseVideo?.();setIsPlaying(false);window.requestAnimationFrame(()=>document.getElementById(`learning-editor-${segment.id}`)?.focus({preventScroll:true}))}
  function cancelSegmentEdit(){void releaseEditLease();setEditDraft("")}
  async function saveSegmentEdit(segment:Segment){if(!editLease||editBusy)return;const text=editDraft.trim();if(!text){setMessage("字幕內容不能留白");return}setEditBusy(true);setMessage(null);try{const response=await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/segments/${segment.id}/suggestion`,{method:"POST",credentials:"same-origin",headers:{...headers(),"X-Review-Lease":editLease.lease_token},body:JSON.stringify({text})});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||"字幕修改建議送出失敗");await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease/release`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({lease_token:editLease.lease_token})});setEditLease(null);setEditingSegmentId(null);setEditDraft("");setMessage(body.created?"字幕修改建議已送出，等待管理員審核。":"字幕修改建議已更新。");await reload()}catch(e){setMessage(e instanceof Error?e.message:"字幕修改建議送出失敗")}finally{setEditBusy(false)}}
  async function releaseEditLease(){if(!editLease)return;await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease/release`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({lease_token:editLease.lease_token})});setEditLease(null);setEditingSegmentId(null)}
  async function statePatch(payload:Record<string,unknown>,success?:string){try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/state`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify(payload)});const error=await mutationError(r,"更新失敗，請稍後再試");if(error)throw new Error(error);if(success)setMessage(success);await reload()}catch(e){setMessage(e instanceof Error?e.message:"更新失敗，請稍後再試")}}
  async function addBookmark(){try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/bookmarks`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({start_ms:currentMs,label:`${stamp(currentMs)} 重點`})});const error=await mutationError(r,"書籤新增失敗");if(error)throw new Error(error);setMessage("已把這個時間點加入書籤");await reload()}catch(e){setMessage(e instanceof Error?e.message:"書籤新增失敗")}}
  async function addNote(){if(!noteBody.trim()){setMessage("請先寫下筆記內容");return}try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/notes`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({title:noteTitle||null,body:noteBody,start_ms:currentMs})});const error=await mutationError(r,"筆記儲存失敗");if(error)throw new Error(error);setNoteBody("");setNoteTitle("");setMessage("筆記已儲存，並連結到目前影片時間");await reload()}catch(e){setMessage(e instanceof Error?e.message:"筆記儲存失敗")}}
  async function removeNote(id:string){if(!window.confirm("確定刪除這則自己的筆記？刪除後無法復原。"))return;try{const r=await fetch(`/api/v1/review/learning/notes/${id}`,{method:"DELETE",credentials:"same-origin",headers:headers()});const error=await mutationError(r,"筆記刪除失敗");if(error)throw new Error(error);setMessage("筆記已刪除");await reload()}catch(e){setMessage(e instanceof Error?e.message:"筆記刪除失敗")}}
  async function removeBookmark(id:string){if(!window.confirm("確定移除這個書籤？"))return;try{const r=await fetch(`/api/v1/review/learning/bookmarks/${id}`,{method:"DELETE",credentials:"same-origin",headers:headers()});const error=await mutationError(r,"書籤移除失敗");if(error)throw new Error(error);setMessage("書籤已移除");await reload()}catch(e){setMessage(e instanceof Error?e.message:"書籤移除失敗")}}
  async function rateCard(card:StudyItem,rating:"again"|"hard"|"good"|"easy"){if(!lesson?.artifact)return;try{const r=await fetch("/api/v1/review/learning/flashcards/review",{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({artifact_id:lesson.artifact.id,card_key:asText(card.id)||asText(card.front),rating})});const error=await mutationError(r,"複習紀錄儲存失敗");if(error)throw new Error(error);setRevealedCard(null);setMessage("已依熟悉程度安排這張卡片的下次複習")}catch(e){setMessage(e instanceof Error?e.message:"複習紀錄儲存失敗")}}
  async function gradeQuiz(){const quiz=lesson?.artifact?.content.quiz||[];if(!quiz.length||!lesson?.artifact)return;const unanswered=quiz.filter((q,i)=>{const id=asText(q.id)||asText(q.question)||String(i);return answers[id]===undefined}).length;if(unanswered){setMessage(`還有 ${unanswered} 題尚未作答，完成後再送出。`);return}let score=0;for(const [i,q] of quiz.entries()){const id=asText(q.id)||asText(q.question)||String(i);if(answers[id]===Number(q.answer_index))score++}try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/quiz-attempts`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({score,total:quiz.length,artifact_id:lesson.artifact.id,answers})});const error=await mutationError(r,"測驗紀錄儲存失敗");if(error)throw new Error(error);setQuizResult(`${score} / ${quiz.length}`);setMessage("測驗完成，成績已保存")}catch(e){setMessage(e instanceof Error?e.message:"測驗紀錄儲存失敗")}}
  async function finishReview(){try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/review-complete`,{method:"POST",credentials:"same-origin",headers:headers()});const error=await mutationError(r,"複習完成狀態儲存失敗");if(error)throw new Error(error);setMessage("本次複習完成，已安排下一次複習時間");await reload()}catch(e){setMessage(e instanceof Error?e.message:"複習完成狀態儲存失敗")}}

  if(message&&!lesson)return <main className={styles.page}><div className={styles.shell}><p className={styles.error} role="alert">{message}</p></div></main>;
  if(!lesson)return <main className={styles.page}><div className={styles.shell}><p className={styles.status}>正在準備這堂課…</p></div></main>;
  const artifact=lesson.artifact;const content=artifact?.content||{};const active=activeSegment;
  return <main className={`${styles.page} ${styles.playbackPage}`}><div className={styles.shell}>
    <header className={styles.topbar}><a className={styles.brand} href="/review/learn"><span className={styles.brandMark}>學</span><span className={styles.brandText}><strong>佛學共學平台</strong><span>我的學習中心</span></span></a><nav className={styles.nav} aria-label="學習功能"><a href="/review/learn">學習中心</a><a href="/review/videos">字幕共修</a><a href="/review/learn/review">複習中心</a><a href="/review/learn/search">知識搜尋</a><a href="/review/help">使用說明</a></nav></header>
    <div className={styles.workspace}>
      <section className={styles.videoPanel}>
        <div className={styles.lessonInfoBar}>
          <div className={styles.lessonHeading}><div><p className={styles.eyebrow}>正在學習</p><h1>{lesson.video.title}</h1><p className={styles.muted}>目前 {stamp(currentMs)} / {stamp(lesson.video.duration_ms||0)} {active?`・字幕第 ${active.segment_index} 段`:""}</p></div></div>
          <button aria-expanded={courseInfoOpen} className={styles.infoToggle} onClick={()=>setCourseInfoOpen(value=>!value)} type="button">{courseInfoOpen?"收合課程資訊":"課程資訊"}<span aria-hidden="true">⌄</span></button>
        </div>
        {courseInfoOpen?<div className={styles.courseInfoPanel}><p className={styles.courseInfoHint}>需要時展開操作；播放時畫面會回到影片與字幕。</p><div className={styles.actionRow}>
          <button type="button" onClick={()=>void addBookmark()}>🔖 收藏此刻</button>
          <button type="button" onClick={()=>void statePatch({saved:!Boolean(lesson.learning_state.saved)},lesson.learning_state.saved?"已取消收藏課程":"已收藏這堂課")}>{lesson.learning_state.saved?"★ 已收藏":"☆ 收藏課程"}</button>
          {lesson.learning_state.learning_status==="completed"?<button type="button" onClick={()=>void statePatch({learning_status:"in_progress"},"已改為學習中")}>改為學習中</button>:<button className={styles.accent} type="button" onClick={()=>{if(window.confirm("確定已完成這堂課的學習？之後仍可繼續觀看、複習與做筆記。"))void statePatch({learning_status:"completed"},"已標記這堂課為學完，系統會安排後續複習")}}>✓ 我已學完</button>}
          <a href={`/review/videos/${videoId}`}>協助校字幕</a><a href="/review/help">? 使用說明</a><ReviewAdminLink className={styles.courseAdminLink}/>
        </div></div>:null}
        <div aria-label="課程影片播放器" className={styles.videoFrame} ref={hostRef}/>
        {message?<p aria-live="polite" className={styles.playbackMessage} role="status">{message}</p>:null}
        <div aria-label="課程內容分頁" className={styles.tabs} role="tablist">
          {tabOptions.map(([key,label])=><button aria-controls={`learning-panel-${key}`} aria-selected={tab===key} className={tab===key?styles.active:""} id={`learning-tab-${key}`} key={key} onClick={()=>setTab(key)} role="tab" type="button">{label}</button>)}
        </div>
        <div aria-labelledby={`learning-tab-${tab}`} className={styles.tabBody} id={`learning-panel-${tab}`} role="tabpanel">
          {tab==="transcript"?<div className={styles.transcriptWorkspace}>{!followPlayback&&isPlaying?<button className={styles.returnToPlayback} onClick={()=>{setFollowPlayback(true);scrollActiveSegmentToTop("smooth")}} type="button">回到播放位置</button>:null}<div className={styles.list} ref={transcriptListRef}>{lesson.segments.map(segment=>{const selected=selectedSegmentId===segment.id;const editing=editingSegmentId===segment.id;const pending=segment.my_suggestion_status==="pending"&&!segment.my_suggestion_withdrawn&&Boolean(segment.my_suggested_text);const shownText=pending?segment.my_suggested_text:segment.working_text;return <article aria-current={active?.id===segment.id?"true":undefined} className={`${styles.listItem} ${active?.id===segment.id?styles.listItemActive:""} ${selected?styles.listItemSelected:""}`} key={segment.id} ref={element=>{if(element)segmentRefs.current.set(segment.id,element);else segmentRefs.current.delete(segment.id)}}><button aria-label={`跳到 ${stamp(segment.start_ms)}，字幕第 ${segment.segment_index} 段`} className={styles.segmentTimeButton} onClick={()=>seek(segment.start_ms)} type="button"><strong>{stamp(segment.start_ms)}</strong></button><div className={styles.segmentCopy}>{editing?<><p className={styles.originalHint}>目前字幕：{segment.working_text}</p><textarea aria-label={`編輯第 ${segment.segment_index} 段字幕`} autoFocus id={`learning-editor-${segment.id}`} onChange={event=>setEditDraft(event.target.value)} rows={3} value={editDraft}/><div className={styles.editActions}><button className={styles.saveButton} disabled={editBusy} onClick={()=>void saveSegmentEdit(segment)} type="button">{editBusy?"送出中…":"送出修改"}</button><button className={styles.cancelButton} disabled={editBusy} onClick={cancelSegmentEdit} type="button">取消</button></div></>:<><button aria-label={`選取字幕第 ${segment.segment_index} 段`} className={styles.segmentTextButton} onClick={()=>selectSegment(segment)} type="button">{shownText}</button><div className={styles.segmentMeta}><span>{pending?"待審核修改":`第 ${segment.segment_index} 段`}</span>{selected?<button aria-label={`編輯第 ${segment.segment_index} 段字幕`} className={styles.segmentEditButton} onClick={()=>void startSegmentEdit(segment)} title="編輯這段字幕" type="button">✎</button>:null}</div></>}</div></article>})}</div></div>:null}
          {tab==="notes"?<>{!artifact?<div className={styles.empty}>這堂課的 AI 學習整理尚未產生。你仍可正常看影片、做自己的筆記與書籤。</div>:<>{artifact.is_stale?<p className={styles.error}>字幕已有較新的核准版本。這份整理仍以舊版字幕為來源，可閱讀與回看來源，但請以原影片與最新正式字幕為準，等待管理員重新整理。</p>:null}<div className={styles.noteSection}><h3>{asText(content.overview?.title)||"本堂總覽"}</h3><p>{asText(content.overview?.summary)}</p>{content.overview?sourceButtons(content.overview):null}</div>{(content.detailed_notes||[]).map((item,i)=><div className={styles.noteSection} key={i}><h3>{asText(item.heading)||`重點 ${i+1}`}</h3><ul>{asStrings(item.points).map((p,j)=><li key={j}>{p}</li>)}</ul>{sourceButtons(item)}</div>)}{(content.glossary||[]).length?<div className={styles.noteSection}><h3>名詞整理</h3>{(content.glossary||[]).map((item,i)=><p key={i}><strong>{asText(item.term)}</strong>：{asText(item.explanation)} {sourceButtons(item)}</p>)}</div>:null}<p className={styles.muted}>依管理員核定的不可變字幕來源整理・{artifact.model}・每個時間標記都可回到影片核對。</p></>}</> :null}
          {tab==="review"?<>{!artifact?<div className={styles.empty}>AI 快速複習尚未產生，可以先回看影片與自己的筆記。</div>:<><div className={styles.noteSection}><h3>3 分鐘快速複習</h3>{(content.quick_review_3m||[]).map((item,i)=><p key={i}>{i+1}. {asText(item.text)} {sourceButtons(item)}</p>)}</div><div className={styles.noteSection}><h3>10 分鐘複習</h3>{(content.quick_review_10m||[]).map((item,i)=><div key={i}><strong>{asText(item.heading)}</strong><p>{asText(item.summary)}</p>{sourceButtons(item)}</div>)}</div><div className={styles.noteSection}><h3>本堂一定要記住</h3>{(content.key_points||[]).map((item,i)=><p key={i}>✓ {asText(item.text)} {sourceButtons(item)}</p>)}</div><button className={styles.primaryButton} type="button" onClick={()=>void finishReview()}>完成本次複習</button></>}</>:null}
          {tab==="qa"?<>{(content.qa||[]).length?(content.qa||[]).map((item,i)=><div className={styles.noteSection} key={i}><h3>Q：{asText(item.question)}</h3><p>A：{asText(item.answer)}</p>{sourceButtons(item)}</div>):<div className={styles.empty}>尚無本堂問答整理。</div>}</>:null}
          {tab==="cards"?<>{(content.flashcards||[]).length?(content.flashcards||[]).map((card,i)=>{const key=asText(card.id)||String(i);const open=revealedCard===key;return <div className={styles.flashcard} key={key}><strong>{asText(card.front)}</strong>{open?<><div className={styles.flashAnswer}>{asText(card.back)}<div>{sourceButtons(card)}</div></div><div aria-label="這張卡片的熟悉程度" className={styles.ratingRow}><button onClick={()=>void rateCard(card,"again")} type="button">再來一次</button><button onClick={()=>void rateCard(card,"hard")} type="button">有點難</button><button onClick={()=>void rateCard(card,"good")} type="button">記得</button><button onClick={()=>void rateCard(card,"easy")} type="button">很熟</button></div></>:<button aria-expanded={open} className={styles.primaryButton} style={{marginTop:14}} onClick={()=>setRevealedCard(key)} type="button">看答案</button>}</div>}):<div className={styles.empty}>尚無 Flashcards。</div>}</>:null}
          {tab==="quiz"?<>{(content.quiz||[]).length?(content.quiz||[]).map((q,i)=>{const id=asText(q.id)||asText(q.question)||String(i);return <div className={styles.quizQuestion} key={id}><strong>{i+1}. {asText(q.question)}</strong><div className={styles.choices}>{asStrings(q.choices).map((choice,j)=><button aria-pressed={answers[id]===j} className={answers[id]===j?styles.selected:""} key={j} onClick={()=>setAnswers(a=>({...a,[id]:j}))} type="button">{choice}</button>)}</div>{quizResult?<p className={styles.muted}>正解：{asStrings(q.choices)[Number(q.answer_index)]}。{asText(q.explanation)} {sourceButtons(q)}</p>:null}</div>}):<div className={styles.empty}>尚無自我測驗。</div>}{(content.quiz||[]).length?<><button className={styles.primaryButton} onClick={()=>void gradeQuiz()} type="button">送出答案</button>{quizResult?<p aria-live="polite"><strong>本次成績：{quizResult}</strong></p>:null}</>:null}</>:null}
          {tab==="personal"?<div><h3>新增自己的筆記</h3><input aria-label="筆記標題" className={styles.input} placeholder="標題（可留空）" value={noteTitle} onChange={e=>setNoteTitle(e.target.value)}/><textarea aria-label="筆記內容" className={styles.textarea} placeholder={`把此刻 ${stamp(currentMs)} 的理解、疑問或提醒寫下來…`} value={noteBody} onChange={e=>setNoteBody(e.target.value)}/><button className={styles.saveButton} disabled={!noteBody.trim()} onClick={()=>void addNote()} type="button">儲存筆記（連結目前時間）</button><div className={styles.miniList}>{lesson.notes.length?lesson.notes.map(n=><div className={styles.miniItem} key={n.id}><button aria-label={`刪除筆記 ${n.title||stamp(n.start_ms||0)}`} onClick={()=>void removeNote(n.id)} type="button">刪除</button><strong>{n.title||stamp(n.start_ms||0)}</strong><p>{n.body}</p>{n.start_ms!=null?<button className={styles.citation} onClick={()=>seek(n.start_ms||0)} type="button">▶ {stamp(n.start_ms||0)}</button>:null}</div>):<div className={styles.empty}>還沒有自己的筆記。可以把理解、疑問或提醒記在這裡。</div>}</div></div>:null}
        </div>
      </section>
      <aside className={styles.sidePanel}><h2>這堂課</h2><div className={styles.metaRow}><span className={styles.badge}>{lesson.learning_state.learning_status==="completed"?"已學完":lesson.learning_state.learning_status==="in_progress"?"學習中":"尚未開始"}</span>{artifact?<span className={styles.badge}>{artifact.is_stale?"AI 整理待更新":"AI 學習整理"}</span>:null}{lesson.review_schedule?.next_due_at?<span className={styles.badge}>已排複習</span>:null}</div><p className={styles.muted}>觀看進度、學習完成、複習與字幕共修分開記錄，不會因影片播完就自動算學習完成。</p><h2 style={{marginTop:22}}>我的書籤</h2><div className={styles.miniList}>{lesson.bookmarks.length?lesson.bookmarks.map(mark=><div className={styles.miniItem} key={mark.id}><button aria-label={`移除書籤 ${mark.label||stamp(mark.start_ms)}`} onClick={()=>void removeBookmark(mark.id)} type="button">刪除</button><a href="#" onClick={e=>{e.preventDefault();seek(mark.start_ms)}}>{mark.label||stamp(mark.start_ms)}</a>{mark.note?<p>{mark.note}</p>:null}</div>):<div className={styles.empty}>看到重要段落時按「收藏此刻」。</div>}</div><h2 style={{marginTop:22}}>最近測驗</h2><div className={styles.miniList}>{lesson.quiz_attempts.length?lesson.quiz_attempts.slice(0,5).map(a=><div className={styles.miniItem} key={a.id}><strong>{a.score} / {a.total}</strong><p>{new Date(a.created_at).toLocaleString("zh-TW")}</p></div>):<p className={styles.muted}>還沒有測驗紀錄。</p>}</div></aside>
    </div>
  </div></main>
}
