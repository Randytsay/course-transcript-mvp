from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path

LOCAL_TOOLS = Path(os.environ.get("COURSE_TRANSCRIPT_LOCAL_TOOLS", "/app/data/local-tools/faster-whisper"))
if LOCAL_TOOLS.is_dir():
    sys.path.insert(0, str(LOCAL_TOOLS))

from faster_whisper import WhisperModel  # type: ignore  # noqa: E402
import numpy as np  # type: ignore  # noqa: E402


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_context(source: Path, target: Path, start_ms: int, end_ms: int, context_ms: int) -> tuple[int, int]:
    clip_start = max(0, start_ms - context_ms)
    clip_end = end_ms + context_ms
    duration_ms = clip_end - clip_start
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{clip_start / 1000:.3f}",
        "-i", str(source),
        "-t", f"{duration_ms / 1000:.3f}",
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(120, round(duration_ms / 1000) + 120))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg extraction failed")
    return clip_start, clip_end


def load_pcm_wav(path: Path) -> "np.ndarray":
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000 or handle.getsampwidth() != 2:
            raise RuntimeError("local ASR WAV must be mono 16kHz signed PCM16")
        frames = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Provider-free local ASR fallback for bounded transcript gaps.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--gaps-json", required=True, help='JSON list: [{"start_ms":...,"end_ms":...}, ...]')
    parser.add_argument("--patch-index-base", type=int, default=3_000_000)
    parser.add_argument("--model", default="small")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--context-ms", type=int, default=5000)
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--download-root", default="/app/data/local-tools/faster-whisper-models")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    job_dir = data_dir / "jobs" / args.job_id
    source = job_dir / "normalized.flac"
    if not source.is_file():
        raise SystemExit(f"missing normalized audio: {source}")
    gaps = json.loads(args.gaps_json)
    if not isinstance(gaps, list) or not gaps:
        raise SystemExit("gaps-json must be a non-empty list")

    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type=args.compute_type,
        download_root=args.download_root,
        cpu_threads=max(1, min(4, os.cpu_count() or 1)),
    )

    evidence = {
        "schema_version": 1,
        "job_id": args.job_id,
        "created_at": datetime.now(UTC).isoformat(),
        "provider_calls_started": False,
        "provider": "local_faster_whisper",
        "provenance": "local_asr_fallback",
        "model": args.model,
        "compute_type": args.compute_type,
        "patches": [],
    }

    temp_root = data_dir / "tmp" / "local-asr-fallback"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=args.job_id + "-", dir=temp_root) as temp:
        temp_dir = Path(temp)
        for offset, gap in enumerate(gaps, start=1):
            start_ms = int(gap["start_ms"])
            end_ms = int(gap["end_ms"])
            if end_ms <= start_ms:
                continue
            patch_index = args.patch_index_base + offset
            wav = temp_dir / f"gap-{patch_index}.wav"
            clip_start_ms, clip_end_ms = extract_context(
                source, wav, start_ms, end_ms, args.context_ms
            )
            audio_pcm = load_pcm_wav(wav)
            segments, info = model.transcribe(
                audio_pcm,
                language="zh",
                beam_size=5,
                best_of=5,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
                temperature=0.0,
            )
            words: list[dict[str, object]] = []
            segment_rows: list[dict[str, object]] = []
            for segment in segments:
                seg_start = clip_start_ms + round(float(segment.start) * 1000)
                seg_end = clip_start_ms + round(float(segment.end) * 1000)
                segment_rows.append(
                    {
                        "start_ms": seg_start,
                        "end_ms": seg_end,
                        "text": segment.text.strip(),
                        "avg_logprob": getattr(segment, "avg_logprob", None),
                        "no_speech_prob": getattr(segment, "no_speech_prob", None),
                    }
                )
                for word in segment.words or []:
                    absolute_start = clip_start_ms + round(float(word.start) * 1000)
                    absolute_end = clip_start_ms + round(float(word.end) * 1000)
                    midpoint = (absolute_start + absolute_end) // 2
                    if not (start_ms <= midpoint < end_ms):
                        continue
                    token = str(word.word or "").strip()
                    if not token or absolute_end <= absolute_start:
                        continue
                    words.append(
                        {
                            "word": token,
                            "start_ms": absolute_start,
                            "end_ms": absolute_end,
                            "probability": float(word.probability),
                            "source": "local_faster_whisper",
                        }
                    )

            chunk_dir = job_dir / "chunks" / f"chunk-{patch_index:03d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(
                chunk_dir / "words.json",
                {
                    "job": args.job_id,
                    "chunk_index": patch_index,
                    "source": "local_faster_whisper",
                    "provenance": "local_asr_fallback",
                    "words": words,
                },
            )
            atomic_json(
                chunk_dir / "manifest.json",
                {
                    "chunk_index": patch_index,
                    "role": "patch",
                    "patch_mode": "replace_window",
                    "source_start_ms": start_ms,
                    "source_end_ms": end_ms,
                    "status": "SUCCEEDED",
                    "processing_strategy": "LOCAL_ASR_FALLBACK",
                    "dynamic_batching": False,
                    "provider": "local_faster_whisper",
                    "provenance": "local_asr_fallback",
                    "model": args.model,
                    "word_count": len(words),
                    "max_end_ms": max((int(item["end_ms"]) for item in words), default=start_ms),
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            evidence["patches"].append(
                {
                    "patch_index": patch_index,
                    "gap_start_ms": start_ms,
                    "gap_end_ms": end_ms,
                    "context_start_ms": clip_start_ms,
                    "context_end_ms": clip_end_ms,
                    "word_count": len(words),
                    "segments": segment_rows,
                }
            )
            print(
                f"LOCAL_ASR patch={patch_index} gap={start_ms}-{end_ms} words={len(words)}",
                flush=True,
            )

    atomic_json(job_dir / "local-asr-gap-evidence.json", evidence)
    print(f"LOCAL_ASR=PASS patches={len(evidence['patches'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
