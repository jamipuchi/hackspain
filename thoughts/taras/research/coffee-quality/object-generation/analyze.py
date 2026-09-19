"""Summarize recorded API usage and diagnose generated star outlines."""

import csv
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
MODELS = ("flash-lite", "deepseek", "glm", "qwen", "gemini")


def main():
    rows = []
    for model in (*MODELS, "deepseek-repair"):
        for path in sorted((HERE / "results" / model).glob("*/openrouter_response.json")):
            record = json.loads(path.read_text())
            response = record["response"]
            usage = response.get("usage", {})
            rows.append({
                "model": model, "case": path.parent.name,
                "latency_s": record["latency_s"], "cost_usd": usage.get("cost"),
                "finish_reason": response["choices"][0]["finish_reason"],
                "validated_recipe": (path.parent / "recipe.json").exists(),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            })
    (HERE / "api-metrics.json").write_text(json.dumps(rows, indent=2) + "\n")
    if rows:
        with (HERE / "api-metrics.csv").open("w") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    summary = {}
    for model in MODELS:
        group = [row for row in rows if row["model"] == model]
        if group:
            summary[model] = {
                "completed_responses": len(group),
                "validated_recipes": sum(row["validated_recipe"] for row in group),
                "mean_api_s": statistics.mean(row["latency_s"] for row in group),
                "reported_cost_usd": sum(row["cost_usd"] for row in group if row["cost_usd"] is not None),
            }
    print(json.dumps({"models": summary, "reported_cost_with_retry_usd":
                      sum(row["cost_usd"] for row in rows if row["cost_usd"] is not None)}, indent=2))

    diagnostics = []
    for model in ("flash-lite", "deepseek", "glm", "qwen-repair", "gemini"):
        path = HERE / "results" / model / "star" / "recipe.json"
        if not path.exists():
            continue
        points = json.loads(path.read_text())["parts"][0]["outline"]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
        turns = []
        for index, point in enumerate(points):
            before, after = points[index - 1], points[(index + 1) % len(points)]
            cross = ((point[0] - before[0]) * (after[1] - point[1])
                     - (point[1] - before[1]) * (after[0] - point[0]))
            turns.append(cross * (1 if area > 0 else -1))
        diagnostics.append({"model": model, "vertices": len(points),
                            "concave_valleys": sum(turn < -1e-8 for turn in turns),
                            "convex_vertices": sum(turn > 1e-8 for turn in turns)})
    (HERE / "star-diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")


if __name__ == "__main__":
    main()
