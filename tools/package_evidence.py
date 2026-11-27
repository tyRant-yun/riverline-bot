"""Bundle development receipts without repacking the frozen runtime archive."""

from pathlib import Path
import hashlib
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]


def run():
    target = ROOT / "dist/hu-bot-v1.9.1-evidence.zip"
    if target.exists():
        raise FileExistsError(target)
    sources = [(p, "candidate/" + p.relative_to(ROOT).as_posix())
               for p in sorted((ROOT / "reports").rglob("*")) if p.is_file()]
    sources += [(ROOT / "dist/manifest-v1.9.1.json", "candidate/dist/manifest-v1.9.1.json"),
                (WORKSPACE / "reports/upgrades/HU-RIVER-V191/RESULTS.md",
                 "workspace/reports/upgrades/HU-RIVER-V191/RESULTS.md"),
                (WORKSPACE / "docs/PROJECT-STATE.md", "workspace/docs/PROJECT-STATE.md"),
                (WORKSPACE / "docs/OPEN-UPGRADES.md", "workspace/docs/OPEN-UPGRADES.md"),
                (WORKSPACE / "AGENTS.md", "workspace/AGENTS.md")]
    index = {name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path, name in sources}
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for path, name in sources:
            archive.write(path, name)
        archive.writestr("INDEX-SHA256.json", json.dumps(index, indent=2) + "\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8")
    return {"evidence_zip": str(target), "sha256": digest,
            "files": len(index), "bytes": target.stat().st_size}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
