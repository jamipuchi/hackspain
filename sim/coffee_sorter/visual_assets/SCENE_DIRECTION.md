# Scene direction: cameras, looks, lighting

Visual direction for the full simulated conveyor in a cinematic industrial studio. `scene_direction.json` holds every number. `scene_lighting.py` applies it. Taras owns visual acceptance.

```python
import scene_lighting
settings = scene_lighting.configure_shot("hero", "noir-rim")   # returns a JSON-serializable dict
```

`configure_shot` creates the active camera, the world, the area lights, the colour management, and the look's material override. It does not delete meshes, move beans, edit geometry, render, or save. A second call replaces only its own camera and lights (collection `SceneDirection`). It replaces `scene.world` and the view-layer `material_override`, so call it after the scene is built.

## Three looks, three roles

Taras picked these looks from the moodboard ([MOODBOARD.md](MOODBOARD.md)). Each look has one role. Do not mix two looks in one shot.

| Look | Role in the clip | What it can and cannot show |
|---|---|---|
| `noir-rim` | Hero and realism. Dark studio, cool rim strip from behind, small warm key, low cool fill. Machine and bean materials stay intact. | The only look that shows colour defects (black, sour, faded). Use it for every shot where a defect class or a sorting decision must be visible. |
| `blueprint` | Explainer. One blue material on everything, with bright shader lines on panel edges and silhouettes. | Shows how the machine works: belt, scan line, nozzle bank, splitter. It shows no bean colour, so it cannot show why a bean was rejected. |
| `warm-roastery` | Form and composition. Warm window light on one matte grey clay material. | Intro, transitions, layout review. Shape defects (broken, insect bores) stay visible. Colour defects do not. |

Lighting choices that answer the brief:

- Large soft sources and raking angles show surface texture and panel edges. Fill lights have `glossy: false`, so they add light without a second highlight. This avoids the glossy-plastic look.
- `noir-rim` uses AgX Medium High Contrast, not the High Contrast of the moodboard tile. High Contrast may hide black beans in the shadows. This contrast comparison remains unverified. A top `Belt pool` light keeps them readable.
- Rim lights sit higher than the camera's mirror angle over the belt. This limits belt glare.
- `blueprint` lines come from the shader: a Bevel-normal test marks edges and a facing test marks silhouettes. Freestyle is supported but off (`looks.blueprint.freestyle.enabled`). Its cost grows with triangle count, and the replay holds several hundred 12,000-triangle beans. The line width and silhouette range use `shots.<name>.blueprint`. A height mask prevents the studio floor from emitting white outlines.

## Shots

| Key | Camera | Purpose |
|---|---|---|
| `hero` | 50 mm, f/5.6, 4.7 m from the machine, front side, discharge end nearest | Entire machine. It fills about 58% of the width and 62% of the height at 16:9, so a 2.39:1 crop also holds. |
| `inspection` | 20 mm, f/11, 5 cm above the belt at the belt end, diagonal across the belt | Sharp beans at the scan line below, light bars and housing underside receding above. |
| `discharge` | 35 mm, f/8, three-quarter from the discharge end, above the near chute wall | Nozzle bank across the upper third, belt end behind it, splitter in the centre and lower right. |
| `inspection-station` | 32 mm, f/5.6, optional fallback | Whole inspection station. Beans are about 20 px wide at 1920 px, so it shows no bean surface. |

The sensor fit is horizontal, so the horizontal framing survives an aspect change. The hero was framed against the tight bounds of `replay['machine']`: X -1.192 to 0.763, Y ±0.32, Z 0 to 1.05 m. Added machine detail can exceed these bounds.

One geometric limit: bean surface and camera hardware do not fit one frame at macro scale. The housing is 0.42 m above beans that are 1 cm long. `inspection` is the compromise. Its sharpest beans are about 90 px wide at 1920 px, which shows crease and colour but not skin texture. Bean skin detail belongs to the macro stills (`good`, `defects` in the moodboard).

## Calibration status

I calibrated framing and exposure on a throwaway stand-in scene: the replay primitives with simple materials and beans from the 2.0 s sample, at 640×360 and 12 samples. I did not calibrate on the integrated machine. Expect one correction pass after the first full-scene proof. The `inspection` silhouette range was computed after the last preview and was not re-rendered.

Useful knobs, all in the JSON: `energy_w` and `spread_deg` per light, `exposure_offset` per rig, `world.camera_color` (the backdrop the camera sees, separate from the world light).

## Assumptions and open points

1. **Floor.** The integrated scene extends the recorded floor to 400 × 400 m with adjoining slabs. The original slab retains its dimensions.
2. **Overrides replace every material.** In `blueprint` and `warm-roastery` the transparent chute walls become opaque, and emissive parts stop emitting. In `discharge` the near wall then covers part of the splitter. The integrated still preserves every recorded wall. Its current chute materials are opaque in every look. The returned dict flags this as `override_replaces_transparent_materials`.
3. **Emissive light bars.** At emission strength 5 the stand-in bars clipped to white in `inspection` with `noir-rim`. Keep their strength near 1 to 2, or send me the value and I will offset the rig exposure.
4. **Beans near the lens.** The `inspection` camera sits where beans leave the belt. On some frames a bean can cover much of the lens. Choose the frame, or raise the camera z.
5. **Noir floor.** If the floor is the brightest element in the noir hero, reduce `Warm key` energy or darken the floor material. Do not raise the contrast.
6. **Blender.** Checked on Blender 5.2.2 LTS: all 12 shot and look pairs apply, and no mesh is removed. Not run on 4.5.4. The API used (area light spread, Bevel node, `visible_glossy`) exists in 4.x.

## Remaining realism limits

Cycles path tracing does not make this scene hyperrealistic. These limits remain:

- **Beans.** Procedural prototypes, not scans. One geometry per class, albedo variation of at most ±3%, so repetition is visible in a crowd. Only good, black, insect-damaged, and broken have detailed assets. Faded, sour, shell, and husk are the good silhouette with a class colour. Stone is a box and stick is a capsule.
- **Machine.** Built from simulator primitives plus the detail `scene_machine.py` adds. No CAD data and no reference photos. Materials are informed guesses. A later physical rig needs reference photos before its render can claim likeness.
- **Motion.** The replay is sampled near 30 Hz, so motion blur stays off (see [RECORDING.md](RECORDING.md)). Beans at 3 m/s will look frozen. Rollers and the belt texture do not move.
- **Air jets.** The still does not draw air or animate valves. The earlier recording renderer shows sampled valve indicators. Short pulses can fall between samples.
- **Light.** This is studio lighting for a film. It is not the inspection camera's illumination and says nothing about classifier accuracy.
- **No wear.** No dust, scratches, fingerprints, chaff, or belt wear. Clean surfaces are the most visible gap between this scene and a photograph.
