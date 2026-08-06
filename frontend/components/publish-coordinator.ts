export const PUBLISH_POLL_INTERVAL_MS = 5_000;
export const PUBLISH_POLL_MAX_MS = 10 * 60 * 1_000;

export type PublishStatus = {
  status: "idle" | "publishing" | "completed" | "failed" | "ambiguous";
  job_status: string | null;
  batch_status: string | null;
  current_revision: number;
  published_revision: number | null;
  revision_changed_during_publish: boolean;
  zero_edit_review: boolean;
  drive_publish_status: string | null;
  editor_publish_event_count: number;
  total_editor_publish_event_count: number;
  can_publish: boolean;
  can_retry: boolean;
  message: string;
};

export type NoticeKind = "neutral" | "success" | "warning" | "error";

export type PublishSnapshot = {
  status: PublishStatus | null;
  busy: boolean;
  attemptLocked: boolean;
  notice: string;
  noticeKind: NoticeKind;
  showButton: boolean;
  canSubmit: boolean;
  buttonLabel: string;
  completed: boolean;
};

type PublishResult = {
  status: string;
  published_revision: number;
  current_revision: number;
  zero_edit_review: boolean;
  backup_count: number;
};

type TimerHandle = ReturnType<typeof setTimeout>;

export type PublishCoordinatorOptions = {
  subtitleId: string;
  revision: number;
  canPublishToSource: boolean;
  apiBase?: string;
  fetchImpl?: typeof fetch;
  confirm?: (message: string) => boolean;
  now?: () => number;
  setTimer?: (callback: () => void, delay: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
  pollIntervalMs?: number;
  pollMaxMs?: number;
  onChange?: (snapshot: PublishSnapshot) => void;
  onPublished?: () => void | Promise<void>;
};

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

export function apiErrorMessage(payload: unknown, status: number): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return `API 回應 ${status}`;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const record = item as { msg?: unknown; loc?: unknown };
        const message = typeof record.msg === "string" ? record.msg : null;
        if (!message) return null;
        const location = Array.isArray(record.loc)
          ? record.loc.map(String).filter((part) => part !== "body").join(".")
          : "";
        return location ? `${location}：${message}` : message;
      })
      .filter((message): message is string => Boolean(message));
    if (messages.length > 0) return messages.join("；");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return `API 回應 ${status}`;
}

export function isAmbiguousTransportFailure(error: unknown): boolean {
  if (error instanceof ApiRequestError) {
    return [500, 502, 503, 504].includes(error.status);
  }
  if (error instanceof TypeError) return true;
  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    return [
      "econnreset",
      "socket hang up",
      "network",
      "timeout",
      "timed out",
      "failed to fetch",
    ].some((marker) => message.includes(marker));
  }
  return false;
}

function failClosedStatus(revision: number, message: string): PublishStatus {
  return {
    status: "ambiguous",
    job_status: null,
    batch_status: null,
    current_revision: revision,
    published_revision: null,
    revision_changed_during_publish: false,
    zero_edit_review: false,
    drive_publish_status: null,
    editor_publish_event_count: 0,
    total_editor_publish_event_count: 0,
    can_publish: false,
    can_retry: false,
    message,
  };
}

export class PublishCoordinator {
  readonly options: Required<
    Pick<
      PublishCoordinatorOptions,
      | "subtitleId"
      | "revision"
      | "canPublishToSource"
      | "apiBase"
      | "fetchImpl"
      | "confirm"
      | "now"
      | "setTimer"
      | "clearTimer"
      | "pollIntervalMs"
      | "pollMaxMs"
    >
  > & Pick<PublishCoordinatorOptions, "onChange" | "onPublished">;

  private status: PublishStatus | null = null;
  private busy = true;
  private attemptLocked = false;
  private notice = "正在檢查發布狀態…";
  private noticeKind: NoticeKind = "neutral";
  private requestStarted = false;
  private polling = false;
  private pollStartedAt = 0;
  private timer: TimerHandle | null = null;
  private controller: AbortController | null = null;
  private disposed = false;
  private completedNotified = false;

  constructor(options: PublishCoordinatorOptions) {
    this.options = {
      ...options,
      apiBase: (options.apiBase ?? "/api/v1").replace(/\/$/, ""),
      fetchImpl: options.fetchImpl ?? fetch,
      confirm: options.confirm ?? ((message) => window.confirm(message)),
      now: options.now ?? Date.now,
      setTimer: options.setTimer ?? setTimeout,
      clearTimer: options.clearTimer ?? clearTimeout,
      pollIntervalMs: options.pollIntervalMs ?? PUBLISH_POLL_INTERVAL_MS,
      pollMaxMs: options.pollMaxMs ?? PUBLISH_POLL_MAX_MS,
    };
  }

  getSnapshot(): PublishSnapshot {
    const completed = Boolean(
      this.status?.status === "completed"
      && this.status.published_revision === this.status.current_revision
    );
    const allowedByStatus = Boolean(
      this.status
      && (
        (this.status.status === "idle" && this.status.can_publish)
        || (this.status.status === "failed" && this.status.can_retry)
      )
    );
    const canSubmit = Boolean(
      this.options.canPublishToSource
      && allowedByStatus
      && !this.busy
      && !this.attemptLocked
      && !this.requestStarted
    );
    const showButton = this.options.canPublishToSource && !completed;
    const buttonLabel = this.busy
      ? (this.status === null ? "檢查發布狀態…" : "正在發布…")
      : this.status?.status === "failed" && this.status.can_retry
        ? "重新嘗試安全回寫"
        : this.options.revision === 0
          ? "確認無需修改並回寫"
          : "安全回寫 SRT＋TXT";

    return {
      status: this.status,
      busy: this.busy,
      attemptLocked: this.attemptLocked,
      notice: this.notice,
      noticeKind: this.noticeKind,
      showButton,
      canSubmit,
      buttonLabel,
      completed,
    };
  }

  private emit(): void {
    if (!this.disposed) this.options.onChange?.(this.getSnapshot());
  }

  private path(suffix: "publish" | "publish-status"): string {
    return `${this.options.apiBase}/subtitles/${
      encodeURIComponent(this.options.subtitleId)
    }/${suffix}`;
  }

  private async requestJson<T>(
    suffix: "publish" | "publish-status",
    init: RequestInit,
  ): Promise<T> {
    const controller = this.controller;
    if (!controller || controller.signal.aborted) {
      throw new DOMException("Request aborted", "AbortError");
    }
    const response = await this.options.fetchImpl(this.path(suffix), {
      cache: "no-store",
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const payload = await response.json().catch(() => null) as unknown;
    if (!response.ok) {
      throw new ApiRequestError(
        response.status,
        apiErrorMessage(payload, response.status),
      );
    }
    return payload as T;
  }

  private clearScheduledPoll(): void {
    if (this.timer !== null) {
      this.options.clearTimer(this.timer);
      this.timer = null;
    }
  }

  private async markCompleted(result: PublishStatus): Promise<void> {
    this.polling = false;
    this.clearScheduledPoll();
    this.status = result;
    this.busy = false;
    this.attemptLocked = true;
    this.notice = "字幕已成功發布至 Google Drive。";
    this.noticeKind = "success";
    this.emit();
    if (!this.completedNotified && this.options.onPublished) {
      this.completedNotified = true;
      await this.options.onPublished();
    }
  }

  private async applyObservedStatus(
    result: PublishStatus,
    duringReconciliation: boolean,
  ): Promise<void> {
    if (this.disposed) return;
    this.status = result;

    if (
      result.status === "completed"
      && result.published_revision === result.current_revision
    ) {
      await this.markCompleted(result);
      return;
    }

    if (result.status === "publishing") {
      this.busy = true;
      this.notice = "正在發布至 Google Drive，請勿重複操作。";
      this.noticeKind = "neutral";
      this.emit();
      return;
    }

    if (result.status === "failed") {
      this.polling = false;
      this.clearScheduledPoll();
      this.busy = false;
      this.notice = result.message || "發布已明確失敗，目前不能安全重試。";
      this.noticeKind = "error";
      this.emit();
      return;
    }

    if (result.status === "ambiguous") {
      this.busy = duringReconciliation;
      this.notice = duringReconciliation
        ? "發布狀態仍在查證，請勿重複送出。"
        : result.message || "發布狀態無法確認，請勿重複送出。";
      this.noticeKind = "warning";
      this.emit();
      return;
    }

    // An idle response during reconciliation can be a short race before the
    // backend persists its publication intent. Keep the POST guard locked.
    this.busy = duringReconciliation;
    this.notice = duringReconciliation
      ? "已送出發布要求，正在確認背景處理狀態，請勿重複操作。"
      : result.message || "可發布目前版本。";
    this.noticeKind = "neutral";
    this.emit();
  }

  private stopAsStillProcessing(): void {
    this.polling = false;
    this.clearScheduledPoll();
    this.busy = false;
    this.attemptLocked = true;
    this.notice = "發布仍可能在背景處理，請稍後重新開啟此頁確認，請勿再次送出。";
    this.noticeKind = "warning";
    this.emit();
  }

  private schedulePoll(): void {
    this.clearScheduledPoll();
    this.timer = this.options.setTimer(() => {
      void this.pollOnce();
    }, this.options.pollIntervalMs);
  }

  private async pollOnce(): Promise<void> {
    if (
      this.disposed
      || !this.controller
      || this.controller.signal.aborted
      || !this.polling
    ) return;

    if (this.options.now() - this.pollStartedAt >= this.options.pollMaxMs) {
      this.stopAsStillProcessing();
      return;
    }

    try {
      const result = await this.requestJson<PublishStatus>(
        "publish-status",
        { method: "GET" },
      );
      if (this.disposed) return;
      await this.applyObservedStatus(result, true);
      if (
        this.polling
        && result.status !== "completed"
        && result.status !== "failed"
      ) {
        this.schedulePoll();
      }
    } catch {
      if (
        this.disposed
        || !this.controller
        || this.controller.signal.aborted
      ) return;
      this.busy = true;
      this.notice = "暫時無法取得發布狀態，仍在背景查證，請勿重複操作。";
      this.noticeKind = "warning";
      this.emit();
      if (this.polling) this.schedulePoll();
    }
  }

  private async beginPolling(): Promise<void> {
    if (this.polling || this.disposed) return;
    this.polling = true;
    this.pollStartedAt = this.options.now();
    this.busy = true;
    this.emit();
    await this.pollOnce();
  }

  async start(): Promise<void> {
    if (this.disposed) return;
    this.controller?.abort();
    this.clearScheduledPoll();
    this.controller = new AbortController();
    this.requestStarted = false;
    this.attemptLocked = false;
    this.polling = false;
    this.pollStartedAt = 0;
    this.completedNotified = false;
    this.status = null;
    this.busy = this.options.canPublishToSource;
    this.notice = this.options.canPublishToSource
      ? "正在檢查發布狀態…"
      : "";
    this.noticeKind = "neutral";
    this.emit();

    if (!this.options.canPublishToSource) return;

    try {
      const result = await this.requestJson<PublishStatus>(
        "publish-status",
        { method: "GET" },
      );
      if (this.disposed) return;
      await this.applyObservedStatus(result, false);
      if (result.status === "publishing") await this.beginPolling();
    } catch (cause) {
      if (
        this.disposed
        || !this.controller
        || this.controller.signal.aborted
      ) return;
      const message = cause instanceof Error
        ? cause.message
        : "無法確認發布狀態";
      this.status = failClosedStatus(this.options.revision, message);
      this.busy = false;
      this.notice = "無法確認發布狀態，為避免重複寫入，暫不允許發布。";
      this.noticeKind = "warning";
      this.emit();
    }
  }

  async publish(): Promise<void> {
    if (
      this.disposed
      || !this.status
      || !this.controller
      || this.controller.signal.aborted
      || this.requestStarted
      || this.attemptLocked
      || this.busy
    ) return;

    const allowed = (
      (this.status.status === "idle" && this.status.can_publish)
      || (this.status.status === "failed" && this.status.can_retry)
    );
    if (!allowed) return;

    if (
      this.options.revision === 0
      && !this.options.confirm(
        "已確認目前字幕內容無需修改，並要將 SRT 與 TXT 發布回原始 Drive 資料夾嗎？",
      )
    ) return;

    // Set before the first await so rapid clicks cannot create a second POST.
    this.requestStarted = true;
    this.attemptLocked = true;
    this.busy = true;
    this.notice = "正在發布至 Google Drive，請勿重複操作。";
    this.noticeKind = "neutral";
    this.emit();

    try {
      const result = await this.requestJson<PublishResult>(
        "publish",
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: this.options.revision,
            output_formats: ["srt", "txt"],
          }),
        },
      );
      if (this.disposed) return;
      await this.markCompleted({
        status: "completed",
        job_status: "completed",
        batch_status: "completed",
        current_revision: result.current_revision,
        published_revision: result.published_revision,
        revision_changed_during_publish: (
          result.current_revision !== result.published_revision
        ),
        zero_edit_review: result.zero_edit_review,
        drive_publish_status: result.status,
        editor_publish_event_count: 1,
        total_editor_publish_event_count: 1,
        can_publish: false,
        can_retry: false,
        message: "字幕已成功發布至 Google Drive。",
      });
    } catch (cause) {
      if (
        this.disposed
        || !this.controller
        || this.controller.signal.aborted
      ) return;
      if (isAmbiguousTransportFailure(cause)) {
        this.notice = "連線已中斷，但後端可能仍在發布；正在查證狀態。";
        this.noticeKind = "warning";
        this.emit();
        await this.beginPolling();
        return;
      }
      this.busy = false;
      this.notice = cause instanceof Error
        ? cause.message
        : "Drive 回寫失敗";
      this.noticeKind = "error";
      this.emit();
      // Keep the attempt locked. A fresh page lifecycle and status read are
      // required before any explicitly authorized retry.
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.polling = false;
    this.clearScheduledPoll();
    this.controller?.abort();
  }
}
