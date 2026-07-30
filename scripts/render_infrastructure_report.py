"""Merge safe rclone output with container test results into the final report."""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/course-transcript")
LOGS = ROOT / "logs"
result = json.loads((LOGS / "infrastructure-test-results.json").read_text(encoding="utf-8"))
rclone = json.loads((LOGS / "rclone-readonly-result.json").read_text(encoding="utf-8"))
versions = {}
for name, command in {
    "Python": ["python3", "--version"],
    "Docker": ["docker", "--version"],
    "Docker Compose": ["docker", "compose", "version"],
    "FFmpeg": ["ffmpeg", "-version"],
    "FFprobe": ["ffprobe", "-version"],
    "rclone": ["rclone", "version"],
}.items():
    versions[name] = subprocess.check_output(command, text=True).splitlines()[0]
checks = result["checks"] + [{"name": rclone["name"], "status": rclone["status"], "detail": f"{rclone['detail']} (item count: {rclone['item_count']}).", "error": None}]
context = result["context"]
lines = ["# Course Transcript MVP — Infrastructure Test Report", "", f"- Executed (UTC): {datetime.now(UTC).isoformat()}", "- OS / architecture: Ubuntu 24.04 / aarch64"]
lines.extend(f"- {name}: {value}" for name, value in versions.items())
lines.extend([f"- Gemini model / location: {context['vertex_model']} / {context['vertex_location']}", f"- Speech model / location: chirp_3 / {context['speech_location']}", "", "## Results", "", "| Test | Status | Detail |", "|---|---|---|"])
lines.extend(f"| {row['name']} | {row['status']} | {row['detail']} |" for row in checks)
lines.extend(["", "## Errors and remediation", ""])
failures = [row for row in checks if row["status"] != "PASS"]
if failures:
    for row in failures:
        lines.extend([f"### {row['name']}", "", f"- Error: {row.get('error') or 'See service log.'}", "- Remediation: inspect the reported API/IAM/configuration issue; no automated IAM, firewall, or public-service change was attempted.", ""])
else:
    lines.append("No failures occurred.\n")
lines.extend([
    "## Diagnostic history",
    "",
    "- Initial Vertex request returned no text because an 8-token output cap was too small; the cap was raised to 64 and the exact VERTEX_OK response passed.",
    "- Initial Speech request used global and Chirp 3 was unavailable there; the test was moved to us with the us-speech.googleapis.com endpoint and passed.",
    "- A first rclone attempt ran as root and could not see the ubuntu gdrive configuration; the final read-only check ran as ubuntu and passed.",
    "",
])
lines.extend(["## Next step", "", "Do not proceed to the full MVP without user approval. The next approved stage should scaffold the single-file transcription pipeline and run a real 5-minute media test."])
(LOGS / "infrastructure-test-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
