"""Shared object catalog: frozen built-in values, validation, and Wall of Fame paging."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import object_catalog
from object_catalog import (
    MASS_BASIS,
    MAX_WALL_PAGE,
    CatalogError,
    archive_type,
    catalog_labels,
    catalog_revision,
    class_spec,
    definition_sha256,
    load_catalog,
    load_definition,
    profile_from_catalog,
    require_label_order,
    validate_type_definition,
    wall_of_fame_page,
)
from object_definitions import SIM_FROM_ASSET_QUATERNION_WXYZ
from profiles import PROFILES

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


class RepoWallOfFameTest(unittest.TestCase):
    def test_the_repo_wall_starts_empty(self):
        self.assertEqual(0, wall_of_fame_page(object_catalog.CATALOG_ROOT)["total"])


if __name__ == "__main__":
    unittest.main()
