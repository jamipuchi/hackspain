"""Verify one immutable bundle in a separate child process.

It reuses object_catalog.verify_bundle: there is one verifier, and this file is only the
child-process entry point. It also runs the real live.load_preset on the bundled preset.
profiles loads its catalog once at import, so main() binds the bundle's own catalog
before object_catalog or profiles exists in this child. A parent never revalidates a
candidate against its own cached profiles.

The result is one JSON object on stdout. No output carries a host path: a bundle is named
by its sha and by the relative paths it lists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

USAGE_EXIT = 2
# The same name as object_catalog.CATALOG_ROOT_ENV. That module reads the variable once at
# import, so this file cannot import it before main() sets the value.
CATALOG_ROOT_ENV = "COFFEE_OBJECT_CATALOG_ROOT"


def validate(bundle_dir: Path) -> dict:
    """Check the bytes, then the catalog, the model, and the policy that they describe."""
    import object_catalog  # imported here: main() binds the bundle catalog first
    from object_catalog import BUNDLE_PRESET, CatalogError

    result = {"ok": False, "bundle_sha256": Path(bundle_dir).name, "catalog_revision": None,
              "labels": [], "failures": []}
    try:
        object_catalog.verify_bundle(bundle_dir)
    except CatalogError as error:
        result["failures"].append(f"bundle: {error}")
        return result

    bundle = Path(bundle_dir)
    try:
        catalog = object_catalog.load_catalog(bundle / "catalog")
        result["catalog_revision"] = catalog["catalog_revision"]
        result["labels"] = object_catalog.catalog_labels(catalog)
    except CatalogError as error:
        result["failures"].append(f"catalog: {error}")
        return result

    try:
        preset = json.loads((bundle / BUNDLE_PRESET).read_text())
        model = _load_model(bundle / preset["model_path"])
        object_catalog.require_label_order(catalog, list(model.classes))
        result["failures"].extend(
            _revision_failures(bundle, model, preset["model_path"],
                               catalog["catalog_revision"]))
    except Exception as error:
        # A child-process boundary: malformed model bytes raise anything joblib and
        # pickle raise, and one sanitized JSON result must still reach the caller.
        result["failures"].append(f"model: {_sanitized(error, bundle)}")

    failure = _preset_failure(bundle.resolve() / BUNDLE_PRESET, object_catalog.CATALOG_ROOT)
    if failure is not None:
        result["failures"].append(failure)

    try:
        policy = json.loads((bundle / "policy.json").read_text())
        if not isinstance(policy, dict):
            raise TypeError("policy.json must be a JSON object")
        reject = list(policy.get("reject_classes", []))
        unknown = sorted(set(reject) - set(result["labels"]))
        if unknown:
            result["failures"].append(f"policy: unknown reject classes: {', '.join(unknown)}")
    except (OSError, ValueError, TypeError) as error:
        result["failures"].append(f"policy: {_sanitized(error, bundle)}")

    result["ok"] = not result["failures"]
    return result


def _revision_failures(bundle: Path, model, model_path: str, revision: str) -> list[str]:
    """An activated model must be bound to the catalog it was trained for.

    Label order alone cannot see a changed definition: the same labels can describe
    different physics. Both the model meta and its manifest must record the revision.
    """
    manifest_path = bundle / Path(model_path).with_suffix(".manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return ["model_catalog_unrecorded: the bundled model manifest cannot be read"]
    recorded = [_catalog_revision(getattr(model, "meta", None)),
                _catalog_revision(manifest.get("provenance"))]
    if any(value is None for value in recorded):
        # An unbound model must never activate.
        return ["model_catalog_unrecorded: the bundled model records no catalog revision"]
    if any(value != revision for value in recorded):
        return ["model_catalog_mismatch: the bundled model was trained for another catalog"]
    return []


def _catalog_revision(source) -> str | None:
    """Read the revision out of a model meta block or a manifest provenance block."""
    if isinstance(source, dict) and "provenance" in source:
        source = source["provenance"]
    config = source.get("config") if isinstance(source, dict) else None
    value = config.get("catalog_revision") if isinstance(config, dict) else None
    return value if isinstance(value, str) and value else None


def _preset_failure(preset_path: Path, bound_root: Path) -> str | None:
    """Run the service loader itself against the bundle's own catalog.

    Label order alone cannot see a layout, a feed rate, or a camera cadence that the model
    was not trained for. The loader resolves its profile from the catalog this child
    bound, so an unbound root or an already imported profiles module is a refusal.
    """
    bundle = preset_path.parent
    if "profiles" in sys.modules or Path(bound_root).resolve() != bundle / "catalog":
        return "preset_catalog_unbound: the bundle catalog is not bound in this process"
    try:
        from live import load_preset  # imported here: it pulls aiohttp into this child

        load_preset(preset_path)
    except Exception as error:
        # A child-process boundary: the loader raises whatever the model and the catalog
        # raise, and one sanitized JSON result must still reach the caller.
        return f"preset_incompatible: {_sanitized(error, bundle)}"
    return None


def _load_model(path: Path):
    from classifier import Model  # imported here so a bundle failure reports before the import

    return Model.load(path)


def _sanitized(error: BaseException, bundle: Path) -> str:
    """Keep the reason, drop any absolute path that an underlying library added."""
    text = str(error) or type(error).__name__
    return text.replace(str(bundle), "<bundle>").replace(str(bundle.parent), "<bundles>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    try:
        args = parser.parse_args()
    except SystemExit:
        return USAGE_EXIT
    # Before any object_catalog or profiles import: both read this root once.
    os.environ[CATALOG_ROOT_ENV] = str(args.bundle.resolve() / "catalog")
    result = validate(args.bundle)
    encoded = json.dumps(result, sort_keys=True)
    if args.json_out is not None:
        args.json_out.write_text(encoded + "\n")
    print(encoded, flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
