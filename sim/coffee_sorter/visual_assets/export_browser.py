"""Bake separate lightweight GLB prototypes; never overwrite the hero library."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_assets import make_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=HERE/'browser')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prototypes = make_assets(export_obj=False)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.cycles.seed = 230948
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 16
    scene.render.bake.margin = 8
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    manifest = dict(blender=bpy.app.version_string, threads=16, texture_size=256,
                    seed=230948, script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    coordinates='GLB uses Y up: (x,y,z)=(Blender x, Blender z, -Blender y); restore Z up with +90 degrees about X.',
                    objects={})
    for kind, obj in prototypes.items():
        tick = time.perf_counter()
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        obj.data.calc_loop_triangles()
        hero_triangles = len(obj.data.loop_triangles)
        bounds = [(min(v.co[a] for v in obj.data.vertices), max(v.co[a] for v in obj.data.vertices)) for a in range(3)]
        modifier = obj.modifiers.new('Browser LOD', 'DECIMATE')
        modifier.ratio = min(1, 600/hero_triangles)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        # Decimation can overshoot the cut plane by micrometres. Restore the
        # original local bounds before baking; never move the object origin.
        for axis, (low, high) in enumerate(bounds):
            new_low = min(v.co[axis] for v in obj.data.vertices)
            new_high = max(v.co[axis] for v in obj.data.vertices)
            for vertex in obj.data.vertices:
                vertex.co[axis] = low + (vertex.co[axis]-new_low)*(high-low)/(new_high-new_low)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=.035)
        bpy.ops.object.mode_set(mode='OBJECT')
        # Work on copied shader nodes so baking one prototype cannot affect another.
        for slot in obj.material_slots:
            slot.material = slot.material.copy()
        images = {}
        for label, bake_type in (('basecolor', 'DIFFUSE'), ('normal', 'NORMAL'), ('roughness', 'ROUGHNESS')):
            image = bpy.data.images.new(f'{kind}_{label}', width=256, height=256, alpha=False)
            if label != 'basecolor':
                image.colorspace_settings.name = 'Non-Color'
            for slot in obj.material_slots:
                nodes = slot.material.node_tree.nodes
                node = nodes.new('ShaderNodeTexImage')
                node.image = image
                nodes.active = node
            bpy.ops.object.bake(type=bake_type)
            image.pack()
            images[label] = image
        mat = bpy.data.materials.new(f'{kind}_baked_PBR')
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        shader = nodes.get('Principled BSDF')
        shader.inputs['Metallic'].default_value = 0
        for label, socket in (('basecolor', 'Base Color'), ('roughness', 'Roughness'), ('normal', 'Normal')):
            texture = nodes.new('ShaderNodeTexImage')
            texture.image = images[label]
            output = texture.outputs['Color']
            if label == 'normal':
                normal = nodes.new('ShaderNodeNormalMap')
                links.new(output, normal.inputs['Color'])
                output = normal.outputs['Normal']
            links.new(output, shader.inputs[socket])
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        for face in obj.data.polygons:
            face.material_index = 0
        obj.data.calc_loop_triangles()
        path = args.output_dir/f'bean_{kind}_lod.glb'
        bpy.ops.export_scene.gltf(filepath=str(path.resolve()), export_format='GLB',
                                  use_selection=True, export_yup=True, export_animations=False,
                                  export_cameras=False, export_lights=False)
        manifest['objects'][kind] = dict(file=path.name, bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), hero_triangles=hero_triangles,
            lod_triangles=len(obj.data.loop_triangles), material_count=1,
            hero_obj_bytes=(HERE/'generated'/f'bean_{kind}.obj').stat().st_size,
            bake_and_export_seconds=round(time.perf_counter()-tick, 3))
        print(json.dumps(manifest['objects'][kind]), flush=True)
    (args.output_dir/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
