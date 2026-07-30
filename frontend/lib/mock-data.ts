import type { ReviewTerm, TranscriptJob, TranscriptSegment } from "./types";

export const jobs: TranscriptJob[] = [
  {
    id: "voice-11386603-seg1",
    filename: "voice_11386603-seg1.mp3",
    sourcePath: "gdrive:01 美安/01 態度與知識/01 GMTSS課程/08 產品課/20251207 楊筑雅-女性保健/voice_11386603-seg1.mp3",
    course: "女性保健產品課",
    duration: "55:49",
    durationSeconds: 3349,
    progress: 78,
    status: "review",
    createdAt: "2026/07/30 21:14",
    updatedAt: "2026/07/31 00:48",
    language: "繁體中文（台灣）",
    model: "Chirp 3 + Gemini 3.6 Flash",
    words: 12184,
    reviewTerms: 15,
    pipeline: [
      { id: "drive", label: "取得檔案", detail: "Drive read-back verified", status: "completed" },
      { id: "audio", label: "音訊正規化", detail: "16 kHz mono FLAC", status: "completed" },
      { id: "chirp", label: "Chirp 分段", detail: "4 / 4 chunks completed", status: "completed" },
      { id: "merge", label: "時間軸合併", detail: "0 regressions", status: "completed" },
      { id: "gemini", label: "Gemini 校正", detail: "197 segments corrected", status: "completed" },
      { id: "qa", label: "人工審查", detail: "15 terms need review", status: "warning" }
    ]
  },
  {
    id: "gmtss-20260724",
    filename: "語音 260724_162531.m4a",
    sourcePath: "gdrive:測試樣本/語音 260724_162531.m4a",
    course: "5 分鐘辨識樣本",
    duration: "05:00",
    durationSeconds: 300,
    progress: 100,
    status: "completed",
    createdAt: "2026/07/30 16:31",
    updatedAt: "2026/07/30 18:02",
    language: "繁體中文（台灣）",
    model: "Chirp 3 + Gemini 3.6 Flash",
    words: 1210,
    reviewTerms: 0,
    pipeline: []
  },
  {
    id: "product-training-0719",
    filename: "產品教育訓練_0719.mp3",
    sourcePath: "gdrive:01 美安/課程/產品教育訓練_0719.mp3",
    course: "產品教育訓練",
    duration: "42:18",
    durationSeconds: 2538,
    progress: 62,
    status: "correcting",
    createdAt: "2026/07/30 22:44",
    updatedAt: "2026/07/31 01:03",
    language: "繁體中文（台灣）",
    model: "Chirp 3 + Gemini 3.6 Flash",
    words: 8450,
    reviewTerms: 0,
    pipeline: []
  },
  {
    id: "leadership-0718",
    filename: "領導力訓練_0718.m4a",
    sourcePath: "gdrive:01 美安/課程/領導力訓練_0718.m4a",
    course: "領導力訓練",
    duration: "01:12:06",
    durationSeconds: 4326,
    progress: 18,
    status: "transcribing",
    createdAt: "2026/07/31 00:12",
    updatedAt: "2026/07/31 01:18",
    language: "繁體中文（台灣）",
    model: "Chirp 3 + Gemini 3.6 Flash",
    words: 0,
    reviewTerms: 0,
    pipeline: []
  }
];

export const transcriptSegments: TranscriptSegment[] = [
  { id: "126", startMs: 748120, endMs: 753480, rawText: "女性在不同的生命階段需要的營養支持不太一樣", correctedText: "女性在不同的生命階段，需要的營養支持不太一樣。", revision: 0 },
  { id: "127", startMs: 753480, endMs: 758920, rawText: "特別是進入更年期之前就要開始注意日常保養", correctedText: "特別是在進入更年期之前，就要開始注意日常保養。", revision: 0 },
  { id: "128", startMs: 758920, endMs: 764300, rawText: "接下來介紹的是美安的 OPC 三還有相關的抗氧化營養", correctedText: "接下來介紹的是美安的 OPC-3，以及相關的抗氧化營養。", uncertainTerms: ["OPC-3"], revision: 0 },
  { id: "129", startMs: 764300, endMs: 769840, rawText: "這不是單一成分而是讓整體配方可以互相協同", correctedText: "這不是單一成分，而是讓整體配方可以互相協同。", revision: 0 },
  { id: "130", startMs: 769840, endMs: 775240, rawText: "在實際分享的時候要先了解對方目前最在意的問題", correctedText: "實際分享時，要先了解對方目前最在意的問題。", revision: 0 }
];

export const reviewTerms: ReviewTerm[] = [
  { id: "term-1", heard: "OPC 三", suggestion: "OPC-3", timestamp: "12:38", confidence: "medium", status: "pending" },
  { id: "term-2", heard: "美安活酵母", suggestion: "美安活酵母／產品正式名稱待確認", timestamp: "19:42", confidence: "low", status: "pending" },
  { id: "term-3", heard: "楊竹雅", suggestion: "楊筑雅", timestamp: "00:18", confidence: "medium", status: "pending" },
  { id: "term-4", heard: "植化素", suggestion: "植化素", timestamp: "31:06", confidence: "medium", status: "confirmed" }
];

export function getJob(id: string) {
  return jobs.find((job) => job.id === id) ?? jobs[0];
}
