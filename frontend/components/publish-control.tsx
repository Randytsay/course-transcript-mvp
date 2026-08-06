"use client";

import { Check, CloudUpload, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  PublishCoordinator,
  type PublishSnapshot,
} from "./publish-coordinator";

type PublishControlProps = {
  subtitleId: string;
  revision: number;
  canPublishToSource: boolean;
  onPublished?: () => void | Promise<void>;
  onError?: (message: string | null) => void;
};

const initialSnapshot: PublishSnapshot = {
  status: null,
  busy: true,
  attemptLocked: false,
  notice: "正在檢查發布狀態…",
  noticeKind: "neutral",
  showButton: true,
  canSubmit: false,
  buttonLabel: "檢查發布狀態…",
  completed: false,
};

export default function PublishControl({
  subtitleId,
  revision,
  canPublishToSource,
  onPublished,
  onError,
}: PublishControlProps) {
  const [snapshot, setSnapshot] = useState<PublishSnapshot>({
    ...initialSnapshot,
    busy: canPublishToSource,
    showButton: canPublishToSource,
    notice: canPublishToSource ? initialSnapshot.notice : "",
  });
  const coordinatorRef = useRef<PublishCoordinator | null>(null);
  const onPublishedRef = useRef(onPublished);
  const onErrorRef = useRef(onError);

  onPublishedRef.current = onPublished;
  onErrorRef.current = onError;

  useEffect(() => {
    if (!snapshot.busy || snapshot.completed) return;
    const warnBeforeLeave = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeave);
    return () => window.removeEventListener("beforeunload", warnBeforeLeave);
  }, [snapshot.busy, snapshot.completed]);

  useEffect(() => {
    const coordinator = new PublishCoordinator({
      subtitleId,
      revision,
      canPublishToSource,
      onChange(next) {
        setSnapshot(next);
        onErrorRef.current?.(
          next.noticeKind === "error" ? next.notice : null,
        );
      },
      onPublished() {
        return onPublishedRef.current?.();
      },
    });
    coordinatorRef.current = coordinator;
    void coordinator.start();

    return () => {
      coordinator.dispose();
      if (coordinatorRef.current === coordinator) {
        coordinatorRef.current = null;
      }
    };
  }, [subtitleId, revision, canPublishToSource]);

  if (!canPublishToSource) return null;

  return (
    <div
      data-testid="publish-control"
      style={{
        display: "flex",
        gap: "8px",
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      {snapshot.showButton && (
        <button
          type="button"
          className="button button--primary"
          data-testid="publish-button"
          disabled={!snapshot.canSubmit}
          onClick={() => void coordinatorRef.current?.publish()}
        >
          {snapshot.busy ? (
            <LoaderCircle className="spin" size={18} />
          ) : (
            <CloudUpload size={18} />
          )}
          {snapshot.buttonLabel}
        </button>
      )}
      {snapshot.completed && (
        <span
          className="status-badge status-badge--completed"
          data-testid="publish-completed"
        >
          <Check size={15} />已成功發布
        </span>
      )}
      {snapshot.notice && (
        <span
          data-testid="publish-notice"
          role={snapshot.noticeKind === "error" ? "alert" : "status"}
          style={{
            color: snapshot.noticeKind === "error"
              ? "#b91c1c"
              : snapshot.noticeKind === "warning"
                ? "#92400e"
                : snapshot.noticeKind === "success"
                  ? "#166534"
                  : "var(--text-muted)",
            fontSize: ".9rem",
          }}
        >
          {snapshot.notice}
        </span>
      )}
    </div>
  );
}
