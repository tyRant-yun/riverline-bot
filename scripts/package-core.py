"""Build the unchanged fixed core accepted by the Riverline JSONL adapter."""
from pathlib import Path
import hashlib
import json
import shutil

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'adapters/riverline/source.json').read_text())
target = root / 'build/core'
for name, digest in manifest['files'].items():
    source = root / 'submission' / name
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Fixed adapter core changed; publish a new version: ' + name)
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
target.mkdir(parents=True, exist_ok=True)
(target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print('Verified fixed core:', target)
