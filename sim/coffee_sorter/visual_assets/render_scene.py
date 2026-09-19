"""Build and render a complete coffee sorter from one recorded frame."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_assets import make_assets
from render_recording import apply_recorded_frame, create_recorded_beans, sha
from render_stills import configure_scene
from scene_lighting import configure_shot
from scene_machine import build_machine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path, default=HERE.parent / 'web/replay.json')
    parser.add_argument('--frame', type=int, default=60, help='Zero-based source frame index.')
    parser.add_argument('--shot', choices=('hero', 'inspection', 'discharge'), default='hero')
    parser.add_argument('--look', choices=('noir-rim', 'blueprint', 'warm-roastery'), default='noir-rim')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--samples', type=int, help='Override the preview or final sample count.')
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    payload = json.loads(args.replay.read_bytes())
    expected = dict(stride=9, positionScale=10000, quaternionScale=10000, axesScale=100000)
    if any(payload['schema'].get(k) != v for k, v in expected.items()):
        parser.error('The replay must use the supported nine-value pose schema.')
    if not 0 <= args.frame < len(payload['frames']):
        parser.error('The frame index must be inside the recording.')
    if args.threads < 1 or (args.samples is not None and args.samples < 1):
        parser.error('Threads and samples must be positive integers.')

    started = time.perf_counter()
    replay_hash = sha(args.replay)
    frame = payload['frames'][args.frame]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    library = bpy.data.collections['CoffeeBeanPrototypes']
    library.hide_render = True
    library.hide_viewport = True
    beans = bpy.data.collections.new('RecordedBeans')
    bpy.context.scene.collection.children.link(beans)
    objects, metadata = create_recorded_beans(payload, [frame], prototypes, beans)
    machine = build_machine(payload)
    samples = args.samples or (24 if args.preview else 96)
    scene = configure_scene(args.preview, samples)
    scene.render.resolution_x, scene.render.resolution_y = (960, 540) if args.preview else (1920, 1080)
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = args.threads
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = 'PNG'
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    scene.frame_start = scene.frame_end = args.frame + 1
    scene.frame_set(args.frame + 1)
    scene.render.filepath = str(output / 'scene.png')
    direction = configure_shot(args.shot, args.look)
    position_error, rotation_error = apply_recorded_frame(frame, objects)

    record = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        git_revision=subprocess.check_output(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], text=True).strip(),
        source_sha256={name: sha(HERE / name) for name in (
            'render_scene.py', 'scene_machine.py', 'scene_lighting.py', 'scene_direction.json',
            'render_recording.py', 'render_stills.py', 'build_assets.py')},
        replay_path=str(args.replay.resolve()), replay_sha256=replay_hash,
        source=payload['source'], config=payload['config'], schema=payload['schema'],
        source_frame_index=args.frame, source_time_seconds=frame['t'],
        shot=args.shot, look=args.look, direction=direction,
        active_beans=len(objects), beans=[metadata[uid] for uid in sorted(objects)],
        source_frame=frame,
        recorded_machine=payload['machine'],
        machine=machine,
        max_position_error_m=position_error, max_quaternion_component_error=rotation_error,
        blender=bpy.app.version_string,
        render=dict(engine=scene.render.engine, device=scene.cycles.device, samples=scene.cycles.samples,
                    threads=scene.render.threads, seed=scene.cycles.seed,
                    resolution=[scene.render.resolution_x, scene.render.resolution_y],
                    resolution_percentage=scene.render.resolution_percentage,
                    denoising=scene.cycles.use_denoising,
                    adaptive_sampling=scene.cycles.use_adaptive_sampling,
                    adaptive_threshold=scene.cycles.adaptive_threshold,
                    max_bounces=scene.cycles.max_bounces, diffuse_bounces=scene.cycles.diffuse_bounces,
                    glossy_bounces=scene.cycles.glossy_bounces,
                    transmission_bounces=scene.cycles.transmission_bounces,
                    volume_bounces=scene.cycles.volume_bounces,
                    color_mode=scene.render.image_settings.color_mode,
                    color_depth=scene.render.image_settings.color_depth,
                    compression=scene.render.image_settings.compression,
                    motion_blur=False, exposure=scene.view_settings.exposure,
                    view_transform=scene.view_settings.view_transform, look=scene.view_settings.look),
        limitations=[
            'This is one recorded instant. The blend contains no animation.',
            'Added machine details are presentation geometry, not simulated collision surfaces.',
            'Detailed bean assets cover good, black, insect, and broken classes. Other classes use appearance stand-ins.',
            'Blueprint and clay remove color differences between bean classes.',
            'The image does not establish sorting accuracy or mechanical feasibility.',
        ],
    )
    record['build_seconds'] = round(time.perf_counter() - started, 3)
    tick = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    record['render_seconds'] = round(time.perf_counter() - tick, 3)
    record['png_sha256'] = sha(output / 'scene.png')
    if sha(args.replay) != replay_hash:
        raise RuntimeError('The replay changed during rendering.')
    bpy.data.texts.new('source_manifest.json').write(json.dumps(record, indent=2) + '\n')
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'scene.blend'), compress=True)
    record['blend_sha256'] = sha(output / 'scene.blend')
    (output / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: record[key] for key in (
        'shot', 'look', 'active_beans', 'source_time_seconds', 'build_seconds', 'render_seconds',
        'max_position_error_m', 'max_quaternion_component_error')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
