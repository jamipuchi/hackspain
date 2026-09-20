#!/usr/bin/env python3
"""Reset CINTA to its deployed built-in bundle and archive the current jobs."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

COFFEE_ROOT = Path(__file__).resolve().parents[2] / "sim" / "coffee_sorter"
sys.path.insert(0, str(COFFEE_ROOT))

import object_catalog


DEFAULT_ITEM_ROOT = Path("/srv/hackspain-coffee/item-control")
SEED_MARKER = "seed-transaction.json"
BACKUPS = "reset-backups"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BACKUP_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
ALLOWED_ROOT_NAMES = {
    "active", "history", "provider-cache", BACKUPS,
}
ALLOWED_HISTORY_NAMES = {
    "activations.jsonl", "jobs", "training.lease", "wall-of-fame", "writer.lock",
}


class ResetError(RuntimeError):
    """The item-control layout or reset transaction is unsafe."""


def _regular_file(path: Path, name: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ResetError(f"{name} must be a regular file")


def _directory(path: Path, name: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ResetError(f"{name} must be a directory")


def _json(path: Path, name: str) -> dict:
    _regular_file(path, name)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ResetError(f"{name} must contain JSON") from error
    if not isinstance(value, dict):
        raise ResetError(f"{name} must contain an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_layout(root: Path) -> dict:
    _directory(root, "item-control root")
    unknown = sorted(path.name for path in root.iterdir()
                     if path.name not in ALLOWED_ROOT_NAMES)
    if unknown:
        raise ResetError(f"item-control root has unknown entries: {', '.join(unknown)}")

    active_root = root / "active"
    history = root / "history"
    jobs = history / "jobs"
    provider_cache = root / "provider-cache"
    writer_lock = history / "writer.lock"
    for path, name in ((active_root, "active root"), (jobs, "jobs root"),
                       (history, "history root"),
                       (provider_cache, "provider-cache root")):
        _directory(path, name)
    _regular_file(writer_lock, "writer lock")
    unknown_history = sorted(path.name for path in history.iterdir()
                             if path.name not in ALLOWED_HISTORY_NAMES)
    if unknown_history:
        raise ResetError(f"history root has unknown entries: {', '.join(unknown_history)}")
    lease = history / "training.lease"
    if lease.exists():
        _regular_file(lease, "training lease")
    activations = history / "activations.jsonl"
    if activations.exists():
        _regular_file(activations, "activation history")
    wall = history / "wall-of-fame"
    if wall.exists():
        _directory(wall, "Wall of Fame root")

    expected_active = {"active", "bundles", SEED_MARKER}
    actual_active = {path.name for path in active_root.iterdir()}
    if actual_active != expected_active:
        raise ResetError("active root does not match the deployed layout")
    _directory(active_root / "active", "active pointer directory")
    if {path.name for path in (active_root / "active").iterdir()} != {"catalog.json"}:
        raise ResetError("active pointer directory has unknown entries")
    _directory(active_root / "bundles", "bundle root")

    marker = _json(active_root / SEED_MARKER, "seed marker")
    if set(marker) != {"bundle_sha256"} or not SHA256.fullmatch(
            str(marker.get("bundle_sha256", ""))):
        raise ResetError("seed marker must name one bundle sha256")
    baseline_sha = marker["bundle_sha256"]
    baseline_bundle = active_root / "bundles" / baseline_sha
    object_catalog.verify_bundle(baseline_bundle)
    baseline_catalog = object_catalog.load_catalog(baseline_bundle / "catalog")
    if any(definition["provenance"]["kind"] != "builtin"
           for definition in baseline_catalog["definitions"]):
        raise ResetError("seed bundle is not the built-in catalog")

    current = object_catalog.read_active(active_root)
    current_sha = current.get("active_bundle_sha256")
    if not isinstance(current_sha, str) or not SHA256.fullmatch(current_sha):
        raise ResetError("active pointer does not name one bundle sha256")
    object_catalog.verify_bundle(active_root / "bundles" / current_sha)

    backups = root / BACKUPS
    if backups.exists():
        _directory(backups, "reset backup root")
        unexpected = sorted(path.name for path in backups.iterdir()
                            if path.is_symlink() or not path.is_dir()
                            or not BACKUP_NAME.fullmatch(path.name))
        if unexpected:
            raise ResetError("reset backup root has unknown entries")

    return {
        "root": root,
        "active_root": active_root,
        "jobs": jobs,
        "history": history,
        "provider_cache": provider_cache,
        "writer_lock": writer_lock,
        "training_lease": lease,
        "baseline_sha": baseline_sha,
        "current_sha": current_sha,
        "pointer": active_root / "active" / "catalog.json",
        "marker": active_root / SEED_MARKER,
        "backups": backups,
    }


def _generated_archive_plan(layout: dict) -> list[dict]:
    catalog = object_catalog.read_active(layout["active_root"])
    generated = [definition for definition in catalog["definitions"]
                 if definition["provenance"]["kind"] == "generated"]
    if not generated:
        return []
    current_bundle = layout["active_root"] / "bundles" / layout["current_sha"]
    model_manifests = sorted((current_bundle / "model").glob("*.manifest.json"))
    if len(model_manifests) != 1:
        raise ResetError("active generated types require one bundle model manifest")
    plans = []
    for definition in generated:
        type_id = definition["object_type_id"]
        matches = []
        for job in sorted(layout["jobs"].iterdir()):
            if not job.is_dir() or job.is_symlink():
                continue
            candidate = job / "definition.json"
            try:
                value = json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("object_type_id") == type_id:
                matches.append(job)
        if len(matches) != 1:
            raise ResetError(
                f"active generated type {type_id} must have one retained source job")
        job = matches[0]
        evidence = {
            "object.glb": job / "previews" / "object.glb",
            "perspective.png": job / "previews" / "perspective.png",
            "top.png": job / "previews" / "top.png",
            "model.manifest.json": model_manifests[0],
        }
        for name, path in evidence.items():
            _regular_file(path, f"archive evidence {name}")
        plans.append({"definition": definition, "evidence": evidence})
    return plans


def _validate_archive_plan(layout: dict, plans: list[dict], retired_at: str) -> None:
    wall = layout["history"] / "wall-of-fame"
    if wall.exists():
        _directory(wall, "Wall of Fame root")
    with tempfile.TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        for plan in plans:
            type_id = plan["definition"]["object_type_id"]
            existing = wall / type_id
            if existing.exists():
                _directory(existing, f"Wall of Fame entry {type_id}")
                record = _json(existing / "archive.json", "Wall of Fame archive")
                recorded_at = record.get("retired_at")
                if not isinstance(recorded_at, str):
                    raise ResetError(f"Wall of Fame entry {type_id} has no retirement time")
                object_catalog.archive_type(
                    layout["history"], plan["definition"], recorded_at, plan["evidence"])
            else:
                object_catalog.archive_type(
                    scratch, plan["definition"], retired_at, plan["evidence"])


def _stage_archives(backup: Path, plans: list[dict], retired_at: str) -> Path:
    staged = backup / "generated-history"
    for plan in plans:
        object_catalog.archive_type(
            staged, plan["definition"], retired_at, plan["evidence"])
    return staged


def _publish_archives(layout: dict, staged: Path) -> None:
    staged_wall = staged / "wall-of-fame"
    if not staged_wall.is_dir():
        return
    wall = layout["history"] / "wall-of-fame"
    wall.mkdir(exist_ok=True)
    for source in sorted(staged_wall.iterdir()):
        target = wall / source.name
        if target.exists():
            continue
        os.replace(source, target)


def _backup_name() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: dict) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".tmp-",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file(source: Path, target: Path) -> None:
    target.write_bytes(source.read_bytes())
    os.chmod(target, stat.S_IMODE(source.stat().st_mode))


def _apply(layout: dict, plans: list[dict], retired_at: str) -> Path:
    root = layout["root"]
    backups = layout["backups"]
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = backups / _backup_name()
    backup.mkdir(mode=0o700)
    old_jobs = backup / "jobs"
    jobs = layout["jobs"]
    jobs_stat = jobs.stat()
    jobs_moved = False
    try:
        _copy_file(layout["pointer"], backup / "active.catalog.json")
        _copy_file(layout["marker"], backup / SEED_MARKER)
        _write_json(backup / "reset.json", {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "previous_bundle_sha256": layout["current_sha"],
            "baseline_bundle_sha256": layout["baseline_sha"],
            "active_pointer_sha256": _file_sha256(layout["pointer"]),
            "seed_marker_sha256": _file_sha256(layout["marker"]),
            "archived_active_type_ids": [
                plan["definition"]["object_type_id"] for plan in plans],
        })
        staged_archives = _stage_archives(backup, plans, retired_at)
        if layout["training_lease"].exists():
            _copy_file(layout["training_lease"], backup / "training.lease")
        os.replace(jobs, old_jobs)
        jobs_moved = True
        jobs.mkdir(mode=stat.S_IMODE(jobs_stat.st_mode))
        os.chown(jobs, jobs_stat.st_uid, jobs_stat.st_gid)
        layout["training_lease"].unlink(missing_ok=True)
        object_catalog.write_active_pointer(layout["active_root"], layout["baseline_sha"])
        _publish_archives(layout, staged_archives)
        return backup
    except BaseException as error:
        rollback = []
        try:
            current = object_catalog.read_active(layout["active_root"])
            pointer_sha = current.get("active_bundle_sha256")
        except BaseException as inspect_error:
            rollback.append(f"pointer inspection failed: {inspect_error}")
        else:
            if pointer_sha != layout["current_sha"]:
                try:
                    object_catalog.write_active_pointer(
                        layout["active_root"], layout["current_sha"])
                    restored = object_catalog.read_active(layout["active_root"])
                    if restored.get("active_bundle_sha256") != layout["current_sha"]:
                        raise ResetError("pointer restoration did not select the prior bundle")
                except BaseException as rollback_error:
                    rollback.append(f"pointer rollback failed: {rollback_error}")
        if jobs_moved:
            try:
                jobs.rmdir()
                os.replace(old_jobs, jobs)
                if (backup / "training.lease").is_file():
                    os.replace(backup / "training.lease", layout["training_lease"])
            except BaseException as rollback_error:
                rollback.append(f"jobs rollback failed: {rollback_error}")
        if rollback:
            raise ResetError(f"reset failed: {error}; {'; '.join(rollback)}") from error
        shutil.rmtree(backup, ignore_errors=True)
        raise


def reset_state(root: Path, *, apply: bool) -> dict:
    root = Path(root)

    def result(layout, plans, lock_available):
        value = {
            "mode": "apply" if apply else "dry-run",
            "previous_bundle_sha256": layout["current_sha"],
            "baseline_bundle_sha256": layout["baseline_sha"],
            "retained_job_count": sum(path.is_dir() for path in layout["jobs"].iterdir()),
            "active_generated_type_ids": [
                plan["definition"]["object_type_id"] for plan in plans],
            "writer_lock_available": lock_available,
            "backup": None,
        }
        return value

    if not apply:
        layout = _validate_layout(root)
        retired_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        plans = _generated_archive_plan(layout)
        _validate_archive_plan(layout, plans, retired_at)
        available = True
        with layout["writer_lock"].open("r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                available = False
        return result(layout, plans, available)

    _directory(root, "item-control root")
    writer_lock = root / "history" / "writer.lock"
    _regular_file(writer_lock, "writer lock")
    with writer_lock.open("r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ResetError("coffee still owns the item-control writer lock") from error
        layout = _validate_layout(root)
        retired_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        plans = _generated_archive_plan(layout)
        _validate_archive_plan(layout, plans, retired_at)
        value = result(layout, plans, True)
        value["backup"] = _apply(layout, plans, retired_at).name
        return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-control-root", type=Path, default=DEFAULT_ITEM_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="Apply the validated reset. The default is a dry-run.")
    mode.add_argument("--dry-run", action="store_true",
                      help="Validate and report the reset without changing files.")
    args = parser.parse_args(argv)
    try:
        result = reset_state(args.item_control_root, apply=args.apply)
    except (OSError, ValueError, object_catalog.CatalogError, ResetError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
