"""Load the active object catalog from validated JSON data.

Built-in and generated object types use one definition contract. The loader
reads data files only. It never imports or executes a definition. Only the
active manifest can select active definitions. Physics dimensions and mass are
unmeasured estimates derived from a contact proxy. A replaced type moves to one
immutable Wall of Fame entry that a reload never activates.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from assets import FAMILIES as TEXTURE_FAMILIES  # the material families that the engine compiles
from object_definitions import SIM_FROM_ASSET_QUATERNION_WXYZ, SUPPORTED_PROXY_SHAPES

SCHEMA_VERSION = 1
CATALOG_ROOT = Path(__file__).resolve().parent / "object_catalog"
MAX_WALL_PAGE = 24
BUILTIN_SHAPES = frozenset({"ellipsoid", "half", "box", "capsule"})
SEVERITIES = frozenset({"none", "minor", "major", "foreign"})
ACTIVE_LIFECYCLE = "active_ready"
ARCHIVED_LIFECYCLE = "archived"
MASS_BASIS = "density_times_contact_proxy_volume"
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_MANIFEST_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}(?:/[a-z0-9][a-z0-9._-]{0,79}){0,3}$")
_DEFINITION_FIELDS = {
    "schema_version", "object_type_id", "display_name", "classifier_label", "provenance",
    "feed", "truth", "visual", "physics", "lifecycle_state", "validation_status",
}
_CATALOG_FIELDS = {
    "schema_version", "profile_name", "belt_rgb", "catalog_revision", "max_active_types",
    "active_type_ids", "definition_sha256", "active_bundle_sha256",
}


class CatalogError(ValueError):
    """The catalog or one definition is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def definition_sha256(definition: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(definition)).hexdigest()


def catalog_revision(active_type_ids: list[str], hashes: Mapping[str, str]) -> str:
    """Identify one ordered active set and its exact definition contents."""
    ordered = [[type_id, hashes[type_id]] for type_id in active_type_ids]
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


def validate_type_definition(value: Any) -> None:
    """Validate one built-in or generated object type definition."""
    value = _mapping(value, "definition")
    _exact(value, _DEFINITION_FIELDS, "definition")
    if value["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("definition schema version is unsupported")
    _identifier(value["object_type_id"], "object_type_id")
    _text(value["display_name"], "display_name", 120)
    if not isinstance(value["classifier_label"], str) or not _LABEL_RE.fullmatch(value["classifier_label"]):
        raise CatalogError("classifier_label has an invalid format")
    if not _one_of(value["lifecycle_state"], {ACTIVE_LIFECYCLE, ARCHIVED_LIFECYCLE}):
        raise CatalogError("draft or unknown lifecycle states cannot enter a catalog")
    if value["validation_status"] != "validated":
        raise CatalogError("definition validation_status must be validated")

    provenance = _mapping(value["provenance"], "provenance")
    _exact(provenance, {"kind", "source", "source_sha256"}, "provenance")
    kind = provenance["kind"]
    if not _one_of(kind, {"builtin", "generated"}):
        raise CatalogError("provenance.kind is invalid")
    _text(provenance["source"], "provenance.source", 400)
    if kind == "generated":
        _sha256(provenance["source_sha256"], "provenance.source_sha256")
    elif provenance["source_sha256"] is not None:
        raise CatalogError("builtin provenance cannot declare a source hash")

    feed = _mapping(value["feed"], "feed")
    _exact(feed, {"prior"}, "feed")
    prior = _number(feed["prior"], "feed.prior")
    if not 0 < prior <= 1:
        raise CatalogError("feed.prior must be in (0, 1]")

    truth = _mapping(value["truth"], "truth")
    _exact(truth, {"defect", "severity"}, "truth")
    if not isinstance(truth["defect"], bool) or not _one_of(truth["severity"], SEVERITIES):
        raise CatalogError("truth fields are invalid")
    if truth["defect"] == (truth["severity"] == "none"):
        raise CatalogError("truth.defect must agree with truth.severity")

    visual = _mapping(value["visual"], "visual")
    _exact(visual, {"shape", "size_mm", "rgb", "rgb_jitter", "texture", "asset"}, "visual")
    allowed = BUILTIN_SHAPES if kind == "builtin" else SUPPORTED_PROXY_SHAPES
    if not _one_of(visual["shape"], allowed):
        raise CatalogError(f"visual.shape is not a supported {kind} contact shape")
    size = visual["size_mm"]
    if not isinstance(size, list) or len(size) != 3:
        raise CatalogError("visual.size_mm must contain three ranges")
    for index, axis in enumerate(size):
        if not isinstance(axis, list) or len(axis) != 2:
            raise CatalogError(f"visual.size_mm[{index}] must be a low and high pair")
        low, high = (_number(item, f"visual.size_mm[{index}]") for item in axis)
        if not 0 < low <= high <= 1000:
            raise CatalogError(f"visual.size_mm[{index}] must satisfy 0 < low <= high <= 1000 mm")
    rgb = visual["rgb"]
    if not isinstance(rgb, list) or len(rgb) != 3 or any(not 0 <= _number(c, "visual.rgb") <= 1 for c in rgb):
        raise CatalogError("visual.rgb must contain three values in [0, 1]")
    if not 0 <= _number(visual["rgb_jitter"], "visual.rgb_jitter") <= 1:
        raise CatalogError("visual.rgb_jitter must be in [0, 1]")
    # The inspection camera sees a generated type as a flat colour proxy, so it has no texture.
    texture = visual["texture"]
    if texture is not None and (kind == "generated" or not _one_of(texture, TEXTURE_FAMILIES)):
        raise CatalogError("visual.texture must be null or a built-in engine material family")
    _validate_asset(visual["asset"], kind)

    physics = _mapping(value["physics"], "physics")
    _exact(physics, {"contact_shape", "density_kg_m3", "mass_basis", "measurement_status"}, "physics")
    if physics["contact_shape"] != visual["shape"]:
        raise CatalogError("physics.contact_shape must equal visual.shape")
    if not 0 < _number(physics["density_kg_m3"], "physics.density_kg_m3") <= 30000:
        raise CatalogError("physics.density_kg_m3 must be in (0, 30000]")
    if physics["mass_basis"] != MASS_BASIS or physics["measurement_status"] != "unmeasured_estimate":
        raise CatalogError("physics must declare an unmeasured proxy-based mass estimate")


def load_definition(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    """Read one definition as data and check its declared content hash."""
    try:
        definition = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"definition cannot be read: {Path(path).name}") from error
    validate_type_definition(definition)
    if expected_sha256 is not None and definition_sha256(definition) != expected_sha256:
        raise CatalogError(f"definition hash does not match the manifest: {definition['object_type_id']}")
    return definition


def load_catalog(root: Path = CATALOG_ROOT, manifest: str = "active/catalog.json") -> dict[str, Any]:
    """Load one validated manifest and only the definitions that it selects."""
    root = Path(root)
    # Safe relative segments only. An absolute or parent path would leave the catalog root.
    if not isinstance(manifest, str) or not _MANIFEST_RE.fullmatch(manifest):
        raise CatalogError("catalog manifest path must stay inside the catalog root")
    try:
        catalog = json.loads(_inside(root, root / manifest).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"catalog manifest cannot be read: {manifest}") from error
    catalog = dict(_mapping(catalog, "catalog"))
    _exact(catalog, _CATALOG_FIELDS, "catalog")
    if catalog["schema_version"] != SCHEMA_VERSION:
        raise CatalogError("catalog schema version is unsupported")
    _text(catalog["profile_name"], "profile_name", 80)
    belt = catalog["belt_rgb"]
    if not isinstance(belt, list) or len(belt) != 3 or any(not 0 <= _number(c, "belt_rgb") <= 1 for c in belt):
        raise CatalogError("belt_rgb must contain three values in [0, 1]")
    ids, hashes = catalog["active_type_ids"], _mapping(catalog["definition_sha256"], "definition_sha256")
    limit = catalog["max_active_types"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 32:
        raise CatalogError("max_active_types must be an integer in [1, 32]")
    if not isinstance(ids, list) or len(ids) != limit:
        raise CatalogError("active type count must equal max_active_types")
    for type_id in ids:
        _identifier(type_id, "active_type_ids")
    if len(set(ids)) != len(ids) or set(hashes) != set(ids):
        raise CatalogError("active type identifiers must be unique and hashed exactly once")
    if catalog["catalog_revision"] != catalog_revision(ids, hashes):
        raise CatalogError("catalog_revision does not match the ordered active set")
    if catalog["active_bundle_sha256"] is not None:
        _sha256(catalog["active_bundle_sha256"], "active_bundle_sha256")

    definitions = []
    for type_id in ids:
        _sha256(hashes[type_id], f"definition_sha256[{type_id}]")
        definition = load_definition(_inside(root, root / "definitions" / f"{type_id}.json"), hashes[type_id])
        if definition["object_type_id"] != type_id:
            raise CatalogError(f"definition identifier does not match its file name: {type_id}")
        if definition["lifecycle_state"] != ACTIVE_LIFECYCLE:
            raise CatalogError(f"archived definition cannot be active: {type_id}")
        definitions.append(definition)
    labels = [definition["classifier_label"] for definition in definitions]
    if len(set(labels)) != len(labels):
        raise CatalogError("classifier labels must be unique in one catalog")
    catalog["definitions"] = definitions
    return catalog


def catalog_labels(catalog: Mapping[str, Any]) -> list[str]:
    return [definition["classifier_label"] for definition in catalog["definitions"]]


def require_label_order(catalog: Mapping[str, Any], model_classes: list[str]) -> None:
    """A model is compatible only when its labels equal the catalog order."""
    if list(model_classes) != catalog_labels(catalog):
        raise CatalogError("model label order does not equal the active catalog label order")


def archive_type(root: Path, definition: Mapping[str, Any], retired_at: str,
                 evidence: Mapping[str, Path] | None = None) -> Path:
    """Store one replaced type and its evidence as one immutable Wall of Fame entry."""
    validate_type_definition(definition)
    _rfc3339_utc(retired_at, "retired_at")
    archived = {**dict(definition), "lifecycle_state": ARCHIVED_LIFECYCLE}
    files: dict[str, bytes] = {}
    for name, source in dict(evidence or {}).items():
        if not isinstance(name, str) or not _FILE_RE.fullmatch(name) or name in {"definition.json", "archive.json"}:
            raise CatalogError(f"evidence file name is invalid: {name}")
        try:
            files[name] = Path(source).read_bytes()
        except OSError as error:
            raise CatalogError(f"evidence file cannot be read: {name}") from error
    files["definition.json"] = _pretty(archived)
    files["archive.json"] = _pretty({
        "object_type_id": archived["object_type_id"],
        "display_name": archived["display_name"],
        "classifier_label": archived["classifier_label"],
        "retired_at": retired_at,
        "definition_sha256": definition_sha256(archived),
        "provenance": archived["provenance"],
        "evidence": {name: hashlib.sha256(files[name]).hexdigest() for name in sorted(evidence or {})},
    })

    wall = Path(root) / "wall-of-fame"
    directory = wall / archived["object_type_id"]
    # One writer: activation runs serialized inside one service process.
    if directory.exists():
        current = {item.name: item.read_bytes() for item in directory.iterdir() if item.is_file()}
        if current != files:
            raise CatalogError(f"an archived type is immutable: {archived['object_type_id']}")
        return directory
    wall.mkdir(parents=True, exist_ok=True)
    # The dot prefix keeps an interrupted write outside _ID_RE, so no page can read it.
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=wall))
    try:
        for name, data in files.items():
            (staging / name).write_bytes(data)
        os.replace(staging, directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return directory


def wall_of_fame_page(root: Path = CATALOG_ROOT, offset: int = 0, limit: int = MAX_WALL_PAGE) -> dict[str, Any]:
    """Return one bounded, newest-first page of inactive archived definitions."""
    for name, number in (("offset", offset), ("limit", limit)):
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise CatalogError(f"{name} must be a non-negative integer")
    limit = min(limit, MAX_WALL_PAGE)
    wall = Path(root) / "wall-of-fame"
    entries, unreadable = [], 0
    for directory in sorted(wall.iterdir()) if wall.is_dir() else []:
        record = directory / "archive.json"
        if not (directory.is_dir() and _ID_RE.fullmatch(directory.name) and record.is_file()):
            continue
        # One damaged entry must not hide the other archived types. The page reports the count.
        try:
            entry = json.loads(record.read_text())
            _rfc3339_utc(entry["retired_at"], "retired_at")
            if entry["object_type_id"] != directory.name:
                raise CatalogError("archive identifier does not match its directory")
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
            continue
        entries.append(entry)
    entries.sort(key=lambda entry: (entry["retired_at"], entry["object_type_id"]), reverse=True)
    return {"total": len(entries), "unreadable": unreadable, "offset": offset, "limit": limit,
            "entries": entries[offset:offset + limit]}


def _inside(root: Path, path: Path) -> Path:
    """Follow symlinks, then require that the file remains below the catalog root."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise CatalogError(f"catalog path leaves the catalog root: {path.name}")
    return resolved


def _validate_asset(asset: Any, kind: str) -> None:
    if asset is None:
        if kind == "generated":
            raise CatalogError("a generated definition requires a visual asset")
        return
    asset = _mapping(asset, "visual.asset")
    _exact(asset, {"visual_asset_id", "glb_sha256", "units", "source_up_axis", "engine_up_axis",
                   "sim_from_asset_quaternion_wxyz"}, "visual.asset")
    _sha256(asset["glb_sha256"], "visual.asset.glb_sha256")
    if asset["visual_asset_id"] != f"sha256:{asset['glb_sha256']}":
        raise CatalogError("visual_asset_id must equal the GLB content hash")
    if asset["units"] != "m" or asset["source_up_axis"] != "+Y" or asset["engine_up_axis"] != "+Z":
        raise CatalogError("visual asset units or axes are invalid")
    if asset["sim_from_asset_quaternion_wxyz"] != list(SIM_FROM_ASSET_QUATERNION_WXYZ):
        raise CatalogError("visual asset pose correction is invalid")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _rfc3339_utc(value: Any, path: str) -> None:
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise CatalogError(f"{path} must be an RFC 3339 UTC timestamp") from error
    if not isinstance(value, str) or not value.endswith("Z") or parsed.utcoffset() != datetime.timedelta(0):
        raise CatalogError(f"{path} must be an RFC 3339 UTC timestamp")


def _one_of(value: Any, allowed) -> bool:
    """Check the type first. A JSON list or object is unhashable and would raise TypeError."""
    return isinstance(value, str) and value in allowed


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{path} must be an object")
    return value


def _exact(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise CatalogError(f"{path} has incorrect fields")


def _text(value: Any, path: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CatalogError(f"{path} must be nonempty text of at most {maximum} characters")


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CatalogError(f"{path} must be a finite number")
    return float(value)


def _identifier(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) > 160 or not _ID_RE.fullmatch(value):
        raise CatalogError(f"{path} has an invalid format")


def _sha256(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CatalogError(f"{path} must be a lowercase SHA-256 digest")
