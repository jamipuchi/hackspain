"""Check generated-object artifacts against the current coffee sorter contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]
COFFEE = ROOT / "sim/coffee_sorter"
OBJECT_GENERATION = ROOT / "thoughts/taras/research/coffee-quality/object-generation"
sys.path.insert(0, str(OBJECT_GENERATION))
sys.path.insert(0, str(COFFEE))

from classifier import Model
from probe import PART_FIELDS, validate_recipe
from profiles import PROFILES, BOX, CAPSULE, ELLIPSOID, HALF
from vision import FEATURES


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB file is too short")
    magic, version, declared_length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("GLB header is invalid")
    offset = 12
    document = None
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == 0x4E4F534A:
            document = json.loads(chunk)
    if document is None:
        raise ValueError("GLB JSON chunk is missing")
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("GLB asset version is not 2.0")
    return document


def inspect_fixture(case_dir: Path) -> dict:
    recipe_path = case_dir / "recipe.json"
    render_dir = case_dir / "render"
    render_path = render_dir / "render.json"
    glb_path = render_dir / "object.glb"
    recipe = json.loads(recipe_path.read_text())
    validate_recipe(recipe)
    render = json.loads(render_path.read_text())
    if render["recipe_sha256"] != sha256(recipe_path):
        raise ValueError(f"{case_dir.name}: render metadata uses another recipe")
    if render["glb_units"] != "meters" or render["glb_scale_from_recipe_mm"] != 0.001:
        raise ValueError(f"{case_dir.name}: GLB unit metadata is invalid")
    document = glb_json(glb_path)
    mesh_nodes = [
        {
            key: node[key]
            for key in ("name", "mesh", "translation", "rotation", "scale", "matrix")
            if key in node
        }
        for node in document.get("nodes", [])
        if "mesh" in node
    ]
    return {
        "case": case_dir.name,
        "recipe_sha256": sha256(recipe_path),
        "glb_sha256": sha256(glb_path),
        "glb_bytes": glb_path.stat().st_size,
        "recipe_part_kinds": [part["kind"] for part in recipe["parts"]],
        "bounding_dimensions_mm": render["bounding_dimensions_mm"],
        "mesh_count": len(document.get("meshes", [])),
        "single_mesh_node": mesh_nodes[0] if len(mesh_nodes) == 1 else None,
        "primitive_count": sum(
            len(mesh.get("primitives", [])) for mesh in document.get("meshes", [])
        ),
        "material_count": len(document.get("materials", [])),
        "image_count": len(document.get("images", [])),
        "texture_count": len(document.get("textures", [])),
        "extensions_required": document.get("extensionsRequired", []),
        "generator": document["asset"].get("generator"),
    }


def model_record(path: Path, profile_name: str) -> dict:
    model = Model.load(path)
    profile = PROFILES[profile_name]
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "classes": list(model.classes),
        "profile_classes": profile.names,
        "classes_match_profile": list(model.classes) == profile.names,
        "features_match_runtime": model.meta.get("features") == FEATURES,
        "feature_count": len(FEATURES),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-revision")
    args = parser.parse_args()

    gemini = OBJECT_GENERATION / "results/gemini"
    fixtures = [inspect_fixture(gemini / name) for name in ("earring", "logo", "star")]
    live_js = (COFFEE / "live_web/live.js").read_text()
    proof = (COFFEE / "visual_assets/browser/proof.html").read_text()
    preset = json.loads((COFFEE / "configs/continuous_demo.json").read_text())
    configured_model = COFFEE / preset["model_path"]

    model_records = []
    for profile_name in ("green_arabica", "roasted"):
        path = COFFEE / f"runs/generalization/{profile_name}/model/{profile_name}.joblib"
        if path.is_file():
            model_records.append(model_record(path, profile_name))

    source_paths = (
        COFFEE / "engine.py",
        COFFEE / "live.py",
        COFFEE / "live_web/live.js",
        COFFEE / "profiles.py",
        COFFEE / "scene.py",
        COFFEE / "sim.py",
        COFFEE / "vision.py",
        COFFEE / "classifier.py",
        COFFEE / "controller.py",
        COFFEE / "rolling_scores.py",
        OBJECT_GENERATION / "probe.py",
        OBJECT_GENERATION / "render_recipe.py",
    )
    current_revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    source_revision = current_revision
    if args.baseline_revision:
        source_revision = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", f"{args.baseline_revision}^{{commit}}"],
            text=True,
        ).strip()
        relative_paths = [str(path.relative_to(ROOT)) for path in source_paths]
        check = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--quiet", source_revision, "--", *relative_paths]
        )
        if check.returncode != 0:
            raise RuntimeError("Audited source files differ from the requested baseline revision")

    result = {
        "source_revision": source_revision,
        "execution_revision": current_revision,
        "recipe_kinds": PART_FIELDS["kind"]["enum"],
        "physics_pool_shapes": [ELLIPSOID, HALF, BOX, CAPSULE],
        "direct_recipe_physics_shape_overlap": sorted(
            set(PART_FIELDS["kind"]["enum"]) & {ELLIPSOID, HALF, BOX, CAPSULE}
        ),
        "profiles": {
            name: [
                {
                    "class_name": spec.name,
                    "shape": spec.shape,
                    "defect": spec.defect,
                    "severity": spec.severity,
                }
                for spec in profile.classes
            ]
            for name, profile in PROFILES.items()
        },
        "generated_fixtures": fixtures,
        "models": model_records,
        "configured_live_model": {
            "path": str(configured_model.relative_to(ROOT)),
            "exists_in_checkout": configured_model.is_file(),
            "adjacent_manifest_exists": configured_model.with_suffix(".manifest.json").is_file(),
        },
        "source_checks": {
            "live_ui_hardcodes_stone": "class_name: 'stone'" in live_js,
            "browser_proof_requires_one_mesh_and_one_material": (
                "expected one primitive/material" in proof
            ),
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in source_paths
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "passed", "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
