from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.skills.dacheng_subtitle_review import (
    CONTENT_MODE,
    review_lesson,
    write_review_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review one 佛說彌勒大成佛經 lesson without provider calls."
    )
    parser.add_argument("--srt", required=True, type=Path)
    parser.add_argument("--human-txt", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--lesson-date", required=True)
    parser.add_argument(
        "--content-mode",
        required=True,
        choices=[CONTENT_MODE],
        help="Explicit mode; filenames never infer Dacheng handling.",
    )
    parser.add_argument("--stem", required=True)
    args = parser.parse_args()

    result = review_lesson(
        srt_text=args.srt.read_text(encoding="utf-8"),
        human_text=args.human_txt.read_text(encoding="utf-8"),
        data_dir=args.data_dir,
        lesson_id=args.lesson_id,
        lesson_date=args.lesson_date,
    )
    paths = write_review_bundle(
        result,
        output_dir=args.output_dir,
        stem=args.stem,
    )
    summary = {
        "status": result["qa"]["status"],
        "learning_applied": result["learning_applied"],
        "paths": paths,
        "qa": {
            key: result["qa"][key]
            for key in (
                "cue_count",
                "median_duration_ms",
                "max_duration_ms",
                "gt_15s_count",
                "lt_250ms_count",
                "human_missing_count",
                "source_missing_count",
            )
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["qa"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
