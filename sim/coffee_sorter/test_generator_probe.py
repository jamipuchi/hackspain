"""Focused material contract checks for generated Blender recipes."""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "generator"))

import probe


class MaterialContractTest(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((HERE / "tests/fixtures/recipe.json").read_text())

    def test_blender_roughness_range_is_closed_zero_to_one(self):
        for value in (0, 0.05, 1):
            recipe = copy.deepcopy(self.recipe)
            recipe["parts"][0]["roughness"] = value
            probe.validate_recipe(recipe)

        for value in (-0.001, 1.001):
            recipe = copy.deepcopy(self.recipe)
            recipe["parts"][0]["roughness"] = value
            with self.assertRaisesRegex(ValueError, "number outside permitted range"):
                probe.validate_recipe(recipe)


if __name__ == "__main__":
    unittest.main()
