#!/usr/bin/env python3
"""Two-request refinement for the coffee Jev policy-language spike.

The original six results remain untouched in results.json. This script sends only
the refined English and Spanish requests. It stores raw choices and compiled
effective actions separately, without credentials or request headers.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("COFFEE_JEV_SPIKE_OUT", str(Path(tempfile.gettempdir()) / "coffee-jev-policy-spike")))
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
ENV_FILE = Path(os.environ.get("COFFEE_JEV_SPIKE_ENV_FILE", ".env"))
KEY_FILE = Path(os.environ.get("COFFEE_JEV_SPIKE_KEY_FILE", str(Path.home() / ".config" / "typesafe" / "api_key")))
GREEN = {
    "good": {"severity": "none", "current_action": "keep"},
    "faded": {"severity": "minor", "current_action": "reject"},
    "black": {"severity": "major", "current_action": "reject"},
    "sour": {"severity": "major", "current_action": "reject"},
    "insect": {"severity": "major", "current_action": "reject"},
    "broken": {"severity": "major", "current_action": "reject"},
    "shell": {"severity": "major", "current_action": "reject"},
    "husk": {"severity": "foreign", "current_action": "reject"},
    "stone": {"severity": "foreign", "current_action": "reject"},
    "stick": {"severity": "foreign", "current_action": "reject"},
}
CASES = (
    ("english_policy_refined", "Keep faded beans, but reject black beans and foreign material."),
    ("spanish_policy_refined", "Conserva los granos descoloridos, pero rechaza los negros y el material extraño."),
)
EXPECTED = {"faded": "keep", "black": "reject", "husk": "reject", "stone": "reject", "stick": "reject"}


def load_key() -> str:
    value = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if value:
        return value
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, separator, raw = line.strip().removeprefix("export ").partition("=")
            if separator and name.strip() == "TYPESAFE_API_KEY":
                parsed = shlex.split(raw, comments=True)
                if len(parsed) == 1 and parsed[0]:
                    return parsed[0]
    if KEY_FILE.exists() and KEY_FILE.read_text().strip():
        return KEY_FILE.read_text().strip()
    raise RuntimeError("no authorized TypeSafe credential found")


def request_for(instruction: str) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "product": {
            "type": "choice",
            "instructions": "Select only the requested supported product profile. Do not use this answer to report unsupported class recognition.",
            "criteria": {
                "green_arabica": "The existing green coffee profile.",
                "roasted": "The existing roasted coffee profile.",
                "no_profile_change": "No supported product profile is selected or changed.",
            },
        },
        "capability": {
            "type": "choice",
            "instructions": "Does the selected product profile support every requested recognition class?",
            "criteria": {
                "supported": "Every requested class is present in the selected profile metadata.",
                "unsupported": "At least one requested recognition class is absent from the selected profile metadata.",
            },
        },
        "intent": {
            "type": "choice",
            "instructions": "Classify the request. Do not invent recognition classes, visual features, or actuator commands.",
            "criteria": {
                "policy_change": "A supported per-class keep or reject policy change, or a supported profile switch.",
                "unsupported_recognition": "Requests recognition of an absent class.",
                "ambiguous": "Contains conflicting directives for a class.",
                "irrelevant": "Does not request supported sorting control.",
            },
        },
        "ambiguity": {
            "type": "choice",
            "instructions": "Assess only direct conflicts in keep or reject directives. Do not treat language, confidence, or foreign-class grouping as ambiguity.",
            "criteria": {
                "clear": "No class receives both keep and reject directives.",
                "ambiguous": "At least one class receives both keep and reject directives.",
            },
        },
    }
    for name, metadata in GREEN.items():
        questions[f"action_{name}"] = {
            "type": "choice",
            "instructions": f"Choose the requested proposed action for {name}. Choose unchanged when the instruction does not alter it. This cannot activate a policy.",
            "criteria": {
                "keep": "Keep this class in the accepted stream.",
                "reject": "Reject this class from the accepted stream.",
                "unchanged": f"Preserve its current action: {metadata['current_action']}.",
            },
        }
    return {
        "model": MODEL,
        "state": {
            "instruction": instruction,
            "selected_profile_context": "green_arabica",
            "green_arabica_classes": GREEN,
            "foreign_classes": ["husk", "stone", "stick"],
            "limits": [
                "Return typed choices only.",
                "Do not generate image features or visual labels.",
                "This is a proposal only. It cannot activate a policy.",
            ],
        },
        "questions": questions,
    }


def call(key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload, allow_nan=False).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"TypeSafe HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"TypeSafe request failed: {type(error).__name__}") from None
    if not isinstance(body, dict) or "error" in body:
        raise RuntimeError("TypeSafe returned an invalid response")
    return body, time.monotonic() - started


def scrub(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in ("authorization", "credential", "secret", "api_key")):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def choice(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        return {"choice": None, "probabilities": {}, "confidence": None}
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    clean = {str(k): float(v) for k, v in probabilities.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    return {"choice": answer.get("choice"), "probabilities": clean, "confidence": answer.get("confidence")}


def compile_action(raw: str | None, current: str) -> str | None:
    if raw == "unchanged":
        return current
    if raw in ("keep", "reject"):
        return raw
    return None


def record(case_id: str, instruction: str, response: dict[str, Any], elapsed: float) -> dict[str, Any]:
    answers = response.get("answers") if isinstance(response.get("answers"), dict) else {}
    controls = {field: choice(answers.get(field)) for field in ("product", "capability", "intent", "ambiguity")}
    raw_actions = {name: choice(answers.get(f"action_{name}")) for name in GREEN}
    effective_actions = {name: compile_action(raw_actions[name]["choice"], metadata["current_action"])
                         for name, metadata in GREEN.items()}
    expected_matches = {name: effective_actions[name] == target for name, target in EXPECTED.items()}
    return {
        "id": case_id,
        "instruction": instruction,
        "expected": {"product": "green_arabica", "capability": "supported", "intent": "policy_change", "ambiguity": "clear", "effective_actions": EXPECTED},
        "observed": controls,
        "raw_actions": raw_actions,
        "effective_actions": effective_actions,
        "effective_action_matches": expected_matches,
        "effective_policy_match": all(expected_matches.values()),
        "api_model": response.get("model"),
        "wall_latency_s": round(elapsed, 3),
        "usage": response.get("usage"),
        "cost_usd": response.get("cost_usd"),
        "uncertainty": "A typed response is not calibrated policy evidence. Local validation must gate activation.",
        "raw_sanitized_response": scrub(response),
    }


def total_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {field: sum(int((row.get("usage") or {}).get(field, 0)) for row in rows)
            for field in ("input_tokens", "output_tokens")}


def write_markdown(original: list[dict[str, Any]], refined: list[dict[str, Any]]) -> None:
    lines = ["# Coffee Jev typed-policy micro-spike", "", "Exploratory evidence only. No policy was activated and no simulator was called.", "",
             "## Original six requests", "", "These raw results use a product question that also included unsupported-class detection. Do not interpret its product answer as a clean capability result.", "",
             "| Case | Original outcome | Latency | Interpretation limit |", "| --- | --- | ---: | --- |"]
    for row in original:
        o = row["observed"]
        outcome = f"{o['product']['choice']}; {o['intent']['choice']}; {o['ambiguity']['choice']}"
        limit = "Raw actions differ from effective policy when unchanged preserves current action." if row["id"] in ("english_policy", "spanish_policy") else "Original question structure applies."
        lines.append(f"| {row['id']} | {outcome} | {row['wall_latency_s']:.3f}s | {limit} |")
    lines.extend(["", "## Refined policy requests", "", "`unchanged` compiles to the current local action. The table reports raw model choices and the effective compiled policy separately.", "",
                  "| Case | Product, capability, intent, ambiguity | Raw foreign actions | Effective foreign actions | Effective policy match | Local control gate | Model | Latency |", "| --- | --- | --- | --- | --- | --- | --- | ---: |"])
    for row in refined:
        o = row["observed"]
        controls = "; ".join(str(o[field]["choice"]) for field in ("product", "capability", "intent", "ambiguity"))
        raw = ", ".join(f"{name}={row['raw_actions'][name]['choice']}" for name in ("husk", "stone", "stick"))
        effective = ", ".join(f"{name}={row['effective_actions'][name]}" for name in ("husk", "stone", "stick"))
        control_gate = "defer: ambiguity" if o["ambiguity"]["choice"] == "ambiguous" else "eligible for local review"
        lines.append(f"| {row['id']} | {controls} | {raw} | {effective} | {'pass' if row['effective_policy_match'] else 'mixed'} | {control_gate} | {row['api_model']} | {row['wall_latency_s']:.3f}s |")
    combined = total_usage(original + refined)
    lines.extend(["", f"Combined usage: {combined['input_tokens']} input tokens and {combined['output_tokens']} output tokens across eight serial requests. The API returned no cost field.",
                  "", "See `results.json` for the preserved original responses and `refined_results.json` for the refined sanitized responses."])
    (ROOT / "results.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    original_payload = json.loads((ROOT / "results.json").read_text())
    original = original_payload["results"]
    key = load_key()
    refined = []
    for case_id, instruction in CASES:
        response, elapsed = call(key, request_for(instruction))
        refined.append(record(case_id, instruction, response, elapsed))
    refined_payload = {
        "experiment": "coffee Jev typed-policy micro-spike refinement",
        "endpoint": ENDPOINT,
        "requested_model": MODEL,
        "request_count": len(refined),
        "automatic_retries": False,
        "prompt_change": "Product selection, capability, and directive ambiguity are separate typed questions. Foreign-class metadata is explicit.",
        "results": refined,
    }
    (ROOT / "refined_results.json").write_text(json.dumps(refined_payload, indent=2, ensure_ascii=False) + "\n")
    original_payload["interpretation_limits"] = [
        "Original English and Spanish raw action choices require local compilation. unchanged preserves the configured action.",
        "The original product question combined profile selection and unsupported capability detection. Do not use its product choices as isolated capability evidence.",
    ]
    original_payload["refinement"] = {"artifact": "refined_results.json", "request_count": 2,
                                      "combined_usage": total_usage(original + refined)}
    (ROOT / "results.json").write_text(json.dumps(original_payload, indent=2, ensure_ascii=False) + "\n")
    write_markdown(original, refined)


if __name__ == "__main__":
    main()
