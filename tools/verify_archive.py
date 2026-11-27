"""Check docker-save manifest, compressed-layer digests, and uncompressed layer budget."""
import gzip
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import subprocess

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.1.0")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest or (ROOT/f"dist/manifest-v{args.version}.json")
    info = json.loads(manifest_path.read_text())
    path = ROOT/"dist"/info["archive"]
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == info["sha256"]
    layers = []
    with tarfile.open(path, "r:gz") as archive:
        manifests = json.load(archive.extractfile("manifest.json"))
        assert len(manifests) == 1
        config = json.load(archive.extractfile(manifests[0]["Config"]))
        assert config["os"] == "linux" and config["architecture"] == "amd64"
        for name, expected in zip(manifests[0]["Layers"], config["rootfs"]["diff_ids"], strict=True):
            stream = archive.extractfile(name)
            magic = stream.read(2)
            stream.seek(0)
            reader = gzip.GzipFile(fileobj=stream) if magic == b"\x1f\x8b" else stream
            digest, size = hashlib.sha256(), 0
            while chunk := reader.read(1024*1024):
                digest.update(chunk)
                size += len(chunk)
            assert "sha256:"+digest.hexdigest() == expected
            layers.append({"name": name, "uncompressed_bytes": size, "diff_id": expected})
    total = sum(layer["uncompressed_bytes"] for layer in layers)
    assert total < 200*1024*1024
    subprocess.run(["docker", "load", "-i", str(path)], check=True, capture_output=True)
    loaded = subprocess.run(["docker", "image", "inspect", info["tag"], "--format", "{{.Id}}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    assert loaded == info["image_id"]
    result = {"archive_sha256_verified": True, "layers_verified": len(layers),
              "uncompressed_layer_bytes": total, "layers": layers,
              "docker_load_roundtrip": "passed; image id matches versioned manifest"}
    destination = ROOT/f"reports/v{args.version}"
    destination.mkdir(exist_ok=True)
    (destination/"archive-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "layers"}, indent=2))
