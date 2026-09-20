"""Shared object catalog: frozen built-in values, validation, and Wall of Fame paging."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import object_catalog
from object_catalog import (
    ANOMALY_REFERENCE_LABEL,
    MASS_BASIS,
    MAX_WALL_PAGE,
    CatalogError,
    archive_type,
    candidate_catalog,
    catalog_labels,
    catalog_revision,
    definition_sha256,
    load_catalog,
    load_definition,
    read_glb_evidence,
    recipe_rgb,
    require_label_order,
    select_victim,
    type_definition_from_draft,
    validate_type_definition,
    wall_of_fame_page,
    write_catalog,
)
from object_definitions import SIM_FROM_ASSET_QUATERNION_WXYZ, build_object_definition
from profiles import PROFILES, class_spec, profile_from_catalog

STAR = Path(__file__).resolve().parents[2] / (
    "thoughts/taras/research/coffee-quality/object-generation/results/gemini/star")

# Frozen copy of the pre-migration literals in profiles.py at afad65b. Each row is
# (name, prior, shape, size_mm, rgb, rgb_jitter, density, texture, defect, severity).
BEAN = ((4.2, 5.6), (3.1, 4.0), (2.2, 2.9))
ROAST = ((4.6, 6.0), (3.5, 4.4), (2.6, 3.2))
STONE = ((2.5, 5.0), (2.0, 4.0), (1.5, 3.5))
STICK = ((8.0, 16.0), (0.9, 1.4), (0.9, 1.4))
EXPECTED = {
    "green_arabica": {
        "belt_rgb": (0.10, 0.22, 0.62),
        "classes": [
            ("good", 0.86, "ellipsoid", BEAN, (0.50, 0.60, 0.46), 0.06, 1150.0, "good", False, "none"),
            ("faded", 0.03, "ellipsoid", BEAN, (0.70, 0.66, 0.42), 0.05, 1150.0, "faded", True, "minor"),
            ("black", 0.025, "ellipsoid", BEAN, (0.13, 0.11, 0.09), 0.03, 1150.0, "black", True, "major"),
            ("sour", 0.025, "ellipsoid", BEAN, (0.50, 0.32, 0.18), 0.05, 1150.0, "sour", True, "major"),
            ("insect", 0.02, "ellipsoid", BEAN, (0.50, 0.60, 0.46), 0.06, 1150.0, "insect", True, "major"),
            ("broken", 0.02, "half", BEAN, (0.50, 0.60, 0.46), 0.06, 1150.0, "good", True, "major"),
            ("shell", 0.01, "ellipsoid", ((4.0, 5.2), (2.8, 3.6), (0.6, 1.0)), (0.62, 0.70, 0.55), 0.05, 600, "good", True, "major"),
            ("husk", 0.01, "ellipsoid", ((4.5, 7.0), (3.0, 5.0), (0.3, 0.6)), (0.85, 0.78, 0.60), 0.05, 300, None, True, "foreign"),
            ("stone", 0.005, "box", STONE, (0.45, 0.44, 0.42), 0.08, 2600, None, True, "foreign"),
            ("stick", 0.005, "capsule", STICK, (0.42, 0.30, 0.16), 0.06, 700, None, True, "foreign"),
        ],
        "priors": [0.8514851485148517, 0.02970297029702971, 0.02475247524752476, 0.02475247524752476,
                   0.019801980198019806, 0.019801980198019806, 0.009900990099009903,
                   0.009900990099009903, 0.004950495049504951, 0.004950495049504951],
    },
    "roasted": {
        "belt_rgb": (0.10, 0.22, 0.62),
        "classes": [
            ("good", 0.90, "ellipsoid", ROAST, (0.36, 0.22, 0.13), 0.05, 650, "roast", False, "none"),
            ("quaker", 0.04, "ellipsoid", ROAST, (0.72, 0.56, 0.36), 0.05, 600, "faded", True, "major"),
            ("burnt", 0.02, "ellipsoid", ROAST, (0.08, 0.07, 0.06), 0.02, 550, "black", True, "major"),
            ("broken", 0.03, "half", ROAST, (0.36, 0.22, 0.13), 0.05, 650, "roast", True, "major"),
            ("stone", 0.005, "box", STONE, (0.45, 0.44, 0.42), 0.08, 2600, None, True, "foreign"),
            ("stick", 0.005, "capsule", STICK, (0.42, 0.30, 0.16), 0.06, 700, None, True, "foreign"),
        ],
        "priors": [0.90, 0.04, 0.02, 0.03, 0.005, 0.005],
    },
}


def builtin_definition(label="good", **changes):
    value = {
        "schema_version": 1,
        "object_type_id": f"builtin.test.{label}",
        "display_name": f"Test {label}",
        "classifier_label": label,
        "provenance": {"kind": "builtin", "source": "test fixture", "source_sha256": None},
        "feed": {"prior": 0.5},
        "truth": {"defect": True, "severity": "major"},
        "visual": {
            "shape": "ellipsoid",
            "size_mm": [[4.2, 5.6], [3.1, 4.0], [2.2, 2.9]],
            "rgb": [0.50, 0.60, 0.46],
            "rgb_jitter": 0.06,
            "texture": "good",
            "asset": None,
        },
        "physics": {
            "contact_shape": "ellipsoid",
            "density_kg_m3": 1150.0,
            "mass_basis": MASS_BASIS,
            "measurement_status": "unmeasured_estimate",
        },
        "lifecycle_state": "active_ready",
        "validation_status": "validated",
    }
    value.update(copy.deepcopy(changes))
    return value


def generated_definition(label="star_token", **changes):
    digest = hashlib.sha256(f"glb-{label}".encode()).hexdigest()
    value = builtin_definition(label)
    value.update({
        "object_type_id": f"generated.{label}",
        "display_name": f"Generated {label}",
        "provenance": {
            "kind": "generated",
            "source": "item job 0f1b",
            "source_sha256": hashlib.sha256(f"recipe-{label}".encode()).hexdigest(),
        },
        "visual": {
            "shape": "box",
            "size_mm": [[8.0, 8.0], [6.0, 6.0], [1.0, 1.0]],
            "rgb": [0.82, 0.68, 0.21],
            "rgb_jitter": 0.04,
            "texture": None,
            "asset": {
                "visual_asset_id": f"sha256:{digest}",
                "glb_sha256": digest,
                "units": "m",
                "source_up_axis": "+Y",
                "engine_up_axis": "+Z",
                "sim_from_asset_quaternion_wxyz": list(SIM_FROM_ASSET_QUATERNION_WXYZ),
            },
        },
        "physics": {
            "contact_shape": "box",
            "density_kg_m3": 1200.0,
            "mass_basis": MASS_BASIS,
            "measurement_status": "unmeasured_estimate",
        },
    })
    value.update(copy.deepcopy(changes))
    return value


def star_draft(**changes):
    """One valid draft definition built from the tracked star generator artifacts."""
    values = {
        "description": "A small five-point star token.",
        "recipe_path": STAR / "recipe.json",
        "render_metadata_path": STAR / "render/render.json",
        "glb_path": STAR / "render/object.glb",
        "object_key": "star_token",
        "physics_proposal": {
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Assumed density of a light decorative token.",
            "limitations": "Density and contact geometry are unmeasured proxy estimates.",
        },
        "sorting_proposal": {"class_name": "star_token", "defect": False, "severity": "none",
                             "proposed_action": "keep"},
    }
    values.update(changes)
    return build_object_definition(**values)


def tiny_glb(index_count=3, primitives=1, meshes=1, **primitive):
    """A few bytes of valid glTF 2.0, built in code: one padded JSON chunk, no binary."""
    part = {"attributes": {"POSITION": 1}, "indices": 0, **primitive}
    return glb_container(json.dumps({
        "asset": {"version": "2.0"},
        "accessors": [{"componentType": 5123, "count": index_count, "type": "SCALAR"},
                      {"componentType": 5126, "count": 3, "type": "VEC3"}],
        "meshes": [{"primitives": [part] * primitives}] * meshes}).encode())


def glb_container(chunk, version=2, chunk_type=0x4E4F534A, declared=None, total=None):
    chunk += b" " * (-len(chunk) % 4)
    return b"glTF" + struct.pack(
        "<IIII", version, 20 + len(chunk) if total is None else total,
        len(chunk) if declared is None else declared, chunk_type) + chunk


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class CatalogRootTest(unittest.TestCase):
    """Every catalog test builds its own temporary root. No test touches the repo data."""

    def make_root(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def write_catalog(self, root, definitions, manifest="active/catalog.json", **changes):
        for value in definitions:
            write_json(root / "definitions" / f"{value['object_type_id']}.json", value)
        ids = [value["object_type_id"] for value in definitions]
        hashes = {value["object_type_id"]: definition_sha256(value) for value in definitions}
        catalog = {
            "schema_version": 1,
            "profile_name": "test",
            "belt_rgb": [0.10, 0.22, 0.62],
            "catalog_revision": "",
            "max_active_types": len(ids),
            "active_type_ids": ids,
            "definition_sha256": hashes,
            "active_bundle_sha256": None,
        }
        catalog.update(changes)
        if "catalog_revision" not in changes:
            catalog["catalog_revision"] = catalog_revision(
                catalog["active_type_ids"], catalog["definition_sha256"])
        write_json(root / manifest, catalog)
        return catalog

    def make_catalog(self, labels=("good", "stone"), **changes):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition(label) for label in labels], **changes)
        return root


class BuiltinMigrationTest(unittest.TestCase):
    """The data migration must not change one built-in value."""

    def test_profiles_match_the_frozen_pre_migration_values(self):
        self.assertEqual(["green_arabica", "roasted"], list(PROFILES))
        for name, expected in EXPECTED.items():
            profile = PROFILES[name]
            with self.subTest(profile=name):
                self.assertEqual(name, profile.name)
                self.assertIsInstance(profile.belt_rgb, tuple)
                self.assertEqual(expected["belt_rgb"], profile.belt_rgb)
                self.assertEqual([row[0] for row in expected["classes"]], profile.names)
                self.assertEqual(expected["priors"], [float(p) for p in profile.priors()])
            for row, spec in zip(expected["classes"], profile.classes):
                with self.subTest(profile=name, cls=row[0]):
                    self.assertEqual(row, (spec.name, spec.prior, spec.shape, spec.size_mm, spec.rgb,
                                           spec.rgb_jitter, spec.density, spec.texture,
                                           spec.defect, spec.severity))
                    self.assertIsInstance(spec.size_mm, tuple)
                    self.assertIsInstance(spec.rgb, tuple)
                    for axis in spec.size_mm:
                        self.assertIsInstance(axis, tuple)
                    self.assertIs(type(row[6]), type(spec.density))

    def test_priors_keep_the_defect_boost_behaviour(self):
        profile = PROFILES["green_arabica"]
        boosted = profile.priors(defect_boost=2.0)

        self.assertAlmostEqual(1.0, float(boosted.sum()))
        self.assertLess(boosted[0], profile.priors()[0])

    def test_repo_catalogs_stay_within_their_declared_active_limits(self):
        green = load_catalog()
        roasted = load_catalog(manifest="builtin/roasted.catalog.json")

        self.assertEqual(10, green["max_active_types"])
        self.assertEqual(6, roasted["max_active_types"])
        self.assertEqual(green["max_active_types"], len(green["definitions"]))
        self.assertEqual(roasted["max_active_types"], len(roasted["definitions"]))
        self.assertEqual(PROFILES["green_arabica"].names, catalog_labels(green))
        self.assertEqual(PROFILES["roasted"].names, catalog_labels(roasted))


class DefinitionValidationTest(CatalogRootTest):
    """One validator accepts built-in and generated definitions and rejects the rest."""

    def asset_change(self, **changes):
        visual = copy.deepcopy(generated_definition()["visual"])
        visual["asset"].update(changes)
        return visual

    def test_builtin_and_generated_definitions_share_one_validator(self):
        for value in (builtin_definition(), generated_definition()):
            with self.subTest(kind=value["provenance"]["kind"]):
                validate_type_definition(value)

        spec = class_spec(generated_definition())
        self.assertEqual("box", spec.shape)
        self.assertEqual(((8.0, 8.0), (6.0, 6.0), (1.0, 1.0)), spec.size_mm)
        self.assertEqual(1200.0, spec.density)

    def test_generated_definition_activates_next_to_a_builtin(self):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition("good"), generated_definition()])
        catalog = load_catalog(root)
        profile = profile_from_catalog(catalog)

        self.assertEqual(["good", "star_token"], catalog_labels(catalog))
        self.assertEqual((0.10, 0.22, 0.62), profile.belt_rgb)
        self.assertEqual("box", profile.by_name("star_token").shape)

    def test_rejects_invalid_definitions(self):
        cases = {
            "draft lifecycle": builtin_definition(lifecycle_state="draft"),
            "unknown lifecycle": builtin_definition(lifecycle_state="active"),
            "unreviewed validation": builtin_definition(validation_status="draft_unreviewed"),
            "generated ellipsoid": generated_definition(visual={
                **generated_definition()["visual"], "shape": "ellipsoid"}),
            "generated half": generated_definition(visual={
                **generated_definition()["visual"], "shape": "half"}),
            "generated unknown shape": generated_definition(visual={
                **generated_definition()["visual"], "shape": "torus"}),
            "builtin unknown shape": builtin_definition(visual={
                **builtin_definition()["visual"], "shape": "torus"}),
            "contact shape disagrees": builtin_definition(physics={
                **builtin_definition()["physics"], "contact_shape": "box"}),
            "asset units": generated_definition(visual=self.asset_change(units="mm")),
            "asset source axis": generated_definition(visual=self.asset_change(source_up_axis="+Z")),
            "asset engine axis": generated_definition(visual=self.asset_change(engine_up_axis="+Y")),
            "asset pose": generated_definition(visual=self.asset_change(
                sim_from_asset_quaternion_wxyz=[1.0, 0.0, 0.0, 0.0])),
            "asset id disagrees with hash": generated_definition(
                visual=self.asset_change(visual_asset_id="sha256:" + "b" * 64)),
            "generated without an asset": generated_definition(visual={
                **generated_definition()["visual"], "asset": None}),
            "builtin claims a source hash": builtin_definition(provenance={
                "kind": "builtin", "source": "test", "source_sha256": "c" * 64}),
            "unknown provenance kind": builtin_definition(provenance={
                "kind": "imported", "source": "test", "source_sha256": None}),
            "measured mass claim": builtin_definition(physics={
                **builtin_definition()["physics"], "measurement_status": "measured"}),
            "wrong mass basis": builtin_definition(physics={
                **builtin_definition()["physics"], "mass_basis": "weighed"}),
            "truth disagrees": builtin_definition(truth={"defect": False, "severity": "major"}),
            "unknown severity": builtin_definition(truth={"defect": True, "severity": "critical"}),
            "invalid label": builtin_definition(classifier_label="Good Bean"),
            "invalid identifier": builtin_definition(object_type_id="../evil"),
            "empty display name": builtin_definition(display_name="  "),
            "wrong schema version": builtin_definition(schema_version=2),
            "extra definition field": {**builtin_definition(), "notes": "extra"},
            "missing definition field": {k: v for k, v in builtin_definition().items() if k != "feed"},
            "extra provenance field": builtin_definition(provenance={
                "kind": "builtin", "source": "test", "source_sha256": None, "extra": 1}),
            "extra visual field": builtin_definition(visual={
                **builtin_definition()["visual"], "extra": 1}),
            "extra physics field": builtin_definition(physics={
                **builtin_definition()["physics"], "extra": 1}),
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(CatalogError):
                    validate_type_definition(value)

    def test_rejects_non_finite_and_out_of_bound_numbers(self):
        cases = {
            "nan prior": builtin_definition(feed={"prior": float("nan")}),
            "infinite prior": builtin_definition(feed={"prior": float("inf")}),
            "zero prior": builtin_definition(feed={"prior": 0}),
            "prior above one": builtin_definition(feed={"prior": 1.5}),
            "zero density": builtin_definition(physics={
                **builtin_definition()["physics"], "density_kg_m3": 0}),
            "density above bound": builtin_definition(physics={
                **builtin_definition()["physics"], "density_kg_m3": 40000}),
            "nan density": builtin_definition(physics={
                **builtin_definition()["physics"], "density_kg_m3": float("nan")}),
            "rgb above one": builtin_definition(visual={
                **builtin_definition()["visual"], "rgb": [1.5, 0.5, 0.5]}),
            "negative jitter": builtin_definition(visual={
                **builtin_definition()["visual"], "rgb_jitter": -0.1}),
            "zero size": builtin_definition(visual={
                **builtin_definition()["visual"], "size_mm": [[0.0, 5.0], [3.1, 4.0], [2.2, 2.9]]}),
            "inverted size": builtin_definition(visual={
                **builtin_definition()["visual"], "size_mm": [[6.0, 5.0], [3.1, 4.0], [2.2, 2.9]]}),
            "size above bound": builtin_definition(visual={
                **builtin_definition()["visual"], "size_mm": [[4.2, 1200.0], [3.1, 4.0], [2.2, 2.9]]}),
            "two size axes": builtin_definition(visual={
                **builtin_definition()["visual"], "size_mm": [[4.2, 5.6], [3.1, 4.0]]}),
            "bool prior": builtin_definition(feed={"prior": True}),
            "bool density": builtin_definition(physics={
                **builtin_definition()["physics"], "density_kg_m3": True}),
            "bool jitter": builtin_definition(visual={
                **builtin_definition()["visual"], "rgb_jitter": True}),
            "text prior": builtin_definition(feed={"prior": "0.5"}),
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(CatalogError):
                    validate_type_definition(value)

    def test_rejects_a_non_finite_number_read_from_a_file(self):
        root = self.make_root()
        path = root / "definitions" / "builtin.test.good.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(builtin_definition(feed={"prior": float("nan")})))

        with self.assertRaises(CatalogError):
            load_definition(path)


class CatalogLoadingTest(CatalogRootTest):
    """Only a validated manifest can select active definitions."""

    def test_loads_only_the_definitions_that_the_manifest_lists(self):
        root = self.make_catalog(("good", "stone"))
        write_json(root / "definitions" / "builtin.test.husk.json", builtin_definition("husk"))

        catalog = load_catalog(root)

        self.assertEqual(["good", "stone"], catalog_labels(catalog))
        self.assertEqual(2, len(catalog["definitions"]))
        self.assertTrue((root / "definitions" / "builtin.test.husk.json").is_file())

    def test_never_imports_a_definition_directory_python_file(self):
        root = self.make_catalog(("good", "stone"))
        sentinel = root / "sentinel.txt"
        code = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"
        for name in ("evil.py", "builtin.test.good.py", "builtin.test.husk.py"):
            (root / "definitions" / name).write_text(code)
        before = set(sys.modules)

        catalog = load_catalog(root)

        self.assertEqual(["good", "stone"], catalog_labels(catalog))
        self.assertFalse(sentinel.exists())
        self.assertEqual(before, set(sys.modules))
        self.assertNotIn("evil", sys.modules)

    def test_rejects_invalid_manifests(self):
        good, stone = builtin_definition("good"), builtin_definition("stone")
        twin = builtin_definition("good")
        twin["object_type_id"] = "builtin.test.twin"
        cases = {
            "duplicate identifiers": ([good, good], {}),
            "count above the limit": ([good, stone], {"max_active_types": 3}),
            "count below the limit": ([good, stone], {"max_active_types": 1}),
            "bool limit": ([good], {"max_active_types": True}),
            "zero limit": ([good], {"max_active_types": 0}),
            "wrong revision": ([good, stone], {"catalog_revision": "0" * 64}),
            "path traversal identifier": ([good], {
                "active_type_ids": ["../evil"],
                "definition_sha256": {"../evil": definition_sha256(good)}}),
            "unhashed identifier": ([good, stone], {
                "definition_sha256": {"builtin.test.good": definition_sha256(good)},
                "catalog_revision": "0" * 64}),
            "extra catalog field": ([good], {"notes": "extra"}),
            "wrong schema version": ([good], {"schema_version": 2}),
            "belt colour out of range": ([good], {"belt_rgb": [1.4, 0.2, 0.6]}),
            "belt colour length": ([good], {"belt_rgb": [0.1, 0.2]}),
            "empty profile name": ([good], {"profile_name": " "}),
            "invalid bundle hash": ([good], {"active_bundle_sha256": "short"}),
            "duplicate labels": ([good, twin], {}),
        }
        for name, (definitions, changes) in cases.items():
            with self.subTest(case=name):
                root = self.make_root()
                self.write_catalog(root, definitions, **changes)
                with self.assertRaises(CatalogError):
                    load_catalog(root)

    def test_rejects_a_mismatched_definition_hash(self):
        root = self.make_catalog(("good", "stone"))
        drifted = builtin_definition("good")
        drifted["feed"]["prior"] = 0.4
        write_json(root / "definitions" / "builtin.test.good.json", drifted)

        with self.assertRaisesRegex(CatalogError, "hash does not match"):
            load_catalog(root)

    def test_rejects_a_definition_that_renames_itself(self):
        root = self.make_root()
        renamed = builtin_definition("good")
        renamed["object_type_id"] = "builtin.test.other"
        write_json(root / "definitions" / "builtin.test.good.json", renamed)
        self.write_catalog(root, [builtin_definition("stone")],
                           active_type_ids=["builtin.test.good", "builtin.test.stone"],
                           max_active_types=2,
                           definition_sha256={
                               "builtin.test.good": definition_sha256(renamed),
                               "builtin.test.stone": definition_sha256(builtin_definition("stone"))})

        with self.assertRaisesRegex(CatalogError, "file name"):
            load_catalog(root)

    def test_rejects_an_archived_definition_listed_as_active(self):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition("good", lifecycle_state="archived")])

        with self.assertRaisesRegex(CatalogError, "archived definition cannot be active"):
            load_catalog(root)

    def test_rejects_a_missing_manifest_or_definition(self):
        root = self.make_root()
        with self.assertRaisesRegex(CatalogError, "manifest cannot be read"):
            load_catalog(root)

        root = self.make_catalog(("good",))
        (root / "definitions" / "builtin.test.good.json").unlink()
        with self.assertRaisesRegex(CatalogError, "cannot be read"):
            load_catalog(root)

    def test_model_labels_must_equal_the_catalog_order(self):
        catalog = load_catalog(self.make_catalog(("good", "stone", "husk")))

        require_label_order(catalog, ["good", "stone", "husk"])
        for wrong in (["stone", "good", "husk"], ["good", "stone"], ["good", "stone", "husk", "stick"]):
            with self.subTest(labels=wrong):
                with self.assertRaisesRegex(CatalogError, "model label order"):
                    require_label_order(catalog, wrong)


class WallOfFameTest(CatalogRootTest):
    """Archived types stay inactive, immutable, and readable in bounded pages."""

    def archive(self, root, label, minute=0, **kwargs):
        return archive_type(root, builtin_definition(label),
                            f"2026-09-19T12:{minute:02d}:00Z", **kwargs)

    def test_archives_a_replaced_type_with_evidence(self):
        root = self.make_root()
        preview = root / "preview.png"
        preview.write_bytes(b"\x89PNG preview")
        directory = self.archive(root, "stone", 5, evidence={"preview.png": preview})

        record = json.loads((directory / "archive.json").read_text())
        stored = json.loads((directory / "definition.json").read_text())

        self.assertEqual("builtin.test.stone", record["object_type_id"])
        self.assertEqual("Test stone", record["display_name"])
        self.assertEqual("stone", record["classifier_label"])
        self.assertEqual("2026-09-19T12:05:00Z", record["retired_at"])
        self.assertEqual({"kind": "builtin", "source": "test fixture", "source_sha256": None},
                         record["provenance"])
        self.assertEqual(hashlib.sha256(b"\x89PNG preview").hexdigest(), record["evidence"]["preview.png"])
        self.assertEqual(b"\x89PNG preview", (directory / "preview.png").read_bytes())
        self.assertEqual("archived", stored["lifecycle_state"])
        self.assertEqual(definition_sha256(stored), record["definition_sha256"])
        validate_type_definition(stored)

    def test_archiving_is_idempotent_and_immutable(self):
        root = self.make_root()
        first = self.archive(root, "stone", 5)
        repeated = self.archive(root, "stone", 5)

        self.assertEqual(first, repeated)
        self.assertEqual(1, wall_of_fame_page(root)["total"])

        with self.assertRaisesRegex(CatalogError, "immutable"):
            self.archive(root, "stone", 6)
        with self.assertRaisesRegex(CatalogError, "immutable"):
            archive_type(root, builtin_definition("stone", display_name="Renamed stone"),
                         "2026-09-19T12:05:00Z")
        self.assertEqual("2026-09-19T12:05:00Z",
                         json.loads((first / "archive.json").read_text())["retired_at"])

    def test_rejects_invalid_retirement_timestamps_and_evidence_names(self):
        root = self.make_root()
        for retired_at in ("2026-09-19", "2026-09-19T12:00:00+02:00", "yesterday", 1758283200, None):
            with self.subTest(retired_at=retired_at):
                with self.assertRaisesRegex(CatalogError, "RFC 3339"):
                    archive_type(root, builtin_definition("stone"), retired_at)

        evidence = root / "preview.png"
        evidence.write_bytes(b"preview")
        for name in ("../evil.png", "/tmp/evil.png", "definition.json", "archive.json", ""):
            with self.subTest(name=name):
                with self.assertRaisesRegex(CatalogError, "evidence file name"):
                    archive_type(root, builtin_definition("stone"), "2026-09-19T12:00:00Z",
                                 evidence={name: evidence})

    def test_pages_are_newest_first_and_bounded(self):
        root = self.make_root()
        for index in range(MAX_WALL_PAGE + 2):
            self.archive(root, f"wall{index}", index)
        newest = f"builtin.test.wall{MAX_WALL_PAGE + 1}"

        page = wall_of_fame_page(root, limit=MAX_WALL_PAGE * 4)

        self.assertEqual(MAX_WALL_PAGE + 2, page["total"])
        self.assertEqual(MAX_WALL_PAGE, page["limit"])
        self.assertEqual(MAX_WALL_PAGE, len(page["entries"]))
        self.assertEqual(newest, page["entries"][0]["object_type_id"])
        retired = [entry["retired_at"] for entry in page["entries"]]
        self.assertEqual(sorted(retired, reverse=True), retired)

    def test_offset_paging_walks_the_whole_wall(self):
        root = self.make_root()
        for index in range(5):
            self.archive(root, f"wall{index}", index)
        everything = wall_of_fame_page(root)["entries"]

        page = wall_of_fame_page(root, offset=1, limit=2)

        self.assertEqual(everything[1:3], page["entries"])
        self.assertEqual({"offset": 1, "limit": 2, "total": 5},
                         {key: page[key] for key in ("offset", "limit", "total")})
        self.assertEqual([], wall_of_fame_page(root, offset=9)["entries"])
        self.assertEqual([], wall_of_fame_page(root, limit=0)["entries"])

    def test_rejects_invalid_paging_arguments(self):
        root = self.make_root()
        for offset, limit in ((-1, 4), (0, -1), (True, 4), (0, True), ("0", 4), (0, 1.5)):
            with self.subTest(offset=offset, limit=limit):
                with self.assertRaises(CatalogError):
                    wall_of_fame_page(root, offset=offset, limit=limit)

    def test_archiving_never_changes_the_active_catalog(self):
        root = self.make_catalog(("good", "stone"))
        before = load_catalog(root)
        self.archive(root, "husk", 1)
        self.archive(root, "stick", 2)

        after = load_catalog(root)

        self.assertEqual(before, after)
        self.assertEqual(2, len(after["definitions"]))
        self.assertEqual(2, after["max_active_types"])
        self.assertEqual(2, wall_of_fame_page(root)["total"])
        self.assertNotIn("husk", catalog_labels(after))

    def test_an_archived_definition_cannot_be_reactivated_by_a_reload(self):
        root = self.make_catalog(("good", "stone"))
        directory = self.archive(root, "husk", 1)
        write_json(root / "definitions" / "builtin.test.husk.json",
                   json.loads((directory / "definition.json").read_text()))

        catalog = load_catalog(root)

        self.assertEqual(["good", "stone"], catalog_labels(catalog))
        self.assertNotIn("builtin.test.husk", catalog["active_type_ids"])

    def test_an_empty_wall_reads_as_an_empty_page(self):
        root = self.make_root()
        (root / "wall-of-fame").mkdir()
        (root / "wall-of-fame" / ".gitkeep").write_text("")

        self.assertEqual({"total": 0, "unreadable": 0, "offset": 0, "limit": MAX_WALL_PAGE, "entries": []},
                         wall_of_fame_page(root))
        self.assertEqual(0, wall_of_fame_page(self.make_root())["total"])

    def test_one_damaged_archive_does_not_hide_the_other_entries(self):
        root = self.make_root()
        self.archive(root, "husk", 1)
        for name, content in (("builtin.test.cut", "{not json"), ("builtin.test.bare", "{}"),
                              ("builtin.test.moved", json.dumps({"object_type_id": "builtin.test.other",
                                                                 "retired_at": "2026-09-19T12:00:00Z"}))):
            (root / "wall-of-fame" / name).mkdir()
            (root / "wall-of-fame" / name / "archive.json").write_text(content)

        page = wall_of_fame_page(root)

        self.assertEqual((1, 3), (page["total"], page["unreadable"]))
        self.assertEqual(["builtin.test.husk"], [entry["object_type_id"] for entry in page["entries"]])


class ArchiveCompletenessTest(CatalogRootTest):
    """Gate A: a generated victim is archived only with its complete evidence."""

    def evidence(self, root, definition, **changes):
        glb = root / "object.glb"
        glb.write_bytes(b"glTF" + b"\x02\x00\x00\x00" + b"generated asset bytes")
        digest = hashlib.sha256(glb.read_bytes()).hexdigest()
        definition["visual"]["asset"]["glb_sha256"] = digest
        definition["visual"]["asset"]["visual_asset_id"] = f"sha256:{digest}"
        files = {"object.glb": glb}
        for name, data in (("perspective.png", b"\x89PNG perspective"),
                           ("top.png", b"\x89PNG top")):
            (root / name).write_bytes(data)
            files[name] = root / name
        manifest = root / "model.manifest.json"
        manifest.write_text(json.dumps({"artifact_sha256": "b" * 64, "version": 3}))
        files["model.manifest.json"] = manifest
        files.update(changes)
        return files

    def test_a_generated_archive_without_evidence_is_refused(self):
        root = self.make_root()
        with self.assertRaises(CatalogError) as raised:
            archive_type(root, generated_definition(), "2026-09-20T01:00:00Z")

        message = str(raised.exception)
        for name in ("object.glb", "perspective.png", "top.png", "model.manifest.json"):
            self.assertIn(name, message)
        # Nothing may be written when the archive is refused.
        self.assertFalse((root / "wall-of-fame").exists())
        self.assertEqual([], sorted(root.iterdir()))

    def test_each_missing_evidence_file_is_named(self):
        for missing in ("object.glb", "perspective.png", "top.png", "model.manifest.json"):
            root = self.make_root()
            definition = generated_definition()
            files = self.evidence(root, definition)
            files.pop(missing)
            with self.assertRaises(CatalogError) as raised:
                archive_type(root, definition, "2026-09-20T01:00:00Z", evidence=files)
            self.assertIn(missing, str(raised.exception))
            self.assertFalse((root / "wall-of-fame").exists())

    def test_a_wrong_asset_or_model_manifest_is_refused(self):
        root = self.make_root()
        definition = generated_definition()
        files = self.evidence(root, definition)

        (root / "object.glb").write_bytes(b"glTF" + b"different asset bytes")
        with self.assertRaisesRegex(CatalogError, "does not match the definition hash"):
            archive_type(root, definition, "2026-09-20T01:00:00Z", evidence=files)

        (root / "object.glb").write_bytes(b"NOTGLTF" + b"x" * 8)
        definition["visual"]["asset"]["glb_sha256"] = hashlib.sha256(
            (root / "object.glb").read_bytes()).hexdigest()
        definition["visual"]["asset"]["visual_asset_id"] = (
            f"sha256:{definition['visual']['asset']['glb_sha256']}")
        with self.assertRaisesRegex(CatalogError, "not a GLB file"):
            archive_type(root, definition, "2026-09-20T01:00:00Z", evidence=files)

        definition = generated_definition()
        files = self.evidence(root, definition)
        (root / "model.manifest.json").write_text(json.dumps({"artifact_sha256": "short"}))
        with self.assertRaisesRegex(CatalogError, "artifact_sha256"):
            archive_type(root, definition, "2026-09-20T01:00:00Z", evidence=files)
        (root / "model.manifest.json").write_text("not json")
        with self.assertRaisesRegex(CatalogError, "is not JSON"):
            archive_type(root, definition, "2026-09-20T01:00:00Z", evidence=files)
        self.assertFalse((root / "wall-of-fame").exists())

    def test_a_complete_generated_archive_records_the_model_artifact(self):
        root = self.make_root()
        definition = generated_definition()
        directory = archive_type(root, definition, "2026-09-20T01:00:00Z",
                                 evidence=self.evidence(root, definition))

        record = json.loads((directory / "archive.json").read_text())
        self.assertEqual(record["model_artifact_sha256"], "b" * 64)
        self.assertEqual(sorted(record["evidence"]),
                         ["model.manifest.json", "object.glb", "perspective.png", "top.png"])

    def test_a_builtin_victim_keeps_optional_evidence(self):
        root = self.make_root()
        directory = archive_type(root, builtin_definition("stone"), "2026-09-20T01:00:00Z")
        record = json.loads((directory / "archive.json").read_text())
        self.assertIsNone(record["model_artifact_sha256"])

        manifest = root / "model.manifest.json"
        manifest.write_text(json.dumps({"artifact_sha256": "c" * 64}))
        other = archive_type(root, builtin_definition("stick"), "2026-09-20T01:00:00Z",
                             evidence={"model.manifest.json": manifest})
        self.assertEqual(json.loads((other / "archive.json").read_text())["model_artifact_sha256"],
                         "c" * 64)

    def test_a_page_entry_describes_a_thumbnail(self):
        root = self.make_root()
        definition = generated_definition()
        archive_type(root, definition, "2026-09-20T01:00:00Z",
                     evidence=self.evidence(root, definition))
        archive_type(root, builtin_definition("stone"), "2026-09-20T00:00:00Z")

        entries = wall_of_fame_page(root)["entries"]

        generated = next(entry for entry in entries if entry["object_type_id"].startswith("gen"))
        # size_mm [[8, 8], [6, 6], [1, 1]] gives the mean of each range in metres.
        self.assertEqual(generated["preview"],
                         {"shape": "box", "axes_m": [0.008, 0.006, 0.001],
                          "rgb": [0.82, 0.68, 0.21]})
        builtin = next(entry for entry in entries if entry["object_type_id"].startswith("builtin"))
        self.assertEqual(builtin["preview"]["shape"], "ellipsoid")
        self.assertEqual(len(builtin["preview"]["axes_m"]), 3)


    def test_an_archive_without_a_readable_definition_counts_as_unreadable(self):
        """An archived type without its definition is incomplete, not a nameless entry.

        Showing a name with no geometry would be a half-truth, and the page already
        reports damaged entries with a count that never hides the healthy ones.
        """
        root = self.make_root()
        archive_type(root, builtin_definition("stone"), "2026-09-20T00:00:00Z")
        definition = generated_definition()
        archive_type(root, definition, "2026-09-20T01:00:00Z",
                     evidence=self.evidence(root, definition))
        damaged = root / "wall-of-fame" / definition["object_type_id"] / "definition.json"

        damaged.unlink()
        page = wall_of_fame_page(root)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["unreadable"], 1)
        self.assertEqual([entry["object_type_id"] for entry in page["entries"]],
                         ["builtin.test.stone"])

        damaged.write_text("{not json}")
        self.assertEqual(wall_of_fame_page(root)["unreadable"], 1)
        damaged.write_text(json.dumps({"visual": {"shape": "box"}}))
        self.assertEqual(wall_of_fame_page(root)["unreadable"], 1)
        # The healthy entry still carries its thumbnail description.
        self.assertIsNotNone(wall_of_fame_page(root)["entries"][0]["preview"])


class BundleMappingKeyTest(unittest.TestCase):
    """A mapping key can carry a host path as easily as a value can."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def sources(self, files):
        from test_validate_bundle import bundle_files, pretty
        value = bundle_files()
        value["sources.json"] = pretty({"source_revision": "c" * 40, "files": files})
        return value

    def test_an_unsafe_mapping_key_is_refused_on_publish_and_on_verify(self):
        for key in ("/Users/taras/live.py", "../outside/live.py", "/etc/passwd"):
            with self.assertRaises(CatalogError) as raised:
                object_catalog.publish_bundle(self.root / "bundles",
                                              self.sources({key: "b" * 64}))
            self.assertIn("sources.json", str(raised.exception))

        from test_validate_bundle import pretty
        digest = object_catalog.publish_bundle(self.root / "good",
                                               self.sources({"live.py": "b" * 64}))
        directory = self.root / "good" / digest
        self.assertEqual(object_catalog.verify_bundle(directory)["bundle_sha256"], digest)
        # The same key, edited after publication, is refused on verify.
        (directory / "sources.json").write_bytes(
            pretty({"source_revision": "c" * 40, "files": {"/etc/passwd": "b" * 64}}))
        with self.assertRaises(CatalogError):
            object_catalog.verify_bundle(directory)

    def test_ordinary_keys_and_user_text_stay_accepted(self):
        digest = object_catalog.publish_bundle(
            self.root / "ok", self.sources({"live.py": "b" * 64, "1.2.0-rc.1": "c" * 64,
                                            "model/candidate.joblib": "d" * 64}))
        self.assertEqual(object_catalog.verify_bundle(self.root / "ok" / digest)["bundle_sha256"],
                         digest)


class BundlePointerReproductionTest(CatalogRootTest):
    """Gate B reproduction from the work order, against the packaged catalog."""

    def test_a_packaged_catalog_with_an_unknown_bundle_pointer_is_refused(self):
        root = self.make_root() / "catalog"
        shutil.copytree(object_catalog.PACKAGED_CATALOG_ROOT, root)
        manifest = root / "active" / "catalog.json"
        value = json.loads(manifest.read_text())
        self.assertIsNone(value["active_bundle_sha256"])
        # The packaged default still loads with a null pointer.
        self.assertEqual(len(load_catalog(root)["definitions"]), value["max_active_types"])

        value["active_bundle_sha256"] = "f" * 64
        manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

        with self.assertRaisesRegex(CatalogError, "requires a bundles root"):
            load_catalog(root)
        with self.assertRaisesRegex(CatalogError, "bundle directory is missing"):
            load_catalog(root, bundles_root=root.parent / "bundles")


class ReviewRegressionTest(CatalogRootTest):
    """Defects that the Phase 1 spec review reproduced. The loader gates activation."""

    def test_the_manifest_path_cannot_leave_the_catalog_root(self):
        root = self.make_catalog(("good", "stone"))
        outside = self.make_catalog(("husk", "stick"))
        for manifest in (str(outside / "active/catalog.json"), "../active/catalog.json",
                         "active/../../x.json", "/etc/passwd", "", "active//catalog.json", 5):
            with self.subTest(manifest=manifest), self.assertRaisesRegex(CatalogError, "inside the catalog root"):
                load_catalog(root, manifest=manifest)

    def test_wrong_value_types_are_rejected_as_catalog_errors(self):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition("good")], active_type_ids=[["a"]],
                           max_active_types=1, catalog_revision="0" * 64)
        with self.assertRaises(CatalogError):
            load_catalog(root)
        value = generated_definition()
        value["visual"]["asset"]["sim_from_asset_quaternion_wxyz"] = 5
        with self.assertRaises(CatalogError):
            validate_type_definition(value)


class TextureFamilyTest(unittest.TestCase):
    """An unknown material family passed validation and raised KeyError during spawn."""

    def test_a_builtin_texture_must_be_an_engine_material_family(self):
        validate_type_definition(builtin_definition("good"))
        for texture in ("missing_family", "", 5, ["good"]):
            value = builtin_definition("good")
            value["visual"]["texture"] = texture
            with self.subTest(texture=texture), self.assertRaisesRegex(CatalogError, "material family"):
                validate_type_definition(value)

    def test_a_generated_type_has_no_texture(self):
        value = generated_definition()
        self.assertIsNone(value["visual"]["texture"])
        value["visual"]["texture"] = "good"
        with self.assertRaisesRegex(CatalogError, "material family"):
            validate_type_definition(value)

    def test_the_validator_and_the_engine_use_the_same_families(self):
        import re
        import assets
        self.assertIs(object_catalog.TEXTURE_FAMILIES, assets.FAMILIES)
        source = (Path(object_catalog.__file__).parent / "sim.py").read_text()  # importing sim needs mujoco
        families = re.search(r"for fam in \(([^)]*)\):\s*\n\s*self\.material_ids\[fam\]", source).group(1)
        self.assertEqual(set(assets.FAMILIES), set(re.findall(r'"([a-z]+)"', families)))


class EnumFieldTypeTest(unittest.TestCase):
    """A JSON list in an enum-like field is unhashable. It raised TypeError instead of CatalogError."""

    def test_enum_like_fields_reject_non_text_values_as_catalog_errors(self):
        fields = (("lifecycle_state",), ("provenance", "kind"), ("truth", "severity"),
                  ("visual", "shape"), ("visual", "texture"))
        for path in fields:
            for bad in (["active_ready"], {"a": 1}, 5, True):
                value = builtin_definition("good")
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = bad
                with self.subTest(path=path, bad=bad), self.assertRaises(CatalogError):
                    validate_type_definition(value)


class CatalogRootContainmentTest(CatalogRootTest):
    """A safe name is not enough. A symlink below the root can still read a file outside it."""

    def outside_copy(self, root, relative):
        outside = self.make_root() / Path(relative).name
        outside.write_bytes((root / relative).read_bytes())
        (root / relative).unlink()
        return outside

    def test_a_definition_symlink_cannot_leave_the_catalog_root(self):
        root = self.make_catalog(("good", "stone"))
        relative = "definitions/builtin.test.good.json"
        (root / relative).symlink_to(self.outside_copy(root, relative))
        with self.assertRaisesRegex(CatalogError, "leaves the catalog root"):
            load_catalog(root)

    def test_a_manifest_symlink_cannot_leave_the_catalog_root(self):
        root = self.make_catalog(("good", "stone"))
        (root / "active/catalog.json").symlink_to(self.outside_copy(root, "active/catalog.json"))
        with self.assertRaisesRegex(CatalogError, "leaves the catalog root"):
            load_catalog(root)

    def test_a_symlinked_definitions_directory_cannot_leave_the_catalog_root(self):
        root = self.make_catalog(("good", "stone"))
        outside = self.make_root() / "definitions"
        (root / "definitions").rename(outside)
        (root / "definitions").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(CatalogError, "leaves the catalog root"):
            load_catalog(root)

    def test_a_symlink_that_stays_below_the_root_still_loads(self):
        root = self.make_catalog(("good", "stone"))
        relative = "definitions/builtin.test.good.json"
        (root / "kept.json").write_bytes((root / relative).read_bytes())
        (root / relative).unlink()
        (root / relative).symlink_to(root / "kept.json")
        self.assertEqual(["good", "stone"], catalog_labels(load_catalog(root)))


class GeneratedTypeTest(CatalogRootTest):
    """One validated supported draft becomes one active generated type."""

    def build(self, draft=None, **changes):
        values = {"rgb": [0.82, 0.68, 0.21], "prior": 0.03, "source_sha256": "c" * 64}
        values.update(changes)
        return type_definition_from_draft(draft or star_draft(), **values)

    def test_builds_an_active_generated_type_from_a_draft(self):
        draft = star_draft()
        definition = self.build(draft)

        validate_type_definition(definition)
        self.assertEqual("generated", definition["provenance"]["kind"])
        self.assertEqual("c" * 64, definition["provenance"]["source_sha256"])
        self.assertEqual("active_ready", definition["lifecycle_state"])
        self.assertEqual("validated", definition["validation_status"])
        self.assertEqual(draft["object_type_id"], definition["object_type_id"])
        self.assertEqual({"defect": False, "severity": "none"}, definition["truth"])
        self.assertEqual(0.03, definition["feed"]["prior"])
        self.assertEqual([[8.0, 8.0], [6.0, 6.0], [1.0, 1.0]], definition["visual"]["size_mm"])
        self.assertEqual(1200.0, definition["physics"]["density_kg_m3"])
        self.assertEqual(MASS_BASIS, definition["physics"]["mass_basis"])
        self.assertEqual("unmeasured_estimate", definition["physics"]["measurement_status"])

    def test_a_generated_type_has_no_texture_and_binds_the_draft_glb_hash(self):
        draft = star_draft()
        asset = self.build(draft)["visual"]["asset"]

        self.assertIsNone(self.build(draft)["visual"]["texture"])
        self.assertEqual(draft["visual"]["visual_asset_id"], asset["visual_asset_id"])
        self.assertEqual(draft["visual"]["visual_asset_id"], f"sha256:{asset['glb_sha256']}")
        self.assertEqual("m", asset["units"])
        self.assertEqual(list(SIM_FROM_ASSET_QUATERNION_WXYZ),
                         asset["sim_from_asset_quaternion_wxyz"])

    def test_an_unsupported_draft_is_refused(self):
        draft = star_draft(physics_proposal={
            "shape": "unsupported",
            "unsupported_reason": "The ring opening is meaningful geometry.",
            "limitations": "Box and capsule primitives cannot preserve the opening.",
        })

        with self.assertRaisesRegex(CatalogError, "supported contact proxy"):
            self.build(draft)

    def test_the_label_always_derives_from_the_object_key(self):
        self.assertEqual("star_token", self.build()["classifier_label"])
        with self.assertRaises(TypeError):
            self.build(classifier_label="other_token")

    def test_a_draft_without_a_sorting_proposal_carries_no_truth(self):
        with self.assertRaisesRegex(CatalogError, "sorting proposal"):
            self.build(star_draft(sorting_proposal=None))

    def test_a_capsule_proxy_uses_half_length_and_radius(self):
        draft = star_draft(object_key="example_stick", physics_proposal={
            "shape": "capsule", "dimensions_m": [0.018, 0.004, 0.004], "density_kg_m3": 700.0,
            "material_assumption": "Dry wood.", "limitations": "The proxy excludes irregularity.",
        })
        definition = self.build(draft)

        self.assertEqual("capsule", definition["visual"]["shape"])
        for axis, expected in zip(definition["visual"]["size_mm"], (7.0, 2.0, 2.0)):
            with self.subTest(semi_axis_mm=expected):
                self.assertAlmostEqual(expected, axis[0])
                self.assertEqual(axis[0], axis[1])

    def test_a_label_that_collides_with_a_survivor_is_refused(self):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition("good"), builtin_definition("stone"),
                                  builtin_definition("star_token")])
        catalog = load_catalog(root)
        victim = catalog["definitions"][-1]["object_type_id"]

        # Removing stone leaves the built-in star_token, which collides with the new label.
        with self.assertRaisesRegex(CatalogError, "labels must be unique"):
            candidate_catalog(catalog, self.build(), catalog["definitions"][1]["object_type_id"])
        candidate_catalog(catalog, self.build(), victim)

    def test_the_flat_proxy_colour_is_the_mean_of_the_recipe_part_colours(self):
        recipe = {"parts": [{"color": [1.0, 0.8, 0.2]}, {"color": [0.6, 0.4, 0.0]}]}

        self.assertEqual([0.8, 0.6000000000000001, 0.1], recipe_rgb(recipe))
        self.assertEqual([1.0, 0.78, 0.22], recipe_rgb(json.loads((STAR / "recipe.json").read_text())))
        for wrong in ({"parts": []}, {"parts": [{"color": [1.0, 0.8]}]},
                      {"parts": [{"color": [1.2, 0.8, 0.2]}]}, {"parts": [{}]}):
            with self.subTest(recipe=wrong):
                with self.assertRaises(CatalogError):
                    recipe_rgb(wrong)


class ReplacementTest(CatalogRootTest):
    """The victim is the last current Keep type, and the candidate leads the new order."""

    def catalog(self, labels=("good", "faded", "stone", "stick")):
        root = self.make_root()
        self.write_catalog(root, [builtin_definition(label) for label in labels])
        return load_catalog(root)

    def test_the_victim_is_the_last_type_the_policy_keeps(self):
        catalog = self.catalog()

        self.assertEqual("builtin.test.stick", select_victim(catalog, []))
        self.assertEqual("builtin.test.stone", select_victim(catalog, ["stick"]))
        self.assertEqual("builtin.test.faded", select_victim(catalog, ["stick", "stone"]))

    def test_the_anomaly_reference_is_never_a_victim(self):
        catalog = self.catalog(("good", "stone"))

        self.assertEqual("good", ANOMALY_REFERENCE_LABEL)
        self.assertIsNone(select_victim(catalog, ["stone"]))
        self.assertIsNone(select_victim(self.catalog(("good",)), []))

    def test_a_rejected_label_is_never_a_victim(self):
        catalog = self.catalog(("good", "faded", "stone"))

        self.assertEqual("builtin.test.faded", select_victim(catalog, ["stone"]))
        self.assertIsNone(select_victim(catalog, ["faded", "stone"]))

    def test_the_candidate_leads_and_survivors_keep_their_order(self):
        catalog = self.catalog()
        new_definition = generated_definition()
        candidate = candidate_catalog(catalog, new_definition, "builtin.test.stick")

        self.assertEqual(["star_token", "good", "faded", "stone"], catalog_labels(candidate))
        self.assertEqual(catalog["max_active_types"], candidate["max_active_types"])
        self.assertEqual(len(catalog["active_type_ids"]), len(candidate["active_type_ids"]))
        self.assertNotEqual(catalog["catalog_revision"], candidate["catalog_revision"])
        self.assertEqual(catalog_revision(candidate["active_type_ids"],
                                          candidate["definition_sha256"]),
                         candidate["catalog_revision"])
        self.assertEqual(definition_sha256(new_definition),
                         candidate["definition_sha256"][new_definition["object_type_id"]])
        self.assertIsNone(candidate["active_bundle_sha256"])

    def test_a_victim_that_is_not_active_is_refused(self):
        with self.assertRaisesRegex(CatalogError, "victim is not active"):
            candidate_catalog(self.catalog(), generated_definition(), "builtin.test.absent")

    def test_the_candidate_model_must_train_on_the_candidate_order(self):
        candidate = candidate_catalog(self.catalog(), generated_definition(), "builtin.test.stick")

        require_label_order(candidate, ["star_token", "good", "faded", "stone"])
        with self.assertRaisesRegex(CatalogError, "model label order"):
            require_label_order(candidate, ["good", "faded", "stone", "star_token"])

    def test_a_written_candidate_root_loads_back_unchanged(self):
        candidate = candidate_catalog(self.catalog(), generated_definition(), "builtin.test.stick")
        root = self.make_root() / "candidate"
        write_catalog(root, candidate)
        loaded = load_catalog(root)

        self.assertEqual(candidate["active_type_ids"], loaded["active_type_ids"])
        self.assertEqual(candidate["catalog_revision"], loaded["catalog_revision"])
        self.assertEqual(candidate["definitions"], loaded["definitions"])
        self.assertEqual(["star_token", "good", "faded", "stone"], catalog_labels(loaded))
        self.assertEqual(["star_token", "good", "faded", "stone"],
                         profile_from_catalog(loaded).names)

    def test_an_existing_root_is_never_overwritten(self):
        candidate = candidate_catalog(self.catalog(), generated_definition(), "builtin.test.stick")
        root = self.make_root() / "candidate"
        write_catalog(root, candidate)

        with self.assertRaisesRegex(CatalogError, "already exists"):
            write_catalog(root, candidate)


class ReferenceVictimGuardTest(CatalogRootTest):
    """select_victim never offers the anomaly reference. A direct caller cannot remove it either."""

    def test_the_anomaly_reference_type_cannot_be_the_victim(self):
        catalog = load_catalog(self.make_catalog(("good", "stone")))
        with self.assertRaisesRegex(CatalogError, "anomaly reference"):
            candidate_catalog(catalog, generated_definition(), "builtin.test.good")
        replaced = candidate_catalog(catalog, generated_definition(), "builtin.test.stone")
        self.assertEqual(["star_token", "good"], catalog_labels(replaced))


class WriteCatalogGuardTest(CatalogRootTest):
    """write_catalog builds file names from definitions, so it checks them itself."""

    def candidate(self):
        return load_catalog(self.make_catalog(("good", "stone")))

    def test_an_unsafe_type_identifier_never_becomes_a_path(self):
        catalog = self.candidate()
        catalog["definitions"][1] = {**catalog["definitions"][1], "object_type_id": "../escaped"}
        target = self.make_root() / "candidate"
        with self.assertRaises(CatalogError):
            write_catalog(target, catalog)
        self.assertFalse(target.exists())
        self.assertEqual([], [item.name for item in target.parent.iterdir()])

    def test_definitions_must_match_the_ordered_active_set(self):
        catalog = self.candidate()
        catalog["definitions"].reverse()
        with self.assertRaisesRegex(CatalogError, "ordered active set"):
            write_catalog(self.make_root() / "candidate", catalog)

    def test_a_second_writer_cannot_replace_a_published_directory(self):
        target = self.make_root() / "candidate"
        write_catalog(target, self.candidate())
        before = sorted(item.name for item in target.rglob("*"))
        with self.assertRaisesRegex(CatalogError, "already exists"):
            object_catalog._publish_staged(target, {"active/catalog.json": b"{}"})
        self.assertEqual(before, sorted(item.name for item in target.rglob("*")))
        self.assertEqual([target.name], [item.name for item in target.parent.iterdir()])


class AtomicReplaceTest(unittest.TestCase):
    """The seed marker and the active pointer both commit through this one helper."""

    def test_the_rename_is_made_durable_by_fsyncing_the_directory(self):
        order = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(descriptor):
            kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
            order.append(kind)
            return real_fsync(descriptor)

        def replace(source, target):
            order.append("replace")
            return real_replace(source, target)

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "pointer.json"
            with patch.object(os, "fsync", fsync), patch.object(os, "replace", replace):
                object_catalog._replace_atomically(path, b'{"ok": true}')

            # The bytes alone are not enough. The name itself lives in the directory.
            self.assertEqual(["file", "replace", "directory"], order)
            self.assertEqual(b'{"ok": true}', path.read_bytes())
            self.assertEqual(["pointer.json"], [item.name for item in Path(raw).iterdir()])


class GlbReaderTest(unittest.TestCase):
    """Every count comes from the GLB bytes. What cannot be counted is refused."""

    def test_the_counts_come_from_the_json_chunk(self):
        data = tiny_glb(index_count=6, primitives=2)
        self.assertEqual(read_glb_evidence(data), {
            "byte_length": len(data), "mesh_count": 1, "primitive_count": 2,
            "triangle_count": 4})
        self.assertEqual(read_glb_evidence(tiny_glb(meshes=2))["mesh_count"], 2)
        # An explicit triangle mode reads the same as the default.
        self.assertEqual(read_glb_evidence(tiny_glb(mode=4))["triangle_count"], 1)

    def test_the_tracked_star_matches_the_contract_measurements(self):
        data = (STAR / "render/object.glb").read_bytes()
        self.assertEqual(read_glb_evidence(data), {
            "byte_length": 15804, "mesh_count": 1, "primitive_count": 1,
            "triangle_count": 276})

    def test_anything_but_a_bounded_indexed_triangle_glb_is_refused(self):
        valid = tiny_glb()
        cases = {
            "not bytes": "glTF",
            "too short": b"glTF",
            "a wrong magic": b"glTX" + valid[4:],
            "version 1": glb_container(b"{}", version=1),
            "a length that is not the byte count": valid + b"\x00",
            "a first chunk that is not JSON": glb_container(b"{}", chunk_type=0x004E4942),
            "a chunk past the end": glb_container(b"{}", declared=64),
            "invalid JSON": glb_container(b"{"),
            "nesting past the parser": glb_container(b"[" * 100000),
            "a document that is not an object": glb_container(b"[]"),
            "no mesh": glb_container(b'{"meshes": [], "accessors": []}'),
            "a mesh without a primitive": glb_container(b'{"meshes": [{}], "accessors": []}'),
            "line primitives": tiny_glb(mode=1),
            "a primitive without indices": tiny_glb(indices=None),
            "an indices accessor out of range": tiny_glb(indices=2),
            "a boolean accessor index": tiny_glb(indices=True),
            "a count that is not a triangle list": tiny_glb(index_count=4),
            "a boolean count": tiny_glb(index_count=True),
            "an empty index list": tiny_glb(index_count=0),
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(CatalogError):
                read_glb_evidence(data)
        for cap in ("MAX_GLB_BYTES", "MAX_GLB_JSON_BYTES"):
            with self.subTest(cap), patch.object(object_catalog, cap, 16), \
                    self.assertRaises(CatalogError):
                read_glb_evidence(valid)


class RepoWallOfFameTest(unittest.TestCase):
    def test_the_repo_wall_starts_empty(self):
        self.assertEqual(0, wall_of_fame_page(object_catalog.PACKAGED_CATALOG_ROOT)["total"])


if __name__ == "__main__":
    unittest.main()
