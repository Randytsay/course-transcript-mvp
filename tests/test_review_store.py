from __future__ import annotations

import pytest

from app.review.store import ReviewConflict, ReviewStore, changed_char_count


def _store(tmp_path):
    return ReviewStore(tmp_path / "course-transcript.db")


def _create_user(store: ReviewStore, *, subject: str = "google-1", name: str = "法專師姐"):
    return store.get_or_create_user_for_identity(
        provider="google",
        provider_subject=subject,
        display_name=name,
        email=f"{subject}@example.test",
    )


def _create_video(store: ReviewStore, *, video_id: str = "yt-video-1"):
    store.upsert_video(
        youtube_video_id=video_id,
        playlist_id="playlist-1",
        title="彌勒大成佛經 第 8 集",
        duration_ms=3_600_000,
        caption_track_id="caption-1",
    )
    return store.import_subtitle_segments(
        youtube_video_id=video_id,
        segments=[
            {
                "segment_index": 1,
                "start_ms": 0,
                "end_ms": 5_000,
                "text": "佛告阿難",
            },
            {
                "segment_index": 2,
                "start_ms": 5_000,
                "end_ms": 10_000,
                "text": "彌勒大成佛今",
            },
        ],
    )


def test_changed_char_count_is_human_oriented():
    assert changed_char_count("彌勒大成佛今", "彌勒大成佛經") == 1
    assert changed_char_count("佛告阿難", "佛告阿難尊者") == 2
    assert changed_char_count("甲乙丙", "甲丙") == 1


def test_first_login_is_auto_active_and_identity_is_stable(tmp_path):
    store = _store(tmp_path)

    first = _create_user(store)
    second = store.get_or_create_user_for_identity(
        provider="google",
        provider_subject="google-1",
        display_name="法專師姐（更新名稱）",
    )

    assert first["status"] == "active"
    assert first["role"] == "reviewer"
    assert second["id"] == first["id"]
    assert second["display_name"] == "法專師姐（更新名稱）"


def test_google_and_line_can_link_to_same_logical_user(tmp_path):
    store = _store(tmp_path)
    user = _create_user(store)

    linked = store.link_identity(
        user_id=user["id"],
        provider="line",
        provider_subject="U-line-123",
    )
    via_line = store.get_or_create_user_for_identity(
        provider="line",
        provider_subject="U-line-123",
        display_name="法專師姐",
    )

    assert via_line["id"] == user["id"]
    assert {item["provider"] for item in linked["identities"]} == {"google", "line"}


def test_identity_cannot_be_silently_merged_into_another_user(tmp_path):
    store = _store(tmp_path)
    first = _create_user(store, subject="google-a", name="甲師兄")
    second = _create_user(store, subject="google-b", name="乙師兄")
    store.link_identity(
        user_id=first["id"],
        provider="line",
        provider_subject="U-shared",
    )

    with pytest.raises(ReviewConflict):
        store.link_identity(
            user_id=second["id"],
            provider="line",
            provider_subject="U-shared",
        )


def test_initial_subtitle_import_never_overwrites_existing_review_state(tmp_path):
    store = _store(tmp_path)
    _create_video(store)

    with pytest.raises(ReviewConflict):
        store.import_subtitle_segments(
            youtube_video_id="yt-video-1",
            segments=[
                {
                    "segment_index": 1,
                    "start_ms": 0,
                    "end_ms": 5_000,
                    "text": "另一份字幕",
                }
            ],
        )


def test_submission_counts_immediately_without_mutating_working_subtitle(tmp_path):
    store = _store(tmp_path)
    user = _create_user(store)
    segments = _create_video(store)

    suggestion = store.submit_suggestion(
        segment_id=segments[1]["id"],
        user_id=user["id"],
        suggested_text="彌勒大成佛經",
    )
    leaderboard = store.contribution_leaderboard()
    current = store.list_segments("yt-video-1")

    assert suggestion["status"] == "pending"
    assert suggestion["changed_chars"] == 1
    assert current[1]["working_text"] == "彌勒大成佛今"
    assert leaderboard[0]["suggestions_sent"] == 1
    assert leaderboard[0]["changed_chars"] == 1
    assert leaderboard[0]["videos_contributed"] == 1


def test_revising_pending_suggestion_does_not_inflate_submission_count(tmp_path):
    store = _store(tmp_path)
    user = _create_user(store)
    segments = _create_video(store)
    suggestion = store.submit_suggestion(
        segment_id=segments[1]["id"],
        user_id=user["id"],
        suggested_text="彌勒大成佛經",
    )

    revised = store.revise_suggestion(
        suggestion_id=suggestion["id"],
        user_id=user["id"],
        suggested_text="彌勒大成佛經。",
    )
    leaderboard = store.contribution_leaderboard()

    assert revised["suggested_text"] == "彌勒大成佛經。"
    assert leaderboard[0]["suggestions_sent"] == 1
    assert leaderboard[0]["changed_chars"] == 2


def test_progress_keeps_reviewed_boundary_but_allows_playback_to_move(tmp_path):
    store = _store(tmp_path)
    user = _create_user(store)
    _create_video(store)

    store.update_progress(
        user_id=user["id"],
        youtube_video_id="yt-video-1",
        last_playback_ms=20_000,
        reviewed_until_ms=15_000,
        last_segment_index=2,
    )
    progress = store.update_progress(
        user_id=user["id"],
        youtube_video_id="yt-video-1",
        last_playback_ms=8_000,
        reviewed_until_ms=10_000,
        last_segment_index=1,
    )
    resume = store.get_resume_point(user["id"])

    assert progress["last_playback_ms"] == 8_000
    assert progress["reviewed_until_ms"] == 15_000
    assert resume is not None
    assert resume["youtube_video_id"] == "yt-video-1"
    assert resume["last_playback_ms"] == 8_000


def test_contribution_detail_lists_videos_and_completed_review(tmp_path):
    store = _store(tmp_path)
    user = _create_user(store)
    segments = _create_video(store)
    store.submit_suggestion(
        segment_id=segments[0]["id"],
        user_id=user["id"],
        suggested_text="佛告阿難尊者",
    )
    store.update_progress(
        user_id=user["id"],
        youtube_video_id="yt-video-1",
        last_playback_ms=3_600_000,
        reviewed_until_ms=3_600_000,
        completed=True,
    )

    detail = store.user_contribution_detail(user["id"])

    assert detail["suggestions_sent"] == 1
    assert detail["changed_chars"] == 2
    assert detail["videos_contributed"] == 1
    assert detail["completed_videos"] == 1
    assert detail["videos"][0]["title"] == "彌勒大成佛經 第 8 集"
