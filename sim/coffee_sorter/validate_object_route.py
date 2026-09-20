"""Validate one draft object definition against the isolated seeded no-air route.

The route is a minimum compatibility gate, not a guarantee under every collision.
It never fires a jet, never attaches a controller, and never loads a model. Every
physical number here stays an unmeasured proxy estimate: the draft declares a
contact primitive and a density, and the mass follows from that proxy volume.

Static validation runs first. A draft without a supported contact proxy reaches
`physics_unsupported` with reason `unsupported_proxy` and runs no simulation at
all. Inconsistent or unreadable physics reaches `physics_unsupported` with reason
`invalid_physics`. The visual uri must be relative, must stay inside the trusted
asset root (`--asset-root`, the repository root by default), and its GLB must
match the declared content hash. That verification is mandatory: a missing file
fails. Only after those checks does the isolated route start, and it
holds the shared exclusive runtime lock for its whole duration (exit 75 when the
lock is busy). The representative load run is evidence only and never changes the
verdict.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from object_definitions import validate_object_definition

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
RUNTIME_LOCK = Path("/private/tmp/hackspain-coffee-runtime.lock")
LOCK_BUSY_EXIT = 75
TRIAL_TIMEOUT_S = 3.0          # one candidate never occupies the route longer than this
SPAWN_ATTEMPTS = 200           # the hand-off zone can be full for a few steps
INJECT_SECONDS = 0.1           # representative load injects one candidate this often
CANDIDATE_LABEL = "candidate"  # isolated route label: it never enters a catalog
CANDIDATE_PRIOR = 0.03
CANDIDATE_RGB = (0.5, 0.5, 0.5)  # the route runs no camera, so this colour is unused
ESTIMATE_BASIS = "unmeasured_proxy_estimate"


def definition_hash(path: Path) -> str:
    """Hash the exact bytes the queue stored, so a non-finite draft still hashes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def static_verdict(draft: Mapping[str, Any],
                   asset_root: Path = REPO_ROOT) -> tuple[str | None, str | None]:
    """Check the whole draft together. Return (reason, detail), or (None, None) when it passes.

    The visual asset is checked before the contact proxy, so a substituted asset blocks
    a supported and an unsupported draft alike.
    """
    try:
        validate_object_definition(draft)
    except (ValueError, TypeError, KeyError) as error:
        return "invalid_physics", str(error)
    visual, physics = draft["visual"], draft["physics"]
    if visual["units"] != "m":
        return "invalid_physics", "visual units must be metres"
    detail = check_visual_asset(visual, asset_root)
    if detail is not None:
        return "invalid_physics", detail
    if physics["proxy"] is None:
        return "unsupported_proxy", physics["unsupported_reason"]
    try:
        volume = proxy_volume(physics["proxy"])
    except (ValueError, TypeError, KeyError) as error:
        return "invalid_physics", str(error)
    density, mass = physics["density_kg_m3"], physics["mass_kg"]
    if not math.isclose(volume, physics["proxy_volume_m3"], rel_tol=1e-12):
        return "invalid_physics", "recomputed proxy volume does not match the draft"
    if not math.isclose(density * volume, mass, rel_tol=1e-12):
        return "invalid_physics", "recomputed proxy mass does not match the draft"
    bounds = [(value, 1000.0) for value in half_extents_mm(physics["proxy"])]
    bounds += [(density, 30000.0), (mass, 1.0)]
    for value, maximum in bounds:
        if not math.isfinite(value) or not 0 < value <= maximum:
            return "invalid_physics", "proxy dimensions, density, or mass leave the engine bounds"
    return None, None


def check_visual_asset(visual: Mapping[str, Any], asset_root: Path) -> str | None:
    """Verify the GLB bytes under the trusted asset root. Return a failure code or None.

    Hash verification is mandatory: the file must exist inside the asset root and its
    sha256 must equal the declared `visual_asset_id`. Containment is decided before any
    read, so a substituted asset outside the root is never hashed. An absolute uri, a
    parent traversal, and a symlink that leaves the root are all refused. No host path
    ever enters the returned code.
    """
    uri = visual["uri"]
    if Path(uri).is_absolute():
        return "visual_uri_not_relative"
    root = Path(asset_root).resolve()
    asset = (root / uri).resolve()
    if not asset.is_relative_to(root):
        return "visual_uri_outside_asset_root"
    if not asset.is_file():
        return "visual_asset_missing"
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    if f"sha256:{digest}" != visual["visual_asset_id"]:
        return "visual_asset_hash_mismatch"
    return None


def proxy_volume(proxy: Mapping[str, Any]) -> float:
    """Recompute the contact proxy volume independently of the draft builder."""
    if proxy["shape"] == "box":
        return 8 * math.prod(proxy["half_extents_m"])
    radius, half_length = proxy["radius_m"], proxy["half_length_m"]
    return math.pi * radius ** 2 * (2 * half_length) + 4 / 3 * math.pi * radius ** 3


def half_extents_mm(proxy: Mapping[str, Any]) -> tuple[float, float, float]:
    """Engine semi-axes in mm: a box keeps its half extents, a capsule uses half length and radius."""
    if proxy["shape"] == "box":
        x, y, z = proxy["half_extents_m"]
        return x * 1e3, y * 1e3, z * 1e3
    radius = proxy["radius_m"] * 1e3
    return proxy["half_length_m"] * 1e3, radius, radius


def candidate_spec(draft: Mapping[str, Any]):
    """One isolated ClassSpec built from the draft proxy. Low equals high, so mass is deterministic."""
    from profiles import ClassSpec

    physics = draft["physics"]
    return ClassSpec(
        CANDIDATE_LABEL, CANDIDATE_PRIOR, physics["proxy"]["shape"],
        tuple((value, value) for value in half_extents_mm(physics["proxy"])),
        CANDIDATE_RGB, 0.0, density=physics["density_kg_m3"], texture=None,
        defect=True, severity="foreign",
    )


def route_profile(spec):
    """The candidate first, then the built-in classes, so the body pools and textures stay valid."""
    from profiles import PROFILES, Profile

    builtin = PROFILES["green_arabica"]
    return Profile("route_candidate", builtin.belt_rgb, [spec, *builtin.classes])


def route_verdict(trials: list[Mapping[str, Any]]) -> tuple[str, str | None]:
    """Pure verdict over the recorded trials. Every trial must reach accept."""
    if not trials or any(trial["outcome"] != "accept" for trial in trials):
        return "physics_unsupported", "route_not_accepted"
    return "accept", None


def run_trials(spec, layout, *, seed: int, trials: int, background_rate: float) -> list[dict[str, Any]]:
    """Run every trial in ONE seeded simulation, so the result stays deterministic."""
    from sim import SorterSim

    sim = SorterSim(route_profile(spec), layout, rate=background_rate, seed=seed)
    records = []
    for _ in range(trials):
        bean = None
        for _ in range(SPAWN_ATTEMPTS):
            bean = sim.spawn(spec)
            if bean is not None:
                break
            sim.step()
        if bean is None:
            records.append({"outcome": "not_spawned", "mass_kg": None, "sim_time_s": None})
            continue
        started = sim.data.time
        while bean.outcome is None and sim.data.time - started < TRIAL_TIMEOUT_S:
            sim.step()
        records.append({
            "outcome": bean.outcome or "unresolved",
            "mass_kg": float(bean.mass),
            "sim_time_s": None if bean.resolved_t is None else bean.resolved_t - bean.spawn_t,
        })
    return records


def representative_load(spec, layout, *, seed: int, rate: float, seconds: float) -> dict[str, Any]:
    """Evidence only: candidate outcomes under a background feed. It never changes the verdict."""
    from sim import SorterSim

    sim = SorterSim(route_profile(spec), layout, rate=rate, seed=seed)
    injected = []
    every = max(1, round(INJECT_SECONDS / sim.dt))
    for step in range(int(seconds / sim.dt)):
        if step % every == 0:
            bean = sim.spawn(spec)
            if bean is not None:
                injected.append(bean)
        sim.step()
    return {
        "evidence_only": True,
        "note": "Representative load is evidence only. It never changes the verdict.",
        "seconds": seconds,
        "background_rate": rate,
        "injected": len(injected),
        "outcomes": dict(Counter(bean.outcome or "unresolved" for bean in injected)),
        "scope": "injected candidates only; the background feed can also sample this class",
    }


def acquire_lock(path: Path):
    """Take the shared exclusive runtime lock without blocking. None means another run holds it."""
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--preset", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--no-air", action="store_true")
    parser.add_argument("--background-rate", type=float)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--load-seconds", type=float, default=0.0)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--expect", choices=("accept", "physics_unsupported"))
    parser.add_argument("--runtime-lock", type=Path, default=RUNTIME_LOCK)
    parser.add_argument("--asset-root", type=Path, default=REPO_ROOT,
                        help="trusted root for visual.uri (default: the repository root)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.trials <= 0:
        parser.error("--trials must be positive")
    if not math.isfinite(args.load_seconds) or args.load_seconds < 0:
        parser.error("--load-seconds must be finite and non-negative")
    try:
        draft = json.loads(args.definition.read_text())
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"--definition cannot be read: {error}")

    result = {
        "verdict": "physics_unsupported", "reason": None, "detail": None,
        "seed": None, "preset_sha256": None, "trials": [], "load": None,
        "estimate_basis": ESTIMATE_BASIS,
        "definition_sha256": definition_hash(args.definition),
        "unsupported_reason": None, "limitations": None,
        # A fixed label, never a path: no host path belongs in this result.
        "asset_root": "asset_root",
        "asset_root_is_default": args.asset_root.resolve() == REPO_ROOT.resolve(),
        "visual_uri": None,
    }
    if isinstance(draft.get("physics"), Mapping):
        result["unsupported_reason"] = draft["physics"].get("unsupported_reason")
        result["limitations"] = draft["physics"].get("limitations")
    uri = draft.get("visual", {}).get("uri") if isinstance(draft.get("visual"), Mapping) else None
    # Only a relative uri is echoed. An absolute one is a host path.
    if isinstance(uri, str) and not Path(uri).is_absolute():
        result["visual_uri"] = uri

    reason, detail = static_verdict(draft, args.asset_root)
    if reason is not None:
        result.update(reason=reason, detail=detail)
        return report(result, args, parser)

    # A route must run from here. Only the no-air route exists.
    if not args.no_air:
        parser.error("the seeded route runs without air: pass --no-air")
    if args.preset is None:
        parser.error("the seeded route needs --preset")
    try:
        preset = json.loads(args.preset.read_text())
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"--preset cannot be read: {error}")
    from scene import Layout

    layout = Layout(**preset["layout"])
    seed = args.seed if args.seed is not None else int(preset["seed"])
    background_rate = args.background_rate if args.background_rate is not None else 0.0
    result.update(seed=seed, preset_sha256=hashlib.sha256(args.preset.read_bytes()).hexdigest())

    handle = acquire_lock(args.runtime_lock)
    if handle is None:
        print(f"runtime lock is occupied: {args.runtime_lock}. No simulation started.", file=sys.stderr)
        return LOCK_BUSY_EXIT
    try:
        spec = candidate_spec(draft)
        result["trials"] = run_trials(spec, layout, seed=seed, trials=args.trials,
                                      background_rate=background_rate)
        verdict, reason = route_verdict(result["trials"])
        result.update(verdict=verdict, reason=reason)
        if verdict == "accept" and args.load_seconds > 0:
            result["load"] = representative_load(spec, layout, seed=seed,
                                                 rate=float(preset["requested_rate"]),
                                                 seconds=args.load_seconds)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
    return report(result, args, parser)


def report(result: dict[str, Any], args, parser) -> int:
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(encoded + "\n")
    print(encoded)
    if args.expect is None:
        return 0
    return 0 if result["verdict"] == args.expect else 1


if __name__ == "__main__":
    sys.exit(main())
