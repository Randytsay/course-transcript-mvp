import assert from "node:assert/strict";
import test from "node:test";

import {
  PublishCoordinator,
} from "../publish-coordinator.ts";

function status(overrides = {}) {
  return {
    status: "idle",
    job_status: "awaiting_review",
    batch_status: "awaiting_review",
    current_revision: 0,
    published_revision: null,
    revision_changed_during_publish: false,
    zero_edit_review: false,
    drive_publish_status: null,
    editor_publish_event_count: 0,
    total_editor_publish_event_count: 0,
    can_publish: true,
    can_retry: false,
    message: "可發布目前版本。",
    ...overrides,
  };
}

function response(payload, code = 200) {
  return {
    ok: code >= 200 && code < 300,
    status: code,
    async json() {
      return payload;
    },
  };
}

class FetchHarness {
  handlers = [];
  calls = [];
  signals = [];

  enqueue(handler) {
    this.handlers.push(handler);
    return this;
  }

  fetch = async (url, init = {}) => {
    this.calls.push({ url: String(url), init });
    if (init.signal) this.signals.push(init.signal);
    const handler = this.handlers.shift();
    if (!handler) throw new Error(`Unexpected fetch: ${url}`);
    if (handler instanceof Error) throw handler;
    if (typeof handler === "function") return handler(url, init);
    return handler;
  };

  count(method) {
    return this.calls.filter(
      ({ init }) => (init.method ?? "GET").toUpperCase() === method,
    ).length;
  }
}

class FakeTimers {
  now = 0;
  nextId = 1;
  tasks = new Map();

  setTimer = (callback, delay) => {
    const id = this.nextId++;
    this.tasks.set(id, { callback, due: this.now + delay });
    return id;
  };

  clearTimer = (id) => {
    this.tasks.delete(id);
  };

  async advance(milliseconds) {
    this.now += milliseconds;
    while (true) {
      const due = [...this.tasks.entries()]
        .filter(([, task]) => task.due <= this.now)
        .sort((left, right) => left[1].due - right[1].due)[0];
      if (!due) break;
      this.tasks.delete(due[0]);
      due[1].callback();
      await Promise.resolve();
      await Promise.resolve();
    }
  }
}

function createCoordinator(harness, overrides = {}) {
  const snapshots = [];
  const coordinator = new PublishCoordinator({
    subtitleId: "job-1",
    revision: 0,
    canPublishToSource: true,
    fetchImpl: harness.fetch,
    confirm: () => true,
    onChange: (snapshot) => snapshots.push(snapshot),
    ...overrides,
  });
  return { coordinator, snapshots };
}

test("POST 200 reaches completed directly", async () => {
  const harness = new FetchHarness()
    .enqueue(response(status()))
    .enqueue(response({
      status: "completed",
      published_revision: 0,
      current_revision: 0,
      zero_edit_review: true,
      backup_count: 2,
    }));
  const { coordinator } = createCoordinator(harness);
  await coordinator.start();
  assert.equal(coordinator.getSnapshot().canSubmit, true);

  await coordinator.publish();

  assert.equal(coordinator.getSnapshot().completed, true);
  assert.equal(coordinator.getSnapshot().showButton, false);
  assert.equal(harness.count("POST"), 1);
  coordinator.dispose();
});

test("HTTP 500 reconciles through GET and never resends POST", async () => {
  const harness = new FetchHarness()
    .enqueue(response(status()))
    .enqueue(response({ detail: "Internal Server Error" }, 500))
    .enqueue(response(status({
      status: "completed",
      job_status: "completed",
      batch_status: "completed",
      published_revision: 0,
      zero_edit_review: true,
      drive_publish_status: "completed",
      editor_publish_event_count: 1,
      total_editor_publish_event_count: 1,
      can_publish: false,
    })));
  const { coordinator } = createCoordinator(harness);
  await coordinator.start();

  await coordinator.publish();

  assert.equal(coordinator.getSnapshot().completed, true);
  assert.equal(harness.count("POST"), 1);
  assert.equal(harness.count("GET"), 2);
  coordinator.dispose();
});

test("network failure reconciles through GET and never resends POST", async () => {
  const harness = new FetchHarness()
    .enqueue(response(status()))
    .enqueue(new TypeError("Failed to fetch"))
    .enqueue(response(status({
      status: "completed",
      job_status: "completed",
      batch_status: "completed",
      published_revision: 0,
      zero_edit_review: true,
      drive_publish_status: "completed",
      editor_publish_event_count: 1,
      total_editor_publish_event_count: 1,
      can_publish: false,
    })));
  const { coordinator } = createCoordinator(harness);
  await coordinator.start();

  await coordinator.publish();

  assert.equal(coordinator.getSnapshot().completed, true);
  assert.equal(harness.count("POST"), 1);
  coordinator.dispose();
});

test("rapid publish calls produce exactly one POST", async () => {
  let resolvePost;
  const pending = new Promise((resolve) => {
    resolvePost = resolve;
  });
  const harness = new FetchHarness()
    .enqueue(response(status()))
    .enqueue(() => pending);
  const { coordinator } = createCoordinator(harness);
  await coordinator.start();

  const first = coordinator.publish();
  const second = coordinator.publish();
  await Promise.resolve();

  assert.equal(harness.count("POST"), 1);
  resolvePost(response({
    status: "completed",
    published_revision: 0,
    current_revision: 0,
    zero_edit_review: true,
    backup_count: 2,
  }));
  await Promise.all([first, second]);
  coordinator.dispose();
});

test("refresh during publishing performs GET polling only", async () => {
  const publishing = status({
    status: "publishing",
    can_publish: false,
    message: "正在發布至 Google Drive，請勿重複操作。",
  });
  const harness = new FetchHarness()
    .enqueue(response(publishing))
    .enqueue(response(publishing));
  const timers = new FakeTimers();
  const { coordinator } = createCoordinator(harness, {
    now: () => timers.now,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  await coordinator.start();

  assert.equal(harness.count("POST"), 0);
  assert.equal(harness.count("GET"), 2);
  assert.equal(coordinator.getSnapshot().busy, true);
  coordinator.dispose();
});

test("publishing state keeps submission disabled", async () => {
  const publishing = status({
    status: "publishing",
    can_publish: false,
  });
  const harness = new FetchHarness()
    .enqueue(response(publishing))
    .enqueue(response(publishing));
  const timers = new FakeTimers();
  const { coordinator } = createCoordinator(harness, {
    now: () => timers.now,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  await coordinator.start();

  assert.equal(coordinator.getSnapshot().canSubmit, false);
  assert.equal(coordinator.getSnapshot().buttonLabel, "正在發布…");
  coordinator.dispose();
});

test("completed current revision hides the publish button", async () => {
  const harness = new FetchHarness().enqueue(response(status({
    status: "completed",
    job_status: "completed",
    batch_status: "completed",
    published_revision: 0,
    zero_edit_review: true,
    drive_publish_status: "completed",
    editor_publish_event_count: 1,
    total_editor_publish_event_count: 1,
    can_publish: false,
  })));
  const { coordinator } = createCoordinator(harness);

  await coordinator.start();

  assert.equal(coordinator.getSnapshot().completed, true);
  assert.equal(coordinator.getSnapshot().showButton, false);
  coordinator.dispose();
});

test("failed without explicit retry authority remains disabled", async () => {
  const harness = new FetchHarness().enqueue(response(status({
    status: "failed",
    can_publish: false,
    can_retry: false,
    message: "發布已記錄失敗；目前不能安全重試。",
  })));
  const { coordinator } = createCoordinator(harness);

  await coordinator.start();

  assert.equal(coordinator.getSnapshot().canSubmit, false);
  assert.match(coordinator.getSnapshot().notice, /不能安全重試/);
  coordinator.dispose();
});

test("failed with explicit retry authority can be submitted on a fresh page", async () => {
  const harness = new FetchHarness().enqueue(response(status({
    status: "failed",
    can_publish: false,
    can_retry: true,
    message: "已確認可安全重試。",
  })));
  const { coordinator } = createCoordinator(harness);

  await coordinator.start();

  assert.equal(coordinator.getSnapshot().canSubmit, true);
  assert.equal(
    coordinator.getSnapshot().buttonLabel,
    "重新嘗試安全回寫",
  );
  coordinator.dispose();
});

test("ambiguous state is cautionary and fail closed", async () => {
  const harness = new FetchHarness().enqueue(response(status({
    status: "ambiguous",
    can_publish: false,
    can_retry: false,
    message: "發布證據尚未一致，請勿重複送出。",
  })));
  const { coordinator } = createCoordinator(harness);

  await coordinator.start();

  const snapshot = coordinator.getSnapshot();
  assert.equal(snapshot.canSubmit, false);
  assert.equal(snapshot.noticeKind, "warning");
  assert.match(snapshot.notice, /請勿重複送出/);
  coordinator.dispose();
});

test("FastAPI structured detail is rendered without object coercion", async () => {
  const harness = new FetchHarness()
    .enqueue(response(status()))
    .enqueue(response({
      detail: [{
        loc: ["body", "expected_revision"],
        msg: "Input should be greater than or equal to 0",
      }],
    }, 422));
  const { coordinator } = createCoordinator(harness);
  await coordinator.start();

  await coordinator.publish();

  const snapshot = coordinator.getSnapshot();
  assert.match(snapshot.notice, /expected_revision/);
  assert.doesNotMatch(snapshot.notice, /\[object Object\]/);
  assert.equal(snapshot.canSubmit, false);
  assert.equal(harness.count("POST"), 1);
  coordinator.dispose();
});

test("dispose aborts fetch signals and clears polling timers", async () => {
  const publishing = status({
    status: "publishing",
    can_publish: false,
  });
  const harness = new FetchHarness()
    .enqueue(response(publishing))
    .enqueue(response(publishing));
  const timers = new FakeTimers();
  const { coordinator } = createCoordinator(harness, {
    now: () => timers.now,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  await coordinator.start();
  assert.equal(timers.tasks.size, 1);
  const callsBeforeDispose = harness.calls.length;

  coordinator.dispose();

  assert.equal(timers.tasks.size, 0);
  assert.ok(harness.signals.length > 0);
  assert.ok(harness.signals.every((signal) => signal.aborted));
  await timers.advance(10_000);
  assert.equal(harness.calls.length, callsBeforeDispose);
});
