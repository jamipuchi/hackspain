# Render moodboard

A cheap way to compare render directions before full stills or the film. One Blender process renders every shot in every look, then writes a static contact sheet. Open [moodboard/index.html](moodboard/index.html) in a browser.

Taras owns visual acceptance. The tiles are drafts for choosing a direction. They are not final renders, classifier validation, or simulation output. Bean positions are staged.

## Decision (2026-09-19)

Taras picked three go-to looks from 4 shots and 5 looks:

| Look | Mode | What it is |
|---|---|---|
| `noir-rim` | textured | Near-black set, cool rim light from behind, small warm key. |
| `blueprint` | textured | One blue material plus Freestyle outlines. |
| `warm-roastery` | clay | Warm window light on one matte grey material. |

Taras wants to use all three together. The role of each look in the clip is not decided yet.
`moodboard.json` records the picks under `picks`. The page shows them first.

Known limits of the picks:

- Clay and blueprint use one material. They cannot show colour defects such as black or faded beans. Shape defects (broken, insect bores) remain visible. Use `noir-rim` when the defect class must be visible by colour.
- `noir-rim` and `warm-roastery` recolour the belt. The simulator belt is blue. This affects the film only, never the inspection camera.

## Run

Drafts were rendered with Blender 5.2.2 LTS on macOS (`brew install --cask blender`). The asset scripts were delivered with Blender 4.5.4 LTS and run unchanged.

```bash
cd sim/coffee_sorter/visual_assets
blender --background --python moodboard.py --                                       # every shot x look, textured and clay
blender --background --python moodboard.py -- --looks noir-rim --shots conveyor-low  # rerender a subset
blender --background --python moodboard.py -- --final                                # picked looks only, 1400x1000, 96 samples
python3 moodboard.py --html-only                                                     # rebuild the page, no Blender needed
```

Measured on an Apple M2 Pro, 10 CPU threads: a 960×720 draft at 24 samples takes about 5 s. The 40 draft tiles took 216 s in total. The 12 final renders of the picks (1400×1000, 96 samples) took 271 s, 19 to 27 s each. `moodboard/tiles.json` records the time, samples, and resolution of every tile.

## Edit

`moodboard.json` holds everything. `moodboard.py` does not edit `render_stills.py`. It reuses its staging functions.

- A **shot** names a staging function and can replace its camera.
- A **look** sets lights, world colour, material overrides, AgX look, exposure, and optional Freestyle outlines. `"lights": "shot"` keeps the stage's own lights. `lab-daylight` uses that as the control column.
- Lights are relative to the camera and the staged beans. Energy scales with distance squared, so one look keeps its exposure across shots of different scale.
- **Clay** mode replaces every material with one grey material. It shows composition and light without texture bias.
- To add reference images, put them in `moodboard/refs/` and rebuild the page.

Blender prints `Error: strokes set empty` for `blueprint` tiles. The outlines still render.
