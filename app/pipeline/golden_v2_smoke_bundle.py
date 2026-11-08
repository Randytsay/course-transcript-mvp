"""Local review deliverables from three checksum-verified smoke receipts.

No provider, Drive or ledger mutation. Text agreement is never Golden QA or
human gold. SRT/TXT are a shared, uncorrected Chirp word-time candidate.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
from typing import Any

from app.pipeline.golden_v2_smoke_ledger import SingleClipSmokeLedger, SmokeConflict
from app.providers.golden_v2_compare import compare_gemini_chirp
from app.subtitles.golden_v2_render import render_golden_v2


def build_smoke_bundle(*, trial: SingleClipSmokeLedger) -> dict[str, Any]:
    plan = trial._scope()
    receipts = trial.verified_receipts()
    primary, chirp, third = [r["raw_response"] for r in receipts]
    for record, model in zip((primary, chirp, third), plan["models_in_order"], strict=True):
        if (not isinstance(record, dict) or record.get("model") != model
                or record.get("clip_sha256") != plan["clip_sha256"]):
            raise SmokeConflict("provider response source/model identity mismatch")
    for record in (primary, third):
        if (not isinstance(record.get("text"), str) or not record["text"].strip()
                or record.get("finish_reason") not in {"STOP", "FINISH_REASON_STOP"}):
            raise SmokeConflict("incomplete recognizer text")
    if chirp.get("timing_authority") != "CHIRP_3" or not chirp.get("words"):
        raise SmokeConflict("independent Chirp word timing required")
    words = []
    for word in chirp["words"]:
        start, end = word.get("start_seconds"), word.get("end_seconds")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= 240):
            raise SmokeConflict("out-of-sample Chirp word timing")
        words.append({"word": word.get("word"), "start_ms": round(start * 1000),
                      "end_ms": round(end * 1000)})
    rendered = render_golden_v2(merged={
        "source_sha": plan["clip_sha256"], "words": words, "timing_authority": "chirp_3"})
    comparisons = [
        {"model": record["model"], "comparison": compare_gemini_chirp(
            record["text"], words, source_sha256=plan["clip_sha256"])}
        for record in (primary, third)
    ]
    decisions = []
    for comparison in comparisons:
        for i, candidate in enumerate(comparison["comparison"]["candidates"]):
            start, end = candidate["source_time_start"], candidate["source_time_end"]
            decisions.append({
                "id": f"{comparison['model']}:{i}", "model": comparison["model"],
                "candidate": candidate,
                "clip_time_start_ms": start, "clip_time_end_ms": end,
                "parent_time_start_ms": None if start is None else start + 600000,
                "parent_time_end_ms": None if end is None else end + 600000,
                "decision": "PENDING", "audio_listened": False,
                "corrected_text": None, "reason": None,
            })
    return {
        "schema": "golden-v2-smoke-review-bundle-v1",
        "clip_sha256": plan["clip_sha256"],
        "parent_source_sha256": plan["source_original_sha256"],
        "parent_interval_seconds": [600, 840],
        "status": "REVIEW_REQUIRED", "golden_pass": False,
        "acoustic_coverage": "NOT_VERIFIED", "human_gold_verified": False,
        "billing_invoice_verified": False, "publication_allowed": False,
        "receipts": [{"model": r["model"], "sha256": hashlib.sha256(
            json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(), "usage_estimate_cents": r["accounted_cents"]} for r in receipts],
        "usage_estimate_total_cents": sum(r["accounted_cents"] for r in receipts),
        "comparisons": comparisons, "rendered": rendered,
        "primary_text": primary["text"], "third_text": third["text"],
        "review": {"schema": "golden-v2-smoke-listening-review-v1",
                   "clip_sha256": plan["clip_sha256"], "reviewer": None,
                   "full_clip_listened": False, "decisions": decisions},
    }


def write_smoke_bundle(*, trial: SingleClipSmokeLedger, output_dir: Path) -> dict[str, Any]:
    """Write a replay-safe immutable candidate bundle, manifest last."""
    bundle = build_smoke_bundle(trial=trial)
    output_dir = Path(output_dir).expanduser()
    if any(p.is_symlink() for p in (output_dir, *output_dir.parents)):
        raise SmokeConflict("linked review output directory")
    # Never write into the source, receipts or trial ledger directory.
    if output_dir.resolve() == trial.plan_path.parent.resolve() or (
            trial.receipt_root.resolve() in output_dir.resolve().parents):
        raise SmokeConflict("review output must be separate from paid evidence")
    wire = lambda obj: (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    rendered = bundle["rendered"]
    rows = "".join(
        "<tr><td>" + html.escape(item["id"]) + "</td><td>" +
        html.escape(str(item["clip_time_start_ms"])) + "–" +
        html.escape(str(item["clip_time_end_ms"])) + " ms</td><td>" +
        html.escape(item["candidate"]["chirp_normalized_excerpt"]) + "</td><td>" +
        html.escape(item["candidate"]["gemini_normalized_excerpt"]) + "</td></tr>"
        for item in bundle["review"]["decisions"])
    page = ("<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Golden V2 四分鐘覆聽</title><style>body{font:18px system-ui;max-width:1000px;"
            "margin:auto;padding:20px}pre{white-space:pre-wrap}td{padding:8px;border:1px solid #aaa}"
            "audio{width:100%}.scroll{overflow:auto}</style><h1>四分鐘測試覆聽</h1>"
            "<p>待人工覆聽。三稿一致也不能證明辨識正確；字幕為 Chirp 原始候選稿，尚未發布。</p>"
            "<p>片段 00:00 對應母檔 10:00。費用為用量估算，尚未核對帳單。</p>"
            "<audio controls preload='metadata' src='clip.flac'></audio>"
            "<h2>Gemini 主稿</h2><pre>" + html.escape(bundle["primary_text"]) +
            "</pre><h2>Chirp 時碼候選稿</h2><pre>" + html.escape(rendered["transcript"]) +
            "</pre><h2>第三引擎</h2><pre>" + html.escape(bundle["third_text"]) +
            "</pre><h2>差異覆聽</h2><div class='scroll'><table><tr><th>項目</th><th>片段時間</th>"
            "<th>Chirp</th><th>Gemini</th></tr>" + rows + "</table></div>"
            "<p>null 表示沒有可信時碼，必須覆聽完整片段。請把決策記入 listening-review.json 的工作副本。</p></html>")
    files = {
        "primary.txt": (bundle["primary_text"] + "\n").encode(),
        "third.txt": (bundle["third_text"] + "\n").encode(),
        "candidate.chirp.srt": rendered["srt"].encode(),
        "candidate.chirp.txt": rendered["transcript"].encode(),
        "comparison.json": wire(bundle), "listening-review.json": wire(bundle["review"]),
        "review.html": page.encode(), "clip.flac": trial.excerpt_audio.read_bytes(),
    }
    if hashlib.sha256(files["clip.flac"]).hexdigest() != bundle["clip_sha256"]:
        raise SmokeConflict("clip changed during review export")
    manifest = {"schema": "golden-v2-smoke-review-manifest-v1",
                "clip_sha256": bundle["clip_sha256"], "status": "REVIEW_REQUIRED",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["manifest.json"] = wire(manifest)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if set(p.name for p in output_dir.iterdir()) - files.keys():
        raise SmokeConflict("unexpected files in immutable review directory")
    # Check all existing artifacts before materializing any new files.
    for name, data in files.items():
        path = output_dir / name
        if path.is_symlink() or (path.exists() and path.read_bytes() != data):
            raise SmokeConflict("previous immutable review export differs")
    for name, data in files.items():
        path = output_dir / name
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise SmokeConflict("concurrent review export differs")
    fd = os.open(output_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return manifest
