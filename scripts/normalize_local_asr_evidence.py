from __future__ import annotations

import argparse
import json
from pathlib import Path


def atomic_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--data-dir", default="/app/data")
    args = parser.parse_args()

    job_dir = Path(args.data_dir) / "jobs" / args.job_id
    evidence_path = job_dir / "local-asr-gap-evidence.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    changed = 0
    for item in payload.get("patches", []):
        if not isinstance(item, dict):
            continue
        word_count = int(item.get("word_count") or 0)
        probs = [
            float(seg["no_speech_prob"])
            for seg in item.get("segments", [])
            if isinstance(seg, dict) and seg.get("no_speech_prob") is not None
        ]
        low_confidence_singleton = (
            word_count <= 1 and bool(probs) and min(probs) >= 0.75
        )
        verified_nonlexical = word_count == 0 or low_confidence_singleton
        classification = (
            "zero_lexical_words"
            if word_count == 0
            else "low_confidence_singleton"
            if low_confidence_singleton
            else "lexical_speech"
        )
        item["classification"] = classification
        item["verified_nonlexical"] = verified_nonlexical

        patch_index = int(item["patch_index"])
        manifest_path = job_dir / "chunks" / f"chunk-{patch_index:03d}" / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["local_asr_classification"] = classification
            if low_confidence_singleton:
                manifest["provider_output_rejected"] = True
                manifest["rejection_reason"] = "local_asr_low_confidence_singleton"
            elif manifest.get("provider") == "local_faster_whisper":
                manifest.pop("provider_output_rejected", None)
                manifest.pop("rejection_reason", None)
            atomic_json(manifest_path, manifest)
        changed += 1

    atomic_json(evidence_path, payload)
    print(json.dumps({
        "job_id": args.job_id,
        "patches_normalized": changed,
        "verified_nonlexical": sum(
            1 for item in payload.get("patches", [])
            if isinstance(item, dict) and item.get("verified_nonlexical")
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
