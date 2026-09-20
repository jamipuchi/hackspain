import pathlib
import sys

import bpy


def output_root() -> pathlib.Path:
    try:
        separator = sys.argv.index("--")
        path = pathlib.Path(sys.argv[separator + 1])
    except (ValueError, IndexError) as error:
        raise SystemExit("usage: blender --python blender_smoke.py -- OUTPUT_DIR") from error
    path.mkdir(parents=True, exist_ok=True)
    return path


root = output_root()
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 1
scene.render.resolution_x = 64
scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = str(root / "smoke.png")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

bpy.ops.mesh.primitive_cube_add(size=0.02, location=(0.0, 0.0, 0.0))
cube = bpy.context.object
cube.name = "CINTA smoke cube"

material = bpy.data.materials.new(name="CINTA smoke material")
material.diffuse_color = (0.13, 0.48, 0.22, 1.0)
cube.data.materials.append(material)

bpy.ops.object.camera_add(location=(0.0, 0.0, 0.08))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.04
camera.data.clip_start = 0.001
scene.camera = camera

bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, 0.05))
light = bpy.context.object
light.data.energy = 250.0
light.data.shape = "DISK"
light.data.size = 0.05

scene.render.film_transparent = True
bpy.ops.render.render(write_still=True)
saved_render = bpy.data.images.load(str(root / "smoke.png"), check_existing=False)
try:
    alpha = list(saved_render.pixels)[3::4]
    if sum(value >= 0.5 for value in alpha) < 16:
        raise RuntimeError("Blender smoke render has no visible object")
finally:
    bpy.data.images.remove(saved_render)

bpy.ops.object.select_all(action="DESELECT")
bpy.context.view_layer.objects.active = cube
cube.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str(root / "smoke.glb"),
    export_format="GLB",
    use_selection=True,
)

for name in ("smoke.png", "smoke.glb"):
    path = root / name
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"missing Blender smoke output: {path}")
