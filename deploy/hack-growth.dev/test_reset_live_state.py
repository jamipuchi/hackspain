"""Focused tests for the CINTA live-state reset tool."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
COFFEE_ROOT = HERE.parents[1] / "sim" / "coffee_sorter"
sys.path.insert(0, str(COFFEE_ROOT))
sys.path.insert(0, str(HERE))

import object_catalog
import reset_live_state
from test_object_catalog import generated_definition
from test_validate_bundle import bundle_files, catalog_manifest, pretty


def snapshot(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def assert_preserved(test: unittest.TestCase, before: dict[str, str], root: Path) -> None:
    after = snapshot(root)
    test.assertEqual(before, {name: after.get(name) for name in before})


class ResetLiveStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "item-control"
        self.active = self.root / "active"
        self.jobs = self.root / "jobs"
        self.history = self.root / "history"
        self.cache = self.root / "provider-cache"
        for path in (self.jobs, self.history / "wall-of-fame" / "retired-token",
                     self.cache / "cache"):
            path.mkdir(parents=True)
        (self.root / "writer.lock").write_text("")

        baseline_files = bundle_files()
        candidate_files = bundle_files(labels=("new_token", "stone"),
                                       model_classes=("new_token", "stone"))
        glb = b"glTFactive-generated-token"
        self.active_glb = glb
        generated = generated_definition("new_token")
        generated["visual"]["asset"]["glb_sha256"] = hashlib.sha256(glb).hexdigest()
        generated["visual"]["asset"]["visual_asset_id"] = \
            f"sha256:{generated['visual']['asset']['glb_sha256']}"
        stone = json.loads(candidate_files[
            "catalog/definitions/builtin.test.stone.json"])
        candidate_manifest = catalog_manifest([generated, stone])
        candidate_files[object_catalog.BUNDLE_CATALOG] = pretty(candidate_manifest)
        del candidate_files["catalog/definitions/builtin.test.new_token.json"]
        candidate_files[f"catalog/definitions/{generated['object_type_id']}.json"] = \
            pretty(generated)
        self.baseline = object_catalog.publish_bundle(self.active / "bundles", baseline_files)
        self.candidate = object_catalog.publish_bundle(self.active / "bundles", candidate_files)
        object_catalog.write_active_pointer(self.active, self.candidate)
        (self.active / reset_live_state.SEED_MARKER).write_text(json.dumps(
            {"bundle_sha256": self.baseline}) + "\n")

        job = self.jobs / "11111111-1111-4111-8111-111111111111"
        (job / "previews").mkdir(parents=True)
        (job / "job.json").write_text('{"state":"training"}\n')
        (job / "definition.json").write_text(json.dumps(
            {"object_type_id": generated["object_type_id"]}) + "\n")
        (job / "previews" / "object.glb").write_bytes(glb)
        (job / "previews" / "perspective.png").write_bytes(b"active-perspective")
        (job / "previews" / "top.png").write_bytes(b"active-top")
        archive = self.history / "wall-of-fame" / "retired-token"
        (archive / "archive.json").write_text('{"object_type_id":"retired-token"}\n')
        (archive / "object.glb").write_bytes(b"glTFarchive")
        (archive / "perspective.png").write_bytes(b"archive-preview")
        (archive / "model.joblib").write_bytes(b"archive-model")
        (self.history / "activations.jsonl").write_text('{"result":"active"}\n')
        (self.cache / "cache" / "provider.json").write_text('{"cached":true}\n')

    def test_dry_run_changes_no_bytes(self):
        before = snapshot(self.root)

        result = reset_live_state.reset_state(self.root, apply=False)

        self.assertEqual("dry-run", result["mode"])
        self.assertEqual(self.candidate, result["previous_bundle_sha256"])
        self.assertEqual(self.baseline, result["baseline_bundle_sha256"])
        self.assertEqual(1, result["retained_job_count"])
        self.assertEqual(["generated.new_token"], result["active_generated_type_ids"])
        self.assertEqual(before, snapshot(self.root))

    def test_apply_archives_jobs_and_repoints_to_the_builtin_bundle(self):
        history_before = snapshot(self.history)
        bundles_before = snapshot(self.active / "bundles")
        cache_before = snapshot(self.cache)
        jobs_before = snapshot(self.jobs)

        result = reset_live_state.reset_state(self.root, apply=True)

        backup = Path(result["backup"])
        self.assertEqual(self.baseline,
                         object_catalog.read_active(self.active)["active_bundle_sha256"])
        self.assertEqual([], list(self.jobs.iterdir()))
        self.assertEqual(jobs_before, snapshot(backup / "jobs"))
        self.assertTrue((backup / "active.catalog.json").is_file())
        self.assertTrue((backup / reset_live_state.SEED_MARKER).is_file())
        assert_preserved(self, history_before, self.history)
        self.assertEqual(bundles_before, snapshot(self.active / "bundles"))
        self.assertEqual(cache_before, snapshot(self.cache))
        self.assertEqual(b"glTFarchive",
                         (self.history / "wall-of-fame" / "retired-token" / "object.glb").read_bytes())
        generated_archive = self.history / "wall-of-fame" / "generated.new_token"
        self.assertEqual(self.active_glb, (generated_archive / "object.glb").read_bytes())
        self.assertEqual(b"active-perspective",
                         (generated_archive / "perspective.png").read_bytes())
        self.assertTrue((generated_archive / "model.manifest.json").is_file())

    def test_unknown_layout_is_refused(self):
        (self.root / "mystery").mkdir()
        with self.assertRaisesRegex(reset_live_state.ResetError, "unknown entries"):
            reset_live_state.reset_state(self.root, apply=False)

    def test_a_running_writer_is_refused(self):
        with (self.root / "writer.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            preview = reset_live_state.reset_state(self.root, apply=False)
            self.assertFalse(preview["writer_lock_available"])
            with self.assertRaisesRegex(reset_live_state.ResetError, "still owns"):
                reset_live_state.reset_state(self.root, apply=True)

    def test_missing_generated_archive_evidence_is_refused_before_mutation(self):
        (self.jobs / "11111111-1111-4111-8111-111111111111" /
         "previews" / "top.png").unlink()
        before = snapshot(self.root)

        with self.assertRaisesRegex(reset_live_state.ResetError,
                                    "archive evidence top.png"):
            reset_live_state.reset_state(self.root, apply=True)

        self.assertEqual(before, snapshot(self.root))

    def test_pointer_write_failure_restores_jobs_and_state(self):
        before = snapshot(self.root)

        with mock.patch.object(object_catalog, "write_active_pointer",
                               side_effect=OSError("pointer unavailable")):
            with self.assertRaisesRegex(OSError, "pointer unavailable"):
                reset_live_state.reset_state(self.root, apply=True)

        self.assertEqual(before, snapshot(self.root))
        self.assertEqual(
            self.candidate,
            object_catalog.read_active(self.active)["active_bundle_sha256"])
        self.assertEqual([], list((self.root / reset_live_state.BACKUPS).iterdir()))

    def test_laptop_wrapper_defaults_to_dry_run_and_scopes_apply_to_coffee(self):
        capture = Path(self.temporary.name) / "capture"
        fake_ssh = Path(self.temporary.name) / "ssh"
        fake_ssh.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$CAPTURE.args\"\n"
            "cat > \"$CAPTURE.stdin\"\n")
        fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
        environment = {**os.environ, "SSH_BIN": str(fake_ssh), "CAPTURE": str(capture)}
        wrapper = HERE / "reset-live-state.sh"

        subprocess.run([str(wrapper)], check=True, env=environment)
        dry_args = (capture.with_suffix(".args")).read_text()
        self.assertIn("hackspain", dry_args)
        self.assertIn("--dry-run", dry_args)

        subprocess.run([str(wrapper), "--apply"], check=True, env=environment)
        remote = (capture.with_suffix(".stdin")).read_text()
        self.assertIn("compose stop coffee", remote)
        self.assertEqual(2, remote.count("compose up -d --no-deps coffee"))
        self.assertLess(remote.index("--dry-run"), remote.index("compose stop coffee"))
        self.assertNotIn("compose down", remote)
        self.assertNotIn("docker stop", remote)


if __name__ == "__main__":
    unittest.main()
