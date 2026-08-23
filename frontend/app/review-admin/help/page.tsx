import styles from "../learning/learning-admin.module.css";

const flows = [
  ["影片同步", "先檢查、再匯入", "先用只讀預覽確認哪些影片 ready；只勾選真正要匯入的影片。預覽不建立 reviewer DB 資料，也不寫 YouTube。"],
  ["字幕共修", "審核建議而不是直接覆蓋", "管理員查看原文、建議、上下文與影片時間；正常建議可核准／拒絕，發生 conflict 時應先重新確認。"],
  ["版本管理", "每次正式變更留下不可變版本", "核准、批次修正與還原都應留下版本紀錄。還原歷史版會建立新版本，不改寫舊版。"],
  ["YouTube 發布", "本地完成不等於已發布", "發布前先看版本、SHA、變更段數與固定時間碼；YouTube captions.update 是獨立高風險動作，不和一般核准綁在一起。"],
  ["AI 學習內容", "先核定來源，再產生", "先把最新不可變字幕版本核定為正式學習來源，再另外確認付費 AI Study Pack。學員讀取不會自動觸發模型。"],
  ["操作紀錄", "重要動作可追溯", "審核、批次、版本、發布與 AI 產生都應留下 actor、時間與結果，避免日後無法追查。"],
];

export default function AdminHelpPage() {
  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div><p className={styles.eyebrow}>Owner Guide</p><h1>管理員使用說明</h1><p>管理端的核心原則不是「越自動越好」，而是把匯入、審核、版本、AI 學習內容與 YouTube 發布拆成可確認、可追溯的階段。</p></div>
          <a href="/review-admin" style={{color:"#315c4b",fontWeight:800,textDecoration:"none"}}>回管理中心 ↗</a>
        </header>

        <div className={styles.note}>建議順序：<strong>檢查影片 → 匯入字幕 → 共修校訂 → 核准／批次修正 → 建立不可變版本 → 核定學習來源 → 產生 AI 學習整理 → 最後才視需要發布 YouTube</strong>。任何學習功能都不應自動跨過 YouTube 發布確認。</div>

        <section className={styles.summary}>
          <div><strong>1</strong><span>先讀／預覽</span></div>
          <div><strong>2</strong><span>再確認變更</span></div>
          <div><strong>3</strong><span>保留版本證據</span></div>
          <div><strong>4</strong><span>發布獨立確認</span></div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>六個管理工作區</h2><p>每一區只做一類事情，降低誤操作。</p></div></div>
          <div className={styles.grid}>
            {flows.map(([title, subtitle, body]) => <article className={styles.videoCard} key={title}>
              <h3>{title}</h3>
              <div className={styles.meta}><span className={styles.badge}>{subtitle}</span></div>
              <p style={{fontSize:13,lineHeight:1.7,color:"#68756e"}}>{body}</p>
            </article>)}
          </div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>AI 學習內容：正確操作</h2><p>這是最容易把「字幕最新版」與「正式學習來源」混在一起的地方。</p></div></div>
          <div className={styles.jobs}>
            <div className={styles.job}><div><strong>① 先完成字幕辨識／共修確認</strong><p>如果字幕完全正確、沒有任何修改，第一次核定時系統也可以把匯入原文凍結為不可變 v1。</p></div><span className={`${styles.status} ${styles.completed}`}>必要</span></div>
            <div className={styles.job}><div><strong>② 核定正式學習來源</strong><p>這個動作回答的是：「這個 exact subtitle version 可以作為正式教材來源嗎？」</p></div><span className={`${styles.status} ${styles.completed}`}>人工確認</span></div>
            <div className={styles.job}><div><strong>③ 另外確認產生 AI Study Pack</strong><p>這才會呼叫付費模型；來源版本、SHA、model、prompt version 與引用時間都會保存。</p></div><span className={`${styles.status} ${styles.running}`}>付費動作</span></div>
            <div className={styles.job}><div><strong>④ 字幕有新版時先停</strong><p>舊 AI 整理保留可追溯，但新一輪 AI 產生必須先重新核定最新字幕版本。</p></div><span className={`${styles.status} ${styles.failed}`}>Fail closed</span></div>
          </div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>發布 YouTube 前的安全檢查</h2><p>這一步和「本地字幕已核准」是兩件不同的事。</p></div></div>
          <div className={styles.jobs}>
            {[
              "確認目標影片與 caption track 正確",
              "確認準備發布的 immutable version 與 SHA",
              "確認變更段數與字數符合預期",
              "確認時間碼／segment 結構沒有改變",
              "如果是歷史版本，確認這其實是在做線上 rollback",
              "完成最後一次明確確認後才呼叫 YouTube publish",
            ].map((item,index)=><div className={styles.job} key={item}><div><strong>{index+1}. {item}</strong></div><span className={`${styles.status} ${styles.completed}`}>檢查</span></div>)}
          </div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>遇到錯誤時</h2><p>不要因畫面 timeout 就直接重送 mutation。</p></div></div>
          <div className={styles.note}>如果匯入、核准、批次修正、版本還原或發布發生 timeout / connection reset，先查資料庫或 audit 實際狀態，判斷「完全沒發生」還是「已成功但 response lost」，再決定是否重試，避免 duplicate mutation。</div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>AI 帳戶與供應商設定</h2><p>兩個管理頁面管理的是不同東西，不要混用。</p></div></div>
          <div className={styles.jobs}>
            <div className={styles.job}><div><strong>AI 帳戶管理（Vertex Account Profile）</strong>
              <p>管理 Google Cloud 服務帳戶設定檔：Service Account + GCP Project + Region + Bucket。
              切換時四項一併更新。額度跟 Project 綁定：Google AI Pro 每月提供 GenAI &amp; Cloud credit；
              新戶 Welcome Credit 通常 US$300／90 天，實際以 Google Cloud Billing Console 為準。</p></div>
              <span className={`${styles.status} ${styles.completed}`}>GCP</span></div>
            <div className={styles.job}><div><strong>AI 模型供應商（OpenRouter / MiniMax）</strong>
              <p>管理 API Key 設定檔。Key 只存伺服器保護目錄（0600），畫面只顯示已設定狀態。
              「測試連線」只做唯讀驗證，不會產生費用。</p></div>
              <span className={`${styles.status} ${styles.running}`}>API Key</span></div>
            <div className={styles.job}><div><strong>語音辨識 vs AI 文字校正的 Batch</strong>
              <p>Chirp Dynamic Batch 是語音辨識的省錢模式；Gemini / OpenRouter Batch 是 AI 文字校正的非即時批次。
              兩者完全不同、價格也不同。AI Batch 可能等待數小時才完成。</p></div>
              <span className={`${styles.status} ${styles.failed}`}>勿混淆</span></div>
            <div className={styles.job}><div><strong>執行模式與失敗備援</strong>
              <p>REALTIME 即時但標準價；BATCH 較慢較便宜（僅支援的 provider/model 可選）。
              失敗時預設保留 Chirp 原文（零額外費用），也可選擇改用其他 provider（會顯示可能的額外費用）。
              每個任務建立時就固定 provider／model／模式，事後改預設不影響進行中的任務。</p></div>
              <span className={`${styles.status} ${styles.completed}`}>Per-job</span></div>
          </div>
        </section>
      </div>
    </main>
  );
}
