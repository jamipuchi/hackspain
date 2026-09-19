"""Export the CinematicMachine collection as one browser GLB for the live page.

Runs inside Blender:  blender --background --python export_machine.py -- [--replay PATH] [--output PATH]
The machine comes from scene_machine.build_machine on the replay payload (same layout as the live engine).
The 200 m studio-floor extension is dropped; the recorded 8 x 6 m floor slab stays. Beans, cameras and
lights are not exported. Blender's glTF export writes Y up; the page rotates the node +90 degrees about X.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scene_machine import COLLECTION_NAME, build_machine  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path, default=HERE.parent / 'web/replay.json')
    parser.add_argument('--output', type=Path, default=HERE / 'browser/machine_lod.glb')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    payload = json.loads(args.replay.read_bytes())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    counts = build_machine(payload)['counts']
    collection = bpy.data.collections[COLLECTION_NAME]
    dropped = 0
    for obj in tuple(collection.objects):
        if obj.name.startswith('Studio floor'):
            bpy.data.objects.remove(obj, do_unlink=True)
            dropped += 1
    bpy.ops.object.select_all(action='DESELECT')
    for obj in collection.objects:
        obj.select_set(True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(args.output), export_format='GLB', use_selection=True,
                              export_apply=True, export_cameras=False, export_lights=False,
                              export_yup=True, export_texcoords=False, export_normals=True,
                              export_materials='EXPORT')
    manifest_path = args.output.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps({
        'blender': bpy.app.version_string,
        'source': str(args.replay.relative_to(HERE.parent.parent.parent)) if args.replay.is_relative_to(HERE.parent.parent.parent) else str(args.replay),
        'replay_sha256': hashlib.sha256(args.replay.read_bytes()).hexdigest(),
        'layout': payload['layout'],
        'objects_exported': len(collection.objects),
        'studio_floor_objects_dropped': dropped,
        'builder_counts': counts,
        'bytes': args.output.stat().st_size,
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'coordinates': 'GLB uses Y up; restore Z up with +90 degrees about X on the root node.',
    }, indent=2))
    print(f'exported {args.output} ({args.output.stat().st_size:,} bytes), {len(collection.objects)} objects, dropped {dropped}')


main()
