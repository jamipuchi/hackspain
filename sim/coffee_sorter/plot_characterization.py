#!/usr/bin/env python3
"""Plot closed-loop coffee-sorter characterization from saved run metrics."""

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


COLORS = ("#176B87", "#E07A5F", "#3D9970", "#7B5EA7", "#C99A00", "#556270")


def fail(message):
    raise SystemExit(f"plot_characterization.py: {message}")


def need(mapping, key, source):
    if key not in mapping or mapping[key] is None:
        fail(f"{source}: required metric '{key}' is absent")
    return mapping[key]


def metric_path(path):
    path = Path(path)
    return path if path.name == "metrics.json" else path / "metrics.json"


def load_runs(paths, group_dir, sort_by_rate=True):
    if group_dir:
        group = Path(group_dir)
        if not group.is_dir():
            fail(f"group directory does not exist: {group}")
        paths = [str(p) for p in sorted(group.glob("*/metrics.json"))]
    if not paths:
        fail("provide run directories/metrics.json paths or --group-dir")
    rows = []
    seen = set()
    for value in paths:
        source = metric_path(value)
        if source in seen:
            continue
        seen.add(source)
        if not source.is_file():
            fail(f"metrics file does not exist: {source}")
        try:
            metrics = json.loads(source.read_text())
        except json.JSONDecodeError as exc:
            fail(f"cannot read JSON in {source}: {exc}")
        rows.append({"path": source.parent, "metrics": metrics})
    if sort_by_rate:
        rows.sort(key=lambda row: need(row["metrics"], "rate", row["path"]))
    return rows


def write_summary(output, plot, rows, extra=None):
    summary = {
        "plot": plot,
        "inputs": [str(row["path"]) for row in rows],
        "metrics": [row["metrics"] for row in rows],
    }
    if extra:
        summary.update(extra)
    output.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n")


def finish(fig, output, plot, rows, extra=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    write_summary(output, plot, rows, extra)
    print(f"wrote {output}")
    print(f"wrote {output.with_suffix('.json')}")


def style():
    plt.rcParams.update({
        "font.size": 12,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "figure.titleweight": "bold",
    })


def rates(rows):
    return np.array([need(row["metrics"], "rate", row["path"]) for row in rows], dtype=float)


def values(rows, key):
    return np.array([need(row["metrics"], key, row["path"]) for row in rows], dtype=float)


def percent_axis(ax):
    ax.set_ylabel("Percent (%)")
    ax.set_ylim(bottom=0)


def line(ax, x, y, title, ylabel=None, color=COLORS[0], label=None):
    ax.plot(x, y, "o-", color=color, linewidth=2.4, markersize=6, label=label)
    ax.set_title(title, loc="left")
    ax.set_xlabel("Requested feed rate (beans/s)")
    ax.grid(axis="y", alpha=0.25)
    if ylabel:
        ax.set_ylabel(ylabel)
    if label:
        ax.legend(frameon=False)


def rate_plot(rows, output):
    style()
    x = rates(rows)
    fig, axes = plt.subplots(3, 3, figsize=(14, 15), constrained_layout=True)
    fig.suptitle("Closed-loop rate characterization", fontsize=20, x=0.04, ha="left")
    percent_specs = (
        ("physical_reject_accuracy", "Physical accuracy", COLORS[0]),
        ("physical_reject_precision", "Reject precision", COLORS[1]),
        ("physical_reject_recall", "Physical reject recall", COLORS[2]),
        ("good_false_eject_rate", "Good false eject rate", COLORS[3]),
        ("spilled_rate", "Spill rate", COLORS[4]),
    )
    for ax, (key, title, color) in zip(axes.flat, percent_specs):
        line(ax, x, 100 * values(rows, key), title, color=color)
        percent_axis(ax)

    late_fraction = []
    late_labels = []
    for row in rows:
        metrics, source = row["metrics"], row["path"]
        late = need(metrics, "late_decisions", source)
        decisions = need(metrics, "reject_decisions", source)
        if decisions == 0:
            fail(f"{source}: reject_decisions is zero; late-decision fraction is undefined")
        late_fraction.append(100 * late / decisions)
        late_labels.append(f"{late}/{decisions}")
    ax = axes.flat[5]
    line(ax, x, late_fraction, "Late reject decisions / reject decisions", color="#B24C63")
    percent_axis(ax)
    for xi, yi, label in zip(x, late_fraction, late_labels):
        ax.annotate(label, (xi, yi), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=9)

    line(axes.flat[6], x, values(rows, "pool_starved"), "Pool-starved admissions", "Attempts", COLORS[5])
    admitted = values(rows, "throughput_beans_per_s")
    line(axes.flat[7], x, admitted, "Admitted throughput vs requested", "Beans/s", COLORS[0], "admitted")
    axes.flat[7].plot(x, x, "--", color="#555555", linewidth=1.5, label="requested")
    axes.flat[7].legend(frameon=False)
    line(axes.flat[8], x, values(rows, "wall_per_sim_second"), "Wall time per simulated second", "Wall s / sim s", COLORS[1])
    finish(fig, output, "rate", rows)


def decision_rows(row):
    source = row["path"] / "decisions.csv"
    if not source.is_file():
        fail(f"{row['path']}: required decisions.csv is absent for latency plot")
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"reject", "late", "headroom_ms"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            fail(f"{source}: required columns are absent: {', '.join(sorted(missing))}")
        data = []
        for index, item in enumerate(reader, start=2):
            try:
                reject = int(item["reject"])
                late = int(item["late"])
                headroom = float(item["headroom_ms"])
            except (TypeError, ValueError) as exc:
                fail(f"{source}:{index}: invalid reject/late/headroom_ms value ({exc})")
            if reject:
                data.append((headroom, late))
    if not data:
        fail(f"{source}: no reject decisions; decision-miss rate is undefined")
    return np.asarray(data, dtype=float)


def induced_delay_ms(row):
    source = row["path"]
    config = need(row["metrics"], "config", source)
    policy = need(config, "policy", source)
    return 1000 * need(policy, "induced_delay", source)


def latency_plot(rows, output):
    style()
    rows = sorted(rows, key=induced_delay_ms)
    x = np.array([induced_delay_ms(row) for row in rows], dtype=float)
    actual = [decision_rows(row) for row in rows]
    fig, axes = plt.subplots(2, 1, figsize=(10, 13), constrained_layout=True)
    fig.suptitle("Decision latency and deadline headroom", fontsize=20, x=0.08, ha="left")
    ax = axes[0]
    widths = min(np.diff(x)) * 0.55 if len(x) > 1 else 6
    violin = ax.violinplot([data[:, 0] for data in actual], positions=x, widths=widths,
                           showmeans=False, showmedians=False, showextrema=False)
    for body in violin["bodies"]:
        body.set_facecolor(COLORS[1])
        body.set_edgecolor(COLORS[1])
        body.set_alpha(0.28)
    ax.scatter(x, [np.median(data[:, 0]) for data in actual], marker="D", s=42, color=COLORS[1],
               label="per-track actual headroom (distribution; diamond = median)", zorder=3)
    nominal_labels = (("p50", "p50", "-"), ("p99", "p99", "--"), ("max", "max", ":"))
    for key, label, linestyle in nominal_labels:
        headroom = []
        for row in rows:
            metrics, source = row["metrics"], row["path"]
            budget = need(metrics, "latency_budget_ms", source)
            latency = need(need(metrics, "latency_ms", source), key, source)
            headroom.append(budget - latency)
        ax.plot(x, headroom, marker="o", linewidth=2.2, linestyle=linestyle, color=COLORS[0],
                label=f"nominal 73.33 ms budget − total latency ({label})")
    ax.axhline(0, color="#555555", linewidth=1, label="nominal zero headroom")
    ax.set_title("Nominal estimate versus actual decision-record distribution", loc="left")
    ax.set_xlabel("Injected availability delay (ms)")
    ax.set_ylabel("Headroom (ms)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncol=2, fontsize=9)

    ax = axes[1]
    all_heads = np.concatenate([data[:, 0] for data in actual])
    lower = math.floor(all_heads.min() / 5) * 5
    upper = math.ceil(all_heads.max() / 5) * 5
    if lower == upper:
        lower -= 5
        upper += 5
    bins = np.arange(lower, upper + 5, 5)
    checks = []
    for index, (row, data, delay) in enumerate(zip(rows, actual, x)):
        centers, misses = [], []
        for lo, hi in zip(bins[:-1], bins[1:]):
            in_bin = data[(data[:, 0] >= lo) & ((data[:, 0] < hi) if hi < bins[-1] else (data[:, 0] <= hi))]
            if len(in_bin):
                centers.append((lo + hi) / 2)
                misses.append(100 * in_bin[:, 1].mean())
        ax.plot(centers, misses, "o-", linewidth=2, markersize=5, color=COLORS[index % len(COLORS)],
                label=f"{delay:g} ms delay: {int(data[:, 1].sum())}/{len(data)} late")
        inferred = data[:, 0] < -2.0
        checks.append({
            "injected_availability_delay_ms": delay,
            "reject_decisions": len(data),
            "recorded_late_decisions": int(data[:, 1].sum()),
            "late_flag_matches_headroom_lt_minus_2ms": int((inferred == data[:, 1].astype(bool)).sum()),
        })
    ax.axvline(0, color="#555555", linewidth=1, label="nominal zero headroom")
    ax.axvline(-2, color="#555555", linewidth=1.2, linestyle="--", label="late threshold (−2 ms)")
    ax.set_title("Observed late-flag rate by actual per-track headroom\n"
                 "Frame backlog is not modeled; headroom is from decisions.csv and late is the recorded controller flag.",
                 loc="left")
    ax.set_xlabel("Actual headroom to jet (ms), 5 ms bins")
    ax.set_ylabel("Late reject decisions (%)")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncol=2, fontsize=9)
    finish(fig, output, "latency", rows, {"late_flag_headroom_check": checks,
                                           "late_threshold_headroom_ms": -2.0,
                                           "frame_backlog_modeled": False})


def cohort_plot(rows, output):
    style()
    x = rates(rows)
    fig, axes = plt.subplots(2, 1, figsize=(10, 11), constrained_layout=True)
    fig.suptitle("Physical outcomes by camera-merge cohort", fontsize=20, x=0.08, ha="left")
    cohorts = (("single_only", "Single only", COLORS[0]), ("ever_merged", "Ever merged", COLORS[1]))
    for ax, (metric, title) in zip(axes, (("defect_removal", "Physical reject recall"),
                                           ("good_yield_loss", "Good false eject rate"))):
        for key, label, color in cohorts:
            y = []
            for row in rows:
                source = row["path"]
                cohort_data = need(need(row["metrics"], "camera_bean_cohorts", source), key, source)
                y.append(100 * need(cohort_data, metric, source))
            ax.plot(x, y, "o-", linewidth=2.4, color=color, label=label)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Requested feed rate (beans/s)")
        percent_axis(ax)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(frameon=False)
    finish(fig, output, "cohorts", rows)


def flatten(value, prefix=""):
    if isinstance(value, dict):
        result = {}
        for key, nested in value.items():
            result.update(flatten(nested, f"{prefix}.{key}" if prefix else key))
        return result
    return {prefix: value}


def config_differences(before, after):
    before_config = flatten(before["metrics"]["config"])
    after_config = flatten(after["metrics"]["config"])
    return [f"{key}: {before_config.get(key)!r} → {after_config.get(key)!r}"
            for key in sorted(set(before_config) | set(after_config))
            if before_config.get(key) != after_config.get(key)]


def tuning_plot(before, candidates, output):
    style()
    for row in [before, *candidates]:
        need(row["metrics"], "config", row["path"])
    differences = {row["path"].name: config_differences(before, row) for row in candidates}
    empty = [name for name, changed in differences.items() if not changed]
    if empty:
        fail(f"tuning inputs have no config difference from baseline: {', '.join(empty)}")
    fig, axes = plt.subplots(2, 1, figsize=(11, 12), constrained_layout=True)
    fig.suptitle("Physical recall versus losses", fontsize=20)
    baseline_recall = 100 * need(before["metrics"], "physical_reject_recall", before["path"])
    candidate_recall = [100 * need(row["metrics"], "physical_reject_recall", row["path"]) for row in candidates]
    candidate_values = []
    for ax, key, title, color in ((axes[0], "good_false_eject_rate", "Good false eject rate", COLORS[3]),
                                  (axes[1], "spilled_rate", "Spill rate", COLORS[4])):
        baseline_loss = 100 * need(before["metrics"], key, before["path"])
        loss = [100 * need(row["metrics"], key, row["path"]) for row in candidates]
        candidate_values.append(loss)
        ax.axvline(baseline_recall, color="#555555", linewidth=1, linestyle="--")
        ax.axhline(baseline_loss, color="#555555", linewidth=1, linestyle="--")
        ax.scatter([baseline_recall], [baseline_loss], marker="s", s=85, color="#222222", label="Baseline", zorder=4)
        for index, (row, xv, yv) in enumerate(zip(candidates, candidate_recall, loss)):
            label = f"{index + 1}. {row['path'].name}"
            ax.scatter([xv], [yv], s=160, color=COLORS[index % len(COLORS)], label=label, zorder=3)
            ax.text(xv, yv, str(index + 1), color="white", fontsize=8,
                    ha="center", va="center", zorder=4)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Physical reject recall (%)")
        ax.set_ylabel("Percent (%)")
        recall_values = [baseline_recall, *candidate_recall]
        recall_pad = max(1.0, (max(recall_values) - min(recall_values)) * 0.1)
        loss_values = [baseline_loss, *loss]
        loss_pad = max(0.2, (max(loss_values) - min(loss_values)) * 0.1)
        ax.set_xlim(max(0, min(recall_values) - recall_pad), max(recall_values) + recall_pad)
        ax.set_ylim(max(0, min(loss_values) - loss_pad), max(loss_values) + loss_pad)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9)
    good = []
    baseline_false, baseline_spill = (100 * need(before["metrics"], key, before["path"])
                                      for key in ("good_false_eject_rate", "spilled_rate"))
    for row, recall, false_eject, spill in zip(candidates, candidate_recall, candidate_values[0], candidate_values[1]):
        if recall > baseline_recall and false_eject <= baseline_false and spill <= baseline_spill:
            good.append((recall - baseline_recall, row, false_eject, spill))
    recommendation = None
    if good:
        _, row, false_eject, spill = max(good, key=lambda item: item[0])
        recommendation = {
            "run": row["path"].name,
            "criterion": "largest recall increase with neither false-eject rate nor spill rate above baseline",
            "physical_reject_recall": need(row["metrics"], "physical_reject_recall", row["path"]),
            "good_false_eject_rate": false_eject / 100,
            "spilled_rate": spill / 100,
        }
        print(f"data-only recommendation: {recommendation['run']}")
    else:
        print("data-only recommendation: none (no candidate improves recall without worsening either loss)")
    finish(fig, output, "tuning", [before, *candidates], {"baseline": str(before["path"]),
                                                            "configuration_differences": differences,
                                                            "recommendation": recommendation})


def add_comparison_inputs(parser):
    parser.add_argument("paths", nargs="*", help="run directories or metrics.json files")
    parser.add_argument("--group-dir", help="directory whose immediate children are run directories")
    parser.add_argument("--output", required=True, type=Path, help="PNG output path; matching .json summary is also written")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("rate", "latency", "cohorts"):
        add_comparison_inputs(subparsers.add_parser(command))
    tuning = subparsers.add_parser("tuning")
    tuning.add_argument("paths", nargs="*", help="baseline run first, then one or more tuned runs")
    tuning.add_argument("--group-dir", help="directory whose immediate children are run directories")
    tuning.add_argument("--baseline", help="baseline run when using --group-dir")
    tuning.add_argument("--rate", type=float, help="include only candidates at this requested rate")
    tuning.add_argument("--output", required=True, type=Path, help="PNG output path; matching .json summary is also written")
    args = parser.parse_args()
    if args.command == "tuning":
        if args.group_dir and not args.baseline:
            fail("--group-dir tuning requires --baseline so the comparison is explicit")
        if args.baseline:
            before = load_runs([args.baseline], None, sort_by_rate=False)[0]
            candidates = load_runs(args.paths, args.group_dir, sort_by_rate=False)
            candidates = [row for row in candidates if row["path"] != before["path"]]
        else:
            rows = load_runs(args.paths, None, sort_by_rate=False)
            if len(rows) < 2:
                fail("provide a baseline followed by at least one tuned run")
            before, candidates = rows[0], rows[1:]
        if args.rate is not None:
            candidates = [row for row in candidates if need(row["metrics"], "rate", row["path"]) == args.rate]
        if not candidates:
            fail("no tuned runs remain after baseline/rate selection")
        tuning_plot(before, candidates, args.output)
        return
    rows = load_runs(args.paths, args.group_dir)
    if args.command == "rate":
        rate_plot(rows, args.output)
    elif args.command == "latency":
        latency_plot(rows, args.output)
    else:
        cohort_plot(rows, args.output)


if __name__ == "__main__":
    main()
