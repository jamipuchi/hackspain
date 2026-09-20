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
import socket
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
# The anomaly reference is the product cloud. It is never a replacement victim.
ANOMALY_REFERENCE_LABEL = "good"
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
BUNDLE_SCHEMA_VERSION = 1
BUNDLE_MANIFEST = "bundle.json"
BUNDLE_CATALOG = "catalog/active/catalog.json"
BUNDLE_PRESET = "preset.json"
GLB_MAGIC = b"glTF"
# A generated victim carries its rendered asset and the model that recognised it.
GENERATED_EVIDENCE = ("object.glb", "perspective.png", "top.png", "model.manifest.json")
_CHUNK_BYTES = 1 << 20
# User and provider text. A date or a slash inside these is valid content, not metadata.
_USER_TEXT_FIELDS = frozenset({"display_name", "description", "design_notes",
                               "material_assumption", "limitations"})
_COMMON_HOST_NAMES = frozenset({"localhost"})
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
# A seeded bundle carries no date, so an activation can never be dated by its bytes.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def load_catalog(root: Path = CATALOG_ROOT, manifest: str = "active/catalog.json",
                 bundles_root: Path | None = None) -> dict[str, Any]:
    """Load one validated manifest and only the definitions that it selects.

    A non-null active_bundle_sha256 is a promise about bytes, not a syntax field. It
    requires a bundles root, a verifying bundle, and an inner catalog that agrees with
    this pointer. The definitions then come from that bundle, never from the root.
    """
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
    definitions_root = root
    if catalog["active_bundle_sha256"] is not None:
        _sha256(catalog["active_bundle_sha256"], "active_bundle_sha256")
        definitions_root = _bound_bundle_catalog(catalog, bundles_root)

    definitions = []
    for type_id in ids:
        _sha256(hashes[type_id], f"definition_sha256[{type_id}]")
        definition = load_definition(
            _inside(definitions_root, definitions_root / "definitions" / f"{type_id}.json"),
            hashes[type_id])
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


def _bound_bundle_catalog(catalog: Mapping[str, Any], bundles_root: Path | None) -> Path:
    """Verify the pointed bundle and require it to agree with the pointer."""
    if bundles_root is None:
        raise CatalogError("an active bundle pointer requires a bundles root")
    bundle = Path(bundles_root) / catalog["active_bundle_sha256"]
    verify_bundle(bundle)
    try:
        inner = json.loads((bundle / BUNDLE_CATALOG).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"the active bundle has no catalog manifest: {bundle.name}") from error
    inner = _mapping(inner, "bundle catalog")
    if inner.get("active_bundle_sha256") is not None:
        raise CatalogError("a bundle catalog must carry a null active_bundle_sha256")
    for field in ("catalog_revision", "active_type_ids", "definition_sha256",
                  "max_active_types", "profile_name"):
        if inner.get(field) != catalog[field]:
            raise CatalogError(f"the active pointer and its bundle disagree on {field}")
    return bundle / "catalog"


def catalog_labels(catalog: Mapping[str, Any]) -> list[str]:
    return [definition["classifier_label"] for definition in catalog["definitions"]]


def require_label_order(catalog: Mapping[str, Any], model_classes: list[str]) -> None:
    """A model is compatible only when its labels equal the catalog order."""
    if list(model_classes) != catalog_labels(catalog):
        raise CatalogError("model label order does not equal the active catalog label order")


def label_from_object_key(object_key: Any) -> str:
    """Derive one classifier label from a draft object key. Only underscores separate words."""
    label = re.sub(r"[.-]", "_", object_key) if isinstance(object_key, str) else ""
    if not _LABEL_RE.fullmatch(label):
        raise CatalogError("object_key does not derive a valid classifier label")
    return label


def recipe_rgb(recipe: Mapping[str, Any]) -> list[float]:
    """The flat proxy colour of a generated type: the mean of the recipe part colours.

    The inspection camera sees this flat colour, never the GLB. A beauty render is
    never classifier evidence.
    """
    parts = _mapping(recipe, "recipe").get("parts")
    if not isinstance(parts, list) or not parts:
        raise CatalogError("a recipe needs at least one part")
    total = [0.0, 0.0, 0.0]
    for part in parts:
        colour = _mapping(part, "recipe part").get("color")
        if not isinstance(colour, list) or len(colour) != 3:
            raise CatalogError("a recipe part colour needs three channels")
        for index, value in enumerate(colour):
            total[index] += _number(value, "recipe part colour")
    rgb = [value / len(parts) for value in total]
    if any(not 0 <= value <= 1 for value in rgb):
        raise CatalogError("recipe part colours must be in [0, 1]")
    return rgb


def type_definition_from_draft(draft: Mapping[str, Any], *, rgb, prior: float,
                              source_sha256: str) -> dict[str, Any]:
    """Build one active generated type from a validated supported draft definition."""
    physics = _mapping(_mapping(draft, "draft")["physics"], "draft.physics")
    proxy = physics["proxy"]
    if proxy is None:
        raise CatalogError("a draft without a supported contact proxy cannot become a type")
    sorting = _mapping(draft["sorting"], "draft.sorting")
    if sorting["status"] == "unassigned":
        raise CatalogError("a draft without a sorting proposal carries no defect truth")
    if proxy["shape"] == "box":
        semi_axes_mm = [value * 1e3 for value in proxy["half_extents_m"]]
    else:  # capsule: half length along its local axis, then the radius twice
        radius_mm = proxy["radius_m"] * 1e3
        semi_axes_mm = [proxy["half_length_m"] * 1e3, radius_mm, radius_mm]
    try:
        visual = _mapping(draft["visual"], "draft.visual")
        asset_id = visual["visual_asset_id"]
        definition = {
            "schema_version": SCHEMA_VERSION,
            "object_type_id": draft["object_type_id"],
            "display_name": draft["display_name"],
            "classifier_label": label_from_object_key(draft["object_key"]),
            "provenance": {"kind": "generated", "source": draft["object_type_id"],
                           "source_sha256": source_sha256},
            "feed": {"prior": prior},
            "truth": {"defect": sorting["defect"], "severity": sorting["severity"]},
            "visual": {
                "shape": proxy["shape"],
                "size_mm": [[value, value] for value in semi_axes_mm],
                "rgb": list(rgb),
                "rgb_jitter": 0.0,
                # The inspection camera sees this flat-colour proxy, never the GLB.
                "texture": None,
                "asset": {
                    "visual_asset_id": asset_id,
                    "glb_sha256": asset_id.removeprefix("sha256:") if isinstance(asset_id, str) else asset_id,
                    "units": visual["units"],
                    "source_up_axis": visual["source_up_axis"],
                    "engine_up_axis": visual["engine_up_axis"],
                    "sim_from_asset_quaternion_wxyz": list(visual["sim_from_asset_quaternion_wxyz"]),
                },
            },
            "physics": {
                "contact_shape": proxy["shape"],
                "density_kg_m3": physics["density_kg_m3"],
                "mass_basis": MASS_BASIS,
                "measurement_status": "unmeasured_estimate",
            },
            "lifecycle_state": ACTIVE_LIFECYCLE,
            "validation_status": "validated",
        }
    except (KeyError, TypeError) as error:
        raise CatalogError("draft definition is missing required fields") from error
    validate_type_definition(definition)
    return definition


def select_victim(catalog: Mapping[str, Any], reject_labels) -> str | None:
    """The last active type the policy keeps. None means the job waits for a replacement."""
    rejected = set(reject_labels)
    for definition in reversed(catalog["definitions"]):
        label = definition["classifier_label"]
        if label not in rejected and label != ANOMALY_REFERENCE_LABEL:
            return definition["object_type_id"]
    return None


def candidate_catalog(catalog: Mapping[str, Any], new_definition: Mapping[str, Any],
                      victim_id: str) -> dict[str, Any]:
    """One candidate catalog: the new type first, then every survivor in its existing order."""
    validate_type_definition(new_definition)
    survivors = [value for value in catalog["definitions"] if value["object_type_id"] != victim_id]
    if len(survivors) == len(catalog["definitions"]):
        raise CatalogError(f"replacement victim is not active: {victim_id}")
    # select_victim never offers it. A direct caller must not remove it either.
    if any(value["object_type_id"] == victim_id and value["classifier_label"] == ANOMALY_REFERENCE_LABEL
           for value in catalog["definitions"]):
        raise CatalogError("the anomaly reference type cannot be a replacement victim")
    definitions = [dict(new_definition), *survivors]
    ids = [value["object_type_id"] for value in definitions]
    labels = [value["classifier_label"] for value in definitions]
    if len(set(ids)) != len(ids):
        raise CatalogError("active type identifiers must be unique and hashed exactly once")
    if len(set(labels)) != len(labels):
        raise CatalogError("classifier labels must be unique in one catalog")
    hashes = {type_id: definition_sha256(value) for type_id, value in zip(ids, definitions)}
    return {**dict(catalog), "active_type_ids": ids, "definition_sha256": hashes,
            "catalog_revision": catalog_revision(ids, hashes),
            # A candidate has no active bundle yet.
            "active_bundle_sha256": None, "definitions": definitions}


def write_catalog(root: Path, catalog: Mapping[str, Any]) -> Path:
    """Write one loadable catalog root atomically. The root must not exist yet."""
    root = Path(root)
    definitions = list(catalog["definitions"])
    manifest = {key: value for key, value in catalog.items() if key != "definitions"}
    _exact(manifest, _CATALOG_FIELDS, "catalog")
    # The file names come from the definitions, so this function checks them itself.
    for definition in definitions:
        validate_type_definition(definition)
    if [definition["object_type_id"] for definition in definitions] != manifest["active_type_ids"]:
        raise CatalogError("catalog definitions do not match the ordered active set")
    if root.exists():
        raise CatalogError(f"catalog root already exists: {root.name}")
    files = {"active/catalog.json": _pretty(manifest)}
    for definition in definitions:
        files[f"definitions/{definition['object_type_id']}.json"] = _pretty(definition)
    return _publish_staged(root, files)


def _publish_staged(target: Path, files: Mapping[str, bytes]) -> Path:
    """Write one complete directory beside its target, then publish it with one rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # The dot prefix keeps an interrupted write outside every identifier pattern.
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent))
    try:
        for name, data in files.items():
            (staging / name).parent.mkdir(parents=True, exist_ok=True)
            (staging / name).write_bytes(data)
        # A rename onto an existing directory fails, so a second writer cannot replace the first.
        if not _replace_directory(staging, target):
            raise CatalogError(f"directory already exists: {target.name}")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _replace_directory(staging: Path, target: Path) -> bool:
    """One rename that never overwrites a published directory.

    Returns False when another writer published that name first, so a raw OSError never
    reaches a caller. The caller decides whether that is an error or a no-op.
    """
    try:
        os.replace(staging, target)
        return True
    except OSError as error:
        if target.exists():
            return False
        raise CatalogError(f"directory cannot be published: {target.name}") from error


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
    model_artifact = _archive_model_artifact(archived, files)
    files["definition.json"] = _pretty(archived)
    files["archive.json"] = _pretty({
        "object_type_id": archived["object_type_id"],
        "display_name": archived["display_name"],
        "classifier_label": archived["classifier_label"],
        "retired_at": retired_at,
        "definition_sha256": definition_sha256(archived),
        "provenance": archived["provenance"],
        "evidence": {name: hashlib.sha256(files[name]).hexdigest() for name in sorted(evidence or {})},
        "model_artifact_sha256": model_artifact,
    })

    wall = Path(root) / "wall-of-fame"
    directory = wall / archived["object_type_id"]
    # One writer: activation runs serialized inside one service process.
    if directory.exists():
        current = {item.name: item.read_bytes() for item in directory.iterdir() if item.is_file()}
        if current != files:
            raise CatalogError(f"an archived type is immutable: {archived['object_type_id']}")
        return directory
    return _publish_staged(directory, files)


def _archive_model_artifact(archived: Mapping[str, Any],
                            files: Mapping[str, bytes]) -> str | None:
    """A generated victim needs its rendered asset and its model evidence.

    Every check runs before any write, so a refused archive leaves nothing behind. A
    built-in victim has no GLB, so its evidence stays optional.
    """
    if archived["provenance"]["kind"] == "generated":
        missing = [name for name in GENERATED_EVIDENCE if name not in files]
        if missing:
            raise CatalogError(
                f"a generated archive requires evidence: {', '.join(missing)}")
        glb = files["object.glb"]
        if not glb.startswith(GLB_MAGIC):
            raise CatalogError("archived object.glb is not a GLB file")
        if hashlib.sha256(glb).hexdigest() != archived["visual"]["asset"]["glb_sha256"]:
            raise CatalogError("archived object.glb does not match the definition hash")
    if "model.manifest.json" not in files:
        return None
    return _model_artifact_sha256(files["model.manifest.json"])


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
        # An archived type without a readable definition is incomplete. The page counts
        # it with the other damaged entries instead of showing a name with no geometry.
        preview = _archive_preview(directory / "definition.json")
        if preview is None:
            unreadable += 1
            continue
        entries.append({**entry, "preview": preview})
    entries.sort(key=lambda entry: (entry["retired_at"], entry["object_type_id"]), reverse=True)
    return {"total": len(entries), "unreadable": unreadable, "offset": offset, "limit": limit,
            "entries": entries[offset:offset + limit]}


def write_bundle_manifest(staging_dir: Path) -> str:
    """Describe every file under one staging directory and return the bundle identity.

    The manifest lists relative posix paths only, so the same content in two different
    parent directories produces the same identity. Nothing inside a bundle names it.
    """
    staging = Path(staging_dir)
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(staging.rglob("*")):
        if path.is_symlink():
            raise CatalogError("a bundle cannot contain a symlink")
        if path.is_dir():
            continue
        name = path.relative_to(staging).as_posix()
        if name == BUNDLE_MANIFEST:
            continue
        _bundle_relative(name)
        digest, size = _file_sha256(path)
        files[name] = {"sha256": digest, "bytes": size}
    data = canonical_json({"schema_version": BUNDLE_SCHEMA_VERSION, "files": files})
    (staging / BUNDLE_MANIFEST).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def publish_bundle(bundles_root: Path, files: Mapping[str, Any]) -> str:
    """Stage one bundle, name it from its own content, and publish it with one rename.

    Each value is either the file bytes or a source path to copy. An identical bundle is
    a no-op. A directory of that name that does not verify is an error.
    """
    bundles_root = Path(bundles_root)
    bundles_root.mkdir(parents=True, exist_ok=True)
    # The dot prefix keeps an interrupted write outside every identifier pattern.
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=bundles_root))
    try:
        for name, source in files.items():
            _bundle_relative(name)
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(source, (bytes, bytearray)):
                target.write_bytes(bytes(source))
            else:
                _copy_file(Path(source), target)
        digest = write_bundle_manifest(staging)
        # Never publish a bundle that verify_bundle would refuse. The name check needs the
        # final directory, so only the content rules can run here.
        listed = json.loads((staging / BUNDLE_MANIFEST).read_text())["files"]
        _verify_bundle_content(staging, listed)
        directory = bundles_root / digest
        # The check and the rename cannot be atomic, so a second writer can win between
        # them. Either answer ends here: the name is the content hash, so a directory of
        # that name that verifies holds exactly this content.
        if directory.exists() or not _replace_directory(staging, directory):
            verify_bundle(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # A successful rename already moved the staging directory away.
    shutil.rmtree(staging, ignore_errors=True)
    return digest


def verify_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Recompute one bundle from its own bytes. Any violation refuses the whole bundle.

    No message names a host path: a bundle is identified by its sha and by the relative
    paths it lists.
    """
    directory = Path(bundle_dir)
    name = directory.name
    if directory.is_symlink() or not directory.is_dir():
        raise CatalogError(f"bundle directory is missing: {name}")
    if not _SHA256_RE.fullmatch(name):
        raise CatalogError(f"bundle directory name is not a sha256: {name}")
    try:
        data = (directory / BUNDLE_MANIFEST).read_bytes()
    except OSError as error:
        raise CatalogError(f"bundle manifest cannot be read: {name}") from error
    if hashlib.sha256(data).hexdigest() != name:
        raise CatalogError(f"bundle manifest hash does not match its directory: {name}")
    try:
        manifest = json.loads(data)
    except json.JSONDecodeError as error:
        raise CatalogError(f"bundle manifest is not JSON: {name}") from error
    manifest = dict(_mapping(manifest, "bundle"))
    _exact(manifest, {"schema_version", "files"}, "bundle")
    if manifest["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise CatalogError(f"bundle schema version is unsupported: {name}")
    listed = _mapping(manifest["files"], "bundle.files")

    present = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise CatalogError(f"bundle contains a symlink: {name}")
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative != BUNDLE_MANIFEST:
            present.add(relative)
    if present != set(listed):
        raise CatalogError(f"bundle files do not match its manifest: {name}")

    for relative in sorted(listed):
        _bundle_relative(relative)
        record = _mapping(listed[relative], f"bundle.files[{relative}]")
        _exact(record, {"sha256", "bytes"}, f"bundle.files[{relative}]")
        _sha256(record["sha256"], f"bundle.files[{relative}].sha256")
        size = record["bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise CatalogError(f"bundle file size is invalid: {relative}")
        path = _inside(directory, directory / relative)
        if not path.is_file():
            raise CatalogError(f"bundle file is missing: {relative}")
        digest, actual = _file_sha256(path)
        if digest != record["sha256"] or actual != size:
            raise CatalogError(f"bundle file does not match its manifest: {relative}")

    _verify_bundle_content(directory, listed)
    return {"bundle_sha256": name, "files": {key: dict(listed[key]) for key in sorted(listed)}}


def write_active_pointer(active_root: Path, bundle_sha256: str) -> Path:
    """Point one persistent root at a verified bundle with one atomic replace."""
    bundle = _verified_bundle(active_root, bundle_sha256)
    try:
        inner = json.loads((bundle / BUNDLE_CATALOG).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"the bundle has no catalog manifest: {bundle.name}") from error
    pointer = {**dict(_mapping(inner, "bundle catalog")), "active_bundle_sha256": bundle.name}
    _exact(pointer, _CATALOG_FIELDS, "catalog")
    target = Path(active_root) / "active" / "catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _replace_atomically(target, _pretty(pointer))
    return target


def read_active(active_root: Path) -> dict[str, Any]:
    """Load the catalog the pointer selects, always through its verified bundle."""
    active_root = Path(active_root)
    return load_catalog(active_root, bundles_root=active_root / "bundles")


def rollback_active(active_root: Path, bundle_sha256: str) -> Path:
    """Repoint to a previously published bundle after verifying it.

    Rollback is one pointer move. It never touches a history directory and never deletes
    a bundle, so job records written after a snapshot survive.
    """
    return write_active_pointer(active_root, bundle_sha256)


def seed_bundle_files(catalog_root: Path, model_path: Path, model_manifest_path: Path,
                      preset: Mapping[str, Any], policy: Mapping[str, Any],
                      sources: Mapping[str, Any]) -> dict[str, bytes]:
    """Build bundle zero from the packaged assets. The packaged tree is never modified."""
    catalog_root, model_path = Path(catalog_root), Path(model_path)
    catalog = load_catalog(catalog_root)
    manifest = {key: value for key, value in catalog.items() if key != "definitions"}
    manifest["active_bundle_sha256"] = None
    files: dict[str, bytes] = {BUNDLE_CATALOG: _pretty(manifest)}
    for definition in catalog["definitions"]:
        files[f"catalog/definitions/{definition['object_type_id']}.json"] = _pretty(definition)
    files[f"model/{model_path.name}"] = model_path.read_bytes()
    files[f"model/{Path(model_manifest_path).name}"] = Path(model_manifest_path).read_bytes()

    reject_classes = list(_mapping(policy, "policy").get("reject_classes", []))
    seeded = dict(_mapping(preset, "preset"))
    seeded["model_path"] = f"model/{model_path.name}"
    seeded["model_path_root"] = "preset"
    seeded["policy"] = {"initial_reject_classes": reject_classes}
    files[BUNDLE_PRESET] = _pretty(seeded)
    files["policy.json"] = _pretty(dict(_mapping(policy, "policy")))
    files["sources.json"] = _pretty(dict(_mapping(sources, "sources")))
    return files


def _verified_bundle(active_root: Path, bundle_sha256: Any) -> Path:
    if not isinstance(bundle_sha256, str) or not _SHA256_RE.fullmatch(bundle_sha256):
        raise CatalogError("a bundle identity must be a lowercase sha256")
    bundle = Path(active_root) / "bundles" / bundle_sha256
    verify_bundle(bundle)
    return bundle


def _verify_bundle_content(directory: Path, listed: Mapping[str, Any]) -> None:
    """Content rules for every JSON file a bundle carries.

    Every producer runs these: publish_bundle on the staged tree and verify_bundle on a
    published one. A fixed file list would let a later producer past them, and the
    per-definition files would then have no cover at all.
    """
    for relative in sorted(listed):
        if not relative.endswith(".json"):
            continue
        try:
            value = json.loads((directory / relative).read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise CatalogError(f"bundle file is not JSON: {relative}") from error
        # The bundle identity needs no rule here: embedding a digest changes the digest,
        # so no bundle can name itself. test_no_file_inside_a_bundle_names_the_bundle
        # asserts that property directly.
        for text in _structural_strings(value):
            reason = _volatile_reason(text)
            if reason is not None:
                raise CatalogError(f"bundle file carries {reason}: {relative}")
        if relative == BUNDLE_CATALOG and _mapping(
                value, "bundle catalog").get("active_bundle_sha256") is not None:
            raise CatalogError("a bundle catalog must carry a null active_bundle_sha256")
        if relative == BUNDLE_PRESET:
            _verify_bundle_preset(directory, value)


def _verify_bundle_preset(directory: Path, preset: Any) -> None:
    """The model travels with the bundle, so its path stays relative and inside."""
    preset = _mapping(preset, "preset")
    if preset.get("model_path_root") != "preset":
        raise CatalogError("a bundle preset must declare model_path_root preset")
    model_path = preset.get("model_path")
    if not isinstance(model_path, str) or not model_path:
        raise CatalogError("a bundle preset must declare a relative model_path")
    _bundle_relative(model_path)
    if not _inside(directory, directory / model_path).is_file():
        raise CatalogError("the bundle preset model path is missing")


def _bundle_relative(name: Any) -> str:
    """A bundle key is one safe relative posix path."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise CatalogError(f"bundle path is invalid: {name if isinstance(name, str) else type(name).__name__}")
    if name.startswith("/") or _DRIVE_RE.match(name):
        raise CatalogError(f"bundle path must be relative: {name}")
    segments = name.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise CatalogError(f"bundle path has an unsafe segment: {name}")
    return name


def _file_sha256(path: Path) -> tuple[str, int]:
    """Hash one file in chunks, so a large model never loads into memory."""
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_file(source: Path, target: Path) -> None:
    with open(source, "rb") as reader, open(target, "wb") as writer:
        shutil.copyfileobj(reader, writer, _CHUNK_BYTES)


def _string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def _structural_strings(value: Any, key: str | None = None):
    """Yield only the strings this code writes.

    User and provider text may legitimately hold a date, a dotted word, or a slash, so a
    display name or a description is never a path or a timestamp for these rules.
    """
    if key in _USER_TEXT_FIELDS:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for name, item in value.items():
            yield from _structural_strings(item, name if isinstance(name, str) else None)
    elif isinstance(value, list):
        for item in value:
            yield from _structural_strings(item, key)


def _volatile_reason(text: str) -> str | None:
    """Why one structural value may not travel inside a bundle.

    Only the approved exclusions: a host path and runtime metadata. A version string, a
    hex digest, an object type id, and a plain relative file name are ordinary content.
    """
    if text.startswith("/") or _DRIVE_RE.match(text):
        return "an absolute path"
    if text == ".." or text.startswith("../") or "/../" in text:
        return "a path that leaves the bundle"
    if _DATE_RE.search(text):
        return "a timestamp"
    host = _host_name()
    if host is not None and host in text:
        return "a host name"
    return None


def _host_name() -> str | None:
    """Only this machine's exact name, and only when it carries signal.

    A broader guess would reject valid content, which is worse than missing a name.
    """
    try:
        name = socket.gethostname()
    except OSError:
        return None
    return None if len(name) < 4 or name.lower() in _COMMON_HOST_NAMES else name


def _replace_atomically(path: Path, data: bytes) -> None:
    """One temp file in the same directory, flushed to disk, then one rename."""
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".tmp-", delete=False)
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _model_artifact_sha256(data: bytes) -> str:
    try:
        manifest = json.loads(data)
    except json.JSONDecodeError as error:
        raise CatalogError("archived model.manifest.json is not JSON") from error
    value = _mapping(manifest, "model manifest").get("artifact_sha256")
    _sha256(value, "model manifest artifact_sha256")
    return value


def _archive_preview(path: Path) -> dict[str, Any] | None:
    """Describe one archived type so the UI can draw it with its one existing renderer."""
    try:
        visual = json.loads(Path(path).read_text())["visual"]
        axes = [round((low + high) / 2000.0, 9) for low, high in visual["size_mm"]]
        return {"shape": visual["shape"], "axes_m": axes, "rgb": list(visual["rgb"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
