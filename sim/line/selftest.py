"""Run every module's self-test and print a table. Owner: integrator.

    cd ~/robotics && .venv/bin/python -m line.selftest [--all-fake]
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from line.contracts import Check, now


def run(modules: dict[str, object], only: str | None = None) -> list[dict]:
    out: list[dict] = []
    for key, m in modules.items():
        if only and key != only:
            continue
        t0 = now()
        try:
            checks = m.selftest()
        except Exception as exc:  # noqa: BLE001
            checks = [Check(f"{key}.selftest", False, f"raised {type(exc).__name__}: {exc}")]
        for c in checks:
            d = asdict(c)
            d["module"] = key
            d["impl"] = getattr(m, "name", type(m).__name__)
            out.append(d)
        out.append({"module": key, "impl": getattr(m, "name", type(m).__name__), "name": f"{key}.status", "ok": True, "detail": "status() answered", "ms": 0.0}) if _status_ok(m) else out.append({"module": key, "impl": getattr(m, "name", "?"), "name": f"{key}.status", "ok": False, "detail": "status() raised or returned non-dict", "ms": 0.0})
        out[-1]["ms"] = round((now() - t0) * 1000, 1)
    return out


def _status_ok(m) -> bool:
    try:
        return isinstance(m.status(), dict)
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    from line.panel import build_modules
    from line.config import load

    all_fake = "--all-fake" in sys.argv
    mods, info = build_modules(load(), all_fake=all_fake)
    res = run(mods)
    bad = 0
    for r in res:
        bad += not r["ok"]
        print(f"{'OK ' if r['ok'] else 'BAD'}  {r['name']:<32} {r['impl']:<22} {r['ms']:>7.1f} ms  {r['detail']}")
    print(f"\n{len(res) - bad}/{len(res)} checks passed; implementations: " + ", ".join(f"{k}={v}" for k, v in info.items()))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
