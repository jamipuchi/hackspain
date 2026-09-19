"""RuleClassifier / SklearnClassifier / train_from_dataset / evaluate — no hardware, no sim."""
import json
import math

import numpy as np
import pytest

from line import classifier as C
from line.config import LineConfig
from line.contracts import Blob, Check, Frame, Verdict


def frame():
    return Frame(np.zeros((4, 4, 3), np.uint8), 0.0, 0, "test")


def blob(**features):
    return Blob(10.0, 10.0, (5, 5, 10, 10), 100.0, False, features)


GOOD = dict(major_mm=11.5, minor_mm=7.5, aspect=1.53, dark_frac=0.04, mean_gray=92.0, n_dark_spots=0)


@pytest.fixture
def cfg():
    return LineConfig()


# ------------------------------------------------------------------ rules
def test_rule_good_bean_passes(cfg):
    v = C.RuleClassifier(cfg).classify(frame(), blob(**GOOD))
    assert isinstance(v, Verdict)
    assert v.label == "good" and not v.suspect and v.p_defect < 0.5
    assert v.reason.startswith("ok:") and "major 11.5 mm" in v.reason and "spots 0" in v.reason
    assert v.classifier == "rule" and v.ms >= 0


@pytest.mark.parametrize("override,label", [
    (dict(major_mm=6.1), "fragment"),
    (dict(major_mm=19.0), "foreign"),
    (dict(aspect=1.0), "odd_shape"),
    (dict(aspect=2.8), "odd_shape"),
    (dict(dark_frac=0.7), "burnt"),
    (dict(mean_gray=25.0), "burnt"),
    (dict(mean_gray=190.0), "quaker"),
    (dict(n_dark_spots=4), "insect"),
])
def test_rule_violations(cfg, override, label):
    v = C.RuleClassifier(cfg).classify(frame(), blob(**{**GOOD, **override}))
    assert v.suspect and v.label == label and v.p_defect >= 0.5
    assert label in v.reason and ("<" in v.reason or ">" in v.reason)


def test_rule_reason_format_example(cfg):
    v = C.RuleClassifier(cfg).classify(frame(), blob(**{**GOOD, "major_mm": 6.1}))
    assert v.reason.startswith("major_mm 6.10 mm < 8.00 mm: fragment")


def test_rule_soft_margin_is_monotonic(cfg):
    rc = C.RuleClassifier(cfg)
    ps = [rc.classify(frame(), blob(**{**GOOD, "major_mm": m})).p_defect for m in (7.99, 7.5, 7.0, 6.0, 4.0)]
    assert all(a < b for a, b in zip(ps, ps[1:]))
    assert 0.45 < ps[0] < 0.55 and ps[-1] > 0.95
    assert rc.classify(frame(), blob(**{**GOOD, "major_mm": 8.01})).p_defect < 0.5


def test_rule_threshold_from_config(cfg):
    cfg.classifier.suspect_threshold = 0.9
    v = C.RuleClassifier(cfg).classify(frame(), blob(**{**GOOD, "major_mm": 7.7}))  # p ≈ 0.6
    assert not v.suspect and v.label == "good"


def test_rule_missing_features_flagged_not_fatal(cfg):
    rc = C.RuleClassifier(cfg)
    v = rc.classify(frame(), blob(major_mm=11.0))
    assert v.label == "good" and not v.suspect
    st = rc.status()
    assert st["missing_features"]["aspect"] == 1 and st["missing_features"]["mean_gray"] == 1
    v2 = rc.classify(frame(), blob())
    assert v2.label == "unknown" and v2.p_defect == 0.5 and v2.suspect


def test_rule_status_and_selftest(cfg):
    rc = C.RuleClassifier(cfg)
    rc.classify(frame(), blob(**GOOD))
    st = rc.status()
    json.dumps(st)
    assert st["name"] == "rule" and st["n_classified"] == 1 and "good" in st["classes"] and st["rules"]["min_major_mm"] == 8.0
    checks = rc.selftest()
    assert all(isinstance(c, Check) for c in checks)
    assert all(c.ok for c in checks if "speed" not in c.name), [c.detail for c in checks if not c.ok]   # speed depends on machine load
    rc.close()


def test_rule_speed(cfg):
    rc = C.RuleClassifier(cfg)
    b = blob(**GOOD)
    import time
    t0 = time.perf_counter()
    for _ in range(200):
        rc.classify(frame(), b)
    assert (time.perf_counter() - t0) / 200 * 1e3 < 5.0   # ms per bean (generous: shared CPU)


# ------------------------------------------------------------------ dataset + sklearn
FEATS = ["area_mm2", "major_mm", "minor_mm", "aspect", "mean_gray", "dark_frac", "n_dark_spots"]


def make_dataset(root, n=40, seed=0):
    rng = np.random.default_rng(seed)
    gen = {
        "good":    lambda: dict(major_mm=rng.normal(11.5, 0.7), minor_mm=rng.normal(7.5, 0.5), mean_gray=rng.normal(95, 10), dark_frac=abs(rng.normal(0.04, 0.02)), n_dark_spots=0),
        "defect":  lambda: dict(major_mm=rng.normal(11, 1.0), minor_mm=rng.normal(7.2, 0.6), mean_gray=rng.normal(35, 8), dark_frac=rng.uniform(0.5, 0.95), n_dark_spots=int(rng.integers(0, 3))),
        "foreign": lambda: dict(major_mm=rng.normal(22, 3), minor_mm=rng.normal(4, 1), mean_gray=rng.normal(120, 20), dark_frac=abs(rng.normal(0.1, 0.05)), n_dark_spots=0),
    }
    for label, g in gen.items():
        d = root / label
        d.mkdir(parents=True)
        for i in range(n):
            f = g()
            f["aspect"] = f["major_mm"] / max(f["minor_mm"], 0.1)
            f["area_mm2"] = math.pi / 4 * f["major_mm"] * f["minor_mm"]
            payload = {"features": f, "label": label, "path": f"{label}/{i}.png", "t": float(i)} if i % 2 else f  # Sample-like or flat
            (d / f"{i:03d}.json").write_text(json.dumps(payload))
    (root / "good" / "junk.json").write_text("not json")   # must be skipped
    return root


def test_load_dataset_both_layouts(tmp_path):
    make_dataset(tmp_path / "beans", n=6)
    X, y, feats = C.load_dataset(tmp_path / "beans")
    assert X.shape == (18, len(feats)) and set(y) == {"good", "defect", "foreign"}
    assert set(feats) == set(FEATS)
    assert feats.index("area_mm2") < feats.index("major_mm")   # coffee_sorter FEATURES order preferred


def test_train_evaluate_and_classify(tmp_path, cfg):
    ds = make_dataset(tmp_path / "beans", n=40)
    out = tmp_path / "models" / "beans_v1.joblib"
    model, report = C.train_from_dataset(ds, out)
    assert out.exists() and (out.parent / "report.json").exists()
    assert report["n_train"] == 120 and report["accuracy"] > 0.9 and "cross-validation" in report["cv"]
    assert report["classes"][0] == "good" and set(report["classes"]) == {"good", "defect", "foreign"}
    assert math.isfinite(report["anomaly_thresh"]) and report["predict_ms_single"] < 200   # wall clock: other agents run tests concurrently

    ev = C.evaluate(ds, out, verbose=False)
    assert ev["accuracy"] > 0.95 and sum(map(sum, ev["confusion"])) == 120

    cfg.classifier.model_path = str(out)
    sk = C.SklearnClassifier(cfg=cfg)
    assert sk.classes[0] == "good" and set(sk.features) == set(FEATS)
    v = sk.classify(frame(), blob(**{k: GOOD[k] for k in GOOD}, area_mm2=math.pi / 4 * 11.5 * 7.5))
    assert v.label == "good" and not v.suspect and v.p_defect < 0.5 and v.classifier == "sklearn"
    v2 = sk.classify(frame(), blob(major_mm=11.0, minor_mm=7.0, aspect=1.57, mean_gray=30.0, dark_frac=0.8, n_dark_spots=1, area_mm2=60.0))
    assert v2.label == "defect" and v2.suspect and v2.p_defect > 0.5 and "p_defect=" in v2.reason
    v3 = sk.classify(frame(), blob(major_mm=24.0, minor_mm=3.5, aspect=6.9, mean_gray=120.0, dark_frac=0.1, n_dark_spots=0, area_mm2=66.0))
    assert v3.suspect and v3.label in ("foreign", "unknown")
    assert 0.0 <= v.p_defect <= 1.0 and 0.0 <= v3.p_defect <= 1.0
    assert v.ms < 200 and v2.ms < 200


def test_sklearn_missing_feature_filled_and_flagged(tmp_path, cfg):
    ds = make_dataset(tmp_path / "beans", n=30)
    out = tmp_path / "m.joblib"
    C.train_from_dataset(ds, out)
    sk = C.SklearnClassifier(out, cfg)
    v = sk.classify(frame(), blob(major_mm=11.5, minor_mm=7.5, aspect=1.53, mean_gray=92.0))   # no dark_frac / spots / area
    assert isinstance(v, Verdict)
    st = sk.status()
    json.dumps(st)
    assert st["missing_features"] == {"dark_frac": 1, "n_dark_spots": 1, "area_mm2": 1}
    assert st["n_classified"] == 1 and st["model_path"] == str(out) and st["n_train"] == 90


def test_sklearn_anomaly_gate_and_selftest(tmp_path, cfg):
    ds = make_dataset(tmp_path / "beans", n=40)
    out = tmp_path / "m.joblib"
    C.train_from_dataset(ds, out)
    sk = C.SklearnClassifier(out, cfg)
    # a blob unlike anything in the training set: anomaly must make it suspect even if the trees are unsure
    v = sk.classify(frame(), blob(major_mm=60.0, minor_mm=58.0, aspect=1.03, mean_gray=200.0, dark_frac=0.0, n_dark_spots=0, area_mm2=2700.0))
    assert v.suspect and "anomaly" in v.reason
    checks = sk.selftest()
    assert all(c.ok for c in checks if "speed" not in c.name), [c.detail for c in checks if not c.ok]   # speed depends on machine load
    sk.close()


def test_fit_with_few_samples_does_not_crash():
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(0, 1, (3, 4)), rng.normal(5, 1, (3, 4))])
    y = np.array(["good"] * 3 + ["defect"] * 3, dtype=object)
    model, report = C.fit(X, y, ["a", "b", "c", "d"])
    assert "fold" in report["cv"] or "training set" in report["cv"]
    assert model.anomaly_thresh == float("inf")   # < 5 good samples: anomaly gate disabled
    P, a = model.predict(X)
    assert P.shape == (6, 2) and np.all(np.isfinite(a))


def test_resolve_relative_to_line_dir(cfg):
    p = C._resolve(None, cfg)
    assert p.is_absolute() and p.parent.name == "models" and p.parent.parent == C.LINE_DIR


def test_minor_rules_are_optional_and_blur_tolerant(cfg):
    rc = C.RuleClassifier(cfg)
    smeared = {**GOOD, "major_mm": 23.0, "aspect": 3.1}          # 11 mm bean + 12 mm motion blur, width intact
    assert rc.classify(frame(), blob(**smeared)).label in ("foreign", "odd_shape")   # length rules alone reject it
    # operator relaxes the length rules and relies on the width instead
    cfg.classifier.rules.update(min_minor_mm=5.0, max_minor_mm=11.0, max_major_mm=40.0, max_aspect=6.0)
    v = rc.classify(frame(), blob(**smeared))
    assert v.label == "good" and not v.suspect
    v_wide = rc.classify(frame(), blob(**{**smeared, "minor_mm": 14.0}))
    assert v_wide.suspect and v_wide.label == "foreign" and "minor_mm 14.0 mm > 11.0 mm" in v_wide.reason
    v_thin = rc.classify(frame(), blob(**{**smeared, "minor_mm": 3.0}))
    assert v_thin.suspect and v_thin.label == "fragment"
    assert "min_minor_mm" not in dict(C.RuleClassifier(LineConfig()).rules)   # default config: rule inactive


def test_load_dataset_skips_partial_blobs(tmp_path):
    make_dataset(tmp_path / "beans", n=4)
    part = {"features": dict(major_mm=90.0, minor_mm=20.0, aspect=4.5, mean_gray=90.0, dark_frac=0.07, n_dark_spots=1, area_mm2=1200.0),
            "partial": True, "label": "good", "verdict": None}
    (tmp_path / "beans" / "good" / "partial.json").write_text(json.dumps(part))
    X, y, _ = C.load_dataset(tmp_path / "beans")
    assert len(y) == 12 and C.load_dataset.skipped_partial == 1
    X2, y2, _ = C.load_dataset(tmp_path / "beans", include_partial=True)
    assert len(y2) == 13
