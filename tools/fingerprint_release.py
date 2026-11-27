"""Freeze reproducible source/table and inherited-baseline fingerprints."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from hu_bot import __version__
from hu_bot.config import Config

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT.parent / "hu-stable-v1.9.0"
UPLOADED = ROOT.parent / "hu-history-v1.8.4"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root):
    files = {path.relative_to(root).as_posix(): sha(path)
             for path in sorted((root / "submission").rglob("*"))
             if path.is_file() and "__pycache__" not in path.parts
             and path.suffix != ".pyc"}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True,
                            separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "files": files}


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    manifest_path = BASELINE / "dist/manifest-v1.9.0.json"
    baseline_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    uploaded_manifest_path = UPLOADED / "dist/manifest-v1.8.4.json"
    uploaded_manifest = json.loads(uploaded_manifest_path.read_text(encoding="utf-8"))
    receipt = {"version": __version__,
               "candidate_root": str(ROOT), "baseline_root": str(BASELINE),
               "candidate_source": tree(ROOT), "baseline_source": tree(BASELINE),
               "baseline_manifest_sha256": sha(manifest_path),
               "baseline_image_sha256": baseline_manifest["sha256"],
               "uploaded_comparator_root": str(UPLOADED),
               "uploaded_comparator_source": tree(UPLOADED),
               "uploaded_comparator_manifest_sha256": sha(uploaded_manifest_path),
               "uploaded_comparator_image_sha256": uploaded_manifest["sha256"],
               "candidate_config_defaults": asdict(Config()),
               "baseline_independent_acceptance": "engineering_passed_new_strategies_not_promoted",
               "baseline_independent_receipt": str(
                   ROOT.parents[1] / "reports/upgrades/HU-STABLE-V190/INDEPENDENT-ACCEPTANCE.md"),
               "uploaded": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {"version": __version__,
            "baseline_source_sha256": receipt["baseline_source"]["sha256"],
            "candidate_source_sha256": receipt["candidate_source"]["sha256"],
            "old_table_sha256": receipt["candidate_source"]["files"][
                "submission/hu_bot/preflop_tables.json"],
            "range_table_sha256": receipt["candidate_source"]["files"][
                "submission/hu_bot/range_spec_v183.json"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))
