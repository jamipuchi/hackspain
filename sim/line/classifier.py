"""Bean classifiers for the physical line. Owner: coffee-sim agent.

  RuleClassifier(cfg)          thresholds from cfg.classifier.rules -> Verdict with a readable reason
  SklearnClassifier(path)      a trained model (this module's LineModel, or a coffee_sorter Model) over named features
  train_from_dataset(dir)      fit from datasets/beans/<label>/*.json written by the camera agent's capture tool
  evaluate(dir, model_path)    confusion matrix of a saved model on a dataset folder
  train_from_sim(...)          bootstrap a model from the MuJoCo sorter rendered on white paper (no real samples needed)

Everything codes against contracts.py (Frame, Blob, Verdict). Features are looked up BY NAME in blob.features;
a name the model expects but the detector does not provide is filled with 0 and counted in status()["missing_features"].
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from line.contracts import Blob, Check, Frame, Verdict

# HistGradientBoosting spawns an OpenMP team per call; on tiny inputs (one bean, 120 training rows) that costs more
# than the maths and thrashes when several processes run at once (86 s for a 120-row fit was measured). One thread.
def _single_thread():
    return threadpool_limits(limits=1, user_api="openmp")

LINE_DIR = Path(__file__).resolve().parent
ROOT = LINE_DIR.parent
COFFEE = ROOT / "coffee_sorter"

# Feature names the line's detector publishes. Prefer the camera agent's list; fall back to the sim's.
try:  # pragma: no cover - depends on another agent's file
    from line.bean_vision import FEATURES as LINE_FEATURES  # type: ignore
except Exception:  # noqa: BLE001
    LINE_FEATURES = None


def _coffee_features() -> list[str]:
    if str(COFFEE) not in sys.path:
        sys.path.append(str(COFFEE))
    from vision import FEATURES  # coffee_sorter/vision.py
    return list(FEATURES)


def default_features() -> list[str]:
    return list(LINE_FEATURES) if LINE_FEATURES else _coffee_features()


def _fmt(x: float) -> str:
    return f"{x:.2f}" if abs(x) < 10 else f"{x:.1f}"


# ====================================================================== rule based
# (feature, bound kind, rule key, label, human hint)
RULES = [
    ("major_mm", "min", "min_major_mm", "fragment", "too short"),
    ("major_mm", "max", "max_major_mm", "foreign", "too long: stick / cluster"),
    ("aspect", "min", "min_aspect", "odd_shape", "too round: stone / cluster"),
    ("aspect", "max", "max_aspect", "odd_shape", "too elongated: husk / stick"),
    ("dark_frac", "max", "max_dark_frac", "burnt", "black surface"),
    ("mean_gray", "min", "min_mean_gray", "burnt", "too dark"),
    ("mean_gray", "max", "max_mean_gray", "quaker", "too pale: quaker / shell"),
    ("n_dark_spots", "max", "max_n_dark_spots", "insect", "holes"),
]


class RuleClassifier:
    """Threshold rules on blob features. p_defect is a soft margin: exactly at a threshold = 0.5, one 'width'
    past it ≈ 0.98 (width = a quarter of the allowed range, or a quarter of the bound when one-sided)."""

    name = "rule"

    def __init__(self, cfg):
        self.cfg = cfg
        self.n_classified = 0
        self.total_ms = 0.0
        self.missing = Counter()
        self.last: Verdict | None = None
        self.labels = Counter()

    @property
    def rules(self) -> dict:
        return self.cfg.classifier.rules

    def _width(self, feat: str, key: str) -> float:
        r = self.rules
        lo, hi = r.get(f"min_{feat}"), r.get(f"max_{feat}")
        if feat == "n_dark_spots":
            return 1.0
        if lo is not None and hi is not None and hi > lo:
            return 0.25 * (hi - lo)
        return max(0.25 * abs(r[key]), 1e-6)

    def scores(self, features: dict) -> list[tuple[float, str, str]]:
        """(p, label, reason) for every applicable rule, worst first."""
        out = []
        missing = set()
        for feat, kind, key, label, hint in RULES:
            if key not in self.rules:
                continue
            if feat not in features:
                missing.add(feat)
                continue
            x, bound = float(features[feat]), float(self.rules[key])
            w = self._width(feat, key)
            e = (bound - x) / w if kind == "min" else (x - bound) / w
            p = 1.0 / (1.0 + math.exp(-4.0 * max(min(e, 10), -10)))
            unit = " mm" if feat.endswith("_mm") else ""
            sym = "<" if kind == "min" else ">"
            reason = f"{feat} {_fmt(x)}{unit} {sym} {_fmt(bound)}{unit}: {label} ({hint})"
            out.append((p, label, reason))
        for feat in missing:
            self.missing[feat] += 1
        out.sort(key=lambda t: -t[0])
        return out

    def classify(self, frame: Frame, blob: Blob) -> Verdict:
        t0 = time.perf_counter()
        f = blob.features or {}
        sc = self.scores(f)
        thr = float(self.cfg.classifier.suspect_threshold)
        if sc:
            p, label, reason = sc[0]
            suspect = p >= thr
            if not suspect:
                label = "good"
                short = {"major_mm": ("major", " mm"), "aspect": ("aspect", ""), "mean_gray": ("gray", ""), "dark_frac": ("dark", ""), "n_dark_spots": ("spots", "")}
                summary = ", ".join(f"{nm} {_fmt(float(f[k])) if k != 'n_dark_spots' else int(f[k])}{unit}" for k, (nm, unit) in short.items() if k in f)
                reason = f"ok: {summary}" if summary else "ok"
        else:
            p, label, suspect, reason = 0.5, "unknown", thr <= 0.5, "no usable features"
        ms = (time.perf_counter() - t0) * 1e3
        v = Verdict(label, float(p), bool(suspect), reason, ms, self.name)
        self.n_classified += 1
        self.total_ms += ms
        self.labels[label] += 1
        self.last = v
        return v

    def status(self) -> dict:
        return dict(name=self.name, classes=sorted({r[3] for r in RULES} | {"good"}), rules=dict(self.rules),
                    suspect_threshold=self.cfg.classifier.suspect_threshold, n_classified=self.n_classified,
                    mean_ms=self.total_ms / self.n_classified if self.n_classified else 0.0,
                    missing_features=dict(self.missing), labels=dict(self.labels),
                    last=(self.last.label if self.last else None))

    def selftest(self) -> list[Check]:
        frame = Frame(np.zeros((2, 2, 3), np.uint8), 0.0, 0, "selftest")
        cases = {
            "good bean": (dict(major_mm=11.5, minor_mm=7.5, aspect=1.5, dark_frac=0.05, mean_gray=95, n_dark_spots=0), False, "good"),
            "fragment": (dict(major_mm=5.5, minor_mm=4.0, aspect=1.4, dark_frac=0.05, mean_gray=95, n_dark_spots=0), True, "fragment"),
            "burnt": (dict(major_mm=11.0, minor_mm=7.0, aspect=1.6, dark_frac=0.8, mean_gray=30, n_dark_spots=0), True, "burnt"),
            "quaker": (dict(major_mm=11.0, minor_mm=7.0, aspect=1.6, dark_frac=0.0, mean_gray=185, n_dark_spots=0), True, "quaker"),
            "insect": (dict(major_mm=11.0, minor_mm=7.0, aspect=1.6, dark_frac=0.1, mean_gray=95, n_dark_spots=4), True, "insect"),
        }
        out = []
        for name, (feat, want_suspect, want_label) in cases.items():
            blob = Blob(0, 0, (0, 0, 1, 1), 1.0, False, feat)
            t0 = time.perf_counter()
            v = self.classify(frame, blob)
            ms = (time.perf_counter() - t0) * 1e3
            ok = v.suspect == want_suspect and v.label == want_label
            out.append(Check(f"rule: {name}", ok, f"{v.label} p={v.p_defect:.2f} — {v.reason}", ms))
        out.append(Check("rule: speed", all(c.ms < 5 for c in out), f"max {max(c.ms for c in out):.2f} ms per bean (budget 30)"))
        return out

    def close(self) -> None:
        pass


# ====================================================================== learned
@dataclass
class LineModel:
    """A trained classifier over named features + a Mahalanobis anomaly gate on the 'good' class.
    Same predict() API as coffee_sorter.classifier.Model so SklearnClassifier can load either."""

    classes: list
    clf: object
    features: list
    good_mean: np.ndarray
    good_icov: np.ndarray
    feature_scale: np.ndarray
    anomaly_thresh: float
    meta: dict = field(default_factory=dict)

    def predict(self, X):
        X = np.asarray(X, float)
        if len(X) == 0:
            return np.zeros((0, len(self.classes))), np.zeros(0)
        with _single_thread():
            P = self.clf.predict_proba(X)
        d = (X - self.good_mean) / self.feature_scale
        a = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", d, self.good_icov, d), 0))
        return P, a


def _load_model(path: Path):
    """joblib.load that can also unpickle a coffee_sorter Model (pickled with module name 'classifier')."""
    import joblib
    try:
        return joblib.load(path)
    except (AttributeError, ModuleNotFoundError):
        prev = sys.modules.get("classifier")
        if str(COFFEE) not in sys.path:
            sys.path.append(str(COFFEE))
        spec = importlib.util.spec_from_file_location("classifier", COFFEE / "classifier.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["classifier"] = mod
        try:
            spec.loader.exec_module(mod)
            return joblib.load(path)
        finally:
            if prev is not None:
                sys.modules["classifier"] = prev
            else:
                sys.modules.pop("classifier", None)


def _model_features(model) -> list[str]:
    if hasattr(model, "features"):
        return list(model.features)
    return list(model.meta.get("features", []))


def _resolve(path, cfg=None) -> Path:
    p = Path(path) if path else Path(cfg.classifier.model_path)
    return p if p.is_absolute() else (LINE_DIR / p)


class SklearnClassifier:
    name = "sklearn"

    def __init__(self, path=None, cfg=None):
        self.cfg = cfg
        self.path = _resolve(path, cfg)
        self.model = _load_model(self.path)
        self.classes = list(self.model.classes)
        self.features = _model_features(self.model)
        self.defect_mask = np.array([c != "good" for c in self.classes])
        self.threshold = float(cfg.classifier.suspect_threshold) if cfg else 0.5
        self.n_classified = 0
        self.total_ms = 0.0
        self.missing = Counter()
        self.labels = Counter()
        self.last: Verdict | None = None

    def vector(self, features: dict) -> np.ndarray:
        x = np.zeros(len(self.features))
        for i, k in enumerate(self.features):
            if k in features:
                x[i] = float(features[k])
            else:
                self.missing[k] += 1
        return x

    def classify(self, frame: Frame, blob: Blob) -> Verdict:
        t0 = time.perf_counter()
        x = self.vector(blob.features or {})
        with _single_thread():
            P, a = self.model.predict(x[None])
        p, a = P[0], float(a[0])
        p_defect = float(p[self.defect_mask].sum())
        anomalous = a > float(self.model.anomaly_thresh)
        k = int(np.argmax(p))
        label = self.classes[k]
        suspect = p_defect >= self.threshold or anomalous
        if anomalous and label == "good":
            label = "unknown"
        reason = f"{label} p={p[k]:.2f}, p_defect={p_defect:.2f}"
        if anomalous:
            reason += f", anomaly {a:.1f} > {float(self.model.anomaly_thresh):.1f}"
        ms = (time.perf_counter() - t0) * 1e3
        v = Verdict(label, min(max(p_defect if not anomalous else max(p_defect, 0.5 + 0.5 * min(a / (2 * float(self.model.anomaly_thresh)), 1.0)), 0.0), 1.0),
                    bool(suspect), reason, ms, self.name)
        self.n_classified += 1
        self.total_ms += ms
        self.labels[label] += 1
        self.last = v
        return v

    def status(self) -> dict:
        meta = getattr(self.model, "meta", {}) or {}
        return dict(name=self.name, classes=self.classes, model_path=str(self.path), features=self.features,
                    suspect_threshold=self.threshold, anomaly_thresh=float(self.model.anomaly_thresh),
                    n_classified=self.n_classified, mean_ms=self.total_ms / self.n_classified if self.n_classified else 0.0,
                    missing_features=dict(self.missing), labels=dict(self.labels),
                    train_accuracy=meta.get("accuracy"), n_train=meta.get("n_train"), trained_on=meta.get("source"),
                    last=(self.last.label if self.last else None))

    def selftest(self) -> list[Check]:
        out = []
        frame = Frame(np.zeros((2, 2, 3), np.uint8), 0.0, 0, "selftest")
        good = dict(zip(self.features, np.asarray(self.model.good_mean, float)))
        t0 = time.perf_counter()
        v = self.classify(frame, Blob(0, 0, (0, 0, 1, 1), 1.0, False, good))
        ms = (time.perf_counter() - t0) * 1e3
        out.append(Check("sklearn: mean good bean", (not v.suspect) and v.label == "good", f"{v.label} — {v.reason}", ms))
        far = {k: (val * 3 + 50) for k, val in good.items()}
        v2 = self.classify(frame, Blob(0, 0, (0, 0, 1, 1), 1.0, False, far))
        out.append(Check("sklearn: absurd features flagged", v2.suspect, f"{v2.label} — {v2.reason}", v2.ms))
        t0 = time.perf_counter()
        for _ in range(10):
            self.classify(frame, Blob(0, 0, (0, 0, 1, 1), 1.0, False, good))
        ms10 = (time.perf_counter() - t0) / 10 * 1e3
        out.append(Check("sklearn: speed", ms10 < 30, f"{ms10:.2f} ms per bean (budget 30)", ms10))
        provided = set(LINE_FEATURES or [])
        if provided:
            miss = [f for f in self.features if f not in provided]
            out.append(Check("sklearn: features covered by bean_vision", not miss, f"missing from detector: {miss}" if miss else "all present"))
        else:
            out.append(Check("sklearn: features covered by bean_vision", True, "bean_vision.FEATURES not importable yet; will fill missing with 0"))
        return out

    def close(self) -> None:
        pass


# ====================================================================== training
def load_dataset(dataset_dir, features: list[str] | None = None):
    """datasets/beans/<label>/*.json -> (X, y, feature_names). A JSON is a Sample ({'features': {...}, 'label': ...})
    or a flat {feature: value} dict. Label = folder name."""
    root = Path(dataset_dir)
    rows, labels = [], []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        for j in sorted(sub.glob("*.json")):
            try:
                d = json.loads(j.read_text())
            except json.JSONDecodeError:
                continue
            f = d.get("features", d) if isinstance(d, dict) else None
            if not isinstance(f, dict) or not f:
                continue
            rows.append({k: float(v) for k, v in f.items() if isinstance(v, (int, float)) and math.isfinite(float(v))})
            labels.append(sub.name)
    if not rows:
        raise ValueError(f"no samples under {root}")
    if features is None:
        seen = set().union(*rows)
        pref = [f for f in default_features() if f in seen]
        features = pref + sorted(seen - set(pref))
    X = np.array([[r.get(k, 0.0) for k in features] for r in rows])
    return X, np.array(labels, dtype=object), list(features)


def fit(X, y, features, classes=None, seed=0, source="dataset") -> tuple[LineModel, dict]:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.metrics import confusion_matrix

    classes = classes or (["good"] if "good" in set(y) else []) + sorted(set(y) - {"good"})
    cid = {c: i for i, c in enumerate(classes)}
    yi = np.array([cid[v] for v in y])
    counts = np.bincount(yi, minlength=len(classes))
    small = len(y) < 400
    clf = HistGradientBoostingClassifier(max_iter=150 if small else 300, learning_rate=0.1 if small else 0.08,
                                         max_leaf_nodes=15 if small else 31, min_samples_leaf=5 if small else 20,
                                         l2_regularization=0.1, early_stopping=not small, random_state=seed)
    k = int(min(5, counts[counts > 0].min()))
    with _single_thread():
        if k >= 2 and len(classes) > 1:
            pred = cross_val_predict(clf, X, yi, cv=StratifiedKFold(k, shuffle=True, random_state=seed))
            cv_note = f"{k}-fold stratified cross-validation"
        else:
            pred = yi.copy()
            cv_note = "too few samples per class for cross-validation: accuracy is on the training set"
        clf.fit(X, yi)
    cm = confusion_matrix(yi, pred, labels=range(len(classes)))
    good = X[y == "good"]
    if len(good) >= 5:
        scale = good.std(0) + 1e-6
        z = (good - good.mean(0)) / scale
        cov = np.cov(z.T) + 0.1 * np.eye(z.shape[1])
        icov = np.linalg.inv(cov)
        dist = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", z, icov, z), 0))
        thresh = float(np.percentile(dist, 99.0) * 1.25 + 1.0)
        gmean = good.mean(0)
    else:
        gmean, scale, icov, thresh = X.mean(0), X.std(0) + 1e-6, np.eye(X.shape[1]), float("inf")
    is_def = np.array([c != "good" for c in classes])
    te_def, pr_def = is_def[yi], is_def[pred]
    Xb = X[:40]
    with _single_thread():
        t0 = time.perf_counter()
        for _ in range(20):
            clf.predict_proba(Xb[:1])
        ms1 = (time.perf_counter() - t0) / 20 * 1e3
    report = dict(source=source, n_train=int(len(y)), classes=classes, counts={c: int(n) for c, n in zip(classes, counts)},
                  features=list(features), accuracy=float((pred == yi).mean()), cv=cv_note,
                  defect_recall=float((pr_def & te_def).sum() / max(te_def.sum(), 1)),
                  good_false_reject=float((pr_def & ~te_def).sum() / max((~te_def).sum(), 1)),
                  confusion=cm.tolist(), anomaly_thresh=thresh, predict_ms_single=ms1,
                  per_class={c: dict(recall=float(cm[i, i] / max(cm[i].sum(), 1)),
                                     precision=float(cm[i, i] / max(cm[:, i].sum(), 1)), n=int(counts[i])) for i, c in enumerate(classes)})
    model = LineModel(classes, clf, list(features), gmean, icov, scale, thresh, report)
    return model, report


def save_model(model: LineModel, out_path: Path, report: dict | None = None) -> Path:
    import joblib
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_path)
    (out_path.parent / "report.json").write_text(json.dumps(report or model.meta, indent=1, default=float))
    return out_path


def train_from_dataset(dataset_dir, out_path=None, cfg=None, seed=0) -> tuple[LineModel, dict]:
    """Fit from the camera agent's labelled crops and write models/beans_v1.joblib (+ report.json next to it)."""
    X, y, features = load_dataset(dataset_dir)
    model, report = fit(X, y, features, seed=seed, source=str(dataset_dir))
    out = _resolve(out_path, cfg) if (out_path or cfg) else LINE_DIR / "models" / "beans_v1.joblib"
    save_model(model, out, report)
    return model, report


def evaluate(dataset_dir, model_path=None, cfg=None, verbose=True) -> dict:
    """Confusion matrix of a saved model on a dataset folder (labels = folder names)."""
    model = _load_model(_resolve(model_path, cfg) if (model_path or cfg) else LINE_DIR / "models" / "beans_v1.joblib")
    feats = _model_features(model)
    X, y, _ = load_dataset(dataset_dir, feats)
    P, a = model.predict(X)
    classes = list(model.classes)
    pred = np.array([classes[i] for i in np.argmax(P, 1)], dtype=object)
    labels = classes + sorted(set(y) - set(classes))
    cm = np.zeros((len(labels), len(labels)), int)
    for t, p in zip(y, pred):
        cm[labels.index(t), labels.index(p)] += 1
    acc = float((pred == y).mean())
    anomalies = int((a > float(model.anomaly_thresh)).sum())
    if verbose:
        w = max(8, max(len(l) for l in labels) + 1)
        print(f"{'true \\ pred':>{w}s} " + " ".join(f"{l:>{w}s}" for l in labels))
        for i, l in enumerate(labels):
            print(f"{l:>{w}s} " + " ".join(f"{cm[i, j]:>{w}d}" for j in range(len(labels))))
        print(f"accuracy {acc:.3f}  n={len(y)}  anomalies flagged {anomalies}")
    return dict(labels=labels, confusion=cm.tolist(), accuracy=acc, n=int(len(y)), anomalies=anomalies)


def train_from_sim(profile: str = "roasted", seconds: float = 8.0, rate: float = 250.0, defect_boost: float = 4.0,
                   out_path=None, seed: int = 3, verbose: bool = True) -> tuple[LineModel, dict]:
    """Bootstrap: run the MuJoCo sorter with white paper under the camera, harvest (features, label) from the
    rendered strip exactly as the live detector would, and fit. Feature names are coffee_sorter's; at run time
    the line detector's blobs are matched by name (missing names -> 0, flagged in status)."""
    if str(COFFEE) not in sys.path:
        sys.path.append(str(COFFEE))
    import assets as coffee_assets
    from profiles import PROFILES
    from sim import SorterSim
    from vision_paper import PaperInspector, paper_profile
    from classifier import label_blobs  # coffee_sorter/classifier.py (ground-truth matcher)

    coffee_assets.build()
    P = paper_profile(PROFILES[profile])
    sim = SorterSim(P, rate=rate, seed=seed, defect_boost=defect_boost)
    insp = PaperInspector(sim)
    Xs, ys = [], []
    n = int(seconds / sim.dt)
    t0 = time.perf_counter()
    for i in range(n):
        sim.step()
        if i % 2 == 0 and sim.data.time > 0.5:
            frame, t = insp.capture()
            blobs = insp.detect(frame, t)
            if blobs.n:
                labels, _ = label_blobs(blobs, sim)
                ok = (labels != "") & ~blobs.partial
                Xs.append(blobs.X[ok])
                ys.extend(labels[ok])
        if verbose and i and i % (n // 5) == 0:
            print(f"  sim {sim.data.time:4.1f}s  samples {sum(len(x) for x in Xs)}  wall {time.perf_counter() - t0:4.0f}s")
    insp.close()
    X = np.concatenate(Xs)
    y = np.array(ys, dtype=object)
    model, report = fit(X, y, _coffee_features(), seed=seed, source=f"sim:{profile} on white paper, {seconds}s @ {rate}/s")
    out = Path(out_path) if out_path else LINE_DIR / "models" / f"beans_sim_{profile}.joblib"
    save_model(model, _resolve(out), report)
    if verbose:
        print(f"trained on {len(y)} sim blobs {dict(Counter(y))}; accuracy {report['accuracy']:.3f}; -> {out}")
    return model, report


if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="train / evaluate the line's bean classifier")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("train"); p.add_argument("dataset"); p.add_argument("--out", default=None)
    p = sub.add_parser("eval"); p.add_argument("dataset"); p.add_argument("--model", default=None)
    p = sub.add_parser("train-sim"); p.add_argument("--profile", default="roasted"); p.add_argument("--seconds", type=float, default=8); p.add_argument("--rate", type=float, default=250)
    a = ap.parse_args()
    if a.cmd == "train":
        _, r = train_from_dataset(a.dataset, a.out); print(json.dumps({k: r[k] for k in ("n_train", "accuracy", "defect_recall", "good_false_reject", "cv")}, indent=1))
    elif a.cmd == "eval":
        evaluate(a.dataset, a.model)
    else:
        train_from_sim(a.profile, a.seconds, a.rate)
