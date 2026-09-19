#!/usr/bin/env python3
"""Bounded, serial TypeSafe Jev policy-language spike.

This script sends six typed requests to the official TypeSafe endpoint. It never
logs credentials, request headers, or raw .env contents. Results retain only
the server body after recursive redaction.
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


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
ROOT = Path(os.environ.get("COFFEE_JEV_SPIKE_OUT", str(Path(tempfile.gettempdir()) / "coffee-jev-policy-spike")))
ENV_FILE = Path(os.environ.get("COFFEE_JEV_SPIKE_ENV_FILE", ".env"))
KEY_FILE = Path(os.environ.get("COFFEE_JEV_SPIKE_KEY_FILE", str(Path.home() / ".config" / "typesafe" / "api_key")))
CLASS_NAMES = ("good", "faded", "black", "sour", "insect", "broken", "shell", "husk", "stone", "stick")
FOREIGN = {"husk", "stone", "stick"}

CASES = (
    {
        "id": "english_policy",
        "instruction": "Keep faded beans, but reject black beans and foreign material.",
        "expected": {"product": "green_arabica", "intent": "policy_change", "ambiguity": "clear",
                     "actions": {"faded": "keep", "black": "reject", "husk": "reject", "stone": "reject", "stick": "reject"}},
    },
    {
        "id": "spanish_policy",
        "instruction": "Conserva los granos descoloridos, pero rechaza los negros y el material extraño.",
        "expected": {"product": "green_arabica", "intent": "policy_change", "ambiguity": "clear",
                     "actions": {"faded": "keep", "black": "reject", "husk": "reject", "stone": "reject", "stick": "reject"}},
    },
    {
        "id": "unsupported_immature",
        "instruction": "Recognize immature beans and reject them.",
        "expected": {"product": "unsupported", "intent": "unsupported_recognition", "ambiguity": "clear"},
    },
    {
        "id": "contradictory_black",
        "instruction": "Keep black beans, then reject black beans.",
        "expected": {"product": "green_arabica", "intent": "ambiguous", "ambiguity": "ambiguous"},
    },
    {
        "id": "irrelevant",
        "instruction": "Tell me a joke about coffee.",
        "expected": {"product": "unsupported", "intent": "irrelevant", "ambiguity": "clear"},
    },
    {
        "id": "switch_roasted",
        "instruction": "Switch to the supported roasted coffee profile.",
        "expected": {"product": "roasted", "intent": "policy_change", "ambiguity": "clear"},
    },
)


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
    if KEY_FILE.exists():
        value = KEY_FILE.read_text().strip()
        if value:
            return value
    raise RuntimeError("no authorized TypeSafe credential found")


def action_default(name: str) -> str:
    if name == "good":
        return "keep"
    return "reject"


def request_for(instruction: str) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "product": {
            "type": "choice",
            "instructions": "Which supported product profile does the instruction select? Choose unsupported when it requests recognition of an unsupported product or class.",
            "criteria": {
                "green_arabica": "The existing green coffee profile with the listed classes.",
                "roasted": "The existing roasted coffee profile.",
                "unsupported": "No supported profile or class can satisfy the instruction.",
            },
        },
        "intent": {
            "type": "choice",
            "instructions": "Classify the requested control intent. Do not invent recognition classes or actions.",
            "criteria": {
                "policy_change": "A supported profile switch or supported per-class keep or reject policy change.",
                "unsupported_recognition": "Requests recognition or sorting of a class not in the supported class list.",
                "ambiguous": "Contains conflicting action instructions that need clarification.",
                "irrelevant": "Does not request a supported sorting control change.",
            },
        },
        "ambiguity": {
            "type": "choice",
            "instructions": "Is the sorting control request clear enough to propose, without activation?",
            "criteria": {
                "clear": "The instruction has no contradictory supported actions.",
                "ambiguous": "The instruction has contradictory actions or needs clarification.",
            },
        },
    }
    for name in CLASS_NAMES:
        questions[f"action_{name}"] = {
            "type": "choice",
            "instructions": f"For class {name}, choose the requested action. If no action is requested, choose unchanged. This is a proposal only.",
            "criteria": {
                "keep": "Keep this class in the accepted stream.",
                "reject": "Reject this class from the accepted stream.",
                "unchanged": "Keep the current action unchanged.",
            },
        }
    return {
        "model": MODEL,
        "state": {
            "instruction": instruction,
            "supported_products": {
                "green_arabica": list(CLASS_NAMES),
                "roasted": ["good", "quaker", "burnt", "broken", "stone"],
            },
            "current_green_actions": {name: action_default(name) for name in CLASS_NAMES},
            "limits": [
                "Return choices only.",
                "Do not generate visual features, labels, or actuator commands.",
                "This request proposes policy only and cannot activate it.",
            ],
        },
        "questions": questions,
    }


def call(key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload, allow_nan=False).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"TypeSafe HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"TypeSafe request failed: {type(error).__name__}") from None
    if not isinstance(result, dict) or "error" in result:
        raise RuntimeError("TypeSafe returned an invalid response")
    return result, time.monotonic() - started


def scrub(value: Any, key: str = "") -> Any:
    if any(term in key.lower() for term in ("authorization", "credential", "secret", "token", "api_key", "key")):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def selected(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        return {"error": "missing answer"}
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    clean_probs = {str(k): float(v) for k, v in probabilities.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    return {"choice": answer.get("choice"), "probabilities": clean_probs,
            "confidence": answer.get("confidence")}


def summarize(case: dict[str, Any], response: dict[str, Any], elapsed: float) -> dict[str, Any]:
    answers = response.get("answers") if isinstance(response.get("answers"), dict) else {}
    choices = {name: selected(answers.get(name)) for name in ("product", "intent", "ambiguity")}
    actions = {name.removeprefix("action_"): selected(answers.get(name))
               for name in answers if name.startswith("action_")}
    expected = case["expected"]
    matches = {
        "product": choices["product"].get("choice") == expected.get("product"),
        "intent": choices["intent"].get("choice") == expected.get("intent"),
        "ambiguity": choices["ambiguity"].get("choice") == expected.get("ambiguity"),
    }
    action_matches = {name: actions.get(name, {}).get("choice") == value
                      for name, value in expected.get("actions", {}).items()}
    matches["actions"] = all(action_matches.values()) if action_matches else True
    return {
        "id": case["id"], "instruction": case["instruction"], "expected": expected,
        "observed": {"product": choices["product"], "intent": choices["intent"],
                     "ambiguity": choices["ambiguity"], "actions": actions},
        "matches": matches, "action_matches": action_matches,
        "api_model": response.get("model"), "wall_latency_s": round(elapsed, 3),
        "usage": response.get("usage"),
        "cost_usd": response.get("cost_usd"),
        "uncertainty": "Inspect top-choice probabilities and response confidence. This spike has no calibration claim.",
        "raw_sanitized_response": scrub(response),
    }


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    key = load_key()
    results = []
    for case in CASES:
        response, elapsed = call(key, request_for(case["instruction"]))
        results.append(summarize(case, response, elapsed))
    payload = {
        "experiment": "coffee Jev typed policy micro-spike",
        "endpoint": ENDPOINT,
        "requested_model": MODEL,
        "request_count": len(results),
        "automatic_retries": False,
        "results": results,
        "cost_note": "The response supplies cost_usd only when the API returns it. No external price estimate was added.",
        "limitation": "Exploratory evidence only. No policy was activated and no simulator was called.",
    }
    (ROOT / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Coffee Jev typed-policy micro-spike", "", "Exploratory evidence only. No policy was activated and no simulator was called.", "",
             "| Case | Expected | Observed | Match | Model | Wall latency | Usage | Cost |", "| --- | --- | --- | --- | --- | ---: | --- | ---: |"]
    for row in results:
        observed = row["observed"]
        expected = row["expected"]
        actual = f"{observed['product']['choice']}; {observed['intent']['choice']}; {observed['ambiguity']['choice']}"
        wanted = f"{expected.get('product')}; {expected.get('intent')}; {expected.get('ambiguity')}"
        match = "pass" if all(row["matches"].values()) else "mixed"
        usage = row["usage"] if row["usage"] is not None else "not returned"
        cost = row["cost_usd"] if row["cost_usd"] is not None else "not returned"
        lines.append(f"| {row['id']} | {wanted} | {actual} | {match} | {row['api_model']} | {row['wall_latency_s']:.3f}s | {usage} | {cost} |")
    lines.extend(["", "See `results.json` for sanitized full responses, per-class actions, probabilities, and confidence values."])
    (ROOT / "results.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
