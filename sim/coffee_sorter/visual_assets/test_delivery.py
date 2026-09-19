"""Artifact contract checks, runnable without Blender or a simulation."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import unittest

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]

# Rendering started at the manifest revision while the recording scripts were uncommitted.
# This direct child is the first immutable revision containing those exact script bytes.
RECORDING_SCRIPT_ARCHIVE_REVISION = 'a230f2cdf3fec8c906e947a9b7f2ad795dea7ec8'


def git_bytes(*args):
    return subprocess.run(
        ['git', *args], cwd=REPOSITORY_ROOT, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


class DeliveryTests(unittest.TestCase):
    def test_native_recording_and_events(self):
        replay_bytes = (HERE.parent/'web/replay.json').read_bytes()
        replay = json.loads(replay_bytes)
        manifest = json.loads((HERE/'recording/manifest.json').read_text())
        self.assertEqual(manifest['replay_sha256'], hashlib.sha256(replay_bytes).hexdigest())
        self.assertEqual(manifest['source'], replay['source'])
        self.assertEqual(manifest['config'], replay['config'])
        start, end = manifest['interval_seconds']
        expected = [(i, f) for i, f in enumerate(replay['frames']) if start <= f['t'] < end]
        self.assertEqual(len(expected), 30)
        self.assertEqual(len(manifest['frames']), 30)
        uids = set()
        for actual, (index, source) in zip(manifest['frames'], expected):
            self.assertEqual((actual['source_index'], actual['t']), (index, source['t']))
            self.assertEqual(actual['active_beans'], len(source['beans'])//9)
            self.assertEqual(actual['counters'], source['counters'])
            self.assertEqual(actual['pose_rows_sha256'], hashlib.sha256(json.dumps(source['beans'], separators=(',', ':')).encode()).hexdigest())
            self.assertEqual(actual['active_valves'], sorted({f[2] for f in replay['fires'] if f[0] <= source['t'] < f[1]}))
            self.assertLess(actual['max_position_error_m'], 1e-6)
            self.assertLess(actual['max_quaternion_component_error'], 1e-5)
            uids.update(source['beans'][::9])
        self.assertEqual(manifest['beans'], [b for b in replay['beans'] if b[0] in uids])
        self.assertEqual(manifest['fires'], [f for f in replay['fires'] if f[0] < end and f[1] >= start])
        self.assertEqual(manifest['decisions'], [d for d in replay['decisions'] if start <= d[0] < end])
        self.assertLessEqual(manifest['threads'], 16)
        archive_parent = git_bytes('rev-parse', f'{RECORDING_SCRIPT_ARCHIVE_REVISION}^').decode().strip()
        self.assertEqual(archive_parent, manifest['script_revision'])
        for name, digest in manifest['script_sha256'].items():
            archived_path = f'sim/coffee_sorter/visual_assets/{name}'
            archived_source = git_bytes('show', f'{RECORDING_SCRIPT_ARCHIVE_REVISION}:{archived_path}')
            self.assertEqual(hashlib.sha256(archived_source).hexdigest(), digest)

    def test_movie_and_poster(self):
        manifest = json.loads((HERE/'recording/manifest.json').read_text())
        movie = (HERE/'recording/coffee-one-second.mp4').read_bytes()
        self.assertEqual(hashlib.sha256(movie).hexdigest(), manifest['encoding']['sha256'])
        self.assertEqual(manifest['encoding']['frames'], 30)
        self.assertEqual(manifest['encoding']['duration_seconds'], 1)
        poster = (HERE/'recording/frame_001.png').read_bytes()
        self.assertEqual(hashlib.sha256(poster).hexdigest(), manifest['frames'][0]['png_sha256'])

    def test_glbs_have_one_draw_and_embedded_pbr_maps(self):
        manifest = json.loads((HERE/'browser/manifest.json').read_text())
        self.assertEqual(hashlib.sha256((HERE/'export_browser.py').read_bytes()).hexdigest(), manifest['script_sha256'])
        for kind, entry in manifest['objects'].items():
            with self.subTest(kind=kind):
                data = (HERE/'browser'/entry['file']).read_bytes()
                magic, version, length, json_length, chunk = struct.unpack_from('<IIIII', data)
                self.assertEqual((magic, version, length, chunk), (0x46546c67, 2, len(data), 0x4e4f534a))
                gltf = json.loads(data[20:20+json_length])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                self.assertEqual(len(data), entry['bytes'])
                self.assertLess(len(data), entry['hero_obj_bytes'])
                primitives = [p for mesh in gltf['meshes'] for p in mesh['primitives']]
                self.assertEqual(len(primitives), 1)
                primitive = primitives[0]
                bounds = gltf['accessors'][primitive['attributes']['POSITION']]
                nominal = json.loads((HERE/'generated/dimensions.json').read_text())['objects'][kind]
                # Standard glTF basis: x, z, -y. Includes broken cut plane at y=0.
                expected_min = [nominal['min_m'][0], nominal['min_m'][2], -nominal['max_m'][1]]
                expected_max = [nominal['max_m'][0], nominal['max_m'][2], -nominal['min_m'][1]]
                for actual, expected in zip(bounds['min']+bounds['max'], expected_min+expected_max):
                    self.assertAlmostEqual(actual, expected, places=8)
                self.assertIn('TEXCOORD_0', primitive['attributes'])
                triangles = gltf['accessors'][primitive['indices']]['count']//3
                self.assertLessEqual(triangles, 650)
                self.assertEqual(triangles, entry['lod_triangles'])
                material = gltf['materials'][primitive['material']]
                self.assertIn('normalTexture', material)
                self.assertIn('baseColorTexture', material['pbrMetallicRoughness'])
                self.assertIn('metallicRoughnessTexture', material['pbrMetallicRoughness'])
                self.assertTrue(all('bufferView' in image and 'uri' not in image for image in gltf['images']))
                self.assertTrue(all('uri' not in buffer for buffer in gltf['buffers']))


if __name__ == '__main__':
    unittest.main()
