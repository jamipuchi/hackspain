"""Bounded TypeSafe Jev decision schema for opaque sorting destinations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Mapping, Sequence


DESTINATIONS = ("bin_A", "bin_B", "bin_C")
OPTIONS = (*DESTINATIONS, "defer")
MIN_TOP_PROBABILITY = 0.8
MIN_PROBABILITY_LEAD = 0.2
PROBABILITY_SUM_TOLERANCE = 0.01

_OUTLINES = {"elongated", "round", "polygonal", "irregular", "unclear"}
_OPENINGS = {"visible", "absent", "unclear"}
_VISIBILITY = {"clear", "unclear"}
_OBSERVATION_KEYS = {"outline", "central_opening", "description", "visibility", "object_count"}


@dataclass(frozen=True)
class JevPrediction:
    destination: str | None
    reason: str | None
    probabilities: dict[str, float]
    confidence: float


def build_jev_request(
    demonstrations: Sequence[Mapping[str, object]],
    observation: Mapping[str, object],
    model: str = "jev-1.13.0",
) -> dict[str, object]:
    """Build a TypeSafe request from visual observations and opaque destinations."""
    state_demonstrations = []
    for demonstration in demonstrations:
        destination = demonstration.get("destination")
        if destination not in DESTINATIONS:
            raise ValueError("demonstration destination is not allowed")
        state_demonstrations.append(
            {
                "observation": _validated_observation(demonstration.get("observation")),
                "destination": destination,
            }
        )
    return {
        "model": model,
        "state": {
            "demonstrations": state_demonstrations,
            "observation": _validated_observation(observation),
        },
        "questions": {
            "destination": {
                "type": "choice",
                "instructions": "Choose the demonstrated destination with the closest visual resemblance. Choose defer when resemblance is not clear.",
                "criteria": {
                    "bin_A": "Matches observations demonstrated for bin_A.",
                    "bin_B": "Matches observations demonstrated for bin_B.",
                    "bin_C": "Matches observations demonstrated for bin_C.",
                    "defer": "No destination has a clear demonstrated resemblance.",
                },
            }
        },
    }


def parse_jev_response(
    response: Mapping[str, object], expected_model: str = "jev-1.13.0"
) -> JevPrediction:
    """Validate an official TypeSafe choice response without accepting an action."""
    if not isinstance(response, Mapping):
        raise ValueError("response must be an object")
    if response.get("model") != expected_model:
        raise ValueError("response model does not match the requested model")
    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise ValueError("response answers must be an object")
    answer = answers.get("destination")
    if not isinstance(answer, Mapping) or answer.get("type") != "choice":
        raise ValueError("destination answer must be a choice")

    choice = answer.get("choice")
    if choice not in OPTIONS:
        raise ValueError("destination choice is not allowed")
    probabilities = _validated_probabilities(answer.get("probabilities"))
    confidence = _validated_confidence(answer.get("confidence"))
    if probabilities[choice] != max(probabilities.values()):
        raise ValueError("destination choice must have the greatest probability")
    if choice == "defer":
        return JevPrediction(None, "model_defer", probabilities, confidence)
    return JevPrediction(choice, None, probabilities, confidence)


def predict_jev(
    response: Mapping[str, object],
    observation: Mapping[str, object],
    expected_model: str = "jev-1.13.0",
) -> JevPrediction:
    """Apply frozen local acceptance gates to a validated Jev response."""
    checked_observation = _validated_observation(observation)
    if _needs_local_defer(checked_observation):
        return JevPrediction(None, "unclear_observation", {}, 0.0)

    parsed = parse_jev_response(response, expected_model=expected_model)
    if parsed.destination is None:
        return parsed
    top_probability = parsed.probabilities[parsed.destination]
    second_probability = max(
        probability
        for destination, probability in parsed.probabilities.items()
        if destination != parsed.destination
    )
    if top_probability < MIN_TOP_PROBABILITY:
        return JevPrediction(None, "top_probability_below_threshold", parsed.probabilities, parsed.confidence)
    if top_probability - second_probability < MIN_PROBABILITY_LEAD:
        return JevPrediction(None, "probability_lead_below_threshold", parsed.probabilities, parsed.confidence)
    return parsed


def _validated_observation(observation: object) -> dict[str, object]:
    if not isinstance(observation, Mapping) or set(observation) != _OBSERVATION_KEYS:
        raise ValueError("observation must contain only the required visual properties")
    outline = observation["outline"]
    opening = observation["central_opening"]
    description = observation["description"]
    visibility = observation["visibility"]
    object_count = observation["object_count"]
    if not _is_allowed(outline, _OUTLINES) or not _is_allowed(opening, _OPENINGS) or not _is_allowed(visibility, _VISIBILITY):
        raise ValueError("observation has an invalid visual enum")
    if not isinstance(description, str) or not description or len(description) > 1000:
        raise ValueError("observation description must be a string")
    if not isinstance(object_count, int) or isinstance(object_count, bool) or not 0 <= object_count <= 100:
        raise ValueError("observation object_count must be an integer")
    return {
        "outline": outline,
        "central_opening": opening,
        "description": description,
        "visibility": visibility,
        "object_count": object_count,
    }


def _needs_local_defer(observation: Mapping[str, object]) -> bool:
    return observation["object_count"] != 1 or observation["visibility"] != "clear"


def _is_allowed(value: object, options: set[str]) -> bool:
    return isinstance(value, str) and value in options


def _validated_probabilities(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(OPTIONS):
        raise ValueError("probabilities must contain exactly the allowed options")
    probabilities: dict[str, float] = {}
    for option in OPTIONS:
        probability = value[option]
        if not isinstance(probability, Real) or isinstance(probability, bool) or not math.isfinite(probability):
            raise ValueError("probabilities must be finite numbers")
        if probability < 0 or probability > 1:
            raise ValueError("probabilities must be between zero and one")
        probabilities[option] = float(probability)
    if abs(sum(probabilities.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise ValueError("probabilities must sum to one")
    return probabilities


def _validated_confidence(value: object) -> float:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("confidence must be a finite number")
    if value < 0 or value > 1:
        raise ValueError("confidence must be between zero and one")
    return float(value)
