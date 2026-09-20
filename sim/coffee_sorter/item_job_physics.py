"""Physics proposal for one item job, by cached replay only.

This wrapper is the ONLY place that knows how a physics proposal reaches the queue.
It follows the generation rules exactly: cache-only first, a miss stops before any
submission, and `--live` is never automatic. A live attempt needs paid mode and an
explicit operator grant, passed by the caller.

By default the request carries the FULL job description. A user prompt is never
truncated and never rewritten. An operator may place reviewed REPLAY METADATA in a
read-only directory, one file named `<recipe_sha256>.json`. That file is used only when
both of its bound hashes equal this job's own artifacts: the sha256 of its `recipe.json`
and of its rendered `previews/object.glb`. Only then does the request carry the shorter
`physics_description` from the metadata. The recomputed request digest must equal the
declared `expected_request_sha256`, and the cache bytes must hash to the declared
`cache_entry_sha256`. Either mismatch is `cache_entry_invalid`, never a miss, because a
miss could lead to a paid request. A binding mismatch is not an error: the metadata is
ignored, the full description is used, and the normal miss applies.

The proposal and the definition are REBUILT here through the real adapter on this job's
own artifacts. No stored proposal and no stored definition is ever copied forward.

Exit codes match the generation wrapper: 0 proposal present, 3 cache miss and never
submitted, 4 submission uncertain, 5 credentials missing, 6 cache entry invalid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from item_jobs import (EXIT_CACHE_ENTRY_INVALID, EXIT_CREDENTIALS, EXIT_FAILED,
                       EXIT_NOT_SUBMITTED, EXIT_OK, EXIT_UNCERTAIN)
from object_definitions import build_object_definition, physics_request, propose_physics

REPLAY_FIELDS = {"schema_version", "binding", "physics_description",
                 "expected_request_sha256", "cache_entry_sha256"}
BINDING_FIELDS = {"recipe_sha256", "glb_sha256"}
SCHEMA_VERSION = 1
MEASUREMENT_STATUS = "unmeasured_proxy_estimate"


class ReplayError(Exception):
    """The bound replay metadata or the cache entry failed verification."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_replay(directory: Path | None, recipe_sha256: str, glb_sha256: str):
    """Return the replay metadata bound to THESE artifacts, or None.

    A file that names another recipe, or whose binding does not match both of this
    job's hashes, is ignored: the caller then uses the full description and takes the
    normal miss. A malformed file that IS bound is refused.
    """
    if directory is None:
        return None
    path = Path(directory) / f"{recipe_sha256}.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReplayError(f"replay metadata cannot be read: {error}") from None
    if not isinstance(value, dict) or set(value) != REPLAY_FIELDS:
        raise ReplayError("replay metadata has incorrect fields")
    binding = value["binding"]
    if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
        raise ReplayError("replay binding has incorrect fields")
    if binding["recipe_sha256"] != recipe_sha256 or binding["glb_sha256"] != glb_sha256:
        # Not an error. The metadata belongs to other artifacts, so it does not apply.
        return None
    if value["schema_version"] != SCHEMA_VERSION:
        raise ReplayError("replay metadata schema version is unsupported")
    for name in ("expected_request_sha256", "cache_entry_sha256"):
        digest = value[name]
        if not isinstance(digest, str) or len(digest) != 64 or not _is_hex(digest):
            raise ReplayError(f"replay {name} must be a sha256 digest")
    description = value["physics_description"]
    if not isinstance(description, str) or not description.strip():
        raise ReplayError("replay physics_description must be text")
    return {**value, "metadata_sha256": sha256_file(path)}


def _is_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)


def verify_cache_entry(cache_dir: Path, digest: str, expected_bytes_sha256: str) -> str:
    """Verify the cache entry BEFORE any reuse. A mismatch is never a miss."""
    path = Path(cache_dir) / f"{digest}.json"
    if not path.is_file():
        return ""
    actual = sha256_file(path)
    if actual != expected_bytes_sha256:
        raise ReplayError("cache entry bytes do not match the declared cache_entry_sha256")
    return actual


def link_cache(job_physics: Path, provider_cache: Path) -> Path:
    """`propose_physics` reads its cache beside the evidence directory.

    The wrapper therefore links `<job>/physics/cache` to the provider cache, exactly as
    the generation wrapper does. It refuses a real directory in that place.
    """
    link = job_physics / "cache"
    target = Path(provider_cache) / "cache"
    if link.is_symlink():
        return link
    if link.exists():
        raise ReplayError("the job cache path already exists as a real directory")
    job_physics.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return link


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--mode", choices=("cached", "paid"), required=True)
    parser.add_argument("--provider-cache", type=Path, required=True)
    parser.add_argument("--replay-dir", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    job_dir = args.job_dir
    recipe = job_dir / "recipe.json"
    glb = job_dir / "previews" / "object.glb"
    status = job_dir / "provider_status.json"
    if not recipe.is_file() or not glb.is_file():
        _status(status, "not_submitted", "the job has no recipe or no rendered asset")
        return EXIT_FAILED

    recipe_sha256, glb_sha256 = sha256_file(recipe), sha256_file(glb)
    physics_dir = job_dir / "physics"
    evidence = physics_dir / "run"
    try:
        metadata = load_replay(args.replay_dir, recipe_sha256, glb_sha256)
        link_cache(physics_dir, args.provider_cache)
    except ReplayError as error:
        _status(status, "not_submitted", str(error))
        return EXIT_CACHE_ENTRY_INVALID

    bounds = _render_bounds(job_dir)
    if bounds is None:
        _status(status, "not_submitted", "the render metadata has no bounding dimensions")
        return EXIT_FAILED
    # A user prompt is never truncated or rewritten. Only reviewed, bound metadata may
    # substitute the shorter physics description.
    description = metadata["physics_description"] if metadata else args.description
    source = "operator_replay_metadata" if metadata else "job_description"
    try:
        _, digest = physics_request(description=description, visual_dimensions_m=bounds)
        if metadata is not None:
            if digest != metadata["expected_request_sha256"]:
                raise ReplayError("the recomputed request digest does not match the replay")
            verify_cache_entry(physics_dir / "cache", digest, metadata["cache_entry_sha256"])
    except ReplayError as error:
        _status(status, "not_submitted", str(error))
        return EXIT_CACHE_ENTRY_INVALID
    except (ValueError, TypeError) as error:
        _status(status, "not_submitted", f"the physics request is invalid: {error}")
        return EXIT_FAILED

    cache_hit = (physics_dir / "cache" / f"{digest}.json").is_file()
    if not cache_hit and not args.live:
        # Stop before submission. The operator decides, and paid mode is off by default.
        _status(status, "not_submitted", "physics request is not cached", request_sha256=digest,
                physics_description_source=source)
        return EXIT_NOT_SUBMITTED
    try:
        proposal = propose_physics(
            description=description, visual_dimensions_m=bounds, evidence_dir=evidence,
            env_file=args.env_file or job_dir / "absent.env", live=bool(args.live))
    except RuntimeError as error:
        if "OPENROUTER_API_KEY" in str(error):
            _status(status, "not_submitted", "credentials missing for a live request")
            return EXIT_CREDENTIALS
        _status(status, "uncertain", f"physics provider call was interrupted: {error}")
        return EXIT_UNCERTAIN
    except ValueError as error:
        _status(status, "not_submitted", f"physics proposal verification failed: {error}")
        return EXIT_CACHE_ENTRY_INVALID
    except OSError as error:
        _status(status, "not_submitted", f"physics evidence cannot be written: {error}")
        return EXIT_FAILED

    # Rebuild the definition through the real adapter on THIS job's own artifacts.
    def build(sorting_proposal):
        return build_object_definition(
            description=args.description,
            recipe_path=recipe,
            render_metadata_path=job_dir / "previews" / "render.json",
            glb_path=glb,
            visual_uri="previews/object.glb",
            physics_proposal=proposal,
            sorting_proposal=sorting_proposal,
        )

    try:
        definition = build(None)
        if definition["physics"]["proxy"] is not None:
            # Activation always adds the new label as Keep, so the draft carries an
            # unreviewed Keep proposal under the adapter's own normalized object key.
            definition = build({"class_name": definition["object_key"], "defect": False,
                                "severity": "none", "proposed_action": "keep"})
    except (ValueError, OSError) as error:
        _status(status, "not_submitted", f"the draft definition is invalid: {error}")
        return EXIT_FAILED
    _write_json(job_dir / "definition.json", definition)
    _write_json(job_dir / "physics.json", {
        "physics_source": "cached_llm_replay",
        "physics_description": description,
        "physics_description_source": source,
        "physics_replay_metadata_sha256": metadata["metadata_sha256"] if metadata else None,
        "physics_request_sha256": digest,
        "physics_cache_entry_sha256": metadata["cache_entry_sha256"] if metadata else (
            sha256_file(physics_dir / "cache" / f"{digest}.json")),
        "physics_measurement_status": MEASUREMENT_STATUS,
        "recipe_sha256": recipe_sha256,
        "glb_sha256": glb_sha256,
    })
    _status(status, "not_submitted", None, cache_hit=cache_hit, request_sha256=digest,
            physics_description_source=source)
    return EXIT_OK


def _render_bounds(job_dir: Path):
    record = _read_json(job_dir / "previews" / "render.json") or {}
    bounds = record.get("bounding_dimensions_mm")
    if not isinstance(bounds, list) or len(bounds) != 3:
        return None
    try:
        return [float(value) * 0.001 for value in bounds]
    except (TypeError, ValueError):
        return None


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _status(path: Path, submission: str, reason: str | None, **extra) -> None:
    _write_json(path, {"provider_submission": submission, "reason": reason,
                       "stage": "physics_proposal", "cache_hit": extra.pop("cache_hit", False),
                       "live_requested": False, **extra})


if __name__ == "__main__":
    raise SystemExit(main())
