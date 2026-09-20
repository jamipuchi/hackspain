from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from object_definitions import (
    _validate_llm_proposal,
    build_object_definition,
    propose_physics,
    validate_object_definition,
)


ROOT = Path(__file__).resolve().parents[2]
STAR = ROOT / "thoughts/taras/research/coffee-quality/object-generation/results/gemini/star"


def box_proposal():
    return {
        "shape": "box",
        "dimensions_m": [0.016, 0.012, 0.002],
        "density_kg_m3": 1200.0,
        "material_assumption": "Decorative polymer with a metallic finish.",
        "limitations": "The rendered gold appearance does not identify the material.",
    }


class ObjectDefinitionTest(unittest.TestCase):
    def build_star(self, **changes):
        values = {
            "description": "A small five-point star token.",
            "recipe_path": STAR / "recipe.json",
            "render_metadata_path": STAR / "render/render.json",
            "glb_path": STAR / "render/object.glb",
            "physics_proposal": box_proposal(),
            "visual_uri": "results/gemini/star/render/object.glb",
        }
        values.update(changes)
        return build_object_definition(**values)

    def test_builds_valid_draft_from_existing_generator_artifacts(self):
        value = self.build_star()

        self.assertEqual("draft", value["lifecycle_state"])
        self.assertEqual("draft_unreviewed", value["physics"]["review_status"])
        self.assertEqual(
            "sha256:7182c057c83b501438fd6f7a9b1d45700bb0e39579b0a73dce73065af62abbdc",
            value["visual"]["visual_asset_id"],
        )
        self.assertEqual([0.008, 0.006, 0.001], value["physics"]["proxy"]["half_extents_m"])
        self.assertEqual("xyzw", value["visual"]["asset_quaternion_order"])
        self.assertEqual("wxyz", value["visual"]["server_quaternion_order"])
        self.assertEqual("unassigned", value["sorting"]["status"])
        expected_volume = 0.016 * 0.012 * 0.002
        self.assertAlmostEqual(expected_volume, value["physics"]["proxy_volume_m3"])
        self.assertAlmostEqual(1200 * expected_volume, value["physics"]["mass_kg"])
        validate_object_definition(value)

    def test_object_type_id_ignores_visual_only_revision(self):
        first = self.build_star()
        revised = copy.deepcopy(first)
        revised["visual"]["visual_asset_id"] = "sha256:" + "a" * 64

        validate_object_definition(revised)
        self.assertEqual(first["object_type_id"], revised["object_type_id"])

    def test_object_type_id_changes_with_physics_semantics(self):
        first = self.build_star()
        proposal = box_proposal()
        proposal["density_kg_m3"] = 1300.0
        second = self.build_star(physics_proposal=proposal)

        self.assertNotEqual(first["object_type_id"], second["object_type_id"])

    def test_caller_can_add_unreviewed_sorting_proposal(self):
        first = self.build_star()
        proposed = self.build_star(sorting_proposal={
            "class_name": "star_token",
            "defect": True,
            "severity": "foreign",
            "proposed_action": "reject",
        })

        self.assertEqual("proposal_unreviewed", proposed["sorting"]["status"])
        self.assertNotEqual(first["object_type_id"], proposed["object_type_id"])
        validate_object_definition(proposed)

    def test_capsule_uses_engine_dimension_conventions(self):
        proposal = {
            "shape": "capsule",
            "dimensions_m": [0.018, 0.004, 0.004],
            "density_kg_m3": 700.0,
            "material_assumption": "Dry wood.",
            "limitations": "The proxy excludes surface irregularity.",
        }
        value = self.build_star(object_key="example_stick", physics_proposal=proposal)
        proxy = value["physics"]["proxy"]

        self.assertEqual("+Z", proxy["local_axis"])
        self.assertAlmostEqual(0.002, proxy["radius_m"])
        self.assertAlmostEqual(0.007, proxy["half_length_m"])
        expected_volume = math.pi * 0.002 ** 2 * 0.014 + 4 / 3 * math.pi * 0.002 ** 3
        self.assertAlmostEqual(expected_volume, value["physics"]["proxy_volume_m3"])

    def test_unsupported_geometry_has_no_mass_claim(self):
        value = self.build_star(physics_proposal={
            "shape": "unsupported",
            "unsupported_reason": "A ring needs a reviewed compound or concave collision representation.",
            "limitations": "The current engine primitives do not preserve the opening.",
        })

        self.assertIsNone(value["physics"]["proxy"])
        self.assertIsNone(value["physics"]["mass_kg"])
        validate_object_definition(value)

    def test_rejects_invalid_dimensions_and_mass(self):
        proposal = box_proposal()
        proposal["dimensions_m"] = [0.01, float("nan"), 0.002]
        with self.assertRaisesRegex(ValueError, "finite"):
            self.build_star(physics_proposal=proposal)

        value = self.build_star()
        value["physics"]["mass_kg"] *= 2
        with self.assertRaisesRegex(ValueError, "mass is inconsistent"):
            validate_object_definition(value)

    def test_rejects_unsupported_proxy_shape_and_review_claims(self):
        proposal = box_proposal()
        proposal["shape"] = "half"
        with self.assertRaisesRegex(ValueError, "shape is unsupported"):
            self.build_star(physics_proposal=proposal)

        proposal["shape"] = "ellipsoid"
        with self.assertRaisesRegex(ValueError, "shape is unsupported"):
            self.build_star(physics_proposal=proposal)

        value = self.build_star()
        value["lifecycle_state"] = "active"
        with self.assertRaisesRegex(ValueError, "draft and unreviewed"):
            validate_object_definition(value)

    def test_rejects_stale_render_metadata(self):
        recipe = json.loads((STAR / "recipe.json").read_text())
        metadata = json.loads((STAR / "render/render.json").read_text())
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            recipe_path = directory / "recipe.json"
            metadata_path = directory / "render.json"
            asset_path = directory / "object.glb"
            recipe_path.write_text(json.dumps(recipe))
            metadata_path.write_text(json.dumps(metadata))
            asset_path.write_bytes(b"glTF" + b"\x00" * 8)

            with self.assertRaisesRegex(ValueError, "recipe hash"):
                build_object_definition(
                    description="A star.",
                    recipe_path=recipe_path,
                    render_metadata_path=metadata_path,
                    glb_path=asset_path,
                    physics_proposal=box_proposal(),
                )

    def test_rejects_non_glb_visual_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            asset_path = Path(folder) / "object.glb"
            asset_path.write_bytes(b"not a glb file")
            with self.assertRaisesRegex(ValueError, "GLB header"):
                self.build_star(glb_path=asset_path)

    def test_llm_wrapper_uses_existing_cached_call_and_records_provenance(self):
        raw = {
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Decorative polymer.",
            "limitations": "Visual appearance does not establish material.",
            "unsupported_reason": "",
        }
        calls = []

        def fake_call(url, key, payload, out, live):
            calls.append((url, key, payload, out, live))
            digest = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
            return {
                "response": {"choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(raw)},
                }]},
                "request_sha256": digest,
                "endpoint": url,
                "cached": True,
            }

        fake_probe = SimpleNamespace(
            OPENROUTER_URL="https://openrouter.example/v1",
            credentials=lambda path: {"OPENROUTER_API_KEY": "secret"},
            provider_schema=lambda value: value,
            call=fake_call,
        )
        with tempfile.TemporaryDirectory() as folder:
            probe_path = Path(folder) / "probe.py"
            probe_path.write_text("fake probe")
            evidence = Path(folder) / "evidence"
            with patch("object_definitions._load_generator_probe", return_value=(fake_probe, probe_path)):
                proposal = propose_physics(
                    description="A small star token.",
                    visual_dimensions_m=[0.016, 0.012, 0.002],
                    evidence_dir=evidence,
                )
                repeated = propose_physics(
                    description="A small star token.",
                    visual_dimensions_m=[0.016, 0.012, 0.002],
                    evidence_dir=evidence,
                )

            self.assertEqual(proposal, repeated)
            self.assertEqual("llm", proposal["provenance"]["kind"])
            self.assertEqual(64, len(proposal["provenance"]["request_sha256"]))
            self.assertEqual(2, len(calls))
            self.assertFalse(calls[0][4])
            self.assertNotIn("secret", (evidence / "physics_request.json").read_text())
            self.assertTrue((evidence / "physics_proposal.json").is_file())

    def test_llm_wrapper_preserves_explicit_unsupported_result(self):
        raw = {
            "shape": "unsupported",
            "dimensions_m": [0.02, 0.02, 0.004],
            "density_kg_m3": 0,
            "material_assumption": "",
            "limitations": "The opening changes collision behavior.",
            "unsupported_reason": "A ring needs a reviewed compound collision representation.",
        }

        def fake_call(*args):
            url, _, payload, _, _ = args
            return {
                "response": {"choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(raw)},
                }]},
                "request_sha256": hashlib.sha256(
                    json.dumps([url, payload], sort_keys=True).encode()
                ).hexdigest(),
                "endpoint": url,
                "cached": False,
            }

        fake_probe = SimpleNamespace(
            OPENROUTER_URL="https://openrouter.example/v1",
            credentials=lambda path: {"OPENROUTER_API_KEY": "secret"},
            provider_schema=lambda value: value,
            call=fake_call,
        )
        with tempfile.TemporaryDirectory() as folder:
            probe_path = Path(folder) / "probe.py"
            probe_path.write_text("fake probe")
            with patch("object_definitions._load_generator_probe", return_value=(fake_probe, probe_path)):
                proposal = propose_physics(
                    description="A small hoop earring.",
                    visual_dimensions_m=[0.02, 0.02, 0.004],
                    evidence_dir=Path(folder) / "evidence",
                )

        self.assertEqual("unsupported", proposal["shape"])
        self.assertNotIn("density_kg_m3", proposal)
        value = self.build_star(physics_proposal=proposal)
        self.assertIsNone(value["physics"]["mass_kg"])
        self.assertEqual("llm", value["physics"]["proposal_provenance"]["kind"])

    def test_llm_wrapper_rejects_mismatched_request_provenance(self):
        raw = {
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Decorative polymer.",
            "limitations": "Visual appearance does not establish material.",
            "unsupported_reason": "",
        }
        fake_probe = SimpleNamespace(
            OPENROUTER_URL="https://openrouter.example/v1",
            credentials=lambda path: {"OPENROUTER_API_KEY": "secret"},
            provider_schema=lambda value: value,
            call=lambda *args: {
                "response": {"choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(raw)},
                }]},
                "request_sha256": "d" * 64,
                "endpoint": "https://openrouter.example/v1",
                "cached": True,
            },
        )
        with tempfile.TemporaryDirectory() as folder:
            probe_path = Path(folder) / "probe.py"
            probe_path.write_text("fake probe")
            with patch("object_definitions._load_generator_probe", return_value=(fake_probe, probe_path)):
                with self.assertRaisesRegex(ValueError, "provenance"):
                    propose_physics(
                        description="A small star token.",
                        visual_dimensions_m=[0.016, 0.012, 0.002],
                        evidence_dir=Path(folder) / "evidence",
                    )

    def test_llm_wrapper_allows_cached_replay_without_credential(self):
        raw = {
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Decorative polymer.",
            "limitations": "Visual appearance does not establish material.",
            "unsupported_reason": "",
        }

        def cached_call(url, key, payload, out, live):
            self.assertEqual("", key)
            self.assertFalse(live)
            digest = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
            return {
                "response": {"choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(raw)},
                }]},
                "request_sha256": digest,
                "endpoint": url,
                "cached": True,
            }

        fake_probe = SimpleNamespace(
            OPENROUTER_URL="https://openrouter.example/v1",
            credentials=lambda path: {},
            provider_schema=lambda value: value,
            call=cached_call,
        )
        with tempfile.TemporaryDirectory() as folder:
            probe_path = Path(folder) / "probe.py"
            probe_path.write_text("fake probe")
            with patch("object_definitions._load_generator_probe", return_value=(fake_probe, probe_path)):
                proposal = propose_physics(
                    description="A small star token.",
                    visual_dimensions_m=[0.016, 0.012, 0.002],
                    evidence_dir=Path(folder) / "evidence",
                )

        self.assertEqual("box", proposal["shape"])

    def test_llm_proposal_enforces_local_schema_bounds(self):
        valid = {
            "shape": "box",
            "dimensions_m": [0.016, 0.012, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Decorative polymer.",
            "limitations": "Visual appearance does not establish material.",
            "unsupported_reason": "",
        }
        for field, invalid in (
            ("dimensions_m", [2.0, 0.012, 0.002]),
            ("dimensions_m", [0.0000001, 0.012, 0.002]),
            ("density_kg_m3", 50000.0),
        ):
            with self.subTest(field=field, invalid=invalid):
                value = {**valid, field: invalid}
                with self.assertRaisesRegex(ValueError, "schema bounds"):
                    _validate_llm_proposal(value)


if __name__ == "__main__":
    unittest.main()
