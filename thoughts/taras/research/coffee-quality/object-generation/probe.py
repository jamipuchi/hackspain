"""Bounded direct provider experiment. Model output is data, never executable code."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "contracts" / "learning"))
from providers import OPENROUTER_URL, TYPESAFE_URL, credentials

CASES = {
    "earring": "A single small gold hoop earring with a connected turquoise bead pendant. "
    "Make the opening clearly visible. The assembled object must fit within 28 mm. "
    "The pendant must physically touch its attachment. Lay the hoop in the XY plane.",
    "star": "A small five-point gold star token, about 18 mm wide and 2 mm thick. "
    "Make one continuous solid with five clear points and five concave valleys. "
    "Lay the silhouette in the XY plane.",
    "logo": "A fictional NOVA company logo badge. Use a 26 mm diameter midnight-blue disk. "
    "Add a raised white geometric N logo above small raised NOVA lettering. "
    "Include a small gold five-point star accent. Keep all raised elements on the disk. "
    "The logo must read from the +Z side. Lay the disk in the XY plane.",
}


def vector(length, low, high):
    return {"type": "array", "items": {"type": "number", "minimum": low, "maximum": high},
            "minItems": length, "maxItems": length}


PART_FIELDS = {
    "name": {"type": "string", "minLength": 1, "maxLength": 80},
    "kind": {"type": "string", "enum": ["ring", "ellipsoid", "box", "cylinder", "polygon", "text"]},
    "position_mm": vector(3, -80, 80),
    "rotation_deg": vector(3, -360, 360),
    "size_mm": vector(3, 0.1, 80),
    "color": vector(3, 0, 1),
    "metallic": {"type": "number", "minimum": 0, "maximum": 1},
    "roughness": {"type": "number", "minimum": 0.08, "maximum": 1},
    "tube_mm": {"type": "number", "minimum": 0, "maximum": 20},
    "outline": {"type": "array", "items": vector(2, -0.5, 0.5), "maxItems": 32},
    "text": {"type": "string", "maxLength": 32},
}
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "design_notes": {"type": "string", "maxLength": 1200},
        "parts": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
            "type": "object", "properties": PART_FIELDS,
            "required": list(PART_FIELDS), "additionalProperties": False,
        }},
    },
    "required": ["name", "design_notes", "parts"],
    "additionalProperties": False,
}

SYSTEM = """Design a small object as a JSON geometry recipe for a trusted Blender renderer.
Return only schema-compliant JSON. Do not return Python, URLs, file paths, or executable code.
Choose the parts, geometry, materials, and placement yourself. Use at most 12 parts.
All dimensions and positions use millimeters. Every part has its origin at its geometric center.
rotation_deg is XYZ Euler rotation. size_mm specifies the local bounding dimensions.
ring: lies in local XY with its hole along Z. tube_mm is the tube radius, positive and smaller
than one quarter of the minimum XY dimension. size_mm Z must equal twice tube_mm.
ellipsoid: size_mm gives the three full diameters. cylinder: axis is local Z.
polygon: outline is a simple non-self-intersecting boundary of XY pairs in [-0.5,0.5].
The renderer multiplies each coordinate by size_mm X/Y, without normalizing its bounds.
It extrudes the boundary symmetrically to the requested size_mm Z. List points in boundary order.
text: text uses Blender's default font, fitted to size_mm X/Y, with thickness size_mm Z.
box: rectangular solid. Use outline=[] except for polygons, text='' except for text,
and tube_mm=0 except for rings. color is sRGB, three values between 0 and 1.
Use plausible metallic and roughness values. Ensure attached parts intersect slightly.
Avoid coplanar surfaces. Raised elements must touch their base, with visible thickness.
All parts must form the requested assembly. Do not include a floor, camera, lights, or labels.
"""


def validate(value, schema, path="recipe"):
    """Validate the closed subset used by this experiment, including numeric bounds."""
    kind = schema["type"]
    expected = {"object": dict, "array": list, "string": str, "number": (int, float)}[kind]
    if not isinstance(value, expected) or isinstance(value, bool):
        raise ValueError(f"{path}: invalid {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unsupported value")
    if kind == "object":
        if set(value) != set(schema["required"]):
            raise ValueError(f"{path}: incorrect fields")
        for key, item in value.items():
            validate(item, schema["properties"][key], f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(f"{path}: incorrect array length")
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    elif kind == "number":
        if not math.isfinite(value) or not schema["minimum"] <= value <= schema["maximum"]:
            raise ValueError(f"{path}: number outside permitted range")
    elif not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1200):
        raise ValueError(f"{path}: incorrect text length")


def validate_recipe(recipe):
    validate(recipe, SCHEMA)
    for part in recipe["parts"]:
        if part["kind"] == "ring":
            if not 0 < part["tube_mm"] < min(part["size_mm"][:2]) / 4:
                raise ValueError("ring tube is invalid")
            if not math.isclose(part["size_mm"][2], 2 * part["tube_mm"], abs_tol=0.001):
                raise ValueError("ring height must equal its tube diameter")
        elif part["tube_mm"] != 0:
            raise ValueError("unused tube radius must be zero")
        if part["kind"] == "polygon":
            points = part["outline"]
            if len(points) < 3 or len(set(map(tuple, points))) != len(points):
                raise ValueError("polygon needs distinct boundary points")
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
            if abs(area) < 1e-5:
                raise ValueError("polygon has zero signed area")
        elif part["outline"]:
            raise ValueError("unused outline must be empty")
        if (part["kind"] == "text") != bool(part["text"].strip()):
            raise ValueError("text field does not match part kind")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def provider_schema(value):
    """Keep provider grammar small. Enforce all numeric and length bounds locally."""
    if isinstance(value, dict):
        return {key: provider_schema(item) for key, item in value.items()
                if key not in {"minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"}}
    if isinstance(value, list):
        return [provider_schema(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", choices=list(CASES) + ["all"], default="all")
    parser.add_argument("--model", default="google/gemini-2.5-flash-lite")
    parser.add_argument("--max-tokens", type=int, default=10000)
    parser.add_argument("--live", action="store_true", help="Permit new billable requests. Cached requests never repeat.")
    args = parser.parse_args()
    keys = credentials(args.env_file)
    if not keys.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is missing")
    args.out.mkdir(parents=True, exist_ok=True)
    save(args.out / "schema.json", SCHEMA)
    save(args.out / "environment.json", {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version, "platform": platform.platform(),
        "thread_environment": {k: os.environ.get(k) for k in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")},
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    for case in CASES if args.case == "all" else [args.case]:
        folder = args.out / case
        folder.mkdir(exist_ok=True)
        classification = {"status": "unavailable", "reason": "Jev credential missing"}
        if keys.get("TYPESAFE_API_KEY"):
            jev_payload = {
                "model": "jev-1.13.0", "state": {"requested_object": CASES[case]},
                "questions": {"geometry_family": {
                    "type": "choice",
                    "instructions": "Classify the requested design from its description. Use defer if the design is unclear.",
                    "criteria": {
                        "open_ring": "A loop or hoop with a visible opening, possibly with attached decorative parts.",
                        "flat_silhouette": "A solid flat outline, such as a star, without raised lettering or a base disk.",
                        "layered_badge": "A badge with a supporting base and raised logo details or lettering.",
                        "defer": "The description does not support one of the geometry families.",
                    },
                }},
            }
            save(folder / "jev_request.json", jev_payload)
            try:
                classification = call(TYPESAFE_URL, keys["TYPESAFE_API_KEY"], jev_payload, args.out, args.live)
                save(folder / "jev_response.json", classification)
            except Exception as error:
                classification = {"status": "failed", "reason": str(error)}
                save(folder / "jev_response.json", classification)
        context = classification.get("response", {}).get("answers", classification)
        prompt = CASES[case] + "\nPreliminary text classification from Jev: " + json.dumps(context)
        payload = {
            "model": args.model, "temperature": 0, "max_tokens": args.max_tokens,
            "provider": {"require_parameters": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "blender_object_recipe", "strict": True, "schema": provider_schema(SCHEMA),
            }},
        }
        if args.model != "google/gemini-2.5-flash-lite":
            payload["reasoning"] = {"effort": "low"}
        if args.model == "google/gemini-3.8-flash":
            # The available Vertex endpoint does not advertise temperature support.
            payload.pop("temperature")
        save(folder / "openrouter_request.json", payload)
        result = call(OPENROUTER_URL, keys["OPENROUTER_API_KEY"], payload, args.out, args.live)
        save(folder / "openrouter_response.json", result)
        response = result["response"]
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError(f"{case}: response did not finish")
        recipe = json.loads(choice["message"]["content"])
        validate_recipe(recipe)
        save(folder / "recipe.json", recipe)
        print(json.dumps({"case": case, "model": response.get("model"),
                          "latency_s": result["latency_s"], "usage": response.get("usage"),
                          "parts": len(recipe["parts"]), "cached": result["cached"]}), flush=True)


def call(url, key, payload, out, live):
    digest = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
    path = out.parent / "cache" / f"{digest}.json"
    if path.exists():
        result = json.loads(path.read_text())
        if "error" in result:
            raise RuntimeError(f"cached provider failure: {result['error']}")
        return {**result, "cached": True}
    if not live:
        raise RuntimeError("request is not cached; use --live to permit a new billable request")
    if url not in (OPENROUTER_URL, TYPESAFE_URL):
        raise ValueError("unsupported endpoint")
    request = urllib.request.Request(url, data=json.dumps(payload, allow_nan=False).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        body = json.loads(error.read().decode().replace(key, "[REDACTED]"))
        result = {"error": body.get("error", {"code": error.code}),
                  "latency_s": time.monotonic() - started, "request_sha256": digest, "endpoint": url}
        save(path, result)
        raise RuntimeError(f"provider returned HTTP {error.code}; diagnostic saved at {path}") from None
    result = {"response": body, "latency_s": time.monotonic() - started,
              "request_sha256": digest, "endpoint": url}
    save(path, result)
    return {**result, "cached": False}


if __name__ == "__main__":
    main()
