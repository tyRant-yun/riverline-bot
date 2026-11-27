"""Create a non-overwriting v2.2.2 source/image receipt."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.2.2"
TAG = "hu-bot:2.2.2"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, **kwargs)


def source_archive(path):
    roots = [ROOT / name for name in ("submission", "lab", "tests", "tools", "reports")]
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
        for root in roots:
            if not root.exists():
                continue
            for item in sorted(root.rglob("*")):
                if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
                    archive.write(item, item.relative_to(ROOT))
        for name in ("README.md", "requirements-dev.txt", "pyproject.toml", "THIRD_PARTY_NOTICES.md"):
            if (ROOT / name).exists():
                archive.write(ROOT / name, name)


def main():
    dist = ROOT / "dist"
    source = dist / "hu-bot-v2.2.2-final-r9-source.zip"
    raw = dist / "hu-bot-v2.2.2-final-r9.tar"
    target = dist / "hu-bot-v2.2.2-final-r9.tar.gz"
    manifest_path = dist / "manifest-v2.2.2-final-r9.json"
    for path in (source, raw, target, manifest_path):
        if path.exists():
            raise SystemExit(f"refusing to overwrite {path}")
    run(sys.executable, "-m", "chipzen", "validate", "submission", "--check-connectivity")
    run("docker", "build", "--platform", "linux/amd64", "-t", TAG, "submission")
    metadata = json.loads(run("docker", "image", "inspect", TAG,
                              capture_output=True, text=True).stdout)[0]
    run("docker", "save", "-o", str(raw), TAG)
    with raw.open("rb") as src, target.open("xb") as dst:
        with gzip.GzipFile(filename="", mode="wb", fileobj=dst, mtime=0) as compressed:
            shutil.copyfileobj(src, compressed)
    raw.unlink()
    source_archive(source)
    manifest = {
        "version": VERSION, "package_label": "final-r9", "tag": TAG,
        "image_id": metadata["Id"], "platform": "linux/amd64",
        "image_bytes": metadata["Size"], "archive": target.name,
        "archive_bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "sdk_version": "0.4.0", "candidate_root": ".work/hu-v2-strength-v2.2.2",
        "runtime_path": "submission/hu_bot/v2_bot.py", "source_archive": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "uploaded": False, "platform_active": False,
        "status": {
            "implemented": True, "self_tested": True,
            "independently_correct": "pending_independent_acceptance",
            "deadline_local": "endpoint_observed_not_platform_sla",
            "action_value_pass": False, "match_strength": False, "packaged": True,
        },
        "limits": [
            "PDF pressure-test range is not matched-event GTO",
            "candidate Q is model-limited and not an action-value proof",
            "profile comparisons and platform settlement remain unverified",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
