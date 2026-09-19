"""Drive the simulated robot from OpenRouter observations and Jev choices."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import cv2

from jev_policy import (
    MIN_PROBABILITY_LEAD, MIN_TOP_PROBABILITY,
    JevPrediction, build_jev_request, predict_jev,
)
from providers import (
    OPENROUTER_URL, TYPESAFE_URL, ProviderError, cached_call, credentials,
    parse_observation, vision_payload,
)
from sim_smoke import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true", help="Allow billable provider calls and simulated robot movement.")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for this simulation experiment")
    document = json.loads(args.policy.read_text())
    thresholds = document.get("thresholds", {})
    if (thresholds.get("minimum_top_probability") != MIN_TOP_PROBABILITY
            or thresholds.get("minimum_probability_lead") != MIN_PROBABILITY_LEAD
            or thresholds.get("clarity_gate") != "visibility=clear and object_count=1"):
        parser.error("saved policy thresholds do not match this experiment")
    keys = credentials(args.env_file)
    if not all(keys.get(name) for name in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY")):
        parser.error("OpenRouter and TypeSafe credentials are required")
    args.out.mkdir(parents=True, exist_ok=True)
    decisions = []

    def predict(crop):
        observation_path = args.out / f"observation_{len(decisions):02d}.png"
        cv2.imwrite(str(observation_path), crop)
        trace = {"image_sha256": hashlib.sha256(observation_path.read_bytes()).hexdigest()}
        try:
            vision, _ = cached_call(
                OPENROUTER_URL, keys["OPENROUTER_API_KEY"],
                vision_payload(observation_path, document["vision_model"]), args.out / "cache/vision",
            )
            observation = parse_observation(vision["response"])
            trace["observation"] = observation
            if observation["visibility"] != "clear" or observation["object_count"] != 1:
                prediction = JevPrediction(None, "unclear_observation", {}, 0.0)
            else:
                payload = build_jev_request(
                    document["candidate_demonstrations"], observation, model=document["jev_model"],
                )
                trace["request"] = payload
                decision, _ = cached_call(
                    TYPESAFE_URL, keys["TYPESAFE_API_KEY"], payload, args.out / "cache/jev",
                )
                prediction = predict_jev(decision["response"], observation, expected_model=document["jev_model"])
        except (ProviderError, ValueError) as error:
            trace["error"] = str(error)
            prediction = JevPrediction(None, "provider_or_schema_error", {}, 0.0)
        trace["prediction"] = asdict(prediction)
        decisions.append(trace)
        (args.out / "decisions.json").write_text(json.dumps(decisions, indent=2) + "\n")
        return prediction

    result = run(None, args.out, predictor=predict)
    result["decision_backend"] = "OpenRouter vision + TypeSafe Jev"
    result["policy_sha256"] = hashlib.sha256(args.policy.read_bytes()).hexdigest()
    (args.out / "results.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
