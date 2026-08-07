import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import JobControls from "../job-controls";

describe("JobControls Component", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders retry stage button when job status is failed and triggers POST /jobs/{id}/retry-stage", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/retry-stage")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: "transcribing",
              revision: 2,
              active_stage: "chirp",
              stage_detail: "重試中",
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            status: "failed",
            revision: 1,
            active_stage: "chirp",
            stage_detail: "chirp 階段失敗",
          }),
      });
    });

    vi.stubGlobal("fetch", fetchMock);

    render(<JobControls jobId="job-failed-123" />);

    // Wait for job to load
    await waitFor(() => {
      expect(screen.getByText(/重試失敗階段 \(chirp\)/i)).toBeInTheDocument();
    });

    // Click retry button
    const retryBtn = screen.getByRole("button", { name: /重試失敗階段/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/jobs/job-failed-123/retry-stage"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ expected_revision: 1, stage: "chirp" }),
        })
      );
    });
  });
});
