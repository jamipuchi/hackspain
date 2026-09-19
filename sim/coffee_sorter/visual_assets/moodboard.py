#!/usr/bin/env python3
"""Render a shot x look moodboard of cheap Cycles drafts and write one static contact sheet.

Shots, looks and the picked go-to looks live in moodboard.json. Each draft tile renders
twice: textured, and clay (one grey material) so composition and light can be judged
without texture bias.

Run with Blender:
  blender --background --python moodboard.py --
  blender --background --python moodboard.py -- --looks noir-rim --shots conveyor,conveyor-low
  blender --background --python moodboard.py -- --final     # picked looks only, 1400x1000, 96 samples
Rebuild only the page without Blender, for example after adding images to moodboard/refs/:
  python3 moodboard.py --html-only
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import sys
import time


HERE = Path(__file__).resolve().parent
MODES = ("textured", "clay")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def tile_name(shot, look, mode, final=False):
    return f"{shot}__{look}__{mode}{'__final' if final else ''}.jpg"


# ---------------------------------------------------------------- rendering

def set_world(color, strength):
    import bpy
    world = bpy.context.scene.world
    world.color = color
    background = world.node_tree.nodes.get("Background") if world.node_tree else None
    if background:
        background.inputs["Color"].default_value = (*color, 1.0)
        background.inputs["Strength"].default_value = strength


def staged_center():
    import bpy
    from mathutils import Vector
    beans = bpy.data.collections["StagedBeans"].objects
    return sum((obj.location for obj in beans), Vector()) / len(beans)


def replace_camera(rs, spec):
    import bpy
    from mathutils import Vector
    bpy.data.objects.remove(bpy.context.scene.camera, do_unlink=True)
    focus = (Vector(spec["location"]) - Vector(spec["target"])).length
    rs.add_camera(spec["location"], spec["target"], spec["lens"], spec.get("fstop", 16), focus)
    bpy.context.scene.camera.data.dof.use_dof = bool(spec.get("dof"))


def replace_lights(rs, lights):
    import bpy
    from mathutils import Vector
    for obj in [obj for obj in bpy.data.objects if obj.type == "LIGHT"]:
        bpy.data.objects.remove(obj, do_unlink=True)
    target = staged_center()
    to_camera = bpy.context.scene.camera.location - target
    camera_azimuth = math.atan2(to_camera.y, to_camera.x)
    for light in lights:
        # Positive azimuth moves the light toward camera right.
        azimuth = camera_azimuth + math.radians(light["azimuth"])
        elevation = math.radians(light["elevation"])
        distance = light["distance"] * to_camera.length
        direction = Vector((math.cos(elevation) * math.cos(azimuth),
                            math.cos(elevation) * math.sin(azimuth), math.sin(elevation)))
        rs.add_area(light["name"], target + direction * distance, target,
                    light["strength"] * distance ** 2, light["size"] * to_camera.length,
                    light["color"])


def add_freestyle(spec):
    import bpy
    scene = bpy.context.scene
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "ABSOLUTE"
    scene.render.line_thickness = spec["thickness"]
    view_layer = bpy.context.view_layer
    view_layer.use_freestyle = True
    view_layer.freestyle_settings.crease_angle = math.radians(100)
    lineset = view_layer.freestyle_settings.linesets.new("Outlines")
    lineset.select_silhouette = lineset.select_border = lineset.select_crease = True
    lineset.linestyle.color = spec["color"]
    lineset.linestyle.thickness = spec["thickness"]


def apply_look(rs, look):
    import bpy
    scene = bpy.context.scene
    if "world" in look:
        set_world(look["world"], look.get("world_strength", 1.0))
    if "view_look" in look:
        scene.view_settings.look = look["view_look"]
    if "exposure" in look:
        scene.view_settings.exposure = look["exposure"]
    for name, override in look.get("materials", {}).items():
        mat = bpy.data.materials.get(name)
        if mat is None:
            continue  # not every stage has every material
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (*override["color"], 1.0)
        bsdf.inputs["Roughness"].default_value = override["roughness"]
    if look["lights"] != "shot":
        replace_lights(rs, look["lights"])
    if "freestyle" in look:
        add_freestyle(look["freestyle"])
    override = look.get("material_override")
    return rs.material("Look override", override["color"], override["roughness"]) if override else None


def render_matrix(args, config):
    import bpy
    sys.path.insert(0, str(HERE))
    import render_stills as rs
    from build_assets import make_assets

    tiles_dir = args.out / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out / "tiles.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else {}
    stages = {"good": rs.stage_good, "defects": rs.stage_defects, "conveyor": rs.stage_conveyor}
    picks = config.get("picks", {}).get("looks", {})

    for shot_name in args.shots:
        shot = config["shots"][shot_name]
        for look_name in args.looks:
            # Looks mutate lights and materials, so every pair starts from a clean scene.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            prototypes = make_assets(export_obj=False)
            beans = bpy.data.collections.new("StagedBeans")
            bpy.context.scene.collection.children.link(beans)
            stages[shot["stage"]](prototypes, beans)
            prototype_collection = bpy.data.collections["CoffeeBeanPrototypes"]
            prototype_collection.hide_render = prototype_collection.hide_viewport = True
            if "camera" in shot:
                replace_camera(rs, shot["camera"])
            use_dof = bpy.context.scene.camera.data.dof.use_dof
            scene = rs.configure_scene(not args.final, args.samples)
            scene.camera.data.dof.use_dof = use_dof  # configure_scene disables it for previews
            if not args.final:  # finals keep configure_scene's 1400x1000
                scene.render.resolution_x, scene.render.resolution_y = args.width, args.width * 3 // 4
            scene.render.image_settings.file_format = "JPEG"
            scene.render.image_settings.quality = 92 if args.final else 90
            look_material = apply_look(rs, config["looks"][look_name])
            clay = rs.material("Clay", (0.55, 0.55, 0.55), 0.9)
            for mode in ([picks[look_name]] if args.final else args.modes):
                bpy.context.view_layer.material_override = clay if mode == "clay" else look_material
                name = tile_name(shot_name, look_name, mode, args.final)
                scene.render.filepath = str(tiles_dir / name)
                started = time.perf_counter()
                bpy.ops.render.render(write_still=True)
                records[name] = {
                    "shot": shot_name, "look": look_name, "mode": mode, "final": args.final,
                    "render_elapsed_seconds": round(time.perf_counter() - started, 2),
                    "samples": args.samples,
                    "resolution": [scene.render.resolution_x, scene.render.resolution_y],
                    "threads": scene.render.threads,
                    "blender_version": bpy.app.version_string,
                }
                records_path.write_text(json.dumps(records, indent=2) + "\n")
                print(f"moodboard tile {name}: {records[name]['render_elapsed_seconds']} s")


# --------------------------------------------------------------------- page

STYLE = """
body{margin:0;padding:28px 32px 60px;background:#1a1a1a;color:#d6d6d2;font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
h1{font-size:18px;font-weight:600;margin:0 0 4px}
p.sub{margin:0 0 18px;color:#8e8e88;max-width:900px}
.bar{position:sticky;top:0;z-index:2;background:#1a1a1a;padding:10px 0;display:flex;gap:8px;align-items:center}
button{background:#2a2a2a;color:#d6d6d2;border:1px solid #3a3a3a;border-radius:4px;padding:5px 12px;font:inherit;cursor:pointer}
button[aria-pressed=true]{background:#d6d6d2;color:#1a1a1a;border-color:#d6d6d2}
.grid{display:grid;gap:10px;align-items:start}
.head{font-weight:600;color:#f0f0ea}.head small,.row small{display:block;font-weight:400;color:#8e8e88;margin-top:2px}
.row{font-weight:600;color:#f0f0ea;padding-top:4px}
.pick{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;background:#d9b36a;color:#1a1a1a;font-size:10px;font-weight:600;vertical-align:1px}
.tile{margin:0}.tile img{width:100%;display:block;border-radius:3px;background:#111;cursor:zoom-in}
.tile figcaption{color:#77776f;font-size:11px;margin-top:3px}
.missing{aspect-ratio:4/3;border:1px dashed #3a3a3a;border-radius:3px;display:flex;align-items:center;justify-content:center;color:#55554f}
h2{font-size:14px;margin:34px 0 8px;color:#f0f0ea}
h3{font-size:13px;margin:18px 0 6px;color:#f0f0ea}h3 small{font-weight:400;color:#8e8e88;margin-left:6px}
.refs{display:flex;flex-wrap:wrap;gap:10px}.refs img{height:220px;border-radius:3px;cursor:zoom-in}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:5;cursor:zoom-out}
#zoom img{max-width:96vw;max-height:96vh}
"""

SCRIPT = """
const zoom=document.querySelector('#zoom'),zoomImg=zoom.querySelector('img');
document.addEventListener('click',e=>{
  if(e.target.closest('#zoom')){zoom.style.display='none';return;}
  if(e.target.matches('.tile img,.refs img')){zoomImg.src=e.target.src;zoom.style.display='flex';}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')zoom.style.display='none';if(e.key==='c')setMode(mode==='clay'?'textured':'clay');});
let mode='textured';
function setMode(next){mode=next;
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
  document.querySelectorAll('.matrix .tile').forEach(t=>{const img=t.querySelector('img');
    img.src=t.dataset[mode]||img.src;t.querySelector('figcaption').textContent=t.dataset[mode+'Caption']||'not rendered';});}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
"""


def caption(record):
    return (f'{"final" if record.get("final") else "draft"} · {record["mode"]} · {record["render_elapsed_seconds"]} s · '
            f'{record["samples"]} samples · {record["resolution"][0]}×{record["resolution"][1]}')


def figure(source, text, alt, data=""):
    esc = html.escape
    return (f'<figure class="tile"{data}><img loading="lazy" src="{esc(source)}" alt="{esc(alt)}">'
            f'<figcaption>{esc(text)}</figcaption></figure>')


def write_html(out, config):
    records_path = out / "tiles.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else {}
    drafts = [record for record in records.values() if not record.get("final")]
    shots = [name for name in config["shots"] if any(r["shot"] == name for r in drafts)]
    looks = [name for name in config["looks"] if any(r["look"] == name for r in drafts)]
    picks = config.get("picks", {})
    esc = html.escape

    picks_html = ""
    for look, mode in picks.get("looks", {}).items():
        figures = []
        for shot in shots:
            # Prefer the final render; fall back to the draft that the pick was made from.
            for name in (tile_name(shot, look, mode, True), tile_name(shot, look, mode)):
                if name in records:
                    figures.append(figure(f"tiles/{name}", f"{shot} · {caption(records[name])}",
                                          f"{shot} in the {look} look, {mode}"))
                    break
        note = ("Clay keeps this look's lights and replaces every material with one grey material."
                if mode == "clay" else config["looks"][look].get("note", ""))
        picks_html += (f'<h3>{esc(look)} · {esc(mode)}<small>{esc(note)}</small></h3>'
                       f'<div class="grid" style="grid-template-columns:repeat({len(shots)},minmax(0,1fr))">{"".join(figures)}</div>')
    if picks_html:
        picks_html = (f'<h2>Go-to looks</h2><p class="sub">Picked by {esc(picks.get("by", ""))} on {esc(picks.get("decided", ""))}. '
                      f'{esc(picks.get("note", ""))}</p>{picks_html}<h2>All drafts</h2>')

    cells = ["<div></div>"]
    for look in looks:
        badge = f'<span class="pick">go-to · {esc(picks["looks"][look])}</span>' if look in picks.get("looks", {}) else ""
        cells.append(f'<div class="head">{esc(look)}{badge}<small>{esc(config["looks"][look].get("note", ""))}</small></div>')
    for shot in shots:
        cells.append(f'<div class="row">{esc(shot)}<small>{esc(config["shots"][shot].get("note", ""))}</small></div>')
        for look in looks:
            data, first = "", None
            for mode in MODES:
                record = records.get(tile_name(shot, look, mode))
                if not record:
                    continue
                source = f"tiles/{tile_name(shot, look, mode)}"
                data += f' data-{mode}="{esc(source)}" data-{mode}-caption="{esc(caption(record))}"'
                first = first or (source, caption(record))
            cells.append(figure(first[0], first[1], f"{shot} in the {look} look", data) if first
                         else '<div class="missing">not rendered</div>')

    refs_dir = out / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    refs = sorted(path for path in refs_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    refs_html = "".join(f'<img loading="lazy" src="refs/{esc(path.name)}" alt="{esc(path.stem)}" title="{esc(path.stem)}">'
                        for path in refs) or '<p class="sub">No reference images yet.</p>'
    total = sum(record["render_elapsed_seconds"] for record in drafts)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Coffee sorter render moodboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{STYLE}</style></head><body>
<h1>Coffee sorter render moodboard</h1>
<p class="sub">Rows are shots, columns are looks. Drafts are low-sample Cycles renders for choosing a direction.
Bean positions are staged, not simulation output. {len(drafts)} draft tiles, {total:.0f} s total render time.
Click a tile to enlarge. Press <b>c</b> to switch the draft matrix between textured and clay.</p>
{picks_html}
<div class="bar"><button data-mode="textured" aria-pressed="true">Textured</button><button data-mode="clay" aria-pressed="false">Clay</button></div>
<div class="grid matrix" style="grid-template-columns:130px repeat({len(looks)},minmax(0,1fr))">{"".join(cells)}</div>
<h2>References</h2>
<p class="sub">Add images to <code>moodboard/refs/</code>, then run <code>python3 moodboard.py --html-only</code>.</p>
<div class="refs">{refs_html}</div>
<div id="zoom"><img alt=""></div><script>{SCRIPT}</script></body></html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"moodboard page: {out / 'index.html'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=HERE / "moodboard.json")
    parser.add_argument("--out", type=Path, default=HERE / "moodboard")
    parser.add_argument("--shots", help="comma-separated subset; default is every shot")
    parser.add_argument("--looks", help="comma-separated subset; default is every look, or every picked look with --final")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--final", action="store_true",
                        help="render the picked look and mode pairs from moodboard.json at 1400x1000")
    parser.add_argument("--samples", type=int, help="default: 24 for drafts, 96 with --final")
    parser.add_argument("--width", type=int, default=960, help="draft tile width in pixels; height is 3/4 of it")
    parser.add_argument("--html-only", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    picks = config.get("picks", {}).get("looks", {})
    for key in ("shots", "looks"):
        default = list(picks) if key == "looks" and args.final else list(config[key])
        chosen = getattr(args, key).split(",") if getattr(args, key) else default
        unknown = [name for name in chosen if name not in config[key]]
        if unknown:
            parser.error(f"unknown {key}: {', '.join(unknown)}")
        setattr(args, key, chosen)
    if args.final and set(args.looks) - set(picks):
        parser.error("--final renders picked looks only; add the look to picks in moodboard.json first")
    args.modes = args.modes.split(",")
    if set(args.modes) - set(MODES):
        parser.error(f"--modes accepts {', '.join(MODES)}")
    args.samples = args.samples or (96 if args.final else 24)
    args.out = args.out.resolve()
    if not args.html_only:
        render_matrix(args, config)
    write_html(args.out, config)


if __name__ == "__main__":
    main()
