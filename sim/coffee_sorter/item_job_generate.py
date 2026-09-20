"""Run the recipe generator for one item job and report one typed stage outcome.

This is the only file that knows the generator. It imports probe as a module and
maps probe's typed outcome classes to stage exit codes. It never maps an exit
code from free text, because provider and user content can appear in that text.

Cache replay is verified before reuse: probe checks the endpoint, the canonical
request digest, and the response metadata, and this wrapper checks that every
later replay for one job reproduces the same immutable artifact hashes.

`--live` is never automatic. The queue passes it only in paid mode and only when
the job record holds an unconsumed operator grant.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from item_jobs import (EXIT_CACHE_ENTRY_INVALID, EXIT_CREDENTIALS, EXIT_FAILED,
                       EXIT_NOT_SUBMITTED, EXIT_OK, EXIT_UNCERTAIN)

CASE = "custom"
# The typed interface this wrapper requires. Free-text matching is never a fallback.
REQUIRED_OUTCOMES = ("CacheMiss", "CredentialMissing", "SubmissionUncertain",
                     "CachedProviderFailure", "CacheEntryInvalid")
EVIDENCE_FIELDS = ("cache_entry_sha256", "request_sha256", "endpoint", "provider_model",
                   "recipe_sha256")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["cached", "paid"], required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--provider-cache", type=Path, required=True)
    parser.add_argument("--generator-root", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--live", action="store_true",
                        help="Permit one authorized billable request. Paid mode only.")
    args = parser.parse_args()
    if args.live and (args.mode != "paid" or args.env_file is None):
        return _status(args, "not_submitted", "a live request needs paid mode and a credential file",
                       EXIT_FAILED)

    try:
        _link_cache(args.out, args.provider_cache)
        probe = _load_probe(args.generator_root)
    except RuntimeError as error:
        return _status(args, "not_submitted", str(error), EXIT_FAILED)

    argv = ["probe.py", "--description", args.description, "--out", str(args.out)]
    if args.mode == "paid" and args.env_file is not None:
        argv += ["--env-file", str(args.env_file)]
    if args.source_revision:
        argv += ["--source-revision", args.source_revision]
    if args.live:
        argv.append("--live")

    saved, sys.argv = sys.argv, argv
    try:
        probe.main()
    except probe.CacheMiss as error:
        return _status(args, "not_submitted", str(error), EXIT_NOT_SUBMITTED)
    except probe.CredentialMissing as error:
        return _status(args, "not_submitted", str(error), EXIT_CREDENTIALS)
    except probe.SubmissionUncertain as error:
        return _status(args, "uncertain", str(error), EXIT_UNCERTAIN)
    except probe.CacheEntryInvalid as error:
        return _status(args, "not_submitted", str(error), EXIT_CACHE_ENTRY_INVALID)
    except probe.CachedProviderFailure as error:
        # A recorded provider failure is a real failure, never a cache miss.
        return _status(args, "not_submitted", str(error), EXIT_FAILED)
    except SystemExit as error:
        return _status(args, "not_submitted", f"generator refused to run: {error}", EXIT_FAILED)
    except Exception as error:
        # An unrecognized fault could still have reached the provider.
        return _status(args, "uncertain", f"unrecognized generator failure: {error}",
                       EXIT_UNCERTAIN)
    finally:
        sys.argv = saved

    return _publish(args)


def _publish(args) -> int:
    """Record immutable artifact hashes and refuse a replay that changes them."""
    source = args.out / CASE / "recipe.json"
    response = _read_json(args.out / CASE / "openrouter_response.json")
    if not source.is_file() or response is None:
        return _status(args, "completed", "the generator produced no recipe", EXIT_FAILED)
    digest = response.get("request_sha256")
    entry = args.out.parent / "cache" / f"{digest}.json"
    evidence = {
        "cache_entry_sha256": _file_sha256(entry),
        "request_sha256": digest,
        "endpoint": response.get("endpoint"),
        "provider_model": (response.get("response") or {}).get("model"),
        "recipe_sha256": _file_sha256(source),
    }
    previous = _read_json(args.evidence)
    if previous is not None and any(previous.get(key) != evidence[key] for key in EVIDENCE_FIELDS):
        return _status(args, "not_submitted", "cache_entry_invalid: artifact hashes changed",
                       EXIT_CACHE_ENTRY_INVALID)
    cached = response.get("cached") is True
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    args.recipe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, args.recipe)
    return _status(args, "not_submitted" if cached else "completed", None, EXIT_OK,
                   cache_hit=cached, evidence=evidence)


def _link_cache(out: Path, provider_cache: Path) -> None:
    """probe.call() reads <out>/../cache. Point it at the stable provider cache root."""
    out.mkdir(parents=True, exist_ok=True)
    link = out.parent / "cache"
    target = Path(provider_cache) / "cache"
    if link.is_symlink():
        if link.readlink() != target:
            link.unlink()
            link.symlink_to(target)
        return
    if link.exists():
        raise RuntimeError(f"a real directory already occupies the cache path: {link}")
    link.symlink_to(target)


def _load_probe(generator_root: Path):
    path = Path(generator_root) / "probe.py"
    if not path.is_file():
        raise RuntimeError(f"the generator root has no probe.py: {generator_root}")
    spec = importlib.util.spec_from_file_location("cinta_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cinta_probe"] = module
    spec.loader.exec_module(module)
    missing = [name for name in REQUIRED_OUTCOMES if not hasattr(module, name)]
    if missing:
        raise RuntimeError("probe.py lacks the typed outcome classes "
                           f"{', '.join(missing)}; apply the Phase 2 generator patch")
    return module


def _status(args, submission: str, reason: str | None, code: int, cache_hit: bool = False,
            evidence: dict | None = None) -> int:
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps({
        "provider_submission": submission, "reason": reason, "mode": args.mode,
        "live_requested": bool(args.live), "cache_hit": cache_hit, "evidence": evidence,
    }, indent=2, sort_keys=True) + "\n")
    if reason:
        sys.stderr.write(reason + "\n")
    return code


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
