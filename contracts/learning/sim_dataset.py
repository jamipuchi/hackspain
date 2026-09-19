"""Generate small image-only demonstration datasets from the THEKER simulation."""

from __future__ import annotations

import json
import math
import os
import sys
from argparse import ArgumentParser
from pathlib import Path

import cv2
import mujoco
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
SIM_ROOT = ROOT / "sim" / "magnet_sorter"
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))
os.environ["SORTER_BUILD"] = "theker_v1"

import scene_def as sd  # noqa: E402
from camera import MujocoPhoneCamera, TableCalibration  # noqa: E402
from hardware import SimArduino  # noqa: E402
from mujoco_model import build_xml  # noqa: E402


FIXED_REGION_CM = (5.5, -5.5, 10.5, 5.5)
SPLIT_SEEDS = {
    "train": 1701,
    "correction": 2701,
    "validation": 3701,
    "test": 4701,
}
M3_FIXTURES = {
    "screw": "screw_m3x20_zinc",
    "nut": "nut_m3_zinc",
    "washer": "washer_m3_zinc",
}
M4_FIXTURES = {
    "screw": "screw_m4x20_zinc",
    "nut": "nut_m4_zinc",
    "washer": "washer_m4_zinc",
}
SPLITS = (
    ("train", M3_FIXTURES, 1),
    ("correction", M3_FIXTURES, 6),
    ("validation", M3_FIXTURES, 6),
    ("test", M4_FIXTURES, 10),
)


def extract_features(image: np.ndarray) -> dict[str, object]:
    """Return contour measurements that depend only on the supplied pixels."""
    if image.size == 0:
        return _empty_features()
    if image.ndim not in (2, 3) or min(image.shape[:2]) < 3:
        return _empty_features()
    mask = _foreground_mask(image)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    candidates = [contour for contour in contours if cv2.contourArea(contour) >= 16.0]
    if not candidates:
        return _empty_features()

    contour = max(candidates, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    _, _, width, height = cv2.boundingRect(contour)
    _, (rect_width, rect_height), _ = cv2.minAreaRect(contour)
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    hole_area = _hole_area(contours, hierarchy, contour)
    _, radius = cv2.minEnclosingCircle(contour)
    hull = cv2.convexHull(contour).reshape(-1, 2).astype(np.float32)
    hull_center = hull.mean(axis=0)
    radii = np.linalg.norm(hull - hull_center, axis=1)
    values = {
        "aspect_ratio": float(max(rect_width, rect_height) / max(1.0, min(rect_width, rect_height))),
        "solidity": float(area / hull_area) if hull_area else 0.0,
        "circularity": float(4.0 * math.pi * area / (perimeter * perimeter)) if perimeter else 0.0,
        "hole_fraction": float(hole_area / area) if area else 0.0,
        "extent": float(area / (width * height)) if width and height else 0.0,
        "enclosing_circle_fill": float(area / (math.pi * radius * radius)) if radius else 0.0,
        "hull_radial_variance": float(np.var(radii) / (np.mean(radii) ** 2)) if np.mean(radii) else 0.0,
        "area_px": area,
        "foreground_fraction": float(np.count_nonzero(mask) / mask.size),
    }
    valid = area < image.shape[0] * image.shape[1] * 0.30
    return {"names": list(values), "values": values, "valid": valid}


def generate_dataset(out_dir: str | Path) -> list[dict[str, object]]:
    """Render the fixed table region for M3 demonstrations and held-out M4 variants."""
    out_path = Path(out_dir)
    image_dir = out_path / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    sd.configure("theker_v1")
    bodies = sd.build()
    pieces = [body for body in bodies if body.piece]
    body_by_name = {body.name: body for body in pieces}
    model = mujoco.MjModel.from_xml_string(build_xml())
    data = mujoco.MjData(model)
    camera = MujocoPhoneCamera(model, data, "A")
    records: list[dict[str, object]] = []

    for split, fixtures, count in SPLITS:
        rng = np.random.default_rng(SPLIT_SEEDS[split])
        for expected_kind, body_name in fixtures.items():
            for index in range(count):
                x_m = 0.080 + float(rng.uniform(-0.010, 0.010))
                y_m = float(rng.uniform(-0.010, 0.010))
                angle = float(rng.uniform(0.0, math.tau))
                _reset_scene(model, data, pieces, body_by_name[body_name], x_m, y_m, angle)
                arduino = SimArduino(model, data, pieces)
                _settle(model, data, arduino)
                frame = camera.grab()
                calibration = TableCalibration()
                if not calibration.fit(frame):
                    raise RuntimeError("THEKER camera A did not detect enough ArUco markers")
                crop = _fixed_table_crop(frame, calibration)
                feature_result = extract_features(crop)
                sample_id = f"{split}_{expected_kind}_{index:02d}"
                relative_image = Path("images") / split / f"{sample_id}.png"
                relative_mask = Path("masks") / split / f"{sample_id}.png"
                image_path = out_path / relative_image
                mask_path = out_path / relative_mask
                image_path.parent.mkdir(parents=True, exist_ok=True)
                mask_path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(image_path), crop):
                    raise RuntimeError(f"could not write {image_path}")
                if not cv2.imwrite(str(mask_path), _foreground_mask(crop)):
                    raise RuntimeError(f"could not write {mask_path}")
                records.append(
                    {
                        "sample_id": sample_id,
                        "split": split,
                        "image": relative_image.as_posix(),
                        "features": feature_result["values"],
                        "valid": feature_result["valid"],
                        "fixture": {
                            "body_name": body_name,
                            "x_cm": round(x_m * 100, 3),
                            "y_cm": round(y_m * 100, 3),
                            "rotation_rad": round(angle, 6),
                        },
                        "evaluator": {
                            "expected_kind": expected_kind,
                            "label_source": "simulated_demonstration",
                        },
                    }
                )

    (out_path / "records.json").write_text(json.dumps(records, indent=2) + "\n")
    for split in SPLIT_SEEDS:
        split_records = [record for record in records if record["split"] == split]
        (out_path / f"{split}.json").write_text(json.dumps(split_records, indent=2) + "\n")
    _save_contact_sheet(out_path, records)
    return records


def _empty_features() -> dict[str, object]:
    values = {
        "aspect_ratio": 0.0,
        "solidity": 0.0,
        "circularity": 0.0,
        "hole_fraction": 0.0,
        "extent": 0.0,
        "enclosing_circle_fill": 0.0,
        "hull_radial_variance": 0.0,
        "area_px": 0.0,
        "foreground_fraction": 0.0,
    }
    return {"names": list(values), "values": values, "valid": False}


def _foreground_mask(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        border = np.concatenate((image[0], image[-1], image[:, 0], image[:, -1]))
        contrast = cv2.absdiff(image, np.full_like(image, int(np.median(border))))
        _, mask = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    else:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 120), (180, 75, 255))
    margin = max(2, min(mask.shape) // 50)
    mask[:margin] = 0
    mask[-margin:] = 0
    mask[:, :margin] = 0
    mask[:, -margin:] = 0
    return mask


def _hole_area(contours: list[np.ndarray], hierarchy: np.ndarray | None, outer: np.ndarray) -> float:
    if hierarchy is None:
        return 0.0
    outer_index = next(index for index, contour in enumerate(contours) if contour is outer)
    child_index = int(hierarchy[0][outer_index][2])
    total = 0.0
    while child_index >= 0:
        total += float(cv2.contourArea(contours[child_index]))
        child_index = int(hierarchy[0][child_index][0])
    return total


def _reset_scene(model, data, pieces, subject, x_m: float, y_m: float, angle: float) -> None:
    mujoco.mj_resetData(model, data)
    for offset, body in enumerate(pieces):
        address = model.jnt_qposadr[model.joint(f"{body.name}_free").id]
        data.qpos[address : address + 7] = [-0.60 - offset * 0.03, 0.0, -0.74, 1, 0, 0, 0]
    address = model.jnt_qposadr[model.joint(f"{subject.name}_free").id]
    height = sd.SURFACE_Z + sd.piece_half_height(subject) + 0.004
    data.qpos[address : address + 7] = [
        x_m,
        y_m,
        height,
        math.cos(angle / 2),
        0,
        0,
        math.sin(angle / 2),
    ]
    mujoco.mj_forward(model, data)


def _settle(model, data, arduino: SimArduino, steps: int = 100) -> None:
    for _ in range(steps):
        mujoco.mj_step(model, data)
        arduino.tick()


def _fixed_table_crop(frame: np.ndarray, calibration: TableCalibration) -> np.ndarray:
    x0, y0, x1, y1 = FIXED_REGION_CM
    pixels = [
        calibration.table_to_pixel(x0 / 100, y0 / 100),
        calibration.table_to_pixel(x0 / 100, y1 / 100),
        calibration.table_to_pixel(x1 / 100, y0 / 100),
        calibration.table_to_pixel(x1 / 100, y1 / 100),
    ]
    height, width = frame.shape[:2]
    left = max(0, min(point[0] for point in pixels))
    right = min(width, max(point[0] for point in pixels))
    top = max(0, min(point[1] for point in pixels))
    bottom = min(height, max(point[1] for point in pixels))
    if right <= left or bottom <= top:
        raise RuntimeError("calibrated inspection region is outside camera A")
    return frame[top:bottom, left:right].copy()


def _save_contact_sheet(out_path: Path, records: list[dict[str, object]]) -> None:
    thumbnail_size = (160, 160)
    columns = 6
    rows = math.ceil(len(records) / columns)
    sheet = Image.new("RGB", (columns * thumbnail_size[0], rows * 184), "white")
    draw = ImageDraw.Draw(sheet)
    for index, record in enumerate(records):
        with Image.open(out_path / str(record["image"])) as source:
            image = source.convert("RGB")
        image.thumbnail(thumbnail_size)
        x = (index % columns) * thumbnail_size[0]
        y = (index // columns) * 184
        sheet.paste(image, (x + (thumbnail_size[0] - image.width) // 2, y))
        draw.text((x + 4, y + 164), str(record["sample_id"]), fill="black")
    sheet.save(out_path / "contact_sheet.png")


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    records = generate_dataset(args.out_dir)
    print(f"wrote {len(records)} simulated demonstration records to {args.out_dir}")


if __name__ == "__main__":
    main()
