"""Immutable bundle, active pointer, rollback, and the child-process validator.

Temp directories only. The model is a tiny picklable object with a classes attribute:
no training, no real artifact.
"""
from __future__ import annotations

import json
import hashlib
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
from object_catalog import (BUNDLE_CATALOG, BUNDLE_MANIFEST, BUNDLE_PRESET, CatalogError,
                            catalog_revision, definition_sha256, load_catalog,
                            publish_bundle, read_active, rollback_active, seed_bundle_files,
                            verify_bundle, write_active_pointer, write_bundle_manifest)
from test_object_catalog import builtin_definition

HERE = Path(__file__).resolve().parent
REVISION = "c" * 40


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
        model = HERE / "models" / "live_green_arabica.joblib"
        if not model.is_file():
            self.skipTest("the packaged model artifact is not present in this checkout")
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


class ValidateBundleCommandTest(BundleFixture, unittest.TestCase):
    def run_cli(self, directory, extra=()):
        result = subprocess.run(
            [sys.executable, str(HERE / "validate_bundle.py"), "--bundle", str(directory), *extra],
            capture_output=True, text=True, cwd=str(HERE), timeout=120)
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
