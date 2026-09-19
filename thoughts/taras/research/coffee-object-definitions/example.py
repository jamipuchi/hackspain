"""Print one validated draft definition from existing generated artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "sim/coffee_sorter"))

from object_definitions import build_object_definition  # noqa: E402


def main() -> None:
    star = ROOT / "thoughts/taras/research/coffee-quality/object-generation/results/gemini/star"
    value = build_object_definition(
        description="A small five-point gold star token.",
        recipe_path=star / "recipe.json",
        render_metadata_path=star / "render/render.json",
        glb_path=star / "render/object.glb",
        visual_uri="../coffee-quality/object-generation/results/gemini/star/render/object.glb",
        physics_proposal={
            "shape": "box",
            "dimensions_m": [0.0169, 0.0161, 0.002],
            "density_kg_m3": 1200.0,
            "material_assumption": "Hypothetical decorative polymer with a metallic finish.",
            "limitations": "The proxy includes empty space between the star points.",
        },
    )
    print(json.dumps(value, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
