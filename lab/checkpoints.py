"""Atomic F02 checkpoints for complete, fingerprint-locked session pairs."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from time import perf_counter
from typing import Any, Callable, Iterable


CONDITIONS = {"depth_bb": 100, "ante": 0, "reset_stacks_each_hand": True}
UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
FROZEN = {"v1.0.0", "v1.1.0"}


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path, directory: str) -> dict[str, str]:
    base = root / directory
    if not base.is_dir():
        raise ValueError(f"missing fingerprint directory: {base}")
    return {p.relative_to(root).as_posix(): _file_sha(p)
            for p in sorted(base.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def _opponent_source(root: Path, name: str, lab: dict[str, str]) -> dict[str, Any]:
    from lab.profiles import STYLES
    if name in STYLES:
        return {"kind": "script", "name": name,
                "files": {key: lab[key] for key in ("lab/match.py", "lab/profiles.py")}}
    if name in FROZEN:
        directory = f".reference/hu-{name}/submission"
        if not (root / directory).is_dir():
            raise ValueError(f"frozen opponent source unavailable: {root / directory}")
        return {"kind": "frozen", "name": name, "files": _files(root, directory)}
    raise ValueError(f"unsupported opponent: {name}")


def source_fingerprints(root: str | Path | None = None,
                        opponents: Iterable[str] = ()) -> dict[str, Any]:
    """Hash all worker source, actual installed versions and selected opponents."""
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    runtime, lab = _files(root, "submission"), _files(root, "lab")
    requirements = {name: _file_sha(root / name) for name in
                    ("requirements-dev.txt", "submission/requirements.txt", "pyproject.toml")}
    installed = [list(item) for item in sorted((dist.metadata["Name"].lower(), dist.version)
                                              for dist in metadata.distributions())]
    return {"runtime_sha256": _sha(runtime), "runtime_files": runtime,
            "lab_sha256": _sha(lab), "lab_files": lab,
            "evaluation_sha256": lab["lab/sessions.py"],
            "tables_models": {"preflop_tables.json": runtime["submission/hu_bot/preflop_tables.json"]},
            "dependencies": {"requirements": requirements, "installed": installed,
                             "python": {"version": sys.version,
                                        "implementation": platform.python_implementation(),
                                        "architecture": platform.architecture()[0],
                                        "platform": sys.platform}},
            "opponent_sources": {name: _opponent_source(root, name, lab)
                                 for name in sorted(set(opponents))}}


def _mode_config(modes: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    from lab.sessions import MODES, configuration
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= set(MODES):
        raise ValueError("modes must be a nonempty distinct subset of session modes")
    return {mode: asdict(configuration(mode)) for mode in modes}


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    units: tuple[dict[str, Any], ...]
    worker: dict[str, Any]
    mode_config: dict[str, dict[str, Any]]
    rules: dict[str, Any]

    @classmethod
    def create(cls, protocol_id: str, units: Iterable[dict[str, Any]], *,
               root: str | Path | None = None, rules: dict[str, Any] | None = None):
        if not protocol_id or not isinstance(protocol_id, str):
            raise ValueError("protocol_id required")
        normalized = tuple(sorted((dict(unit) for unit in units), key=lambda x: x["unit_id"]))
        ids = [unit["unit_id"] for unit in normalized]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("expected unit ids must be nonempty and unique")
        required = {"unit_id", "opponent", "seed", "hands", "modes", "seats", "session_offset"}
        modes = tuple(normalized[0]["modes"])
        configs = _mode_config(modes)
        for unit in normalized:
            if set(unit) != required or not UNIT_ID.fullmatch(unit["unit_id"]) or unit["unit_id"] in {".", ".."}:
                raise ValueError("unit fields/id invalid")
            if (tuple(unit["modes"]) != modes or tuple(unit["seats"]) != (0, 1)
                    or type(unit["seed"]) is not int or type(unit["hands"]) is not int
                    or type(unit["session_offset"]) is not int or unit["hands"] < 1
                    or unit["session_offset"] < 0):
                raise ValueError("unit must declare all modes, both seats, seed, hands and offset")
        if len({(unit["opponent"], unit["seed"], unit["session_offset"])
                for unit in normalized}) != len(normalized):
            raise ValueError("duplicate opponent/seed/session mapping")
        if rules is not None and rules != CONDITIONS:
            raise ValueError("current evaluator only supports fixed 100BB, ante=0 cash conditions")
        worker = source_fingerprints(root, (unit["opponent"] for unit in normalized))
        return cls(protocol_id, normalized, worker, configs, dict(CONDITIONS))

    def as_dict(self) -> dict[str, Any]:
        return {"protocol_id": self.protocol_id, "units": list(self.units),
                "worker": self.worker, "mode_config": self.mode_config, "rules": self.rules}

    @property
    def fingerprint(self) -> str:
        return _sha(self.as_dict())

    def unit(self, unit_id: str) -> dict[str, Any]:
        for unit in self.units:
            if unit["unit_id"] == unit_id:
                return unit
        raise KeyError(f"unknown unit_id: {unit_id}")


@contextmanager
def _file_lock(path: Path):
    """OS file lock auto-released on process exit; leave the lock file in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            handle.seek(0)
            first_byte = handle.read(1)
        except OSError as exc:
            raise ValueError(f"checkpoint lock busy: {path}") from exc
        if not first_byte:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError(f"checkpoint lock busy: {path}") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError(f"checkpoint lock busy: {path}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_new(path: Path, value: Any) -> None:
    """Same-directory temp + atomic exclusive hard link, never replace a result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, path)
        except FileExistsError as exc:
            raise ValueError(f"refusing to overwrite existing checkpoint: {path}") from exc
    finally:
        Path(name).unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable checkpoint: {path}") from exc


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _validate_result(result: dict[str, Any], unit: dict[str, Any], protocol: Protocol) -> None:
    """Validate actual evaluate_sessions fields, without supplying any defaults."""
    from hu_bot import __version__
    modes = tuple(unit["modes"])
    expected = {"version": __version__, "opponent": unit["opponent"],
                "seed": unit["seed"], "sessions": 1,
                "session_indices": [unit["session_offset"]],
                "hands_per_rotation": unit["hands"], "hands_per_mode": 2 * unit["hands"],
                "total_hands": 2 * unit["hands"] * len(modes),
                "conditions": protocol.rules, "mode_config": protocol.mode_config}
    if not isinstance(result, dict):
        raise ValueError("evaluator must return a result object")
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError(f"invalid or missing result field: {key}")
    scores, summaries, diagnostics = (result.get(key) for key in
                                      ("session_pair_bb_per_100", "bb_per_100", "diagnostics"))
    if not all(isinstance(x, dict) and set(x) == set(modes)
               for x in (scores, summaries, diagnostics)):
        raise ValueError("scores/summary/diagnostics mode keys mismatch")
    for mode in modes:
        score = scores[mode]
        if not isinstance(score, list) or len(score) != 1 or not _finite(score[0]):
            raise ValueError(f"incomplete score for mode {mode}")
        summary = summaries[mode]
        if (not isinstance(summary, dict) or not _finite(summary.get("mean"))
                or not math.isclose(summary["mean"], score[0], rel_tol=1e-10, abs_tol=1e-10)
                or summary.get("normal_95_ci", "missing") is not None):
            raise ValueError(f"invalid score summary for mode {mode}")
        diag = diagnostics[mode]
        if not isinstance(diag, dict) or any(
                not isinstance(diag.get(key), list) or len(diag[key]) != 2
                for key in ("end_max_opportunities", "end_response_opportunities")):
            raise ValueError(f"incomplete seat diagnostics for mode {mode}")
        if (not isinstance(diag.get("window_hands"), list)
                or len(diag["window_hands"]) != 3
                or sum(diag["window_hands"]) != 2 * unit["hands"]):
            raise ValueError(f"incomplete hand diagnostics for mode {mode}")
    if not _finite(result.get("elapsed_seconds")) or result["elapsed_seconds"] < 0:
        raise ValueError("invalid worker elapsed_seconds")
    contrasts = result.get("paired_adaptation_gain_bb_per_100")
    keys = {mode + "_minus_frozen" for mode in modes if mode != "frozen"} if "frozen" in modes else set()
    if not isinstance(contrasts, dict) or set(contrasts) != keys:
        raise ValueError("paired contrast keys mismatch")
    for mode in modes:
        if mode == "frozen" or "frozen" not in modes:
            continue
        summary = contrasts[mode + "_minus_frozen"]
        difference = scores[mode][0] - scores["frozen"][0]
        if (not isinstance(summary, dict) or not _finite(summary.get("mean"))
                or not math.isclose(summary["mean"], difference, rel_tol=1e-10, abs_tol=1e-10)
                or summary.get("normal_95_ci", "missing") is not None):
            raise ValueError("invalid paired contrast summary")


class CheckpointStore:
    """Complete pair units, failure history and final coverage are separate."""

    def __init__(self, root: str | Path, protocol: Protocol,
                 *, worker_root: str | Path | None = None):
        self.root, self.protocol = Path(root), protocol
        self.worker_root = Path(worker_root or Path(__file__).resolve().parents[1])
        self.committed = self.root / "committed"
        self.failures = self.root / "failures"
        self.locks = self.root / "locks"
        self.root.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.root / ".protocol.lock"):
            path = self.root / "protocol.json"
            expected = {**protocol.as_dict(), "fingerprint": protocol.fingerprint}
            if path.exists():
                if _read_json(path) != expected:
                    raise ValueError("existing protocol.json fingerprint/config mismatch")
            else:
                _write_new(path, expected)
            for directory in (self.committed, self.failures, self.locks):
                directory.mkdir(exist_ok=True)

    def _verify_protocol(self):
        expected = {**self.protocol.as_dict(), "fingerprint": self.protocol.fingerprint}
        if _read_json(self.root / "protocol.json") != expected:
            raise ValueError("protocol.json changed after initialization")

    def _verify_worker(self):
        opponents = (unit["opponent"] for unit in self.protocol.units)
        if (source_fingerprints(self.worker_root, opponents) != self.protocol.worker
                or _mode_config(tuple(self.protocol.units[0]["modes"])) != self.protocol.mode_config):
            raise ValueError("worker source/config/dependency fingerprint mismatch")

    def _lock(self, unit_id: str):
        self.protocol.unit(unit_id)
        return _file_lock(self.locks / f"{unit_id}.lock")

    def _envelope(self, unit_id: str, payload: dict[str, Any],
                  kind: str, wall_seconds: float) -> dict[str, Any]:
        return {"kind": kind, "protocol_id": self.protocol.protocol_id,
                "protocol_fingerprint": self.protocol.fingerprint,
                "unit_id": unit_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "worker_wall_seconds": wall_seconds, "payload": payload,
                "payload_sha256": _sha(payload)}

    def _validated_envelope(self, envelope: Any, unit_id: str, kind: str) -> dict[str, Any]:
        if not isinstance(envelope, dict) or any(envelope.get(key) != value for key, value in {
                "kind": kind, "protocol_id": self.protocol.protocol_id,
                "protocol_fingerprint": self.protocol.fingerprint, "unit_id": unit_id}.items()):
            raise ValueError(f"checkpoint identity mismatch: {unit_id}")
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or envelope.get("payload_sha256") != _sha(payload):
            raise ValueError(f"corrupt checkpoint payload: {unit_id}")
        if not _finite(envelope.get("worker_wall_seconds")) or envelope["worker_wall_seconds"] < 0:
            raise ValueError(f"invalid checkpoint elapsed: {unit_id}")
        if kind == "result":
            _validate_result(payload, self.protocol.unit(unit_id), self.protocol)
        elif payload.get("unit_id") != unit_id or not isinstance(payload.get("error"), dict):
            raise ValueError(f"invalid failure record: {unit_id}")
        return payload

    def _commit_locked(self, unit_id: str, result: dict[str, Any], wall_seconds: float) -> Path:
        self._verify_protocol()
        self._verify_worker()
        if not _finite(wall_seconds) or wall_seconds < 0:
            raise ValueError("invalid worker elapsed time")
        _validate_result(result, self.protocol.unit(unit_id), self.protocol)
        path = self.committed / f"{unit_id}.json"
        _write_new(path, self._envelope(unit_id, result, "result", wall_seconds))
        return path

    def commit(self, unit_id: str, result: dict[str, Any], *,
               fingerprint: str | None = None, wall_seconds: float = 0.0) -> Path:
        if fingerprint is not None and fingerprint != self.protocol.fingerprint:
            raise ValueError("protocol fingerprint mismatch")
        with self._lock(unit_id):
            return self._commit_locked(unit_id, result, wall_seconds)

    def _record_failure_locked(self, unit_id: str, error: dict[str, Any], wall_seconds: float) -> Path:
        directory = self.failures / unit_id
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob("attempt-*.json"))
        for path in existing:
            self._validated_envelope(_read_json(path), unit_id, "failure")
        path = directory / f"attempt-{len(existing)+1:04d}.json"
        _write_new(path, self._envelope(unit_id, {"unit_id": unit_id, "error": error},
                                        "failure", wall_seconds))
        return path

    def record_failure(self, unit_id: str, error: dict[str, Any], *,
                       fingerprint: str | None = None) -> Path:
        self._verify_protocol()
        if fingerprint is not None and fingerprint != self.protocol.fingerprint:
            raise ValueError("protocol fingerprint mismatch")
        with self._lock(unit_id):
            if (self.committed / f"{unit_id}.json").exists():
                raise ValueError(f"unit already submitted: {unit_id}")
            return self._record_failure_locked(unit_id, error, 0.0)

    def load(self, unit_id: str) -> dict[str, Any]:
        self._verify_protocol()
        self.protocol.unit(unit_id)
        path = self.committed / f"{unit_id}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        return self._validated_envelope(_read_json(path), unit_id, "result")

    def summary(self) -> dict[str, Any]:
        self._verify_protocol()
        expected = {unit["unit_id"] for unit in self.protocol.units}
        committed = {path.stem for path in self.committed.glob("*.json")}
        failed = {path.name for path in self.failures.iterdir() if path.is_dir()}
        if not committed <= expected or not failed <= expected:
            raise ValueError("unknown checkpoint unit")
        for unit_id in committed:
            self.load(unit_id)
        for unit_id in failed:
            for path in (self.failures / unit_id).glob("attempt-*.json"):
                self._validated_envelope(_read_json(path), unit_id, "failure")
        return {"protocol_id": self.protocol.protocol_id,
                "protocol_fingerprint": self.protocol.fingerprint,
                "expected_units": sorted(expected), "committed_units": sorted(committed),
                "failed_units": sorted(failed - committed),
                "failure_history_units": sorted(failed),
                "missing_units": sorted(expected - committed - failed),
                "complete": expected == committed,
                "closed": not (expected - committed - failed)}

    def finalize(self) -> dict[str, Any]:
        with _file_lock(self.root / ".finalize.lock"):
            result = self.summary()
            if not result["complete"]:
                raise ValueError("cannot finalize with missing or failed units")
            result["worker_elapsed_seconds"] = sum(
                _read_json(self.committed / f"{unit_id}.json")["worker_wall_seconds"]
                for unit_id in result["committed_units"])
            path = self.root / "finalized.json"
            if path.exists():
                if _read_json(path) != result:
                    raise ValueError("existing finalized.json differs")
            else:
                _write_new(path, result)
            return result


def run_session_pair(store: CheckpointStore, unit_id: str, *,
                     evaluator: Callable[..., dict[str, Any]] | None = None) -> Path:
    """Skip a validated complete pair; record exceptions; retry failed IDs."""
    unit = store.protocol.unit(unit_id)
    if evaluator is None:
        from lab.sessions import evaluate_sessions
        evaluator = evaluate_sessions
    with store._lock(unit_id):
        store._verify_protocol()
        path = store.committed / f"{unit_id}.json"
        if path.exists():
            store.load(unit_id)
            return path
        start = perf_counter()
        try:
            store._verify_worker()
            result = evaluator(opponent=unit["opponent"], sessions=1, hands=unit["hands"],
                               seed=unit["seed"], modes=tuple(unit["modes"]),
                               session_offset=unit["session_offset"])
            return store._commit_locked(unit_id, result, perf_counter() - start)
        except BaseException as exc:
            store._record_failure_locked(unit_id, {"type": type(exc).__name__,
                                                   "message": str(exc)}, perf_counter() - start)
            raise
