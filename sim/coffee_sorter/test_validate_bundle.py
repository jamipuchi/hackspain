"""Immutable bundle, active pointer, rollback, and the child-process validator.

Temp directories only. The model is a tiny picklable object with a classes attribute:
no training, no real artifact.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock
from pathlib import Path

import joblib

import object_catalog
from object_catalog import (BUNDLE_CATALOG, BUNDLE_MANIFEST, BUNDLE_PRESET, CATALOG_ROOT_ENV,
                            PACKAGED_CATALOG_ROOT, CatalogError, catalog_labels,
                            catalog_revision, definition_sha256, ensure_active_bundle,
                            load_catalog, publish_bundle, read_active, rollback_active,
                            seed_bundle_files, verify_bundle, write_active_pointer,
                            write_bundle_manifest)
from test_object_catalog import builtin_definition

HERE = Path(__file__).resolve().parent
REVISION = "c" * 40
CONTINUOUS_PRESET = HERE / "configs" / "continuous_demo.json"
# A label set that differs from the packaged one: a new type first, then two survivors.
CHANGED_LABELS = ("star_token", "good", "stone")


def pretty(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def catalog_manifest(definitions, bundle_sha256=None):
    ids = [definition["object_type_id"] for definition in definitions]
    hashes = {definition["object_type_id"]: definition_sha256(definition)
              for definition in definitions}
    return {"schema_version": 1, "profile_name": "bundle test", "belt_rgb": [0.1, 0.2, 0.3],
            "catalog_revision": catalog_revision(ids, hashes), "max_active_types": len(ids),
            "active_type_ids": ids, "definition_sha256": hashes,
            "active_bundle_sha256": bundle_sha256}


def bundle_files(labels=("good", "stone"), model_classes=None, reject_classes=("stone",),
                 model_revision=True, manifest_revision=True):
    """One complete, valid bundle content mapping.

    The model records the revision of the catalog it was trained for, in both its meta
    and its manifest, exactly as the two trainers now write it.
    """
    definitions = [builtin_definition(label) for label in labels]
    manifest = catalog_manifest(definitions)
    files = {BUNDLE_CATALOG: pretty(manifest)}
    for definition in definitions:
        files[f"catalog/definitions/{definition['object_type_id']}.json"] = pretty(definition)
    revision = manifest["catalog_revision"]
    meta = {"classes": list(model_classes or labels)}
    if model_revision is not False:
        meta["provenance"] = {"config": {"catalog_revision":
                                         revision if model_revision is True else model_revision}}
    model = tempfile.NamedTemporaryFile(suffix=".joblib", delete=False)
    model.close()
    joblib.dump(types.SimpleNamespace(classes=meta["classes"], meta=meta), model.name)
    files["model/candidate.joblib"] = Path(model.name).read_bytes()
    Path(model.name).unlink()
    model_manifest = {"artifact_sha256": "a" * 64}
    if manifest_revision is not False:
        model_manifest["provenance"] = {"config": {
            "catalog_revision": revision if manifest_revision is True else manifest_revision}}
    files["model/candidate.manifest.json"] = pretty(model_manifest)
    files[BUNDLE_PRESET] = pretty({"name": "bundle-test", "model_path": "model/candidate.joblib",
                                   "model_path_root": "preset"})
    files["policy.json"] = pretty({"reject_classes": list(reject_classes)})
    files["sources.json"] = pretty({"source_revision": REVISION, "files": {}})
    return files


def continuous_bundle_files(labels=CHANGED_LABELS, trained_rate=None):
    """A bundle with the real continuous preset, so live.load_preset runs every check.

    The model stays a tiny picklable object. Its meta and its manifest record what the
    trainers record: the catalog revision, the profile, the labels, and the physical preset.
    """
    from vision import FEATURES

    files = bundle_files(labels=labels)
    catalog = json.loads(files[BUNDLE_CATALOG])
    preset = {**json.loads(CONTINUOUS_PRESET.read_text()), "profile": catalog["profile_name"],
              "model_path": "model/candidate.joblib", "model_path_root": "preset"}
    layout, capture_every = preset["layout"], preset["camera_every_steps"]
    rate = preset["requested_rate"] if trained_rate is None else trained_rate
    provenance = {"config": {
        "catalog_revision": catalog["catalog_revision"], "profile": preset["profile"],
        "rate": rate, "capture_every": capture_every,
        "physical_preset": {"layout": layout, "requested_rate": rate,
                            "camera_every_steps": capture_every,
                            "capture_hz": 1.0 / (float(layout["timestep"]) * capture_every)}}}
    meta = {"profile": preset["profile"], "classes": list(labels), "features": FEATURES,
            "provenance": provenance}
    with tempfile.TemporaryDirectory() as folder:
        model = Path(folder) / "candidate.joblib"
        joblib.dump(types.SimpleNamespace(classes=list(labels), meta=meta), model)
        files["model/candidate.joblib"] = model.read_bytes()
    files["model/candidate.manifest.json"] = pretty({
        "artifact_sha256": hashlib.sha256(files["model/candidate.joblib"]).hexdigest(),
        "features": FEATURES, "provenance": provenance})
    files[BUNDLE_PRESET] = pretty(preset)
    return files


def run_python(code, *arguments, catalog_root=None):
    """Run one fresh interpreter. The catalog root variable is set or absent, never inherited."""
    environment = {name: value for name, value in os.environ.items() if name != CATALOG_ROOT_ENV}
    if catalog_root is not None:
        environment[CATALOG_ROOT_ENV] = str(catalog_root)
    result = subprocess.run([sys.executable, "-c", code, *map(str, arguments)],
                            capture_output=True, text=True, cwd=str(HERE), env=environment,
                            timeout=120)
    if result.returncode != 0:
        raise AssertionError(f"the child interpreter failed: {result.stderr[-400:]}")
    return json.loads(result.stdout)


class BundleFixture:
    """Shared temp root and publish helper. Not a TestCase, so nothing re-runs."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bundles = self.root / "bundles"

    def publish(self, files=None, bundles=None):
        digest = publish_bundle(bundles or self.bundles, files or bundle_files())
        return digest, (bundles or self.bundles) / digest


class BundleTest(BundleFixture, unittest.TestCase):

    def test_a_manifest_identity_does_not_depend_on_its_parent_directory(self):
        digests = []
        for name in ("first", "second"):
            staging = self.root / name / "tree"
            (staging / "catalog").mkdir(parents=True)
            (staging / "catalog" / "a.json").write_bytes(b'{"a": 1}')
            (staging / "b.txt").write_bytes(b"bytes")
            digests.append(write_bundle_manifest(staging))
            listed = json.loads((staging / BUNDLE_MANIFEST).read_text())
            self.assertEqual(sorted(listed["files"]), ["b.txt", "catalog/a.json"])
            self.assertNotIn(BUNDLE_MANIFEST, listed["files"])
        self.assertEqual(digests[0], digests[1])

    def test_publishing_the_same_content_twice_is_one_bundle(self):
        files = bundle_files()
        first, directory = self.publish(files)
        second, _ = self.publish(dict(files))

        self.assertEqual(first, second)
        self.assertEqual(sorted(path.name for path in self.bundles.iterdir()), [first])
        self.assertEqual(verify_bundle(directory)["bundle_sha256"], first)
        self.assertEqual(sorted(self.bundles.glob(".staging-*")), [])

    def test_a_directory_of_that_name_that_does_not_verify_is_an_error(self):
        files = bundle_files()
        digest, directory = self.publish(files)
        (directory / "policy.json").write_bytes(pretty({"reject_classes": []}))

        with self.assertRaises(CatalogError) as raised:
            self.publish(files)
        self.assertIn("does not match its manifest", str(raised.exception))

    def test_no_file_inside_a_bundle_names_the_bundle(self):
        digest, directory = self.publish()
        for path in directory.rglob("*"):
            if path.is_file():
                self.assertNotIn(digest.encode(), path.read_bytes(), path.name)

    def test_every_verification_violation_is_refused(self):
        def fresh():
            root = Path(tempfile.mkdtemp(dir=self.root))
            digest = publish_bundle(root, bundle_files())
            return root / digest

        # A changed byte in a listed file.
        directory = fresh()
        (directory / "policy.json").write_bytes(pretty({"reject_classes": ["good"]}))
        self.assertRaisesMessage(directory, "does not match its manifest")

        # An unlisted extra file.
        directory = fresh()
        (directory / "extra.txt").write_bytes(b"x")
        self.assertRaisesMessage(directory, "do not match its manifest")

        # A missing listed file.
        directory = fresh()
        (directory / "sources.json").unlink()
        self.assertRaisesMessage(directory, "do not match its manifest")

        # A symlinked file and a symlinked directory.
        directory = fresh()
        target = self.root / "outside.json"
        target.write_bytes((directory / "policy.json").read_bytes())
        (directory / "policy.json").unlink()
        (directory / "policy.json").symlink_to(target)
        self.assertRaisesMessage(directory, "symlink")

        directory = fresh()
        moved = self.root / "moved-model"
        shutil.move(str(directory / "model"), str(moved))
        (directory / "model").symlink_to(moved, target_is_directory=True)
        self.assertRaisesMessage(directory, "symlink")

        # A renamed bundle directory no longer matches its manifest hash.
        directory = fresh()
        renamed = directory.with_name("b" * 64)
        directory.rename(renamed)
        self.assertRaisesMessage(renamed, "does not match its directory")

        # An edited manifest.
        directory = fresh()
        listed = json.loads((directory / BUNDLE_MANIFEST).read_text())
        listed["files"].pop("policy.json")
        (directory / BUNDLE_MANIFEST).write_bytes(json.dumps(listed).encode())
        self.assertRaisesMessage(directory, "does not match its directory")

    def test_unsafe_manifest_keys_are_refused(self):
        for key in ("../escape.json", "/absolute.json", "catalog//empty.json",
                    "catalog\\windows.json", "."):
            with self.assertRaises(CatalogError):
                object_catalog._bundle_relative(key)
        with self.assertRaises(CatalogError):
            publish_bundle(self.bundles, {"../escape.json": b"x"})
        with self.assertRaises(CatalogError):
            publish_bundle(self.bundles, {"/absolute.json": b"x"})

    def test_bundle_content_rules_are_enforced(self):
        files = bundle_files()
        # A non-null sha inside the bundle catalog.
        broken = dict(files)
        manifest = json.loads(broken[BUNDLE_CATALOG])
        manifest["active_bundle_sha256"] = "d" * 64
        broken[BUNDLE_CATALOG] = pretty(manifest)
        with self.assertRaises(CatalogError) as raised:
            publish_bundle(self.bundles, broken)
        self.assertIn("null active_bundle_sha256", str(raised.exception))

        # An absolute path value in the preset.
        broken = dict(files)
        broken[BUNDLE_PRESET] = pretty({"name": "x", "model_path": "model/candidate.joblib",
                                        "model_path_root": "preset", "note": "/etc/passwd"})
        with self.assertRaises(CatalogError) as raised:
            publish_bundle(self.bundles, broken)
        self.assertIn("absolute path", str(raised.exception))

        # A model path that leaves the bundle, and a missing model path root.
        broken = dict(files)
        broken[BUNDLE_PRESET] = pretty({"name": "x", "model_path": "../candidate.joblib",
                                        "model_path_root": "preset"})
        with self.assertRaises(CatalogError):
            publish_bundle(self.bundles, broken)

        broken = dict(files)
        broken[BUNDLE_PRESET] = pretty({"name": "x", "model_path": "model/candidate.joblib"})
        with self.assertRaises(CatalogError) as raised:
            publish_bundle(self.bundles, broken)
        self.assertIn("model_path_root", str(raised.exception))

    def assertRaisesMessage(self, directory, fragment):
        with self.assertRaises(CatalogError) as raised:
            verify_bundle(directory)
        self.assertIn(fragment, str(raised.exception))


class ConcurrentPublishTest(BundleFixture, unittest.TestCase):
    def test_two_writers_of_identical_content_both_get_the_same_bundle(self):
        """The existence check and the rename cannot be atomic, so both answers must work."""
        files = bundle_files()
        results, failures = [], []
        barrier = threading.Barrier(2, timeout=60)
        original = object_catalog.os.replace

        def racing_replace(source, target):
            # Both writers have passed the existence check by the time either renames.
            barrier.wait()
            return original(source, target)

        def publish():
            try:
                results.append(publish_bundle(self.bundles, dict(files)))
            except BaseException as error:  # a raw OSError would fail this test
                failures.append(f"{type(error).__name__}: {error}")

        with unittest.mock.patch.object(object_catalog.os, "replace", racing_replace):
            threads = [threading.Thread(target=publish) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)

        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual([path.name for path in self.bundles.iterdir()], [results[0]])
        self.assertEqual(list(self.bundles.glob(".staging-*")), [])
        self.assertEqual(verify_bundle(self.bundles / results[0])["bundle_sha256"], results[0])

    def test_a_loser_whose_published_bundle_does_not_verify_raises_a_catalog_error(self):
        digest, directory = self.publish()
        (directory / "policy.json").write_bytes(pretty({"reject_classes": []}))
        original = object_catalog.os.replace

        def failing_replace(source, target):
            raise OSError(66, "Directory not empty")

        with unittest.mock.patch.object(object_catalog.os, "replace", failing_replace):
            with self.assertRaises(CatalogError):
                publish_bundle(self.bundles, bundle_files())
        self.assertEqual(list(self.bundles.glob(".staging-*")), [])


class VolatileContentTest(BundleFixture, unittest.TestCase):
    """Only host paths and runtime metadata are excluded. User text stays valid."""

    def publish_with(self, sources):
        files = bundle_files()
        files["sources.json"] = pretty(sources)
        return publish_bundle(self.bundles, files)

    def test_structural_values_that_are_refused(self):
        host = object_catalog._host_name()
        cases = {
            "/var/lib/hackspain/model.joblib": "an absolute path",
            "C:\\\\models\\\\candidate.joblib": "an absolute path",
            "../outside/model.joblib": "a path that leaves the bundle",
            "2026-09-20": "a timestamp",
            "2026-09-20T01:02:03Z": "a timestamp",
        }
        if host is not None:
            cases[f"built on {host}"] = "a host name"
        for value, reason in cases.items():
            with self.assertRaises(CatalogError) as raised:
                self.publish_with({"source_revision": REVISION, "note": value})
            self.assertIn(reason, str(raised.exception))
            self.assertIn("sources.json", str(raised.exception))

    def test_ordinary_structural_values_are_accepted(self):
        for value in ("candidate.joblib", "model/candidate.joblib", "3.13.12", "1.2.0-rc.1",
                      "a" * 64, "generated.five_point_gold_star_token.sha256-" + "b" * 16,
                      "builtin.green_arabica.good", "good", "ver. 2.0"):
            digest = self.publish_with({"source_revision": REVISION, "note": value})
            self.assertEqual(verify_bundle(self.bundles / digest)["bundle_sha256"], digest)

    def test_user_text_may_hold_a_date_a_slash_and_a_dotted_word(self):
        """The same string is refused in a structural field and accepted in user text."""
        user_text = "Token from 2024-05-01, ver. 2.0, size 1/2"
        with self.assertRaises(CatalogError):
            self.publish_with({"source_revision": REVISION, "note": user_text})

        files = bundle_files()
        definitions = [builtin_definition("good"), builtin_definition("stone")]
        for definition in definitions:
            definition["display_name"] = user_text
            definition["visual"]["texture"] = "good"
        files[BUNDLE_CATALOG] = pretty(catalog_manifest(definitions))
        for definition in definitions:
            files[f"catalog/definitions/{definition['object_type_id']}.json"] = pretty(definition)

        digest = publish_bundle(self.bundles, files)

        written = json.loads((self.bundles / digest / "catalog" / "definitions"
                              / "builtin.test.good.json").read_text())
        self.assertEqual(written["display_name"], user_text)

    def test_every_json_file_is_covered_including_the_definitions(self):
        files = bundle_files()
        name = "catalog/definitions/builtin.test.good.json"
        definition = json.loads(files[name])
        definition["provenance"]["source"] = "/absolute/source/path"
        files[name] = pretty(definition)

        with self.assertRaises(CatalogError) as raised:
            publish_bundle(self.bundles, files)
        self.assertIn(name, str(raised.exception))


class BundleBindingTest(BundleFixture, unittest.TestCase):
    """Gate B: a pointer is a promise about bytes, never a syntax field."""

    def pointer_root(self, files=None):
        digest, directory = self.publish(files)
        write_active_pointer(self.root, digest)
        return digest, directory

    def test_a_well_formed_unknown_bundle_is_refused(self):
        self.pointer_root()
        manifest = self.root / "active" / "catalog.json"
        value = json.loads(manifest.read_text())
        value["active_bundle_sha256"] = "f" * 64
        manifest.write_bytes(pretty(value))

        with self.assertRaises(CatalogError) as raised:
            load_catalog(self.root, bundles_root=self.bundles)
        self.assertIn("bundle directory is missing", str(raised.exception))

    def test_a_non_null_pointer_without_a_bundles_root_is_refused(self):
        self.pointer_root()
        with self.assertRaises(CatalogError) as raised:
            load_catalog(self.root)
        self.assertIn("requires a bundles root", str(raised.exception))

    def test_definitions_load_from_the_bundle_and_not_from_the_root(self):
        digest, directory = self.pointer_root()
        # A corrupt copy beside the pointer must never be read.
        decoy = self.root / "definitions"
        decoy.mkdir()
        for path in (directory / "catalog" / "definitions").iterdir():
            (decoy / path.name).write_bytes(b"{not json}")

        catalog = read_active(self.root)

        self.assertEqual(catalog["active_bundle_sha256"], digest)
        self.assertEqual(object_catalog.catalog_labels(catalog), ["good", "stone"])

    def test_a_pointer_that_disagrees_with_its_bundle_is_refused(self):
        self.pointer_root()
        manifest = self.root / "active" / "catalog.json"
        value = json.loads(manifest.read_text())
        value["profile_name"] = "a different profile"
        manifest.write_bytes(pretty(value))

        with self.assertRaises(CatalogError) as raised:
            read_active(self.root)
        self.assertIn("disagree on profile_name", str(raised.exception))

    def test_a_pointer_may_differ_from_its_bundle_in_the_sha_alone(self):
        """Only active_bundle_sha256 may differ, so a pointer cannot override a value."""
        digest, directory = self.pointer_root()
        pointer = self.root / "active" / "catalog.json"
        original = json.loads(pointer.read_text())

        # The reported case: an immutable belt colour changed in the pointer only.
        pointer.write_bytes(pretty({**original, "belt_rgb": [0.99, 0.01, 0.01]}))
        with self.assertRaises(CatalogError) as raised:
            read_active(self.root)
        self.assertIn("disagree on belt_rgb", str(raised.exception))

        altered = {
            "schema_version": 2, "profile_name": "another profile",
            "belt_rgb": [0.5, 0.5, 0.5], "catalog_revision": "e" * 64,
            "max_active_types": 1, "active_type_ids": ["builtin.test.good"],
            "definition_sha256": {"builtin.test.good": "f" * 64},
        }
        for field, value in altered.items():
            pointer.write_bytes(pretty({**original, field: value}))
            with self.assertRaises(CatalogError, msg=field):
                read_active(self.root)
        # Every field of the manifest except the pointer sha is covered above.
        self.assertEqual(sorted({*altered, "active_bundle_sha256"}),
                         sorted(object_catalog._CATALOG_FIELDS))

        pointer.write_bytes(pretty(original))
        self.assertEqual(read_active(self.root)["active_bundle_sha256"], digest)

    def test_files_from_two_bundles_can_never_load_together(self):
        first, _ = self.pointer_root()
        second = publish_bundle(self.bundles, bundle_files(labels=("good", "stick")))
        manifest = self.root / "active" / "catalog.json"
        mixed = json.loads(manifest.read_text())
        other = json.loads((self.bundles / second / BUNDLE_CATALOG).read_text())
        mixed["definition_sha256"] = other["definition_sha256"]
        mixed["active_type_ids"] = other["active_type_ids"]
        mixed["catalog_revision"] = other["catalog_revision"]
        manifest.write_bytes(pretty(mixed))

        with self.assertRaises(CatalogError) as raised:
            read_active(self.root)
        self.assertIn("disagree on", str(raised.exception))


class ActivePointerTest(BundleFixture, unittest.TestCase):
    def test_the_pointer_round_trips_through_a_verified_bundle(self):
        digest, directory = self.publish()
        target = write_active_pointer(self.root, digest)

        pointer = json.loads(target.read_text())
        inner = json.loads((directory / BUNDLE_CATALOG).read_text())

        self.assertEqual(pointer["active_bundle_sha256"], digest)
        self.assertIsNone(inner["active_bundle_sha256"])
        self.assertEqual(pointer["catalog_revision"], inner["catalog_revision"])
        catalog = read_active(self.root)
        self.assertEqual(catalog["catalog_revision"], inner["catalog_revision"])
        self.assertEqual(len(catalog["definitions"]), 2)

    def test_a_rollback_returns_the_catalog_model_and_manifest_together(self):
        first = publish_bundle(self.bundles, bundle_files(labels=("good", "stone")))
        second = publish_bundle(self.bundles, bundle_files(labels=("good", "stick"),
                                                           reject_classes=("stick",)))
        write_active_pointer(self.root, second)
        # History lives beside the active unit and a rollback never touches it.
        history = self.root / "history"
        (history / "jobs").mkdir(parents=True)
        (history / "jobs" / "newer.json").write_bytes(b'{"after": "the snapshot"}')
        before = {path.relative_to(history).as_posix(): path.read_bytes()
                  for path in sorted(history.rglob("*")) if path.is_file()}
        self.assertEqual(object_catalog.catalog_labels(read_active(self.root)), ["good", "stick"])

        rollback_active(self.root, first)

        catalog = read_active(self.root)
        self.assertEqual(object_catalog.catalog_labels(catalog), ["good", "stone"])
        bundle = self.bundles / first
        # The model and its manifest returned with the catalog, as one verified unit.
        self.assertTrue((bundle / "model" / "candidate.joblib").is_file())
        preset = json.loads((bundle / BUNDLE_PRESET).read_text())
        self.assertEqual(preset["model_path"], "model/candidate.joblib")
        self.assertEqual(json.loads((bundle / "policy.json").read_text())["reject_classes"],
                         ["stone"])
        after = {path.relative_to(history).as_posix(): path.read_bytes()
                 for path in sorted(history.rglob("*")) if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(sorted(path.name for path in self.bundles.iterdir()),
                         sorted([first, second]))

    def test_a_rollback_to_an_unverifiable_bundle_leaves_the_pointer_unchanged(self):
        good = publish_bundle(self.bundles, bundle_files())
        write_active_pointer(self.root, good)
        pointer = self.root / "active" / "catalog.json"
        before = pointer.read_bytes()
        damaged = publish_bundle(self.bundles, bundle_files(labels=("good", "stick")))
        (self.bundles / damaged / "policy.json").write_bytes(b"{}")

        for target in (damaged, "f" * 64, "not-a-sha"):
            with self.assertRaises(CatalogError):
                rollback_active(self.root, target)

        self.assertEqual(pointer.read_bytes(), before)
        self.assertEqual(read_active(self.root)["active_bundle_sha256"], good)


class SeedBundleTest(BundleFixture, unittest.TestCase):
    def seed(self):
        """A tiny temporary model, so no test depends on the gitignored packaged one."""
        revision = load_catalog(object_catalog.CATALOG_ROOT)["catalog_revision"]
        model = self.root / "live_green_arabica.joblib"
        joblib.dump(types.SimpleNamespace(classes=["good"], meta={
            "provenance": {"config": {"catalog_revision": revision}}}), model)
        model.with_suffix(".manifest.json").write_bytes(pretty({
            "artifact_sha256": "a" * 64,
            "provenance": {"config": {"catalog_revision": revision}}}))
        return seed_bundle_files(
            object_catalog.CATALOG_ROOT, model, model.with_suffix(".manifest.json"),
            preset={"name": "continuous-live-v2", "model_path": "models/live.joblib"},
            policy={"reject_classes": ["stone", "stick"]},
            sources={"source_revision": REVISION, "files": {"live.py": "b" * 64}})

    def test_bundle_zero_carries_no_absolute_path_date_or_host_name(self):
        files = self.seed()
        self.assertIn(BUNDLE_CATALOG, files)
        self.assertIsNone(json.loads(files[BUNDLE_CATALOG])["active_bundle_sha256"])
        preset = json.loads(files[BUNDLE_PRESET])
        self.assertEqual(preset["model_path"], "model/live_green_arabica.joblib")
        self.assertEqual(preset["model_path_root"], "preset")
        self.assertEqual(preset["policy"]["initial_reject_classes"], ["stone", "stick"])
        # _reject_volatile already refuses these, so this asserts the same rule on bytes.
        for name, data in files.items():
            if not name.endswith(".json"):
                continue
            text = data.decode()
            self.assertNotIn('"/', text, name)
            self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}")

    def test_seeding_never_modifies_the_packaged_tree(self):
        packaged = object_catalog.CATALOG_ROOT
        before = {path.relative_to(packaged).as_posix(): path.read_bytes()
                  for path in sorted(packaged.rglob("*")) if path.is_file()}
        files = self.seed()
        digest = publish_bundle(self.bundles, files)
        write_active_pointer(self.root, digest)
        read_active(self.root)
        after = {path.relative_to(packaged).as_posix(): path.read_bytes()
                 for path in sorted(packaged.rglob("*")) if path.is_file()}
        self.assertEqual(after, before)

    def test_a_seeded_bundle_verifies_and_activates(self):
        digest = publish_bundle(self.bundles, self.seed())
        write_active_pointer(self.root, digest)
        catalog = read_active(self.root)
        self.assertEqual(catalog["active_bundle_sha256"], digest)
        self.assertEqual(len(catalog["definitions"]), catalog["max_active_types"])


class CatalogRootEnvironmentTest(BundleFixture, unittest.TestCase):
    """CATALOG_ROOT is read once at import, so every case runs in a fresh interpreter.

    The rule lives in load_catalog, because profiles.py is a hashed model source and
    stays unchanged. Without an explicit root the active manifest follows the root, and a
    builtin manifest, such as roasted, loads whole from the packaged root. One catalog,
    one root.
    """

    ROOT = ("import json, object_catalog as oc; print(json.dumps({"
            "'packaged': oc.CATALOG_ROOT == oc.PACKAGED_CATALOG_ROOT,"
            "'labels': oc.catalog_labels(oc.load_catalog())}))")
    ACTIVE_ONLY = ("import json, object_catalog as oc\n"
                   "try:\n    oc.load_catalog(); refused = False\n"
                   "except oc.CatalogError:\n    refused = True\n"
                   "print(json.dumps({'refused': refused}))")
    PROFILES = ("import json, {order}; print(json.dumps({{"
                "'active': profiles.GREEN_ARABICA.names, 'roasted': profiles.ROASTED.names,"
                "'profiles': list(profiles.PROFILES)}}))")

    def test_an_unset_or_empty_variable_keeps_the_packaged_root(self):
        packaged = catalog_labels(load_catalog(PACKAGED_CATALOG_ROOT))
        for catalog_root in (None, ""):
            with self.subTest(variable="unset" if catalog_root is None else "empty"):
                seen = run_python(self.ROOT, catalog_root=catalog_root)
                self.assertEqual(seen, {"packaged": True, "labels": packaged})

    def test_a_set_variable_selects_the_bundle_catalog(self):
        _, directory = self.publish(bundle_files(labels=CHANGED_LABELS))

        seen = run_python(self.ROOT, catalog_root=directory / "catalog")

        self.assertEqual(seen, {"packaged": False, "labels": list(CHANGED_LABELS)})

    def test_the_active_catalog_never_falls_back_to_the_packaged_root(self):
        """Only a builtin manifest may come from the packaged root. The active one never."""
        empty = self.root / "no-catalog"
        empty.mkdir()

        self.assertEqual(run_python(self.ACTIVE_ONLY, catalog_root=empty), {"refused": True})

    def test_an_explicit_root_never_mixes_with_the_packaged_root(self):
        _, directory = self.publish(bundle_files(labels=CHANGED_LABELS))

        with self.assertRaises(CatalogError):
            load_catalog(directory / "catalog", manifest="builtin/roasted.catalog.json")

    def test_profiles_loads_the_bundle_catalog_and_the_packaged_roasted_catalog(self):
        _, directory = self.publish(bundle_files(labels=CHANGED_LABELS))
        roasted = catalog_labels(load_catalog(PACKAGED_CATALOG_ROOT,
                                              manifest="builtin/roasted.catalog.json"))
        # The bundle carries no roasted file, so roasted can only come from the packaged root.
        self.assertFalse((directory / "catalog" / "builtin").exists())

        for order in ("object_catalog, profiles", "profiles, object_catalog"):
            with self.subTest(order=order):
                seen = run_python(self.PROFILES.format(order=order),
                                  catalog_root=directory / "catalog")
                self.assertEqual(seen["active"], list(CHANGED_LABELS))
                self.assertEqual(seen["roasted"], roasted)
                self.assertEqual(seen["profiles"], ["bundle test", "roasted"])


class EnsureActiveBundleTest(BundleFixture, unittest.TestCase):
    """Startup resolves one verified active bundle before any profiles import."""

    CHILD = ("import json, sys; from pathlib import Path; import object_catalog as oc\n"
             "root, model, preset = (Path(value) for value in sys.argv[1:4])\n"
             "bundle = oc.ensure_active_bundle(root, catalog_root=oc.PACKAGED_CATALOG_ROOT,"
             " model_path=model, model_manifest_path=model.with_suffix('.manifest.json'),"
             " preset=json.loads(preset.read_text()), policy={'reject_classes': []},"
             " sources={'source_revision': 'c' * 40, 'files': {}})\n"
             "print(json.dumps({'bundle': bundle.name, 'loaded': sorted("
             "{'profiles', 'live', 'engine'} & set(sys.modules))}))")

    def setUp(self):
        super().setUp()
        self.active = self.root / "active"
        # A tiny temporary model, so no test depends on the gitignored packaged one.
        self.model = self.root / "packaged" / "live_green_arabica.joblib"
        self.model.parent.mkdir()
        joblib.dump(types.SimpleNamespace(classes=["good"], meta={}), self.model)
        self.model.with_suffix(".manifest.json").write_bytes(pretty({"artifact_sha256": "a" * 64}))

    def ensure(self, active_root=None):
        return ensure_active_bundle(
            active_root or self.active, catalog_root=PACKAGED_CATALOG_ROOT,
            model_path=self.model, model_manifest_path=self.model.with_suffix(".manifest.json"),
            preset=json.loads(CONTINUOUS_PRESET.read_text()),
            policy={"reject_classes": ["stone", "stick"]},
            sources={"source_revision": REVISION, "files": {}})

    def tree(self, *roots):
        return {str(path): path.read_bytes() for root in roots
                for path in sorted(Path(root).rglob("*")) if path.is_file()}

    def test_an_empty_root_seeds_bundle_zero_once_and_points_at_it(self):
        existing = self.root / "empty"
        existing.mkdir()
        digests = set()
        for active_root in (self.active, existing):
            with self.subTest(root=active_root.name):
                bundle = self.ensure(active_root)

                self.assertEqual(bundle.parent, active_root / "bundles")
                self.assertEqual(verify_bundle(bundle)["bundle_sha256"], bundle.name)
                catalog = read_active(active_root)
                self.assertEqual(catalog["active_bundle_sha256"], bundle.name)
                self.assertEqual(catalog_labels(catalog),
                                 catalog_labels(load_catalog(PACKAGED_CATALOG_ROOT)))
                # A second start finds the pointer and publishes nothing new.
                self.assertEqual(self.ensure(active_root), bundle)
                self.assertEqual([path.name for path in (active_root / "bundles").iterdir()],
                                 [bundle.name])
                digests.add(bundle.name)
        self.assertEqual(len(digests), 1)

    def test_the_seeded_preset_keeps_the_engine_policy_and_states_the_initial_classes(self):
        original = json.loads(CONTINUOUS_PRESET.read_text())["policy"]

        seeded = json.loads((self.ensure() / BUNDLE_PRESET).read_text())

        self.assertEqual(seeded["policy"],
                         {**original, "initial_reject_classes": ["stone", "stick"]})
        self.assertEqual(seeded["model_path"], "model/live_green_arabica.joblib")

    def test_seeding_leaves_the_packaged_tree_byte_identical(self):
        before = self.tree(PACKAGED_CATALOG_ROOT, self.model.parent, CONTINUOUS_PRESET.parent)

        self.ensure()

        self.assertEqual(self.tree(PACKAGED_CATALOG_ROOT, self.model.parent,
                                   CONTINUOUS_PRESET.parent), before)

    def test_a_non_empty_root_returns_its_pointed_bundle_and_is_never_reseeded(self):
        digest = publish_bundle(self.active / "bundles", bundle_files(labels=CHANGED_LABELS))
        write_active_pointer(self.active, digest)

        bundle = self.ensure()

        self.assertEqual(bundle, self.active / "bundles" / digest)
        self.assertEqual([path.name for path in (self.active / "bundles").iterdir()], [digest])
        self.assertEqual(catalog_labels(read_active(self.active)), list(CHANGED_LABELS))

    def test_a_root_that_does_not_verify_is_refused_and_never_reseeded(self):
        def damaged(root):
            digest = publish_bundle(root / "bundles", bundle_files())
            write_active_pointer(root, digest)
            (root / "bundles" / digest / "policy.json").write_bytes(pretty({"reject_classes": []}))

        def lost_pointer(root):
            write_active_pointer(root, publish_bundle(root / "bundles", bundle_files()))
            (root / "active" / "catalog.json").unlink()

        def null_pointer(root):
            shutil.copytree(PACKAGED_CATALOG_ROOT, root)

        for case in (damaged, lost_pointer, null_pointer):
            with self.subTest(case=case.__name__):
                root = self.root / case.__name__
                case(root)
                before = self.tree(root)
                with self.assertRaises(CatalogError):
                    self.ensure(root)
                self.assertEqual(self.tree(root), before)

    def test_it_never_imports_profiles_live_or_engine(self):
        seen = run_python(self.CHILD, self.active, self.model, CONTINUOUS_PRESET)

        self.assertEqual(seen["loaded"], [])
        self.assertEqual(verify_bundle(self.active / "bundles" / seen["bundle"])["bundle_sha256"],
                         seen["bundle"])


class ValidateBundleCommandTest(BundleFixture, unittest.TestCase):
    def run_cli(self, directory, extra=(), env=None):
        result = subprocess.run(
            [sys.executable, str(HERE / "validate_bundle.py"), "--bundle", str(directory), *extra],
            capture_output=True, text=True, cwd=str(HERE), timeout=120, env=env)
        payload = json.loads(result.stdout) if result.stdout.strip() else None
        return result.returncode, payload, result.stdout + result.stderr

    def test_a_complete_bundle_reports_ok(self):
        digest, directory = self.publish()
        with tempfile.TemporaryDirectory() as out:
            report = Path(out) / "result.json"
            code, payload, _ = self.run_cli(directory, ["--json-out", str(report)])
            self.assertEqual(json.loads(report.read_text()), payload)

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["bundle_sha256"], digest)
        self.assertEqual(payload["labels"], ["good", "stone"])
        self.assertEqual(payload["failures"], [])
        self.assertIsNotNone(payload["catalog_revision"])

    def test_each_failure_is_reported_without_a_host_path(self):
        # A corrupt bundle.
        digest, directory = self.publish()
        (directory / "policy.json").write_bytes(pretty({"reject_classes": []}))
        code, payload, output = self.run_cli(directory)
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["failures"][0].startswith("bundle:"))

        # A model whose label order does not match the catalog.
        _, directory = self.publish(bundle_files(model_classes=("stone", "good")),
                                    bundles=self.root / "mismatch")
        code, payload, output = self.run_cli(directory)
        self.assertEqual(code, 1)
        self.assertTrue(any(failure.startswith("model:") for failure in payload["failures"]))

        # A reject class that is not a catalog label.
        _, directory = self.publish(bundle_files(reject_classes=("absent",)),
                                    bundles=self.root / "policy")
        code, payload, output = self.run_cli(directory)
        self.assertEqual(code, 1)
        self.assertIn("policy: unknown reject classes: absent", payload["failures"])

        # No output may carry a host path.
        for text in (output, json.dumps(payload)):
            self.assertNotIn(str(self.root), text)
            self.assertNotIn(str(HERE), text)

    def test_a_model_trained_for_another_catalog_is_refused(self):
        """Label order cannot see a changed definition: the same labels, other physics."""
        other = "d" * 64
        for kwargs, code in (({"model_revision": other}, "model_catalog_mismatch"),
                             ({"manifest_revision": other}, "model_catalog_mismatch"),
                             ({"model_revision": False}, "model_catalog_unrecorded"),
                             ({"manifest_revision": False}, "model_catalog_unrecorded")):
            root = Path(tempfile.mkdtemp(dir=self.root))
            _, directory = self.publish(bundle_files(**kwargs), bundles=root)
            exit_code, payload, output = self.run_cli(directory)

            self.assertEqual(exit_code, 1, kwargs)
            self.assertFalse(payload["ok"])
            self.assertTrue(any(failure.startswith(code) for failure in payload["failures"]),
                            f"{kwargs}: {payload['failures']}")
            self.assertNotIn(str(self.root), output)

    def test_a_changed_label_bundle_loads_through_the_real_service_loader(self):
        """The child binds the candidate catalog itself, whatever its parent exported."""
        self.assertNotEqual(list(CHANGED_LABELS),
                            catalog_labels(load_catalog(PACKAGED_CATALOG_ROOT)))
        _, directory = self.publish(continuous_bundle_files())
        # A service parent has its own active catalog exported. The child must not use it.
        _, active = self.publish(bundle_files(labels=("good", "stick")),
                                 bundles=self.root / "active")
        for name, exported in (("unset", None), ("another bundle", active / "catalog"),
                               ("packaged", PACKAGED_CATALOG_ROOT)):
            env = {key: value for key, value in os.environ.items() if key != CATALOG_ROOT_ENV}
            if exported is not None:
                env[CATALOG_ROOT_ENV] = str(exported)
            with self.subTest(parent=name):
                code, payload, output = self.run_cli(directory, env=env)

                self.assertEqual(payload["failures"], [])
                self.assertEqual(code, 0)
                self.assertEqual(payload["labels"], list(CHANGED_LABELS))

    def test_a_loader_refusal_is_one_sanitized_failure_code(self):
        """Labels and revision agree, so only the real loader can see the other feed rate."""
        _, directory = self.publish(continuous_bundle_files(trained_rate=1.0))

        code, payload, output = self.run_cli(directory)

        self.assertEqual(code, 1)
        self.assertEqual(len(payload["failures"]), 1, payload["failures"])
        self.assertRegex(payload["failures"][0], r"^preset_incompatible: .*feed rate")
        self.assertNotIn("Traceback", output)
        for path in (self.root, self.root.resolve(), HERE):
            self.assertNotIn(str(path), output)

    def test_a_process_that_did_not_bind_the_bundle_catalog_is_refused(self):
        """validate() inside a process with a cached catalog root must never pass."""
        _, directory = self.publish(continuous_bundle_files())
        code = ("import json, sys, object_catalog, validate_bundle; from pathlib import Path; "
                "print(json.dumps(validate_bundle.validate(Path(sys.argv[1]))))")

        payload = run_python(code, directory)

        self.assertFalse(payload["ok"])
        self.assertEqual([failure.split(":")[0] for failure in payload["failures"]],
                         ["preset_catalog_unbound"])

    def test_malformed_model_bytes_stay_inside_the_json_contract(self):
        """An empty and a truncated artifact each return one sanitized model failure."""
        for label, payload in (("empty", b""), ("truncated", b"\x80")):
            root = Path(tempfile.mkdtemp(dir=self.root))
            files = bundle_files()
            files["model/candidate.joblib"] = payload
            _, directory = self.publish(files, bundles=root)

            exit_code, payload_json, output = self.run_cli(directory)

            self.assertEqual(exit_code, 1, label)
            self.assertIsNotNone(payload_json, f"{label}: no JSON result")
            self.assertFalse(payload_json["ok"])
            failures = [failure for failure in payload_json["failures"]
                        if failure.startswith("model:")]
            self.assertEqual(len(failures), 1, f"{label}: {payload_json['failures']}")
            self.assertNotIn("Traceback", output, label)
            self.assertNotIn(str(self.root), output, label)
            self.assertNotIn(str(HERE), output, label)

    def test_a_non_object_policy_returns_one_sanitized_failure(self):
        """A list policy must not raise AttributeError out of the child process."""
        for payload in ([], "text", 7):
            root = Path(tempfile.mkdtemp(dir=self.root))
            files = bundle_files()
            files["policy.json"] = pretty(payload)
            with self.assertRaises(CatalogError):
                publish_bundle(root, dict(files))
            # A published bundle can still hold one: the CLI must answer, not crash.
            _, directory = self.publish(bundle_files(), bundles=root)
            (directory / "policy.json").write_bytes(pretty(payload))

            exit_code, result, output = self.run_cli(directory)

            self.assertEqual(exit_code, 1)
            self.assertIsNotNone(result)
            self.assertNotIn("AttributeError", output)
            self.assertNotIn("Traceback", output)
            self.assertNotIn(str(self.root), output)

    def test_a_usage_error_exits_two(self):
        result = subprocess.run([sys.executable, str(HERE / "validate_bundle.py")],
                                capture_output=True, text=True, cwd=str(HERE), timeout=120)
        self.assertEqual(result.returncode, 2)

    def test_a_missing_bundle_is_reported_not_crashed(self):
        code, payload, _ = self.run_cli(self.root / "bundles" / ("e" * 64))
        self.assertEqual(code, 1)
        self.assertTrue(payload["failures"][0].startswith("bundle:"))


if __name__ == "__main__":
    unittest.main()
