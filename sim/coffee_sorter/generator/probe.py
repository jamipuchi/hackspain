"""Bounded direct provider experiment. Model output is data, never executable code."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# providers.py sits beside this file. The search path never leaves this directory.
sys.path.insert(0, str(HERE))
from providers import OPENROUTER_URL, TYPESAFE_URL, credentials

DEFAULT_MODEL = "google/gemini-3.8-flash"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# One generation attempt can send two provider requests: the Jev classification and
# then the recipe. Every caller must be able to see both.
REQUESTS: list[dict] = []


class ProbeOutcome(RuntimeError):
    """One typed outcome. A caller maps these classes, never free provider text."""


class CacheMiss(ProbeOutcome):
    """The exact request is not cached and no live request was authorized."""


class CredentialMissing(ProbeOutcome):
    """An authorized live request has no credential."""


class SubmissionUncertain(ProbeOutcome):
    """The request may have reached the provider. Its billing status is unknown."""


class ResponsePersistenceFailed(ProbeOutcome):
    """The provider answered, but its local cache entry could not be persisted."""

    def __init__(self, message, *, request_sha256, endpoint):
        super().__init__(message)
        self.request_sha256 = request_sha256
        self.endpoint = endpoint


class CachedProviderFailure(ProbeOutcome):
    """The cached entry records a provider failure. It is not a cache miss."""


class CacheEntryInvalid(ProbeOutcome):
    """The cached entry failed verification. It is neither a hit nor a miss."""


def source_revision(value=None):
    """Deployment controls this value. It never comes from a request or a browser."""
    revision = value or os.environ.get("CINTA_SOURCE_REVISION")
    if revision is not None:
        if not SHA_RE.fullmatch(revision):
            raise SystemExit("--source-revision must be a full 40-character lowercase commit SHA")
        return revision
    # Development fallback only. A packaged image carries no .git directory.
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def verify_cache_identity(result, url, digest):
    """Identity first. A renamed or moved entry is invalid, not a recorded failure."""
    def refuse(reason):
        raise CacheEntryInvalid(f"cached entry failed verification: {reason}")

    if not isinstance(result, dict):
        refuse("entry is not an object")
    if url not in (OPENROUTER_URL, TYPESAFE_URL) or result.get("endpoint") != url:
        refuse("endpoint does not match the request")
    if result.get("request_sha256") != digest:
        refuse("request digest does not match the canonical payload")


def verify_cached_entry(result, url, digest):
    """Verify a cached entry before reuse. A mismatch is never a hit and never a miss."""
    def refuse(reason):
        raise CacheEntryInvalid(f"cached entry failed verification: {reason}")

    verify_cache_identity(result, url, digest)
    response = result.get("response")
    if not isinstance(response, dict):
        refuse("response is not an object")
    if url != OPENROUTER_URL:
        return
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        refuse("response did not finish")
    try:
        json.loads(choice.get("message", {}).get("content", ""))
    except (AttributeError, TypeError, ValueError):
        refuse("response content is not JSON")

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
    "roughness": {"type": "number", "minimum": 0, "maximum": 1},
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
            for index in range(len(points)):
                for other in range(index + 2, len(points)):
                    if index == 0 and other == len(points) - 1:
                        continue
                    if segments_intersect(points[index], points[(index + 1) % len(points)],
                                          points[other], points[(other + 1) % len(points)]):
                        raise ValueError("polygon boundary intersects itself")
        elif part["outline"]:
            raise ValueError("unused outline must be empty")
        if (part["kind"] == "text") != bool(part["text"].strip()):
            raise ValueError("text field does not match part kind")


def segments_intersect(a, b, c, d):
    def turn(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    values = (turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    for value, p, q, r in zip(values, (a, a, c, c), (b, b, d, d), (c, d, a, b)):
        if abs(value) < 1e-10 and all(min(p[i], q[i]) - 1e-10 <= r[i] <= max(p[i], q[i]) + 1e-10 for i in (0, 1)):
            return True
    return False


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".tmp-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_attempt(path, data):
    """Keep provider evidence immutable. A different request needs a new output directory."""
    if path.exists():
        previous = json.loads(path.read_text())
        comparable = lambda value: {key: item for key, item in value.items() if key != "cached"}
        if comparable(previous) != comparable(data):
            raise RuntimeError(f"attempt already exists with different data; choose a new --out: {path}")
        return
    save(path, data)


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
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Credential file. Process environment credentials also work.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", choices=list(CASES) + ["all"], default="all")
    parser.add_argument("--description", help="Generate one custom object from this description instead of the comparison cases.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenRouter model ID. Default: {DEFAULT_MODEL}.")
    parser.add_argument("--max-tokens", type=int, default=10000)
    parser.add_argument("--live", action="store_true", help="Permit new billable requests. Cached requests never repeat.")
    parser.add_argument("--source-revision", help="Full 40-character lowercase commit SHA. Falls back to CINTA_SOURCE_REVISION.")
    args = parser.parse_args()
    if args.description is not None:
        if args.case != "all":
            parser.error("--description cannot be combined with a named --case")
        if not 1 <= len(args.description.strip()) <= 2000:
            parser.error("--description must contain between 1 and 2000 characters")
        cases = {"custom": args.description.strip()}
    else:
        cases = CASES if args.case == "all" else {args.case: CASES[args.case]}
    # A pure cache replay needs no credential. call() requires one only for a live request.
    keys = credentials(args.env_file)
    args.out.mkdir(parents=True, exist_ok=True)
    save_attempt(args.out / "schema.json", SCHEMA)
    environment = {
        "source_commit": source_revision(args.source_revision),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version, "platform": platform.platform(),
        "thread_environment": {k: os.environ.get(k) for k in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")},
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not (args.out / "environment.json").exists():
        save(args.out / "environment.json", environment)
    for case, description in cases.items():
        folder = args.out / case
        folder.mkdir(exist_ok=True)
        classification = {"status": "unavailable", "reason": "Jev credential missing"}
        jev_response_path = folder / "jev_response.json"
        # The recipe prompt embeds this classification, so the Jev step is part of the
        # recipe request identity. It always runs. Skipping it would build a different,
        # uncached recipe request and hide the reason.
        jev_payload = {
            "model": "jev-1.13.0", "state": {"requested_object": description},
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
        save_attempt(folder / "jev_request.json", jev_payload)
        if jev_response_path.exists():
            classification = json.loads(jev_response_path.read_text())
        else:
            try:
                classification = call(TYPESAFE_URL, keys.get("TYPESAFE_API_KEY"), jev_payload, args.out, args.live)
                save_attempt(jev_response_path, classification)
            except CredentialMissing:
                # Only an authorized live attempt may continue without Jev, exactly as before.
                if not args.live:
                    raise
            except SubmissionUncertain as error:
                # Today's live fallthrough stays. call() already recorded the uncertainty.
                if not args.live:
                    raise
                classification = {"status": "failed", "reason": str(error)}
                save_attempt(jev_response_path, classification)
        context = classification.get("response", {}).get("answers", classification)
        prompt = description + "\nPreliminary text classification from Jev: " + json.dumps(context)
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
        save_attempt(folder / "openrouter_request.json", payload)
        result = call(OPENROUTER_URL, keys.get("OPENROUTER_API_KEY"), payload, args.out, args.live)
        save_attempt(folder / "openrouter_response.json", result)
        response = result["response"]
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError(f"{case}: response did not finish")
        recipe = json.loads(choice["message"]["content"])
        validate_recipe(recipe)
        save_attempt(folder / "recipe.json", recipe)
        print(json.dumps({"case": case, "model": response.get("model"),
                          "latency_s": result["latency_s"], "usage": response.get("usage"),
                          "parts": len(recipe["parts"]), "cached": result["cached"]}), flush=True)


def call(url, key, payload, out, live):
    digest = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
    path = out.parent / "cache" / f"{digest}.json"
    if path.exists():
        result = json.loads(path.read_text())
        # Identity first: a renamed error entry is an invalid entry, not a provider failure.
        try:
            verify_cache_identity(result, url, digest)
        except CacheEntryInvalid:
            _record(url, digest, path, cached=True, outcome="cache_entry_invalid")
            raise
        if "error" in result:
            _record(url, digest, path, cached=True, outcome="cached_provider_failure")
            raise CachedProviderFailure(f"cached provider failure: {result['error']}")
        # A digest-named file alone is not proof. Check the response metadata too.
        try:
            verify_cached_entry(result, url, digest)
        except CacheEntryInvalid:
            _record(url, digest, path, cached=True, outcome="cache_entry_invalid")
            raise
        _record(url, digest, path, cached=True, outcome="hit")
        return {**result, "cached": True}
    if not live:
        _record(url, digest, path, cached=False, outcome="cache_miss")
        raise CacheMiss("request is not cached; use --live to permit a new billable request")
    if url not in (OPENROUTER_URL, TYPESAFE_URL):
        raise ValueError("unsupported endpoint")
    if not key:
        name = "OPENROUTER_API_KEY" if url == OPENROUTER_URL else "TYPESAFE_API_KEY"
        _record(url, digest, path, cached=False, outcome="credential_missing")
        raise CredentialMissing(f"{name} is missing for an authorized live request")
    request = urllib.request.Request(url, data=json.dumps(payload, allow_nan=False).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace").replace(key, "[REDACTED]")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": {"code": error.code, "body": raw[:8000]}}
        result = {"error": body.get("error", {"code": error.code}),
                  "latency_s": time.monotonic() - started, "request_sha256": digest, "endpoint": url}
        save(path, result)
        _record(url, digest, path, cached=False, outcome="uncertain")
        raise SubmissionUncertain(f"provider returned HTTP {error.code}; diagnostic saved at {path}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        save(path, {"error": "transport or JSON response failure; billing status unknown",
                    "latency_s": time.monotonic() - started, "request_sha256": digest, "endpoint": url})
        _record(url, digest, path, cached=False, outcome="uncertain")
        raise SubmissionUncertain(f"provider connection or JSON response failed; diagnostic saved at {path}") from None
    result = {"response": body, "latency_s": time.monotonic() - started,
              "request_sha256": digest, "endpoint": url}
    try:
        save(path, result)
    except (OSError, TypeError, ValueError):
        # The response is a known completed provider interaction even when local storage
        # fails. The caller must retain that billing truth without inferring it from a
        # cache file that does not exist.
        try:
            _record(url, digest, path, cached=False, outcome="response_persistence_failed")
        except OSError:
            # The diagnostic is best-effort. It must never hide the known response.
            pass
        raise ResponsePersistenceFailed(
            "provider response was received but its cache entry could not be written",
            request_sha256=digest,
            endpoint=url,
        ) from None
    _record(url, digest, path, cached=False, outcome="live")
    return {**result, "cached": False}


def _record(url, digest, path, *, cached, outcome):
    """One ledger row per provider request. One attempt can send Jev and then the recipe."""
    REQUESTS.append({
        "endpoint": url, "request_sha256": digest, "cached": cached, "outcome": outcome,
        "cache_entry_sha256": (hashlib.sha256(path.read_bytes()).hexdigest()
                               if path.exists() else None),
    })


if __name__ == "__main__":
    main()
