"""Render the one-slide summary as a 1920x1080 PNG.

Edit SLIDE below, then run from sim/coffee_sorter:

    python slide/make_slide.py

Needs Pillow and the DejaVu fonts (both ship with most Linux images).
The hero image is a decoded HUD frame from the seed-1, 0.06 N demo run.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HERO = ROOT / "runs/confirmation-seed1/force-0.06/hud_frame100.png"
OUT = HERE / "one_slide.png"

SLIDE = {
    "kicker": "THEKER Robotics · HackSpain '26 · team growth",
    "title": "Optical coffee sorter, simulated end to end",
    "problem": (
        "People still hand-pick defective green coffee off tables and slow belts, "
        "all day. Every bean differs in size, colour, shade and orientation."
    ),
    "terminal": (
        "Belt sorter in MuJoCo: vibratory feeder, 3 m/s belt, 250 fps line camera, "
        "64 air-jet valves, splitter, accept and reject bins. Thousands of beans per second."
    ),
    "loop": (
        "Camera pixels → segmentation → learned classifier + open-set anomaly detector "
        "→ time-of-flight valve scheduling → jets push whatever is physically there."
    ),
    "headline_big": "98% → 52%",
    "headline_small": (
        "Classifier holdout accuracy 97.9%, yet only 51.5% of defects physically reach "
        "the reject bin (4.3% good beans lost, 1,000 beans/s, 2,600 beans, seed 1). "
        "The simulator measures the whole pipeline, not the model."
    ),
    "general": (
        "New product = new profile + retrain, zero controller changes: roasted runs at "
        "39.5% defect capture, 3.8% loss. Never-seen colours are flagged 100%. "
        "Oversize is flagged 0.5%: that is the job for an arm, not air jets."
    ),
    "footer": "Simulation only. No real camera, coffee or hardware timing is claimed.",
}

W, H = 1920, 1080
BG = (251, 251, 250)
INK = (28, 28, 30)
MUTED = (100, 100, 105)
ACCENT = (11, 94, 122)
RULE = (222, 221, 217)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def block(draw, x, y, label, body, width, body_size=26, label_size=18, gap=6) -> int:
    draw.text((x, y), label.upper(), font=font(label_size, True), fill=ACCENT)
    y += label_size + gap + 4
    fnt = font(body_size)
    for line in wrap(draw, body, fnt, width):
        draw.text((x, y), line, font=fnt, fill=INK)
        y += int(body_size * 1.32)
    return y


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    m = 72
    col_w = 880

    d.text((m, m - 8), SLIDE["kicker"], font=font(20), fill=MUTED)
    d.text((m, m + 26), SLIDE["title"], font=font(52, True), fill=INK)
    d.line([(m, m + 108), (W - m, m + 108)], fill=RULE, width=2)

    y = m + 136
    y = block(d, m, y, "The problem", SLIDE["problem"], col_w) + 22
    y = block(d, m, y, "The terminal", SLIDE["terminal"], col_w) + 22
    y = block(d, m, y, "The loop", SLIDE["loop"], col_w) + 22
    y = block(d, m, y, "Generalisation", SLIDE["general"], col_w) + 22

    # Right column: hero image with headline number beneath it.
    rx = m + col_w + 64
    rw = W - m - rx
    hero = Image.open(HERO).convert("RGB")
    scale = rw / hero.width
    hero = hero.resize((rw, int(hero.height * scale)))
    hy = m + 136
    img.paste(hero, (rx, hy))
    d.rectangle([rx, hy, rx + rw - 1, hy + hero.height - 1], outline=RULE, width=2)

    ny = hy + hero.height + 28
    d.text((rx, ny), "HEADLINE", font=font(18, True), fill=ACCENT)
    d.text((rx, ny + 26), SLIDE["headline_big"], font=font(84, True), fill=INK)
    ty = ny + 26 + 100
    fnt = font(22)
    for line in wrap(d, SLIDE["headline_small"], fnt, rw):
        d.text((rx, ty), line, font=fnt, fill=MUTED)
        ty += 30

    d.line([(m, H - m + 4), (W - m, H - m + 4)], fill=RULE, width=2)
    d.text((m, H - m + 16), SLIDE["footer"], font=font(18), fill=MUTED)
    img.save(OUT, optimize=True)
    print(OUT, img.size)


if __name__ == "__main__":
    main()
