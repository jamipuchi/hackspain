"""Compare sklearn inference with an exact single-thread NumPy tree traversal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[name] = "1"

import numpy as np
from threadpoolctl import threadpool_info


ROOT = Path(__file__).resolve().parents[4]
COFFEE = ROOT / "sim" / "coffee_sorter"
sys.path.insert(0, str(COFFEE))

from classifier import Model, _compiled_forest, _predict_proba


def sklearn_predict(model, X):
    if len(X) == 0:
        return np.zeros((0, len(model.classes))), np.zeros(0)
    probabilities = model.clf.predict_proba(X)
    delta = (X - model.good_mean) / model.feature_scale
    anomaly = np.sqrt(np.einsum("ij,jk,ik->i", delta, model.good_icov, delta))
    return probabilities, anomaly


def load_feature_batches(path, feature_count):
    with np.load(path, allow_pickle=False) as saved:
        required = {"features", "batch_offsets", "batch_lengths"}
        missing = sorted(required.difference(saved.files))
        if missing:
            raise ValueError(f"feature archive is missing: {', '.join(missing)}")
        features = np.asarray(saved["features"])
        offsets = np.asarray(saved["batch_offsets"])
        lengths = np.asarray(saved["batch_lengths"])

    if features.ndim != 2 or features.shape[1] != feature_count:
        raise ValueError(f"features must have shape (n, {feature_count})")
    if offsets.ndim != 1 or lengths.ndim != 1 or len(offsets) != len(lengths) + 1:
        raise ValueError("batch offsets must include one final endpoint")
    if offsets.dtype.kind not in "iu" or lengths.dtype.kind not in "iu":
        raise ValueError("batch offsets and lengths must contain integers")
    if np.any(lengths < 0) or np.any(offsets < 0) or np.any(offsets > len(features)):
        raise ValueError("batch offsets or lengths are outside the feature array")
    expected_offsets = np.concatenate((np.zeros(1, dtype=np.int64),
                                       np.cumsum(lengths, dtype=np.int64)))
    if not np.array_equal(offsets, expected_offsets) or int(lengths.sum()) != len(features):
        raise ValueError("feature batches must form one contiguous concatenated array")
    return [features[offset:offset + length].copy() for offset, length in zip(offsets[:-1], lengths)]


def require_same(label, reference, candidate):
    for part, expected, actual in zip(("probabilities", "anomaly"), reference, candidate):
        if expected.shape != actual.shape or expected.dtype != actual.dtype:
            raise AssertionError(f"{label} {part} shape or dtype differs")
        if expected.tobytes() != actual.tobytes():
            raise AssertionError(f"{label} {part} is not bitwise identical")


def verify_exactness(model, batches):
    empty = np.zeros((0, model.clf.n_features_in_), dtype=float)
    require_same("empty batch", sklearn_predict(model, empty), model.predict(empty))

    nonempty = [batch for batch in batches if len(batch)]
    if not nonempty:
        raise RuntimeError("No full camera blobs were available for exactness checks")
    for index, batch in enumerate(batches):
        require_same(f"camera batch {index}", sklearn_predict(model, batch), model.predict(batch))

    row = nonempty[0][:1]
    require_same("one-row batch", sklearn_predict(model, row), model.predict(row))

    nan_rows = np.repeat(row, row.shape[1], axis=0)
    nan_rows[np.arange(row.shape[1]), np.arange(row.shape[1])] = np.nan
    require_same("NaN routing", sklearn_predict(model, nan_rows), model.predict(nan_rows))

    large = np.repeat(row, 65, axis=0)
    require_same("large public fallback", sklearn_predict(model, large), model.predict(large))

    class PublicOnly:
        def predict_proba(self, X):
            return X + 1

    fallback = PublicOnly()
    probe = np.asarray([[1.0, 2.0]])
    if not np.array_equal(_predict_proba(fallback, probe), probe + 1):
        raise AssertionError("unsupported classifier fallback changed public behavior")


def benchmark(reference, candidate, batches, warmups, repeats):
    def replay(predict):
        for batch in batches:
            predict(batch)

    for _ in range(warmups):
        replay(reference)
        replay(candidate)

    samples = {"sklearn": [], "numpy": []}
    functions = {"sklearn": reference, "numpy": candidate}
    for repeat in range(repeats):
        order = ("sklearn", "numpy") if repeat % 2 == 0 else ("numpy", "sklearn")
        for name in order:
            started = time.perf_counter()
            replay(functions[name])
            samples[name].append((time.perf_counter() - started) * 1000)
    return samples


def timing_summary(values):
    sample = np.asarray(values)
    return {
        "runs": len(values),
        "min_ms": float(sample.min()),
        "median_ms": float(np.median(sample)),
        "max_ms": float(sample.max()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, default=COFFEE / "configs" / "default_demo.json")
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.warmups < 0 or args.repeats < 1:
        parser.error("repeats must be positive, and warmups cannot be negative")

    preset = json.loads(args.preset.read_text())
    model_path = Path(preset["model_path"])
    if not model_path.is_absolute():
        model_path = COFFEE / model_path
    model = Model.load(model_path)
    forest = _compiled_forest(model.clf)
    if forest is None:
        raise RuntimeError("fixed model cannot use the production NumPy traversal")

    batches = load_feature_batches(args.features, model.clf.n_features_in_)
    verify_exactness(model, batches)
    samples = benchmark(lambda X: sklearn_predict(model, X), model.predict, batches,
                        args.warmups, args.repeats)
    sklearn_timing = timing_summary(samples["sklearn"])
    numpy_timing = timing_summary(samples["numpy"])
    speedup = sklearn_timing["median_ms"] / numpy_timing["median_ms"]
    result = {
        "status": "accepted" if speedup > 1 else "rejected",
        "model": str(model_path),
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "features": str(args.features.resolve()),
        "features_sha256": hashlib.sha256(args.features.read_bytes()).hexdigest(),
        "native_thread_limits": {name: os.environ[name] for name in
                                 ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                  "VECLIB_MAXIMUM_THREADS")},
        "native_threadpools": [{key: pool.get(key) for key in
                                ("internal_api", "prefix", "version", "num_threads")}
                               for pool in threadpool_info()],
        "feature_batches": len(batches),
        "nonempty_batches": sum(bool(len(batch)) for batch in batches),
        "feature_rows": sum(len(batch) for batch in batches),
        "trees": len(forest.roots),
        "nodes": len(forest.value),
        "max_depth": forest.max_depth,
        "exactness": ["camera batches", "empty batch", "one-row batch", "NaN routing",
                      "large public fallback", "unsupported-model fallback"],
        "sklearn": sklearn_timing,
        "numpy": numpy_timing,
        "median_speedup": speedup,
    }
    print(json.dumps(result, indent=2))
    if speedup <= 1:
        raise SystemExit("NumPy traversal did not improve the measured inference cost")


if __name__ == "__main__":
    main()
