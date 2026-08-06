import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import SubtitleEditor from "../subtitle-editor";

// Mock next/link & lucide-react
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
  revision: 1,
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

describe("SubtitleEditor Component Tests", () => {
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

  // 1. POST 200 → success
  it("1. POST 200 triggers direct success alert and reloads state", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return {
          ok: true,
          json: async () => ({ status: "idle", current_revision: 1, can_retry: false }),
        };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        return {
          ok: true,
          json: async () => ({ backup_count: 2 }),
        };
      }
      if (url.includes("/subtitles/test-job-1")) {
        return {
          ok: true,
          json: async () => mockSubtitleDetail,
        };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    global.fetch = fetchMock as any;

    render(<SubtitleEditor subtitleId="test-job-1" />);
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    const publishBtn = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    expect(publishBtn).not.toBeDisabled();

    await act(async () => {
      fireEvent.click(publishBtn);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/subtitles/test-job-1/publish"),
      expect.objectContaining({ method: "POST" })
    );
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining("已安全備份 2 個"));
  });

  // 2. POST 500 → GET polling → completed
  it("2. POST 500 reconciles via GET polling to completed state", async () => {
    let pollCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        pollCount++;
        if (pollCount === 1) {
          return { ok: true, json: async () => ({ status: "idle", current_revision: 1 }) };
        }
        if (pollCount === 2) {
          return { ok: true, json: async () => ({ status: "publishing" }) };
        }
        return {
          ok: true,
          json: async () => ({ status: "completed", published_revision: 1, editor_publish_event_count: 1 }),
        };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ detail: "Proxy Timeout Socket Hangup" }),
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

    const publishBtn = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    await act(async () => {
      fireEvent.click(publishBtn);
    });

    // Advance 5 seconds for polling
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining("字幕已成功發布至 Google Drive"));
    expect(screen.queryByRole("button", { name: /安全回寫 SRT＋TXT/i })).toBeNull();
    expect(screen.getByText(/字幕已成功發布至 Google Drive/i)).toBeInTheDocument();
  });

  // 3. network error → polling → completed
  it("3. network error reconciles via GET polling to completed state", async () => {
    let pollCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        pollCount++;
        if (pollCount === 1) return { ok: true, json: async () => ({ status: "idle" }) };
        if (pollCount === 2) return { ok: true, json: async () => ({ status: "publishing" }) };
        return { ok: true, json: async () => ({ status: "completed", published_revision: 1 }) };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        throw new TypeError("Failed to fetch (network disconnect)");
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

    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining("字幕已成功發布至 Google Drive"));
  });

  // 4. rapid double click → POST count=1
  it("4. rapid double clicking button triggers POST only once", async () => {
    let postCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "idle" }) };
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

    // Double click rapidly
    act(() => {
      fireEvent.click(publishBtn);
      fireEvent.click(publishBtn);
    });

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(postCount).toBe(1);
  });

  // 5. refresh with publishing → GET only, POST count=0
  it("5. refresh page with publishing status performs GET only and zero POST calls", async () => {
    let postCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "publishing" }) };
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
    expect(screen.getByText(/正在發布至 Google Drive，請勿重複操作/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /正在發布…/i })).toBeDisabled();
  });

  // 6. publishing → button disabled
  it("6. publishing status disables the publish button", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "publishing" }) };
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

    const button = screen.getByRole("button", { name: /正在發布…/i });
    expect(button).toBeDisabled();
  });

  // 7. completed → publish button hidden
  it("7. completed status hides the publish button", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "completed", published_revision: 1 }) };
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
    expect(screen.getByText(/字幕已成功發布至 Google Drive \(版本 1\)/i)).toBeInTheDocument();
  });

  // 8. failed + can_retry=false → button disabled
  it("8. failed status with can_retry=false keeps button disabled", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "failed", can_retry: false }) };
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

  // 9. failed + can_retry=true → button enabled
  it("9. failed status with can_retry=true enables button for retry", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "failed", can_retry: true }) };
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

  // 10. ambiguous → caution notice, button disabled
  it("10. ambiguous status displays caution notice and disables button without showing definitive failure", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "ambiguous", can_retry: false }) };
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
    expect(screen.queryByText(/^發布失敗/)).toBeNull();
  });

  // 11. structured FastAPI detail doesn't render [object Object]
  it("11. structured FastAPI array detail formats readable string without [object Object]", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        return { ok: true, json: async () => ({ status: "idle" }) };
      }
      if (url.includes("/subtitles/test-job-1/publish")) {
        return {
          ok: false,
          status: 422,
          json: async () => ({
            detail: [
              { loc: ["body", "expected_revision"], msg: "expected_revision must be greater than or equal to 0" },
            ],
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

    const publishBtn = screen.getByRole("button", { name: /安全回寫 SRT＋TXT/i });
    await act(async () => {
      fireEvent.click(publishBtn);
    });

    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
    expect(screen.getByText(/expected_revision：expected_revision must be greater than or equal to 0/i)).toBeInTheDocument();
  });

  // 12. unmount → timer & fetch cleanup
  it("12. component unmount cleans up timers and active polling without memory leaks", async () => {
    let pollCount = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/subtitles/test-job-1/publish-status")) {
        pollCount++;
        return { ok: true, json: async () => ({ status: "publishing" }) };
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

    // Advance time after unmount
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(pollCount).toBe(countBeforeUnmount);
  });
});
