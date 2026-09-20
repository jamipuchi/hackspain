"""Build and validate draft object definitions for generated visual assets.

This module does not register a class, change a profile, train a model, or
activate an object. Mass is an estimate derived from a collision proxy.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
SUPPORTED_PROXY_SHAPES = frozenset({"box", "capsule"})
SIM_FROM_ASSET_QUATERNION_WXYZ = (
    0.7071067811865476,
    0.7071067811865476,
    0.0,
    0.0,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
PHYSICS_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": ["box", "capsule", "unsupported"]},
        "dimensions_m": {
            "type": "array",
            "items": {"type": "number", "minimum": 0.000001, "maximum": 1.0},
            "minItems": 3,
            "maxItems": 3,
        },
        "density_kg_m3": {"type": "number", "minimum": 0, "maximum": 30000},
        "material_assumption": {"type": "string", "maxLength": 1000},
        "limitations": {"type": "string", "minLength": 1, "maxLength": 2000},
        "unsupported_reason": {"type": "string", "maxLength": 1000},
    },
    "required": [
        "shape", "dimensions_m", "density_kg_m3", "material_assumption",
        "limitations", "unsupported_reason",
    ],
    "additionalProperties": False,
}
PHYSICS_SYSTEM_PROMPT = """Propose a draft collision proxy for a generated small object.
Return only schema-compliant JSON. Use dimensions in meters.
Choose box or capsule only when that primitive meaningfully represents collisions.
For a capsule, dimensions are total length, diameter, diameter. Its local axis is +Z.
Use unsupported for rings, multipart objects, or meaningful openings that primitives cannot preserve.
Density is an explicit material assumption. It is not a measurement.
Mass will be computed later from density and collision-proxy volume.
For unsupported, set density to 0 and material_assumption to an empty string.
Always state practical limitations. Do not claim review, registration, training, or activation.
"""


def build_object_definition(
    *,
    description: str,
    recipe_path: str | Path,
    render_metadata_path: str | Path,
    glb_path: str | Path,
    physics_proposal: Mapping[str, Any],
    display_name: str | None = None,
    object_key: str | None = None,
    provider_model: str = "google/gemini-3.8-flash",
    visual_uri: str | None = None,
    sorting_proposal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one validated draft definition from existing generator artifacts.

    ``physics_proposal`` may select box, capsule, or unsupported.
    Supported proposals use full dimensions in meters and a positive density.
    """
    recipe_file = Path(recipe_path)
    metadata_file = Path(render_metadata_path)
    asset_file = Path(glb_path)
    recipe_bytes = recipe_file.read_bytes()
    recipe = json.loads(recipe_bytes)
    metadata = json.loads(metadata_file.read_text())
    asset_bytes = asset_file.read_bytes()
    if len(asset_bytes) < 12 or asset_bytes[:4] != b"glTF":
        raise ValueError("visual asset must have a GLB header")

    recipe_hash = hashlib.sha256(recipe_bytes).hexdigest()
    if metadata.get("recipe_sha256") != recipe_hash:
        raise ValueError("render metadata recipe hash does not match recipe bytes")
    if metadata.get("recipe_name") != recipe.get("name"):
        raise ValueError("render metadata recipe name does not match recipe")
    if metadata.get("glb_units") != "meters" or metadata.get("glb_scale_from_recipe_mm") != 0.001:
        raise ValueError("render metadata must declare meter-scale GLB output")

    resolved_key = _normalize_object_key(object_key or recipe.get("name", ""))
    resolved_name = display_name.strip() if display_name else resolved_key.replace("_", " ").title()
    resolved_description = _nonempty_text(description, "description", maximum=2000)
    resolved_model = _nonempty_text(provider_model, "provider_model", maximum=200)
    dimensions_m = _positive_vector(
        [value * 0.001 for value in metadata.get("bounding_dimensions_mm", [])],
        "render metadata bounding dimensions",
    )
    mesh_count = metadata.get("mesh_counts", {}).get("objects")
    if not _is_integer(mesh_count) or mesh_count <= 0:
        raise ValueError("render metadata mesh count must be a positive integer")

    physics = _build_physics(physics_proposal)
    sorting = _build_sorting(sorting_proposal)
    semantic_payload = {
        "schema_version": SCHEMA_VERSION,
        "object_key": resolved_key,
        "physics": {
            "proxy": physics["proxy"],
            "density_kg_m3": physics["density_kg_m3"],
            "material_assumption": physics["material_assumption"],
            "unsupported_reason": physics["unsupported_reason"],
        },
        "sorting": sorting,
        "engine_coordinate_system": "right_handed_z_up",
    }
    semantic_hash = hashlib.sha256(_canonical_json(semantic_payload)).hexdigest()
    glb_hash = hashlib.sha256(asset_bytes).hexdigest()

    value = {
        "schema_version": SCHEMA_VERSION,
        "object_type_id": f"generated.{resolved_key}.sha256-{semantic_hash}",
        "object_key": resolved_key,
        "display_name": resolved_name,
        "description": resolved_description,
        "lifecycle_state": "draft",
        "review_status": "draft_unreviewed",
        "generator": {
            "provider_model": resolved_model,
            "recipe_sha256": recipe_hash,
        },
        "visual": {
            "visual_asset_id": f"sha256:{glb_hash}",
            "uri": visual_uri if visual_uri is not None else asset_file.as_posix(),
            "media_type": "model/gltf-binary",
            "units": "m",
            "source_up_axis": "+Y",
            "engine_up_axis": "+Z",
            "asset_quaternion_order": "xyzw",
            "server_quaternion_order": "wxyz",
            "engine_coordinate_system": "right_handed_z_up",
            "bounds_dimensions_m": dimensions_m,
            "mesh_count": mesh_count,
            "sim_from_asset_quaternion_wxyz": list(SIM_FROM_ASSET_QUATERNION_WXYZ),
            "runtime_lod_reviewed": False,
        },
        "physics": physics,
        "sorting": sorting,
    }
    validate_object_definition(value)
    return value


def propose_physics(
    *,
    description: str,
    visual_dimensions_m: Sequence[float],
    evidence_dir: str | Path,
    env_file: str | Path = ".env",
    model: str = "google/gemini-3.8-flash",
    live: bool = False,
) -> dict[str, Any]:
    """Request one cached, unreviewed physics proposal from the existing flow.

    A cache miss requires ``live=True``. The function never retries a provider
    request. The returned proposal is suitable for ``build_object_definition``.
    """
    description = _nonempty_text(description, "description", maximum=2000)
    dimensions = _positive_vector(visual_dimensions_m, "visual_dimensions_m")
    model = _nonempty_text(model, "model", maximum=200)
    evidence_path = Path(evidence_dir)
    evidence_path.mkdir(parents=True, exist_ok=True)
    probe, probe_path = _load_generator_probe()
    keys = probe.credentials(Path(env_file))

    payload = {
        "model": model,
        "max_tokens": 1800,
        "provider": {"require_parameters": True},
        "messages": [
            {"role": "system", "content": PHYSICS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "description": description,
                "visual_bounds_dimensions_m": dimensions,
            }, allow_nan=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "draft_object_physics_proposal",
                "strict": True,
                "schema": probe.provider_schema(PHYSICS_PROPOSAL_SCHEMA),
            },
        },
        "reasoning": {"effort": "low"},
    }
    if model != "google/gemini-3.8-flash":
        payload["temperature"] = 0
    _save_immutable(evidence_path / "physics_request.json", payload)
    expected_request_sha256 = hashlib.sha256(
        json.dumps([probe.OPENROUTER_URL, payload], sort_keys=True).encode()
    ).hexdigest()
    cache_path = evidence_path.parent / "cache" / f"{expected_request_sha256}.json"
    key = keys.get("OPENROUTER_API_KEY", "")
    if live and not key and not cache_path.exists():
        raise RuntimeError("OPENROUTER_API_KEY is missing for an uncached live request")
    result = probe.call(
        probe.OPENROUTER_URL,
        key,
        payload,
        evidence_path,
        live,
    )
    if (result.get("request_sha256") != expected_request_sha256 or
            result.get("endpoint") != probe.OPENROUTER_URL):
        raise ValueError("physics provider result provenance does not match the request")
    try:
        choice = result["response"]["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("physics proposal did not finish")
        raw = json.loads(choice["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("physics provider response has an invalid structure") from None
    _validate_llm_proposal(raw)
    if raw["shape"] == "unsupported":
        proposal = {
            "shape": "unsupported",
            "unsupported_reason": raw["unsupported_reason"],
            "limitations": raw["limitations"],
        }
    else:
        proposal = {
            "shape": raw["shape"],
            "dimensions_m": raw["dimensions_m"],
            "density_kg_m3": raw["density_kg_m3"],
            "material_assumption": raw["material_assumption"],
            "limitations": raw["limitations"],
        }
        _build_physics(proposal)
    proposal["provenance"] = {
        "kind": "llm",
        "provider_model": model,
        "request_sha256": expected_request_sha256,
        "generator_probe_sha256": hashlib.sha256(probe_path.read_bytes()).hexdigest(),
    }
    _save_immutable(evidence_path / "physics_proposal.json", proposal)
    return proposal


def validate_object_definition(value: Mapping[str, Any]) -> None:
    """Validate one draft object definition and its derived values."""
    _exact_fields(
        value,
        {
            "schema_version", "object_type_id", "object_key", "display_name",
            "description", "lifecycle_state", "review_status", "generator",
            "visual", "physics", "sorting",
        },
        "definition",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("definition schema version is unsupported")
    object_key = value["object_key"]
    if not isinstance(object_key, str) or not _OBJECT_KEY_RE.fullmatch(object_key):
        raise ValueError("object_key has an invalid format")
    _nonempty_text(value["display_name"], "display_name", maximum=120)
    _nonempty_text(value["description"], "description", maximum=2000)
    if value["lifecycle_state"] != "draft" or value["review_status"] != "draft_unreviewed":
        raise ValueError("new object definitions must remain draft and unreviewed")

    generator = _mapping(value["generator"], "generator")
    _exact_fields(generator, {"provider_model", "recipe_sha256"}, "generator")
    _nonempty_text(generator["provider_model"], "generator.provider_model", maximum=200)
    _sha256(generator["recipe_sha256"], "generator.recipe_sha256")

    visual = _mapping(value["visual"], "visual")
    _exact_fields(
        visual,
        {
            "visual_asset_id", "uri", "media_type", "units", "source_up_axis",
            "engine_up_axis", "asset_quaternion_order", "server_quaternion_order",
            "engine_coordinate_system", "bounds_dimensions_m", "mesh_count",
            "sim_from_asset_quaternion_wxyz", "runtime_lod_reviewed",
        },
        "visual",
    )
    asset_id = visual["visual_asset_id"]
    if not isinstance(asset_id, str) or not asset_id.startswith("sha256:"):
        raise ValueError("visual.visual_asset_id must use sha256")
    _sha256(asset_id.removeprefix("sha256:"), "visual.visual_asset_id")
    _nonempty_text(visual["uri"], "visual.uri", maximum=2000)
    if visual["media_type"] != "model/gltf-binary" or visual["units"] != "m":
        raise ValueError("visual asset must be a meter-scale GLB")
    if visual["source_up_axis"] != "+Y" or visual["engine_up_axis"] != "+Z":
        raise ValueError("visual coordinate conventions are invalid")
    if visual["asset_quaternion_order"] != "xyzw" or visual["server_quaternion_order"] != "wxyz":
        raise ValueError("visual quaternion conventions are invalid")
    if visual["engine_coordinate_system"] != "right_handed_z_up":
        raise ValueError("engine coordinate convention is invalid")
    _positive_vector(visual["bounds_dimensions_m"], "visual.bounds_dimensions_m")
    if not _is_integer(visual["mesh_count"]) or visual["mesh_count"] <= 0:
        raise ValueError("visual.mesh_count must be a positive integer")
    correction = _number_vector(
        visual["sim_from_asset_quaternion_wxyz"],
        "visual.sim_from_asset_quaternion_wxyz",
    )
    if tuple(correction) != SIM_FROM_ASSET_QUATERNION_WXYZ:
        raise ValueError("visual asset correction quaternion is invalid")
    if visual["runtime_lod_reviewed"] is not False:
        raise ValueError("a draft definition cannot claim a reviewed runtime LOD")

    physics = _mapping(value["physics"], "physics")
    _validate_physics(physics)
    sorting = _mapping(value["sorting"], "sorting")
    _validate_sorting(sorting)
    semantic_payload = {
        "schema_version": SCHEMA_VERSION,
        "object_key": object_key,
        "physics": {
            "proxy": physics["proxy"],
            "density_kg_m3": physics["density_kg_m3"],
            "material_assumption": physics["material_assumption"],
            "unsupported_reason": physics["unsupported_reason"],
        },
        "sorting": sorting,
        "engine_coordinate_system": "right_handed_z_up",
    }
    expected_id = f"generated.{object_key}.sha256-{hashlib.sha256(_canonical_json(semantic_payload)).hexdigest()}"
    if value["object_type_id"] != expected_id:
        raise ValueError("object_type_id does not match immutable type semantics")


def _build_physics(proposal: Mapping[str, Any]) -> dict[str, Any]:
    proposal = _mapping(proposal, "physics_proposal")
    shape = proposal.get("shape")
    provenance = _proposal_provenance(proposal.get("provenance"))
    fields = set(proposal) - {"provenance"}
    if shape == "unsupported":
        if fields != {"shape", "unsupported_reason", "limitations"}:
            raise ValueError("physics_proposal has incorrect fields")
        reason = _nonempty_text(proposal["unsupported_reason"], "unsupported_reason", maximum=1000)
        limitations = _nonempty_text(proposal["limitations"], "limitations", maximum=2000)
        return {
            "review_status": "draft_unreviewed",
            "proposal_source": "human_or_llm_unreviewed",
            "proposal_provenance": provenance,
            "proxy": None,
            "density_kg_m3": None,
            "proxy_volume_m3": None,
            "mass_kg": None,
            "mass_basis": None,
            "material_assumption": None,
            "limitations": limitations,
            "unsupported_reason": reason,
        }
    if shape not in SUPPORTED_PROXY_SHAPES:
        raise ValueError("physics proposal shape is unsupported")
    if fields != {"shape", "dimensions_m", "density_kg_m3", "material_assumption", "limitations"}:
        raise ValueError("physics_proposal has incorrect fields")
    dimensions = _positive_vector(proposal["dimensions_m"], "physics_proposal.dimensions_m")
    density = _positive_number(proposal["density_kg_m3"], "physics_proposal.density_kg_m3")
    assumption = _nonempty_text(
        proposal["material_assumption"],
        "physics_proposal.material_assumption",
        maximum=1000,
    )
    limitations = _nonempty_text(proposal["limitations"], "physics_proposal.limitations", maximum=2000)
    proxy, volume = _proxy_and_volume(shape, dimensions)
    return {
        "review_status": "draft_unreviewed",
        "proposal_source": "human_or_llm_unreviewed",
        "proposal_provenance": provenance,
        "proxy": proxy,
        "density_kg_m3": density,
        "proxy_volume_m3": volume,
        "mass_kg": density * volume,
        "mass_basis": "density_times_collision_proxy_volume",
        "material_assumption": assumption,
        "limitations": limitations,
        "unsupported_reason": None,
    }


def _build_sorting(proposal: Mapping[str, Any] | None) -> dict[str, Any]:
    if proposal is None:
        return {
            "status": "unassigned",
            "class_name": None,
            "defect": None,
            "severity": None,
            "proposed_action": None,
        }
    proposal = _mapping(proposal, "sorting_proposal")
    _exact_fields(
        proposal,
        {"class_name", "defect", "severity", "proposed_action"},
        "sorting_proposal",
    )
    value = {"status": "proposal_unreviewed", **proposal}
    _validate_sorting(value)
    return value


def _validate_sorting(value: Mapping[str, Any]) -> None:
    _exact_fields(
        value,
        {"status", "class_name", "defect", "severity", "proposed_action"},
        "sorting",
    )
    if value["status"] == "unassigned":
        if any(value[field] is not None for field in (
            "class_name", "defect", "severity", "proposed_action"
        )):
            raise ValueError("unassigned sorting fields must be null")
        return
    if value["status"] != "proposal_unreviewed":
        raise ValueError("sorting proposal status is invalid")
    class_name = _nonempty_text(value["class_name"], "sorting.class_name", maximum=80)
    if _normalize_object_key(class_name) != class_name:
        raise ValueError("sorting class name must use normalized key format")
    if not isinstance(value["defect"], bool):
        raise ValueError("sorting defect must be boolean")
    if value["severity"] not in {"none", "minor", "major", "foreign"}:
        raise ValueError("sorting severity is invalid")
    if value["proposed_action"] not in {"keep", "reject"}:
        raise ValueError("sorting proposed action is invalid")


def _validate_physics(physics: Mapping[str, Any]) -> None:
    _exact_fields(
        physics,
        {
            "review_status", "proposal_source", "proxy", "density_kg_m3",
            "proxy_volume_m3", "mass_kg", "mass_basis", "material_assumption",
            "limitations", "unsupported_reason", "proposal_provenance",
        },
        "physics",
    )
    if physics["review_status"] != "draft_unreviewed":
        raise ValueError("physics proposal must remain unreviewed")
    if physics["proposal_source"] != "human_or_llm_unreviewed":
        raise ValueError("physics proposal source is invalid")
    _proposal_provenance(physics["proposal_provenance"])
    _nonempty_text(physics["limitations"], "physics.limitations", maximum=2000)
    if physics["proxy"] is None:
        if any(physics[field] is not None for field in (
            "density_kg_m3", "proxy_volume_m3", "mass_kg", "mass_basis", "material_assumption"
        )):
            raise ValueError("unsupported physics must not contain derived physical values")
        _nonempty_text(physics["unsupported_reason"], "physics.unsupported_reason", maximum=1000)
        return

    proxy = _mapping(physics["proxy"], "physics.proxy")
    shape = proxy.get("shape")
    if shape == "box":
        _exact_fields(proxy, {"shape", "half_extents_m"}, "physics.proxy")
        dimensions = [2 * value for value in _positive_vector(proxy["half_extents_m"], "physics.proxy.half_extents_m")]
    elif shape == "capsule":
        _exact_fields(proxy, {"shape", "radius_m", "half_length_m", "local_axis"}, "physics.proxy")
        radius = _positive_number(proxy["radius_m"], "physics.proxy.radius_m")
        half_length = _positive_number(proxy["half_length_m"], "physics.proxy.half_length_m")
        if proxy["local_axis"] != "+Z":
            raise ValueError("capsule local axis must be +Z")
        dimensions = [2 * (half_length + radius), 2 * radius, 2 * radius]
    else:
        raise ValueError("physics proxy shape is unsupported")
    _, expected_volume = _proxy_and_volume(shape, dimensions)
    density = _positive_number(physics["density_kg_m3"], "physics.density_kg_m3")
    volume = _positive_number(physics["proxy_volume_m3"], "physics.proxy_volume_m3")
    mass = _positive_number(physics["mass_kg"], "physics.mass_kg")
    if not math.isclose(volume, expected_volume, rel_tol=1e-12):
        raise ValueError("physics proxy volume is inconsistent")
    if not math.isclose(mass, density * volume, rel_tol=1e-12):
        raise ValueError("physics mass is inconsistent")
    if physics["mass_basis"] != "density_times_collision_proxy_volume":
        raise ValueError("physics mass basis is invalid")
    _nonempty_text(physics["material_assumption"], "physics.material_assumption", maximum=1000)
    if physics["unsupported_reason"] is not None:
        raise ValueError("supported physics cannot contain an unsupported reason")


def _proxy_and_volume(shape: str, dimensions: Sequence[float]) -> tuple[dict[str, Any], float]:
    x, y, z = dimensions
    if shape == "box":
        half_extents = [x / 2, y / 2, z / 2]
        return {"shape": shape, "half_extents_m": half_extents}, 8 * math.prod(half_extents)
    if not math.isclose(y, z, rel_tol=1e-6):
        raise ValueError("capsule transverse dimensions must be equal")
    radius = y / 2
    if x <= 2 * radius:
        raise ValueError("capsule total length must exceed its diameter")
    half_length = (x - 2 * radius) / 2
    volume = math.pi * radius ** 2 * (2 * half_length) + 4 / 3 * math.pi * radius ** 3
    return {
        "shape": shape,
        "radius_m": radius,
        "half_length_m": half_length,
        "local_axis": "+Z",
    }, volume


def _validate_llm_proposal(value: Any) -> None:
    value = _mapping(value, "physics provider proposal")
    _exact_fields(value, set(PHYSICS_PROPOSAL_SCHEMA["required"]), "physics provider proposal")
    shape = value["shape"]
    if shape not in SUPPORTED_PROXY_SHAPES | {"unsupported"}:
        raise ValueError("physics provider proposal shape is unsupported")
    dimensions = _positive_vector(value["dimensions_m"], "physics provider proposal dimensions")
    if any(item < 0.000001 or item > 1.0 for item in dimensions):
        raise ValueError("physics provider proposal dimensions are outside schema bounds")
    limitations = _nonempty_text(value["limitations"], "physics provider proposal limitations", maximum=2000)
    if not limitations:
        raise ValueError("physics provider proposal limitations are required")
    if shape == "unsupported":
        if value["density_kg_m3"] != 0 or value["material_assumption"] != "":
            raise ValueError("unsupported provider proposal cannot claim density or material")
        _nonempty_text(value["unsupported_reason"], "physics provider unsupported reason", maximum=1000)
    else:
        density = _positive_number(value["density_kg_m3"], "physics provider density")
        if density > 30000:
            raise ValueError("physics provider density is outside schema bounds")
        _nonempty_text(value["material_assumption"], "physics provider material assumption", maximum=1000)
        if value["unsupported_reason"] != "":
            raise ValueError("supported provider proposal cannot contain an unsupported reason")


def _proposal_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "kind": "manual",
            "provider_model": None,
            "request_sha256": None,
            "generator_probe_sha256": None,
        }
    value = _mapping(value, "physics proposal provenance")
    _exact_fields(
        value,
        {"kind", "provider_model", "request_sha256", "generator_probe_sha256"},
        "physics proposal provenance",
    )
    if value["kind"] == "manual":
        if any(value[field] is not None for field in (
            "provider_model", "request_sha256", "generator_probe_sha256"
        )):
            raise ValueError("manual proposal provenance cannot contain provider data")
    elif value["kind"] == "llm":
        _nonempty_text(value["provider_model"], "physics proposal provider model", maximum=200)
        _sha256(value["request_sha256"], "physics proposal request hash")
        _sha256(value["generator_probe_sha256"], "physics proposal generator hash")
    else:
        raise ValueError("physics proposal provenance kind is invalid")
    return dict(value)


def _load_generator_probe():
    # One generator. The service and this physics proposal path load the same module.
    path = Path(__file__).resolve().parent / "generator" / "probe.py"
    spec = importlib.util.spec_from_file_location("coffee_object_generator_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("object generator probe cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def _save_immutable(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text() != encoded:
            raise RuntimeError(f"evidence already exists with different data: {path}")
        return
    path.write_text(encoded)


def _normalize_object_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("object_key must be text")
    result = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not result or not _OBJECT_KEY_RE.fullmatch(result):
        raise ValueError("object_key cannot be normalized")
    return result


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} has incorrect fields")


def _nonempty_text(value: Any, path: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{path} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _number_vector(value: Any, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{path} must contain four numbers")
    return [_finite_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _positive_vector(value: Any, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{path} must contain three numbers")
    return [_positive_number(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path} must be a finite number")
    return float(value)


def _positive_number(value: Any, path: str) -> float:
    result = _finite_number(value, path)
    if result <= 0:
        raise ValueError(f"{path} must be positive")
    return result


def _sha256(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{path} must be a lowercase SHA-256 digest")


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
