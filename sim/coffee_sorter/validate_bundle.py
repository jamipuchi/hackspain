"""Verify one immutable bundle in a separate child process.

It reuses object_catalog.verify_bundle: there is one verifier, and this file is only the
child-process entry point. It does NOT call live.load_preset, because the bundle-relative
model path has not landed in live.py yet and the integration owner adds that call later.

The result is one JSON object on stdout. No output carries a host path: a bundle is named
by its sha and by the relative paths it lists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import object_catalog
from object_catalog import BUNDLE_PRESET, CatalogError

USAGE_EXIT = 2


def validate(bundle_dir: Path) -> dict:
    """Check the bytes, then the catalog, the model, and the policy that they describe."""
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
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["failures"].append(f"model: {_sanitized(error, bundle)}")

    try:
        policy = json.loads((bundle / "policy.json").read_text())
        reject = list(policy.get("reject_classes", []))
        unknown = sorted(set(reject) - set(result["labels"]))
        if unknown:
            result["failures"].append(f"policy: unknown reject classes: {', '.join(unknown)}")
    except (OSError, ValueError, TypeError) as error:
        result["failures"].append(f"policy: {_sanitized(error, bundle)}")

    result["ok"] = not result["failures"]
    return result


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
    result = validate(args.bundle)
    encoded = json.dumps(result, sort_keys=True)
    if args.json_out is not None:
        args.json_out.write_text(encoded + "\n")
    print(encoded, flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
