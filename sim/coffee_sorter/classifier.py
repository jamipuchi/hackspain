"""Learned defect classifier + open-set anomaly detector, trained from simulated ground truth.

Training never touches the controller: it renders frames, detects blobs exactly like the live
system does, and labels each blob with the class of the nearest simulated bean under it.
"""
from __future__ import annotations

import json, time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import numpy as np
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, classification_report

from vision import FEATURES

MODELS = Path(__file__).resolve().parent / "models"
ANOMALY_REFERENCE_FALLBACK = "good"  # the product cloud, and the reference an empty policy falls back to


def usable_reference(reference) -> bool:
    """A reference can score only with a finite, positive threshold of its own."""
    thresh = reference.get("thresh") if isinstance(reference, dict) else None
    return isinstance(thresh, (int, float)) and bool(np.isfinite(thresh)) and thresh > 0


class _NumericForest:
    def __init__(self, classifier):
        predictors = classifier._predictors
        trees = [tree for iteration in predictors for tree in iteration]
        counts = np.asarray([len(tree.nodes) for tree in trees], dtype=np.int64)
        self.roots = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(counts[:-1])))
        nodes = np.concatenate([tree.nodes for tree in trees])
        self.feature = nodes["feature_idx"]
        self.threshold = nodes["num_threshold"]
        self.missing_left = nodes["missing_go_to_left"].astype(bool, copy=False)
        self.left = nodes["left"]
        self.right = nodes["right"]
        self.is_leaf = nodes["is_leaf"].astype(bool, copy=False)
        self.value = nodes["value"]
        self.iterations = len(predictors)
        self.outputs = int(classifier.n_trees_per_iteration_)
        self.max_depth = int(nodes["depth"].max())

    def predict_proba(self, classifier, X):
        X = classifier._preprocess_X(X, reset=False)
        count = len(X)
        node_index = np.broadcast_to(self.roots, (count, len(self.roots))).copy()
        for _ in range(self.max_depth):
            active_rows, active_trees = np.nonzero(~self.is_leaf[node_index])
            if not len(active_rows):
                break
            current = node_index[active_rows, active_trees]
            values = X[active_rows, self.feature[current]]
            go_left = np.where(np.isnan(values), self.missing_left[current],
                               values <= self.threshold[current])
            child = np.where(go_left, self.left[current], self.right[current])
            node_index[active_rows, active_trees] = self.roots[active_trees] + child
        if np.any(~self.is_leaf[node_index]):
            raise RuntimeError("NumPy traversal stopped before reaching every leaf")

        leaves = self.value[node_index].reshape(count, self.iterations, self.outputs)
        raw = np.zeros((count, self.outputs), dtype=classifier._baseline_prediction.dtype, order="F")
        raw += classifier._baseline_prediction
        for iteration in range(self.iterations):
            raw += leaves[:, iteration, :]
        return classifier._loss.predict_proba(raw)


@lru_cache(maxsize=2)
def _compiled_forest(classifier):
    required = ("_baseline_prediction", "_loss", "_predictors", "_preprocess_X",
                "n_trees_per_iteration_")
    if not isinstance(classifier, HistGradientBoostingClassifier) or any(
            not hasattr(classifier, name) for name in required):
        return None
    predictors = classifier._predictors
    outputs = int(classifier.n_trees_per_iteration_)
    if not predictors or any(len(iteration) != outputs for iteration in predictors):
        return None
    trees = [tree for iteration in predictors for tree in iteration]
    if any(not hasattr(tree, "nodes") or not tree.nodes.dtype.names or
           "is_categorical" not in tree.nodes.dtype.names or
           np.any(tree.nodes["is_categorical"]) for tree in trees):
        return None
    return _NumericForest(classifier)


def _predict_proba(classifier, X):
    if len(X) > 64 or not isinstance(classifier, HistGradientBoostingClassifier):
        return classifier.predict_proba(X)
    forest = _compiled_forest(classifier)
    return classifier.predict_proba(X) if forest is None else forest.predict_proba(classifier, X)


@dataclass
class Model:
    classes: list
    clf: HistGradientBoostingClassifier
    good_mean: np.ndarray
    good_icov: np.ndarray
    anomaly_thresh: float
    feature_scale: np.ndarray
    meta: dict
    # One reference cloud per trained label (mean, icov, scale, thresh, n), fitted from the
    # train partition only. `active_reference_labels` follows the live reject policy. Both
    # default to empty, so an old artifact and an unbound model score exactly as before.
    label_references: dict = field(default_factory=dict)
    active_reference_labels: list = field(default_factory=list)

    def references(self) -> dict:
        """The usable per-label references. An old artifact has none."""
        stored = getattr(self, "label_references", None) or {}
        return {name: value for name, value in stored.items() if usable_reference(value)}

    def set_anomaly_reference(self, labels) -> list:
        """Bind the active reference set to the labels the live policy keeps.

        Returns the active labels in catalog order. A kept label without a usable
        reference is ignored. This never retrains and never moves a threshold: the
        next `predict` call uses the new set.
        """
        selected, references = set(labels), self.references()
        self.active_reference_labels = [name for name in self.classes
                                        if name in selected and name in references]
        return list(self.active_reference_labels)

    def predict(self, X):
        """(probs (n, C), anomaly score (n,)).

        The anomaly score is the Mahalanobis distance to the 'good' cloud whenever that
        cloud is the only reference in use. With several active references the score is
        `min over active c of (d_c / thresh_c) * anomaly_thresh`, so `anomaly_thresh`
        stays the unchanged 'good' threshold. An empty active set falls back to the
        'good' cloud, so the score is always finite.
        """
        if len(X) == 0:
            return np.zeros((0, len(self.classes))), np.zeros(0)
        P = _predict_proba(self.clf, X)
        references = self.references()
        active = [name for name in getattr(self, "active_reference_labels", None) or []
                  if name in references]
        if not active or active == [ANOMALY_REFERENCE_FALLBACK]:
            # Today's expression, bit for bit: the old artifact, the unbound model, the
            # empty active set, and a 'good'-only policy all take this path.
            d = (X - self.good_mean) / self.feature_scale
            return P, np.sqrt(np.einsum("ij,jk,ik->i", d, self.good_icov, d))
        ratios = None
        for name in active:
            reference = references[name]
            d = (X - reference["mean"]) / reference["scale"]
            scaled = np.sqrt(np.einsum("ij,jk,ik->i", d, reference["icov"], d)) / reference["thresh"]
            ratios = scaled if ratios is None else np.minimum(ratios, scaled)
        return P, ratios * self.anomaly_thresh

    def save(self, path):
        joblib.dump(self, path)

    @staticmethod
    def load(path) -> "Model":
        return joblib.load(path)


def label_blobs(blobs, sim, max_dist=0.0045):
    """Ground-truth labels: nearest active bean to each blob centroid (training/evaluation only)."""
    bodies, pos, _ = sim.active_state(rendered=True)
    labels = np.full(blobs.n, "", dtype=object)
    uids = np.full(blobs.n, -1, int)
    if blobs.n == 0 or len(bodies) == 0:
        return labels, uids
    near = np.abs(pos[:, 0] - sim.L.cam_x) < sim.L.cam_fov
    bodies, pos = bodies[near], pos[near]
    if len(bodies) == 0:
        return labels, uids
    d2 = (blobs.x[:, None] - pos[None, :, 0]) ** 2 + (blobs.y[:, None] - pos[None, :, 1]) ** 2
    j = np.argmin(d2, 1)
    ok = d2[np.arange(blobs.n), j] < max_dist ** 2
    for i in np.where(ok)[0]:
        bean = sim.bean_of[bodies[j[i]]]
        labels[i] = bean.cls
        uids[i] = bean.uid
    return labels, uids


def collect(profile, seconds=20.0, rate=900.0, defect_boost=5.0, seed=7, frame_every=2, verbose=True):
    """Run the plant with a defect-rich feed and harvest (features, label) pairs from the camera."""
    from sim import SorterSim
    from vision import Inspector
    sim = SorterSim(profile, rate=rate, seed=seed, defect_boost=defect_boost)
    insp = Inspector(sim)
    Xs, ys = [], []
    n_steps = int(seconds / sim.dt)
    t0 = time.perf_counter()
    for i in range(n_steps):
        sim.step()
        if i % frame_every == 0 and sim.data.time > 0.5:
            frame, t = insp.capture()
            blobs = insp.detect(frame, t)
            if blobs.n:
                labels, _ = label_blobs(blobs, sim)
                ok = (labels != "") & ~blobs.partial
                Xs.append(blobs.X[ok]); ys.extend(labels[ok])
        if verbose and i % (n_steps // 10 or 1) == 0:
            print(f"  collect {sim.data.time:5.1f}s  samples={sum(len(x) for x in Xs)}  wall={time.perf_counter() - t0:5.1f}s")
    insp.close()
    X = np.concatenate(Xs) if Xs else np.zeros((0, len(FEATURES)))
    return X, np.array(ys, dtype=object)


def train(profile, X, y, out_dir: Path, seed=0):
    classes = [c.name for c in profile.classes if c.name in set(y)]
    cid = {c: i for i, c in enumerate(classes)}
    yi = np.array([cid[v] for v in y])
    Xtr, Xte, ytr, yte = train_test_split(X, yi, test_size=0.25, random_state=seed, stratify=yi)
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
                                         l2_regularization=0.05, early_stopping=True, random_state=seed)
    t0 = time.perf_counter(); clf.fit(Xtr, ytr); fit_s = time.perf_counter() - t0
    pred = clf.predict(Xte)
    cm = confusion_matrix(yte, pred, labels=range(len(classes)))
    rep = classification_report(yte, pred, labels=range(len(classes)), target_names=classes, output_dict=True, zero_division=0)
    # open-set anomaly model on the product class only
    good = X[y == "good"]
    scale = good.std(0) + 1e-6
    z = (good - good.mean(0)) / scale
    cov = np.cov(z.T) + 0.05 * np.eye(z.shape[1])
    icov = np.linalg.inv(cov)
    dist = np.sqrt(np.einsum("ij,jk,ik->i", z, icov, z))
    thresh = float(np.percentile(dist, 99.7))
    # inference latency on a realistic batch
    Xb = Xte[:40]
    t0 = time.perf_counter()
    for _ in range(50): clf.predict_proba(Xb)
    lat_ms = (time.perf_counter() - t0) / 50 * 1e3
    # binary view: defect vs product
    is_def = np.array([profile.by_name(c).defect for c in classes])
    te_def, pr_def = is_def[yte], is_def[pred]
    meta = dict(profile=profile.name, n_train=len(Xtr), n_test=len(Xte), fit_seconds=fit_s, iters=int(clf.n_iter_),
                accuracy=float((pred == yte).mean()), defect_recall=float((pr_def & te_def).sum() / max(te_def.sum(), 1)),
                good_false_reject=float((pr_def & ~te_def).sum() / max((~te_def).sum(), 1)),
                predict_ms_per_40=lat_ms, anomaly_thresh=thresh, classes=classes, features=FEATURES,
                per_class={c: dict(precision=rep[c]["precision"], recall=rep[c]["recall"], n=int(rep[c]["support"])) for c in classes},
                confusion=cm.tolist())
    model = Model(classes, clf, good.mean(0), icov, thresh, scale, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(meta, indent=1))
    _plot_confusion(cm, classes, out_dir / "confusion.png")
    return model


def _plot_confusion(cm, classes, path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    norm = cm / np.maximum(cm.sum(1, keepdims=True), 1)
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(classes))); ax.set_yticks(range(len(classes)))
    ax.set_xticklabels(classes, rotation=45, ha="right"); ax.set_yticklabels(classes)
    for i in range(len(classes)):
        for j in range(len(classes)):
            ax.text(j, i, f"{cm[i, j]}", ha="center", va="center", fontsize=8, color="white" if norm[i, j] > 0.5 else "black")
    ax.set_xlabel("predicted"); ax.set_ylabel("true"); ax.set_title("blob classifier, held-out frames")
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)
