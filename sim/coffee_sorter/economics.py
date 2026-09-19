"""Rerunnable economics ledger for coffee-sorter physical metrics.

This is an assumption-led ledger, not evidence of a market price, buyer premium,
certification, or operating cost.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import shlex
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "configs" / "economics.json"
DEFAULT_OUTPUT = ROOT / "runs" / "economics"


def fail(message):
    raise ValueError(message)


def number(value, name, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail(f"{name} must be a finite number")
    if value < 0:
        fail(f"{name} must not be negative")
    if integer and int(value) != value:
        fail(f"{name} must be an integer")
    return int(value) if integer else float(value)


def required(mapping, key, source):
    if not isinstance(mapping, dict) or key not in mapping:
        fail(f"{source}: missing {key}")
    return mapping[key]


def class_is_defect(record, reject_severities, source):
    defect = required(record, "defect", source)
    severity = required(record, "severity", source)
    if not isinstance(defect, bool) or not isinstance(severity, str):
        fail(f"{source}: defect and severity must be bool and string")
    return defect and severity in reject_severities


def calculate(metrics, assumptions, source="metrics.json"):
    """Calculate one ledger row from one preserved physical-metrics document."""
    if not isinstance(metrics, dict) or not isinstance(assumptions, dict):
        fail(f"{source}: metrics and assumptions must be objects")
    rate = number(required(metrics, "throughput_beans_per_s", source),
                  f"{source}: throughput_beans_per_s")
    per_class = required(metrics, "per_class", source)
    denominators = required(metrics, "denominators", source)
    policy = required(required(metrics, "config", source), "policy", source)
    reject_severities = required(policy, "reject_severities", source)
    if not isinstance(per_class, dict) or not per_class:
        fail(f"{source}: per_class must be a non-empty object")
    if (not isinstance(reject_severities, list) or not reject_severities or
            not all(isinstance(value, str) for value in reject_severities)):
        fail(f"{source}: config.policy.reject_severities must be a non-empty string list")
    reject_severities = tuple(reject_severities)

    counts = {"input": 0, "accepted": 0, "rejected": 0, "spilled": 0, "unresolved": 0,
              "defect_input": 0, "defect_accepted": 0, "defect_rejected": 0,
              "defect_spilled": 0, "defect_unresolved": 0,
              "good_rejected": 0, "good_spilled": 0, "good_unresolved": 0}
    classes = {}
    for name, record in per_class.items():
        label = f"{source}: per_class.{name}"
        if not isinstance(name, str) or not isinstance(record, dict):
            fail(f"{label} must be an object")
        n = number(required(record, "n", label), f"{label}.n", integer=True)
        rejected = number(required(record, "rejected", label), f"{label}.rejected", integer=True)
        spilled = number(required(record, "spilled", label), f"{label}.spilled", integer=True)
        unresolved = number(required(record, "unresolved", label), f"{label}.unresolved", integer=True)
        accepted = n - rejected - spilled - unresolved
        if accepted < 0:
            fail(f"{label}: rejected + spilled + unresolved exceeds n")
        defect = class_is_defect(record, reject_severities, label)
        classes[name] = {"n": n, "accepted": accepted, "rejected": rejected, "spilled": spilled,
                         "unresolved": unresolved, "policy_defect": defect,
                         "severity": record["severity"]}
        counts["input"] += n
        counts["accepted"] += accepted
        counts["rejected"] += rejected
        counts["spilled"] += spilled
        counts["unresolved"] += unresolved
        if defect:
            counts["defect_input"] += n
            counts["defect_accepted"] += accepted
            counts["defect_rejected"] += rejected
            counts["defect_spilled"] += spilled
            counts["defect_unresolved"] += unresolved
        else:
            counts["good_rejected"] += rejected
            counts["good_spilled"] += spilled
            counts["good_unresolved"] += unresolved

    if counts["input"] == 0:
        fail(f"{source}: no eligible input; outcome fractions cannot be estimated")
    if counts["input"] != counts["accepted"] + counts["rejected"] + counts["spilled"] + counts["unresolved"]:
        fail(f"{source}: class outcomes do not conserve input")
    expected = {
        "eligible_beans": counts["input"], "resolved_beans": counts["input"] - counts["unresolved"],
        "defects_to_remove": counts["defect_input"], "keep_beans": counts["input"] - counts["defect_input"],
        "accepted_beans": counts["accepted"], "rejected_beans": counts["rejected"],
    }
    for key, value in expected.items():
        observed = number(required(denominators, key, source), f"{source}: denominators.{key}", integer=True)
        if observed != value:
            fail(f"{source}: denominators.{key}={observed} does not match per_class total {value}")

    mass_g = number(required(assumptions, "object_mass_g", "assumptions"), "assumptions.object_mass_g")
    duty = number(required(assumptions, "duty_cycle", "assumptions"), "assumptions.duty_cycle")
    base = number(required(assumptions, "base_feed_price_eur_per_kg", "assumptions"),
                  "assumptions.base_feed_price_eur_per_kg")
    premium = number(required(assumptions, "accepted_stream_premium_eur_per_kg", "assumptions"),
                     "assumptions.accepted_stream_premium_eur_per_kg")
    for key in ("rejected_salvage_eur_per_kg", "spilled_or_unresolved_sale_eur_per_kg",
                "operating_cost_eur_per_h", "capital_cost_eur_per_h", "labor_cost_eur_per_h"):
        if number(required(assumptions, key, "assumptions"), f"assumptions.{key}") != 0:
            fail(f"assumptions.{key} must be zero for this frozen ledger")
    if duty > 1:
        fail("assumptions.duty_cycle must not exceed 1")
    input_kg_h = rate * 3600 * mass_g / 1000 * duty
    if not math.isfinite(input_kg_h):
        fail("derived input mass must be finite")
    kg_per_object_h = input_kg_h / counts["input"]
    masses = {key: value * kg_per_object_h for key, value in counts.items()}
    baseline_revenue = input_kg_h * base
    sorted_revenue = masses["accepted"] * (base + premium)
    zero_premium_value = masses["accepted"] * base - baseline_revenue
    uplift = sorted_revenue - baseline_revenue
    break_even = (base * (input_kg_h - masses["accepted"]) / masses["accepted"]
                  if masses["accepted"] else None)
    if not all(math.isfinite(value) for value in (
            *masses.values(), baseline_revenue, sorted_revenue, zero_premium_value,
            uplift, break_even if break_even is not None else 0.0)):
        fail("derived mass and monetary values must be finite")
    count_percent = lambda part, total: 100 * part / total if total else None
    root_policy_name = metrics.get("policy")
    config_policy_name = policy.get("name")
    if root_policy_name is not None and config_policy_name is not None and root_policy_name != config_policy_name:
        fail(f"{source}: policy does not match config.policy.name")
    return {
        "source": source,
        "effective_rate_beans_per_s": rate,
        "requested_rate_beans_per_s": metrics.get("rate", metrics["config"].get("requested_rate_beans_per_s")),
        "policy": {"name": config_policy_name or root_policy_name, "reject_severities": list(reject_severities)},
        "source_denominators": {key: denominators[key] for key in denominators},
        "counts": counts,
        "class_counts": classes,
        "mass_kg_h": masses,
        "defect_count_percent": {
            "incoming": count_percent(counts["defect_input"], counts["input"]),
            "residual_accepted": count_percent(counts["defect_accepted"], counts["accepted"]),
        },
        "revenue_eur_h": {"baseline_unsorted": baseline_revenue, "sorted": sorted_revenue,
                            "uplift": uplift, "at_zero_premium": zero_premium_value},
        "cost_eur_h": {
            "all_rejected_base_value": masses["rejected"] * base,
            "good_false_ejections": masses["good_rejected"] * base,
            "good_spills": masses["good_spilled"] * base,
            "good_unresolved": masses["good_unresolved"] * base,
            "spills": masses["spilled"] * base,
            "unresolved": masses["unresolved"] * base,
        },
        "break_even_accepted_stream_premium_eur_per_kg": break_even,
        "formula": "Q = measured throughput_beans_per_s * 3600 * object_mass_g / 1000 * duty_cycle",
    }


def load_json(path):
    try:
        with path.open() as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: cannot read JSON ({exc})")


def source_record(path, assumptions):
    metrics = load_json(path)
    resolved = path.resolve()
    source = str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(path)
    row = calculate(metrics, assumptions, source)
    row["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    row["controller_config"] = required(metrics, "config", str(path))
    return row


def scenario_label(row):
    requested = row["requested_rate_beans_per_s"]
    return Path(row["source"]).parent.name + ": " + (f"requested {requested:g}; effective {row['effective_rate_beans_per_s']:g} beans/s"
            if isinstance(requested, (int, float)) else f"effective {row['effective_rate_beans_per_s']:g} beans/s")


def write_csv(rows, output):
    fields = ["label", "source", "effective_rate_beans_per_s", "input_kg_h", "accepted_kg_h",
              "defect_rejected_kg_h", "good_rejected_kg_h", "good_spilled_kg_h", "good_unresolved_kg_h",
              "defect_spilled_kg_h", "defect_unresolved_kg_h", "uplift_eur_h", "zero_premium_eur_h",
              "break_even_accepted_stream_premium_eur_per_kg", "incoming_defect_count_percent",
              "residual_accepted_defect_count_percent"]
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "label": scenario_label(row), "source": row["source"],
                "effective_rate_beans_per_s": row["effective_rate_beans_per_s"],
                "input_kg_h": row["mass_kg_h"]["input"], "accepted_kg_h": row["mass_kg_h"]["accepted"],
                "defect_rejected_kg_h": row["mass_kg_h"]["defect_rejected"],
                "good_rejected_kg_h": row["mass_kg_h"]["good_rejected"],
                **{key + "_kg_h": row["mass_kg_h"][key] for key in (
                    "good_spilled", "good_unresolved", "defect_spilled", "defect_unresolved")},
                "uplift_eur_h": row["revenue_eur_h"]["uplift"],
                "zero_premium_eur_h": row["revenue_eur_h"]["at_zero_premium"],
                "break_even_accepted_stream_premium_eur_per_kg": row["break_even_accepted_stream_premium_eur_per_kg"],
                "incoming_defect_count_percent": row["defect_count_percent"]["incoming"],
                "residual_accepted_defect_count_percent": row["defect_count_percent"]["residual_accepted"],
            })


def plot(rows, assumptions, output):
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is required to create summary.png") from exc
    rows = sorted(rows, key=lambda row: row["effective_rate_beans_per_s"])
    rates = [row["effective_rate_beans_per_s"] for row in rows]
    categorical = len(set(round(rate) for rate in rates)) < len(rows)
    x = list(range(len(rows))) if categorical else rates
    fig, axes = plt.subplots(2, 1, figsize=(9, 8), sharex=True, constrained_layout=True)
    axes[0].plot(x, [row["mass_kg_h"]["input"] for row in rows], marker="o", label="Input")
    axes[0].plot(x, [row["mass_kg_h"]["accepted"] for row in rows], marker="o", label="Accepted")
    axes[0].plot(x, [row["mass_kg_h"]["defect_rejected"] for row in rows], marker="o", label="Defects rejected")
    axes[0].plot(x, [row["mass_kg_h"]["good_rejected"] for row in rows], marker="o", label="Good falsely ejected")
    for key, label in (("good_spilled", "Good spilled"), ("good_unresolved", "Good unresolved"),
                       ("defect_spilled", "Defects spilled"), ("defect_unresolved", "Defects unresolved")):
        axes[0].plot(x, [row["mass_kg_h"][key] for row in rows], marker=".", label=label)
    axes[0].set_ylabel("kg/h")
    axes[0].legend()
    axes[0].grid(alpha=.25)
    axes[1].plot(x, [row["revenue_eur_h"]["uplift"] for row in rows], marker="o", color="#a33", label="Uplift")
    axes[1].axhline(0, color="black", linewidth=.8)
    axes[1].set_xlabel("Observed effective throughput (beans/s)")
    if categorical:
        axes[1].set_xticks(x, [f"{Path(row['source']).parent.name}\n{rate:.0f}/s"
                              for row, rate in zip(rows, rates)], rotation=50, ha="right", fontsize=7)
        axes[1].set_xlabel("Scenario and effective feed (beans/s)")
    axes[1].set_ylabel("EUR/h")
    axes[1].grid(alpha=.25)
    axes[1].legend()
    last_rate = max(x)
    for position, row in zip(x, rows):
        if categorical:
            continue
        requested = row["requested_rate_beans_per_s"]
        label = f"requested {requested:g}" if isinstance(requested, (int, float)) else "requested n/a"
        right = row["effective_rate_beans_per_s"] == last_rate
        axes[1].annotate(label, (position, row["revenue_eur_h"]["uplift"]),
                         xytext=(-5 if right else 0, -15), textcoords="offset points",
                         ha="right" if right else "center", fontsize=8)
    fig.suptitle(f"Hypothetical value: {assumptions['object_mass_g']:g} g/object, "
                 f"{100 * assumptions['duty_cycle']:g}% duty\n"
                 f"EUR {assumptions['base_feed_price_eur_per_kg']:g}/kg input + "
                 f"EUR {assumptions['accepted_stream_premium_eur_per_kg']:g}/kg accepted premium; "
                 "zero salvage, before costs", fontsize=10)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def write_report(rows, assumptions, command, output):
    headline = next((row for row in rows if Path(row["source"]).parent.name in {"rate-1000", "base-1000"}), rows[0])
    r = headline["revenue_eur_h"]
    c = headline["cost_eur_h"]
    mass = headline["mass_kg_h"]
    break_even = headline["break_even_accepted_stream_premium_eur_per_kg"]
    break_even_text = f"EUR {break_even:.3f}/kg" if break_even is not None else "undefined (no accepted mass)"
    residual = headline["defect_count_percent"]["residual_accepted"]
    residual_text = f"{residual:.3f}%" if residual is not None else "undefined (no accepted beans)"
    lines = [
        "# Economics ledger",
        "",
        "This is a rerunnable, hypothetical ledger from preserved physical metrics; it is not a market, premium, certification, or cost claim.",
        "",
        f"Headline (1,000/s reference when present): {scenario_label(headline)}.",
        f"Input {mass['input']:.3f} kg/h; accepted {mass['accepted']:.3f} kg/h; defects rejected {mass['defect_rejected']:.3f} kg/h.",
        f"Good falsely ejected {mass['good_rejected']:.3f} kg/h, spilled {mass['good_spilled']:.3f} kg/h, unresolved {mass['good_unresolved']:.3f} kg/h. Defects spilled {mass['defect_spilled']:.3f} kg/h, unresolved {mass['defect_unresolved']:.3f} kg/h; neither earns a rejection credit.",
        f"Baseline EUR {r['baseline_unsorted']:.2f}/h; sorted EUR {r['sorted']:.2f}/h; uplift EUR {r['uplift']:.2f}/h; zero-premium value EUR {r['at_zero_premium']:.2f}/h.",
        f"Good false-ejection cost EUR {c['good_false_ejections']:.2f}/h; spill cost EUR {c['spills']:.2f}/h; unresolved cost EUR {c['unresolved']:.2f}/h.",
        f"Incoming policy-defect count {headline['defect_count_percent']['incoming']:.3f}%; residual accepted policy-defect count {residual_text}.",
        f"Break-even accepted-stream premium: {break_even_text}, before costs.",
        "All discovered scenarios, including negative uplift results, are retained in summary.json and summary.csv.",
        "",
        "## Assumptions",
        "",
        f"- Equal mass: {assumptions['object_mass_g']} g for every object, including fragments and foreign matter; simulator feed_kg_per_h is deliberately unused.",
        f"- Duty cycle: {assumptions['duty_cycle']}; Q uses measured throughput, never requested rate.",
        f"- Base unsorted feed opportunity price: EUR {assumptions['base_feed_price_eur_per_kg']}/kg. Accepted-stream premium: EUR {assumptions['accepted_stream_premium_eur_per_kg']}/kg only if a buyer pays it; it is unverified.",
        "- Rejected salvage, spill/unresolved sale value, operating, capital, and labor costs are EUR 0 by assumption. No spilled defect receives a benefit credit.",
        "",
        "## Reproduce",
        "",
        f"`{command}`",
        "",
        "Source SHA-256 values, controller configurations, class counts, denominators, and formulas are in summary.json.",
    ]
    output.write_text("\n".join(lines) + "\n")


def metrics_paths(values):
    expanded = []
    for value in values:
        matches = sorted(glob.glob(value))
        expanded.extend(Path(match) for match in matches or [value])
    if not expanded:
        fail("no metrics files found")
    return expanded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", nargs="+", default=[str(ROOT / "runs" / "rate-sweep" / "rate-*" / "metrics.json")])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output directory")
    args = parser.parse_args(argv)
    config = load_json(args.config)
    assumptions = required(config, "assumptions", str(args.config))
    rows = [source_record(path, assumptions) for path in metrics_paths(args.metrics)]
    rows.sort(key=lambda row: row["effective_rate_beans_per_s"])
    args.output.mkdir(parents=True, exist_ok=True)
    invocation = sys.argv[1:] if argv is None else list(argv)
    command = shlex.join([Path(sys.executable).as_posix(), Path(__file__).as_posix(), *invocation])
    portable_command = shlex.join([".venv/bin/python", "economics.py", *[
        value.replace(str(ROOT) + "/", "") for value in invocation]])
    summary = {
        "purpose": "hypothetical economics ledger from preserved physical metrics, not market evidence",
        "selection": "all discovered metrics paths; negative results retained without cherry-picking",
        "assumptions": assumptions,
        "formula": "Q = measured throughput_beans_per_s * 3600 * object_mass_g / 1000 * duty_cycle",
        "commands": {"exact": command, "portable_from_sim_coffee_sorter": portable_command},
        "scenarios": rows,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    write_csv(rows, args.output / "summary.csv")
    plot(rows, assumptions, args.output / "summary.png")
    write_report(rows, assumptions, portable_command, args.output / "report.md")
    print(f"wrote {args.output / 'summary.json'}")


if __name__ == "__main__":
    main()
