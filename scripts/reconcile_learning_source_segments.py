"""Reconcile learner subtitle rows with the approved immutable SRT snapshot.

The reviewer import table and the immutable learning-source table are normally
created together.  This command repairs an existing database when a bootstrap
or external import left those two representations out of sync.  It is
intentionally opt-in: without ``--apply`` it only reports a bounded plan.

The command never calls YouTube, Drive, or a model provider.  A caller should
make an online SQLite backup before applying the plan.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepairPlan:
    youtube_video_id: str
    title: str
    source_version_id: str
    source_version_number: int
    current_count: int
    source_count: int
    dependent_rows: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _source_rows(version: sqlite3.Row) -> list[dict[str, Any]]:
    payload = json.loads(str(version["snapshot_json"]))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"source version {version['id']} has no valid segments")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"source version {version['id']} contains a non-object segment")
        try:
            segment_index = int(item["segment_index"])
            start_ms = int(item["start_ms"])
            end_ms = int(item["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"source version {version['id']} contains invalid timing") from exc
        text = str(item.get("working_text") or item.get("original_text") or "").strip()
        if segment_index < 1 or start_ms < 0 or end_ms <= start_ms or not text:
            raise ValueError(f"source version {version['id']} contains an invalid segment")
        rows.append(
            {
                "segment_index": segment_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
            }
        )
    if len({row["segment_index"] for row in rows}) != len(rows):
        raise ValueError(f"source version {version['id']} contains duplicate segment indexes")
    return rows


def _is_exact(current: list[sqlite3.Row], source: list[dict[str, Any]]) -> bool:
    if len(current) != len(source):
        return False
    return all(
        int(row["segment_index"]) == item["segment_index"]
        and int(row["start_ms"]) == item["start_ms"]
        and int(row["end_ms"]) == item["end_ms"]
        and str(row["working_text"]) == item["text"]
        for row, item in zip(current, source, strict=True)
    )


def _dependent_rows(connection: sqlite3.Connection, video_id: str) -> int:
    queries = (
        """
        SELECT COUNT(*) FROM review_suggestions suggestion
        JOIN review_subtitle_segments segment ON segment.id = suggestion.segment_id
        WHERE segment.youtube_video_id = ?
        """,
        "SELECT COUNT(*) FROM learning_bookmarks WHERE youtube_video_id = ? AND segment_id IS NOT NULL",
        "SELECT COUNT(*) FROM learning_notes WHERE youtube_video_id = ? AND segment_id IS NOT NULL",
    )
    return sum(int(connection.execute(query, (video_id,)).fetchone()[0]) for query in queries)


def _plans(connection: sqlite3.Connection, video_ids: set[str] | None = None) -> list[RepairPlan]:
    rows = connection.execute(
        """
        SELECT video.youtube_video_id, video.title, version.id AS source_version_id,
               version.version_number, version.snapshot_json
        FROM learning_source_versions source
        JOIN review_subtitle_versions version ON version.id = source.subtitle_version_id
        JOIN review_videos video ON video.youtube_video_id = source.youtube_video_id
        ORDER BY video.title
        """
    ).fetchall()
    plans: list[RepairPlan] = []
    for version in rows:
        video_id = str(version["youtube_video_id"])
        if video_ids is not None and video_id not in video_ids:
            continue
        source = _source_rows(version)
        current = connection.execute(
            """
            SELECT segment_index, start_ms, end_ms, working_text
            FROM review_subtitle_segments
            WHERE youtube_video_id = ? ORDER BY segment_index
            """,
            (video_id,),
        ).fetchall()
        if _is_exact(current, source):
            continue
        plans.append(
            RepairPlan(
                youtube_video_id=video_id,
                title=str(version["title"]),
                source_version_id=str(version["source_version_id"]),
                source_version_number=int(version["version_number"]),
                current_count=len(current),
                source_count=len(source),
                dependent_rows=_dependent_rows(connection, video_id),
            )
        )
    return plans


def _apply(connection: sqlite3.Connection, plan: RepairPlan, actor: str) -> None:
    version = connection.execute(
        "SELECT snapshot_json FROM review_subtitle_versions WHERE id = ?",
        (plan.source_version_id,),
    ).fetchone()
    if version is None:
        raise ValueError(f"source version disappeared: {plan.source_version_id}")
    source = _source_rows(version)
    current = connection.execute(
        """
        SELECT id, segment_index, start_ms, end_ms, working_text, revision, created_at
        FROM review_subtitle_segments
        WHERE youtube_video_id = ? ORDER BY segment_index
        """,
        (plan.youtube_video_id,),
    ).fetchall()
    if _is_exact(current, source):
        return
    dependent_rows = _dependent_rows(connection, plan.youtube_video_id)
    if dependent_rows:
        raise ValueError(
            f"refusing {plan.youtube_video_id}: {dependent_rows} existing rows reference old segment ids"
        )

    now = _now()
    if len(current) == len(source):
        for old, item in zip(current, source, strict=True):
            connection.execute(
                """
                UPDATE review_subtitle_segments
                SET segment_index = ?, start_ms = ?, end_ms = ?, original_text = ?,
                    working_text = ?, revision = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    item["segment_index"],
                    item["start_ms"],
                    item["end_ms"],
                    item["text"],
                    item["text"],
                    now,
                    old["id"],
                ),
            )
    else:
        connection.execute(
            "DELETE FROM review_subtitle_segments WHERE youtube_video_id = ?",
            (plan.youtube_video_id,),
        )
        connection.executemany(
            """
            INSERT INTO review_subtitle_segments(
                youtube_video_id, segment_index, start_ms, end_ms,
                original_text, working_text, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            [
                (
                    plan.youtube_video_id,
                    item["segment_index"],
                    item["start_ms"],
                    item["end_ms"],
                    item["text"],
                    item["text"],
                    now,
                    now,
                )
                for item in source
            ],
        )
    connection.execute(
        """
        INSERT INTO review_admin_audit(
            actor, action, entity_type, entity_id, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            "learning_source_segments_reconciled",
            "review_video",
            plan.youtube_video_id,
            json.dumps(
                {
                    "source_version_id": plan.source_version_id,
                    "source_version_number": plan.source_version_number,
                    "previous_segment_count": plan.current_count,
                    "new_segment_count": plan.source_count,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            now,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("/app/data/course-transcript.db"))
    parser.add_argument("--video-id", action="append", dest="video_ids")
    parser.add_argument("--actor", default="system:learning-source-reconcile")
    parser.add_argument("--apply", action="store_true", help="apply the reported repair plan")
    args = parser.parse_args()
    selected = set(args.video_ids) if args.video_ids else None

    connection = _connect(args.database)
    try:
        plans = _plans(connection, selected)
        print(f"PLAN_COUNT={len(plans)}")
        for plan in plans:
            print(
                "PLAN",
                plan.youtube_video_id,
                plan.current_count,
                "->",
                plan.source_count,
                "dependents=",
                plan.dependent_rows,
                plan.title,
            )
        if not args.apply or not plans:
            return 0
        if any(plan.dependent_rows for plan in plans):
            raise SystemExit("refusing to apply while old segment ids have dependents")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for plan in plans:
                _apply(connection, plan, args.actor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        print(f"APPLIED_COUNT={len(plans)}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
