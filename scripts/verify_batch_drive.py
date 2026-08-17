from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

CONFIG = "/home/ubuntu/.config/rclone/rclone.conf"
ROOT = "1upVRLwqDo1agfYwXekrQP2-2PeKGzvN4"
BASE = Path("/tmp/lesson-batch-preview-20260809")
SPECS = (
    ("01", "01. 20260308", "01-20260308"),
    ("02", "02. 20260315", "02-20260315"),
    ("03", "03. 20260322", "03-20260322"),
    ("04", "04. 20260329", "04-20260329"),
    ("05", "05. 20260405", "05-20260405"),
    ("06", "06. 20260412", "06-20260412"),
    ("07", "07. 20260419", "07-20260419"),
    ("08", "08. 20260426", "08-20260426"),
    ("09", "09. 20260510", "09-20260510"),
    ("10", "10. 20260524", "10-20260524"),
    ("11", "11. 20260531", "11-20260531"),
    ("12", "12. 20260607", "12-20260607"),
    ("13", "13. 20260614", "13-20260614"),
    ("14", "14. 20260621", "14-20260621"),
    ("15", "15. 20260628", "15-20260628"),
    ("16", "16. 20260705", "20260705"),
)


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    failures: list[str] = []
    verified = 0
    for idx, folder, stem in SPECS:
        result = subprocess.run(
            [
                "sudo",
                "rclone",
                "--config",
                CONFIG,
                f"--drive-root-folder-id={ROOT}",
                "lsjson",
                f"gdrive:{folder}",
                "--hash",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        remote = {item["Name"]: item for item in json.loads(result.stdout)}
        for ext, local_name in (("srt", "subtitles-preview-dedup.srt"), ("txt", "transcript-preview-dedup.txt"), ("json", "subtitles-preview-dedup.json")):
            name = f"{stem}.{ext}"
            local = BASE / idx / local_name
            item = remote.get(name)
            local_hash = md5(local)
            remote_hash = (item or {}).get("Hashes", {}).get("md5")
            ok = bool(item) and int(item["Size"]) == local.stat().st_size and remote_hash == local_hash
            print(f"{'PASS' if ok else 'FAIL'} {idx} {name} size={item.get('Size') if item else None} md5={remote_hash}")
            verified += int(ok)
            if not ok:
                failures.append(f"{idx}/{name}")
    print(f"VERIFY={'PASS' if not failures else 'FAIL'} files={verified}/48")
    if failures:
        print("FAILURES=" + ",".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
