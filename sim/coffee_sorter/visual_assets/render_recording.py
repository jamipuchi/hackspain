"""Render one second of shipped poses. No simulator import or physics stepping."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import bpy
from mathutils import Quaternion, Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_assets import make_assets, REGULAR_SEMI_AXES, BROKEN_SOURCE_DIMENSIONS
from render_stills import add_area, add_camera, configure_scene, cube, material, SEED


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instance_scale(spec, metadata):
    axes = [value / 100000 for value in metadata[2:5]]
    if spec['shape'] == 'half':
        # Recorded HALF axes are half of its AABB dimensions, not mesh scale.
        return [2 * a / d for a, d in zip(axes, BROKEN_SOURCE_DIMENSIONS)]
    if spec['shape'] == 'box':
        return axes
    return [a / d for a, d in zip(axes, REGULAR_SEMI_AXES)]


def create_recorded_beans(payload, frames, prototypes, beans):
    """Create linked assets for the UIDs in the selected source frames."""
    classes = payload['classes']
    metadata = {b[0]: b for b in payload['beans']}
    uids = sorted({f['beans'][i] for f in frames for i in range(0, len(f['beans']), 9)})
    objects = {}
    fallback_materials = {c['name']: material('fallback_' + c['name'], c['rgb'], .85) for c in classes if c['name'] not in prototypes}
    for uid in uids:
        meta = metadata[uid]
        spec = classes[meta[1]]
        if spec['shape'] == 'box':
            bpy.ops.mesh.primitive_cube_add(size=2)
            obj = bpy.context.object
            obj.data.materials.append(fallback_materials[spec['name']])
        elif spec['shape'] == 'capsule':
            # MuJoCo capsule is local Z; metadata stores half-cylinder length, radius.
            hl, radius = meta[2]/100000, meta[3]/100000
            bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=12, radius=radius)
            obj = bpy.context.object
            for v in obj.data.vertices:
                v.co.z += hl if v.co.z >= 0 else -hl
            obj.data.materials.append(fallback_materials[spec['name']])
        else:
            obj = prototypes.get(spec['name'], prototypes['good']).copy()
            beans.objects.link(obj)
            if spec['name'] not in prototypes:
                # Other classes remain explicitly labelled appearance stand-ins.
                obj.data = obj.data.copy()
                obj.data.materials.clear()
                obj.data.materials.append(fallback_materials[spec['name']])
                for face in obj.data.polygons:
                    face.material_index = 0
        obj.name = f"uid_{uid}_{spec['name']}"
        if obj.name not in beans.objects:
            for collection in tuple(obj.users_collection):
                collection.objects.unlink(obj)
            beans.objects.link(obj)
        obj['uid'], obj['recorded_class'], obj['recorded_outcome'] = uid, spec['name'], meta[6] or ''
        obj.rotation_mode = 'QUATERNION'
        obj.scale = (1, 1, 1) if spec['shape'] == 'capsule' else instance_scale(spec, meta)
        objects[uid] = obj

    return objects, metadata


def apply_recorded_frame(frame, objects):
    """Apply recorded poses and check the resulting world transforms."""
    for obj in objects.values():
        obj.hide_render = True
        obj.hide_viewport = True
    rows = frame['beans']
    for offset in range(0, len(rows), 9):
        uid, x, y, z, qw, qx, qy, qz, decision = rows[offset:offset+9]
        obj = objects[uid]
        obj.hide_render = False
        obj.hide_viewport = False
        obj.location = (x/10000, y/10000, z/10000)
        obj.rotation_quaternion = Quaternion((qw/10000, qx/10000, qy/10000, qz/10000)).normalized()
        obj['recorded_decision'] = decision
    bpy.context.view_layer.update()
    max_position_error = max_rotation_error = 0.0
    for offset in range(0, len(rows), 9):
        obj = objects[rows[offset]]
        expected = Vector([v/10000 for v in rows[offset+1:offset+4]])
        max_position_error = max(max_position_error, (obj.matrix_world.translation-expected).length)
        expected_q = Quaternion([v/10000 for v in rows[offset+4:offset+8]]).normalized()
        q = obj.matrix_world.to_quaternion()
        max_rotation_error = max(max_rotation_error, min((q-expected_q).magnitude, (q+expected_q).magnitude))
    assert max_position_error < 1e-6 and max_rotation_error < 1e-5
    assert sum(not o.hide_render for o in objects.values()) == len(rows)//9
    return max_position_error, max_rotation_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path, default=HERE.parent / 'web/replay.json')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'recording')
    parser.add_argument('--start', type=float, default=2.0)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    payload = json.loads(args.replay.read_bytes())
    assert payload['schema']['stride'] == 9 and payload['schema']['positionScale'] == 10000
    assert payload['schema']['quaternionScale'] == 10000 and payload['schema']['axesScale'] == 100000
    frames = [(i, f) for i, f in enumerate(payload['frames']) if args.start <= f['t'] < args.start + 1]
    if len(frames) != 30 or payload['config']['fps'] != 30 or abs(frames[0][1]['t'] - args.start) > .002:
        parser.error('choose a complete, sample-aligned one-second interval in the shipped 30 Hz replay')
    if payload['frames'][-1]['t'] < args.start + 1:
        parser.error('interval extends past recording')
    if args.preview:
        frames = frames[:1]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    beans = bpy.data.collections.new('RecordedBeans')
    bpy.context.scene.collection.children.link(beans)
    bpy.data.collections['CoffeeBeanPrototypes'].hide_render = True
    bpy.data.collections['CoffeeBeanPrototypes'].hide_viewport = True

    # Exported static machine geoms, including their original local rotations.
    for i, g in enumerate(payload['machine']):
        color = {'belt': (.02, .085, .31), 'frame': (.025, .03, .038),
                 'steel': (.31, .34, .38), 'floor': (.145, .132, .115),
                 'chute': (.22, .26, .28)}.get(g['material'], g['rgba'][:3])
        mat = material(f'machine_{i}', color, .48, .65 if g['material'] == 'steel' else .05)
        if g['type'] == 6:
            obj = cube(g['name'] or f'machine_{i}', g['pos'], [2*x for x in g['size']], mat)
        elif g['type'] == 5:
            bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=g['size'][0], depth=2*g['size'][1], location=g['pos'])
            obj = bpy.context.object
            obj.data.materials.append(mat)
        else:
            raise ValueError(f'unsupported machine geom {g}')
        obj.rotation_mode = 'QUATERNION'
        obj.rotation_quaternion = Quaternion(g['quat'])

    objects, metadata = create_recorded_beans(payload, [f for _, f in frames], prototypes, beans)
    uids = sorted(objects)

    L = payload['layout']
    indicators = []
    pulse_mat = material('Recorded valve ON indicator', (.1, .7, 1), .4)
    shader = pulse_mat.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Emission Color'].default_value = (.1, .7, 1, 1)
    shader.inputs['Emission Strength'].default_value = 3
    for n in range(L['n_nozzles']):
        y = -L['belt_w']/2 + L['belt_w']/L['n_nozzles']*(n+.5)
        indicators.append(cube(f'valve_{n}_indicator', (L['ej_x'], y, L['belt_z']+L['ej_z_offset']), (.003, .004, .004), pulse_mat))

    target = (.12, -.09, .555)
    camera_pos = (.4, -.42, .85)
    add_camera(camera_pos, target, 64, 16, (Vector(camera_pos)-Vector(target)).length)
    scene = configure_scene(args.preview, 16 if args.preview else 48)
    scene.render.resolution_x, scene.render.resolution_y = (640, 360) if args.preview else (1280, 720)
    scene.render.fps = 30
    scene.render.use_motion_blur = False
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    add_area('Softbox', (-.1, -.4, 1.15), target, 3, .6, (1, .9, .75))
    add_area('Fill', (.6, .2, 1), target, 1.5, .5, (.7, .82, 1))
    add_area('Rim', (-.4, .3, .9), target, 2.5, .4, (1, .74, .48))
    for number in (1, len(frames)):
        scene.camera.keyframe_insert(data_path='location', frame=number)
        scene.camera.keyframe_insert(data_path='rotation_euler', frame=number)

    record = dict(started_utc=started, replay_sha256=sha(args.replay),
                  source=payload['source'], config=payload['config'], schema=payload['schema'],
                  replay_path='sim/coffee_sorter/web/replay.json',
                  script_revision=subprocess.check_output(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], text=True).strip(),
                  script_sha256={name: sha(HERE/name) for name in ('render_recording.py', 'build_assets.py', 'render_stills.py')},
                  blender=bpy.app.version_string, render_seed=SEED, threads=scene.render.threads,
                  render_settings=dict(engine='CYCLES', device='CPU', samples=scene.cycles.samples,
                      resolution=[scene.render.resolution_x, scene.render.resolution_y], fps=30,
                      denoising=True, adaptive_threshold=scene.cycles.adaptive_threshold,
                      max_bounces=5, diffuse_bounces=3, glossy_bounces=3, transmission_bounces=2,
                      volume_bounces=0, motion_blur=False, view_transform=scene.view_settings.view_transform,
                      look=scene.view_settings.look, exposure=scene.view_settings.exposure),
                  camera_keyframes=[dict(frame=n, position_m=camera_pos, target_m=target, lens_mm=64,
                      sensor_width_mm=36, dof=False) for n in (1, len(frames))],
                  interval_seconds=[args.start, args.start+1], frames=[],
                  beans=[metadata[uid] for uid in uids],
                  fires=[f for f in payload['fires'] if f[0] < args.start+1 and f[1] >= args.start],
                  decisions=[d for d in payload['decisions'] if args.start <= d[0] < args.start+1],
                  limitation='Near 30 Hz sampled poses; no interpolation, motion blur or inferred dynamics. Short valves may fall between frames. Other than good/black/insect/broken, appearances are stand-ins. Original wall-clock capture timestamp was not recorded by exporter.')
    total = time.perf_counter()
    for number, (source_index, frame) in enumerate(frames, 1):
        scene.frame_set(number)
        rows = frame['beans']
        max_position_error, max_rotation_error = apply_recorded_frame(frame, objects)
        active = sorted({f[2] for f in payload['fires'] if f[0] <= frame['t'] < f[1]})
        for n, obj in enumerate(indicators):
            obj.hide_render = n not in active
        scene.render.filepath = str((args.output_dir / f'frame_{number:03d}.png').resolve())
        tick = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        record['frames'].append(dict(frame=number, source_index=source_index, t=frame['t'],
            active_beans=len(rows)//9, counters=frame['counters'], active_valves=active,
            pose_rows_sha256=hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest(),
            max_position_error_m=max_position_error, max_quaternion_component_error=max_rotation_error,
            render_seconds=round(time.perf_counter()-tick, 3), png_sha256=sha(Path(scene.render.filepath))))
        record['render_loop_seconds'] = round(time.perf_counter()-total, 3)
        (args.output_dir/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')
        print(f"RECORDED_FRAME {number}/{len(frames)} t={frame['t']} wall={record['frames'][-1]['render_seconds']}", flush=True)
    assert sha(args.replay) == record['replay_sha256']
    print(f"REPLAY_UNCHANGED {record['replay_sha256']}", flush=True)


if __name__ == '__main__':
    main()
