"""Opaque-destination cluster policy for sorting demonstrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


POLICY_VERSION = 1
METHODS = {"centroid", "exemplar"}


@dataclass(frozen=True)
class Demonstration:
    """Only the numeric features and teacher destination reach the learner."""

    features: Mapping[str, float]
    destination: str


@dataclass(frozen=True)
class Prediction:
    destination: str | None
    reason: str | None
    distance: float | None


@dataclass
class LearnedClusterPolicy:
    feature_names: tuple[str, ...]
    location: np.ndarray
    scale: np.ndarray
    representatives: dict[str, np.ndarray]
    cutoff: float
    method: str
    ambiguity_margin: float

    def predict(self, features: Mapping[str, float], valid: bool = True) -> Prediction:
        if not valid:
            return Prediction(None, "invalid_segmentation", None)

        nearest = self.nearest_destination(features)
        if nearest.destination is None:
            return nearest
        destination = nearest.destination
        distance = nearest.distance
        assert distance is not None
        distances = self._distances(features)
        assert distances is not None
        if distance > self.cutoff:
            return Prediction(None, "too_far", distance)
        ordered = sorted(distances.items(), key=lambda item: (item[1], item[0]))
        if len(ordered) > 1 and ordered[1][1] - distance <= self.ambiguity_margin:
            return Prediction(None, "ambiguous", distance)
        return Prediction(destination, None, distance)

    def nearest_destination(self, features: Mapping[str, float]) -> Prediction:
        distances = self._distances(features)
        if distances is None:
            return Prediction(None, "invalid_features", None)
        destination, distance = min(distances.items(), key=lambda item: (item[1], item[0]))
        return Prediction(destination, None, distance)

    def _distances(self, features: Mapping[str, float]) -> dict[str, float] | None:
        vector = _feature_vector(features, self.feature_names)
        if vector is None:
            return None
        scaled = (vector - self.location) / self.scale
        return {
            destination: float(np.min(np.linalg.norm(points - scaled, axis=1)))
            for destination, points in self.representatives.items()
        }

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "version": POLICY_VERSION,
            "feature_names": list(self.feature_names),
            "location": self.location.tolist(),
            "scale": self.scale.tolist(),
            "destinations": sorted(self.representatives),
            "cutoff": self.cutoff,
            "method": self.method,
            "ambiguity_margin": self.ambiguity_margin,
        }
        if self.method == "centroid":
            document["centers"] = {
                destination: points[0].tolist()
                for destination, points in sorted(self.representatives.items())
            }
        else:
            document["exemplars"] = {
                destination: points.tolist()
                for destination, points in sorted(self.representatives.items())
            }
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> "LearnedClusterPolicy":
        if document.get("version") != POLICY_VERSION:
            raise ValueError("unsupported policy version")
        method = document.get("method")
        if method not in METHODS:
            raise ValueError("unsupported policy method")
        feature_names = tuple(document["feature_names"])
        if method == "centroid":
            raw = document["centers"]
            if not isinstance(raw, Mapping):
                raise ValueError("invalid policy centers")
            representatives = {
                str(destination): np.asarray([values], dtype=float)
                for destination, values in raw.items()
            }
        else:
            raw = document["exemplars"]
            if not isinstance(raw, Mapping):
                raise ValueError("invalid policy exemplars")
            representatives = {
                str(destination): np.asarray(values, dtype=float)
                for destination, values in raw.items()
            }
        return cls(
            feature_names=feature_names,
            location=np.asarray(document["location"], dtype=float),
            scale=np.asarray(document["scale"], dtype=float),
            representatives=representatives,
            cutoff=float(document["cutoff"]),
            method=method,
            ambiguity_margin=float(document["ambiguity_margin"]),
        )


def fit_policy(demonstrations: Sequence[Demonstration], method: str) -> LearnedClusterPolicy:
    """Fit a policy from demonstrations, with scaling fit only on these inputs.

    The cutoff is 1.25 times the greatest scaled training distance to its
    destination centroid. With zero spread, it is half the nearest centroid
    separation. This keeps a one-example-per-destination baseline usable.
    """
    if method not in METHODS:
        raise ValueError("method must be centroid or exemplar")
    if not demonstrations:
        raise ValueError("at least one demonstration is required")

    feature_names = tuple(sorted(demonstrations[0].features))
    if not feature_names:
        raise ValueError("demonstrations need at least one feature")
    matrix = np.asarray(
        [_required_vector(demo.features, feature_names) for demo in demonstrations],
        dtype=float,
    )
    destinations = [_required_destination(demo.destination) for demo in demonstrations]
    location = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1.0
    scaled = (matrix - location) / scale

    grouped: dict[str, list[np.ndarray]] = {}
    for destination, vector in zip(destinations, scaled, strict=True):
        grouped.setdefault(destination, []).append(vector)
    centroids = {
        destination: np.mean(vectors, axis=0)
        for destination, vectors in grouped.items()
    }
    training_distances = [
        float(np.linalg.norm(vector - centroids[destination]))
        for destination, vector in zip(destinations, scaled, strict=True)
    ]
    max_distance = max(training_distances)
    if max_distance:
        cutoff = max_distance * 1.25
    else:
        centers = list(centroids.values())
        centroid_distances = [
            float(np.linalg.norm(left - right))
            for index, left in enumerate(centers)
            for right in centers[index + 1 :]
        ]
        cutoff = min(centroid_distances) / 2 if centroid_distances else 1e-12
    if method == "centroid":
        representatives = {
            destination: np.asarray([center], dtype=float)
            for destination, center in centroids.items()
        }
    else:
        representatives = {
            destination: np.asarray(vectors, dtype=float)
            for destination, vectors in grouped.items()
        }

    return LearnedClusterPolicy(
        feature_names=feature_names,
        location=location,
        scale=scale,
        representatives=representatives,
        cutoff=cutoff,
        method=method,
        ambiguity_margin=cutoff * 0.05,
    )


def save_policy(policy: LearnedClusterPolicy, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(policy.to_dict(), stream, indent=2, sort_keys=True)
        stream.write("\n")


def load_policy(path: str | Path) -> LearnedClusterPolicy:
    with Path(path).open(encoding="utf-8") as stream:
        return LearnedClusterPolicy.from_dict(json.load(stream))


def _required_vector(features: Mapping[str, float], feature_names: tuple[str, ...]) -> list[float]:
    vector = _feature_vector(features, feature_names)
    if vector is None:
        raise ValueError("all demonstrations need the same finite numeric features")
    return vector.tolist()


def _feature_vector(
    features: Mapping[str, float], feature_names: tuple[str, ...]
) -> np.ndarray | None:
    if set(features) != set(feature_names):
        return None
    values = [features[name] for name in feature_names]
    if any(not isinstance(value, Real) or isinstance(value, bool) for value in values):
        return None
    vector = np.asarray(values, dtype=float)
    if not np.isfinite(vector).all():
        return None
    return vector


def _required_destination(destination: str) -> str:
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("demonstration destination must be a nonempty string")
    return destination
