"""Production entrypoint that serializes pipeline and editor Drive publication."""
from __future__ import annotations

from typing import Any

from app.jobs.drive_lock import drive_publish_lock
from app.pipeline import dynamic_worker_hardened as worker

_ORIGINAL_AUTO_PUBLISH = worker.base._auto_publish_to_source


def _locked_auto_publish(
    store: Any,
    record: dict[str, Any],
    data_dir: Any,
    worker_id: str,
) -> dict[str, Any] | None:
    source_path = str(record.get("source_path") or "")
    with drive_publish_lock(data_dir, source_path):
        return _ORIGINAL_AUTO_PUBLISH(
            store,
            record,
            data_dir,
            worker_id,
        )


worker.base._auto_publish_to_source = _locked_auto_publish


if __name__ == "__main__":
    raise SystemExit(worker.main())
