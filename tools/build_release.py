"""Build/save Docker image without PowerShell binary pipes; validate before exporting."""
import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'__version__ = "([0-9.]+)"',
                    (ROOT/"submission/hu_bot/__init__.py").read_text()).group(1)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, **kwargs)


def source_archive():
    target = ROOT/f"dist/hu-bot-v{VERSION}-source.zip"
    target.parent.mkdir(exist_ok=True)
    paths = [ROOT/name for name in ("submission", "lab", "tests", "tools", "docs", "reports")]
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for root in paths:
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.write(path, path.relative_to(ROOT))
        for name in ("README.md", "requirements-dev.txt", "pyproject.toml", "THIRD_PARTY_NOTICES.md"):
            if (ROOT/name).exists():
                archive.write(ROOT/name, name)
    return target


def verify_existing_source(target):
    """An image-only retry must use precisely the already archived runtime."""
    with zipfile.ZipFile(target) as archive:
        members = [name for name in archive.namelist()
                   if name.startswith("submission/") and not name.endswith("/")]
        expected = {p.relative_to(ROOT).as_posix(): p.read_bytes()
                    for p in (ROOT/"submission").rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        if len(members) != len(set(members)) or set(members) != set(expected):
            raise RuntimeError("Existing source archive has a different runtime file set")
        if any(archive.read(name) != contents for name, contents in expected.items()):
            raise RuntimeError("Existing source archive does not match current runtime")


def main():
    parser = argparse.ArgumentParser()
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--source-only", action="store_true")
    kind.add_argument("--image-only", action="store_true",
                      help="Retry image packaging after completed tests; require an identical existing source ZIP")
    parser.add_argument("--tag", default=f"hu-bot:{VERSION}")
    args = parser.parse_args()
    if args.tag in {"hu-bot:1.0.0", "hu-bot:1.1.0"} and args.tag != f"hu-bot:{VERSION}":
        raise SystemExit("Refusing to overwrite a frozen release image tag")
    if args.source_only:
        print(source_archive())
        return
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    raw = dist/f"hu-bot-v{VERSION}.tar"
    target = dist/f"hu-bot-v{VERSION}.tar.gz"
    manifest_path = dist/f"manifest-v{VERSION}.json"
    source = dist/f"hu-bot-v{VERSION}-source.zip"
    if any(path.exists() for path in (raw, target, manifest_path)):
        raise SystemExit("Refusing to overwrite existing image artifacts for this version")
    if args.image_only:
        verify_existing_source(source)
    else:
        if source.exists():
            raise SystemExit("Source archive already exists; use --image-only only after completed validation")
        run(sys.executable, "-m", "pytest", "-q", "--import-mode=importlib")
        run(sys.executable, "-m", "chipzen", "validate", "submission", "--check-connectivity")
    run("docker", "build", "--platform", "linux/amd64", "-t", args.tag, "submission")
    metadata = json.loads(run("docker", "image", "inspect", args.tag,
                             capture_output=True, text=True).stdout)[0]
    if metadata["Size"] >= 200*1024*1024:
        raise RuntimeError("Image exceeds 200 MiB image cap")
    if metadata["Architecture"] != "amd64" or metadata["Os"] != "linux":
        raise RuntimeError("Expected linux/amd64")
    if not metadata["Config"].get("Entrypoint"):
        raise RuntimeError("Missing ENTRYPOINT")
    run("docker", "save", "-o", str(raw), args.tag)
    with raw.open("rb") as src, target.open("xb") as dst:
        with gzip.GzipFile(filename="", mode="wb", fileobj=dst, mtime=0) as compressed:
            shutil.copyfileobj(src, compressed)
    if target.stat().st_size >= 250*1024*1024:
        raise RuntimeError("Archive exceeds compressed upload cap")
    with tarfile.open(target, "r:gz") as archive:
        if "manifest.json" not in archive.getnames():
            raise RuntimeError("Not a docker-save archive")
    with target.open("rb") as f:
        checksum = hashlib.file_digest(f, "sha256").hexdigest()
    manifest = {"version": VERSION, "tag": args.tag, "image_id": metadata["Id"],
                "platform": "linux/amd64", "image_bytes": metadata["Size"],
                "archive": target.name, "archive_bytes": target.stat().st_size,
                "sha256": checksum,
                "sdk_version": "0.4.0", "sdk_reference_commit": "e6acc70f5a3247b6d345813d09e05b9c19aedf1e"}
    sys.path.insert(0, str(ROOT/"submission"))
    from hu_bot.config import Config
    manifest["default_config"] = asdict(Config())
    manifest["preflop_table_sha256"] = hashlib.sha256(
        (ROOT/"submission/hu_bot/preflop_tables.json").read_bytes()).hexdigest()
    manifest["range_table_sha256"] = hashlib.sha256(
        (ROOT/"submission/hu_bot/range_spec_v183.json").read_bytes()).hexdigest()
    if not args.image_only:
        source_archive()
    manifest["source_archive"] = source.name
    manifest["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2))
    raw.unlink()  # Exact intermediate archive created above, never a recursive deletion.
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
