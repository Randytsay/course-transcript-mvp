import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import SubtitleEditor from "../subtitle-editor";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/subtitles/test-job-1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const mockSubtitleDetail = {
  id: "test-job-1",
  name: "測試課程.mp3",
  kind: "job",
  status: "awaiting_review",
  revision: 2,
  segment_count: 2,
  suspected_count: 0,
  edited_count: 1,
  can_publish_to_source: true,
  segments: [
    {
      segment_id: "seg-1",
      start_ms: 0,
      end_ms: 1000,
      raw_text: "測試",
      ai_text: "測試",
      current_text: "測試修正",
      manually_edited: true,
      suspected: false,
      suspected_reasons: [],
      uncertain_terms: [],
    },
  ],
};

describe("SubtitleEditor Component Tests (Revision-Aware & Fail-Closed)", () => {
  const originalFetch = global.fetch;
  const originalAlert = window.alert;
  const originalConfirm = window.confirm;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    window.alert = vi.fn();
    window.confirm = vi.fn().mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    global.fetch = originalFetch;
    window.alert = originalAlert;
    window.confirm = originalConfirm;
    vi.restoreAllMocks();
  });

  // 1. idle → button enabled
  it("1. idle status enables publish button", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({
            status: "idle",
            current_revision: 2,
            published_revision: null,
            can_publish: true,
            can_retry: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const button = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    expect(button).not.toBeDisabled();
  });

  // 2. completed current revision → button hidden
  it("2. completed status for current revision hides publish button and shows success badge", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({
            status: "completed",
            published_revision: 2,
            current_revision: 2,
            can_publish: false,
            can_retry: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(screen.queryByRole("button", { name: /安全回寫 SRT＋TXT/i })).toBeNull();
    expect(screen.getByText(/字幕已成功發布至 Google Drive \(版本 2\)/i)).toBeInTheDocument();
  });

  // 3. completed older revision → button enabled for current revision
  it("3. completed status for older revision enables publish button for new revision", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({
            status: "completed",
            published_revision: 1,
            current_revision: 2,
            revision_changed_during_publish: true,
            can_publish: true,
            can_retry: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const button = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    expect(button).not.toBeDisabled();
  });

  // 4. failed → button disabled (since can_retry=false in this phase)
  it("4. failed status disables publish button when can_retry=false and can_publish=false", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({
            status: "failed",
            can_publish: false,
            can_retry: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const button = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    expect(button).toBeDisabled();
  });

  // 5. ambiguous → button disabled
  it("5. ambiguous status disables publish button", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({
            status: "ambiguous",
            can_publish: false,
            can_retry: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const button = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    expect(button).toBeDisabled();
  });

  // 6. HTTP 500 / network error → GET reconciliation, no duplicate POST
  it("6. HTTP 500 error reconciles via GET polling without re-sending POST", async () => {
    let pollCount = 0;
    let postCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        pollCount++;
        if (pollCount === 1) return { ok: true, json: async () => ({ status: "idle", can_publish: true }) };
        if (pollCount === 2) return { ok: true, json: async () => ({ status: "publishing", can_publish: false }) };
        return {
          ok: true,
          json: async () => ({
            status: "completed",
            published_revision: 2,
            current_revision: 2,
            can_publish: false,
          }),
        };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        postCount++;
        return { ok: false, status: 500, json: async () => ({ detail: "Proxy Timeout" }) };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const publishBtn = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    await act(async () => {
      fireEvent.click(publishBtn);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(postCount).toBe(1);
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining("字幕已成功發布至 Google Drive"));
  });

  // 7. rapid double click → POST exactly once
  it("7. rapid double clicking button calls POST exactly once", async () => {
    let postCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "idle", can_publish: true }) };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        postCount++;
        return new Promise((resolve) => {
          setTimeout(() => {
            resolve({ ok: true, json: async () => ({ backup_count: 1 }) });
          }, 1000);
        });
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const publishBtn = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });

    act(() => {
      fireEvent.click(publishBtn);
      fireEvent.click(publishBtn);
    });

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(postCount).toBe(1);
  });

  // 8. refresh while publishing → GET polling only
  it("8. refresh page while publishing status performs GET polling only and 0 POST calls", async () => {
    let postCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "publishing", can_publish: false }) };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        postCount++;
        return { ok: true, json: async () => ({}) };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(postCount).toBe(0);
    expect(screen.getByRole("button", { name: /正在發布…/i })).toBeDisabled();
  });

  // 9. unmount → AbortController abort + timer cleanup
  it("9. component unmount cleans up timers without memory leaks", async () => {
    let pollCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        pollCount++;
        return { ok: true, json: async () => ({ status: "publishing", can_publish: false }) };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return { ok: true, json: async () => mockSubtitleDetail };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    const { unmount } = render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const countBeforeUnmount = pollCount;
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(pollCount).toBe(countBeforeUnmount);
  });
});
