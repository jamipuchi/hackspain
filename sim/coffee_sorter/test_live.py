import asyncio
import builtins
import subprocess
import signal
import contextlib
from collections import deque
import hashlib
import io
import json
import shlex
import shutil
import os
import stat
from pathlib import Path
from queue import Full
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import uuid

from aiohttp import web

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import item_jobs
import live
import object_catalog
from live import (COMMAND_EPOCH_SECONDS, MAX_COMMANDS, MAX_COMMANDS_PER_EPOCH,
                  MAX_PENDING_COMMANDS, PUMP_STALE_SECONDS, LiveService,
                  _configured_source_revision, access_middleware, access_rules,
                  configured_access_rules, load_preset, resolve_model_path, worker)

CATALOG_REVISION = 'a' * 64
ORIGIN = 'http://127.0.0.1:8890'


class RecordingQueue:
    def __init__(self, before_put=None, full=False):
        self.items = []
        self.before_put = before_put
        self.full = full

    def put_nowait(self, item):
        if self.before_put is not None:
            self.before_put(item)
        if self.full:
            raise Full
        self.items.append(item)


class FakeWebSocket:
    def __init__(self):
        self.packets = []

    async def send_json(self, packet):
        self.packets.append(packet)


class FakeClient:
    def __init__(self, blocked=False, failed=False):
        self.packets = []
        self.blocked = blocked
        self.failed = failed
        self.force_closed = False

    async def send_str(self, packet):
        if self.blocked:
            await asyncio.Event().wait()
        if self.failed:
            raise ConnectionError('test send failure')
        self.packets.append(json.loads(packet))

    def force_close(self):
        self.force_closed = True


class WorkerQueue:
    def __init__(self):
        self.items = []

    def put(self, item, timeout=None):
        self.items.append(item)

    def get_nowait(self):
        raise __import__('queue').Empty

    def close(self):
        pass


class FakeStop:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True


class AccessRulesTest(unittest.IsolatedAsyncioTestCase):
    def test_loopback_defaults_remain_available(self):
        hosts, origins = access_rules(8890)
        self.assertEqual(hosts, frozenset({'127.0.0.1:8890', 'localhost:8890'}))
        self.assertEqual(origins, frozenset({
            'http://127.0.0.1:8890', 'http://localhost:8890',
        }))

    def test_public_host_and_origin_are_explicit_and_same_host(self):
        hosts, origins = access_rules(
            8890, ['hack-growth.dev'], ['https://hack-growth.dev'])
        self.assertIn('hack-growth.dev', hosts)
        self.assertIn('https://hack-growth.dev', origins)
        with self.assertRaisesRegex(ValueError, 'Every allowed origin'):
            access_rules(8890, ['hack-growth.dev'], ['https://other.example'])

    def test_public_bind_requires_host_and_origin(self):
        for hosts, origins in (([], []), (['hack-growth.dev'], []),
                               ([], ['https://hack-growth.dev'])):
            with self.subTest(hosts=hosts, origins=origins), self.assertRaisesRegex(
                    ValueError, 'requires at least one allowed host and origin'):
                configured_access_rules('0.0.0.0', 8890, hosts, origins)

    def test_wildcards_paths_and_credentials_are_rejected(self):
        for host in ('*', 'hack-growth.dev/path', 'user@hack-growth.dev'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                access_rules(8890, [host], [])
        for origin in ('*', 'https://hack-growth.dev/path', 'https://user@hack-growth.dev'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                access_rules(8890, ['hack-growth.dev'], [origin])

    def test_malformed_hosts_and_ports_are_rejected(self):
        invalid_hosts = (
            'hack-growth.dev:', 'hack_growth.dev', '-hack-growth.dev',
            'hack-growth-.dev', '999.1.1.1', 'hack-growth.dev:port',
            'hack-growth.dev:0', 'hack-growth.dev:65536', '[::1]',
        )
        for host in invalid_hosts:
            with self.subTest(host=host), self.assertRaises(ValueError):
                access_rules(8890, [host], [])
        hosts, _ = access_rules(8890, ['hack-growth.dev:443', '142.132.165.127'], [])
        self.assertIn('hack-growth.dev:443', hosts)
        self.assertIn('142.132.165.127', hosts)

    def test_release_revision_requires_full_sha(self):
        with patch.dict('os.environ', {'CINTA_SOURCE_REVISION': 'a' * 40}):
            self.assertEqual(_configured_source_revision(), 'a' * 40)
        with patch.dict('os.environ', {'CINTA_SOURCE_REVISION': 'A' * 40}):
            self.assertEqual(_configured_source_revision(), 'a' * 40)
        for value in ('short', 'g' * 40):
            with self.subTest(value=value), patch.dict(
                    'os.environ', {'CINTA_SOURCE_REVISION': value}):
                with self.assertRaisesRegex(ValueError, 'full Git SHA'):
                    _configured_source_revision()

    async def test_public_request_accepts_exact_origin_and_sets_no_store(self):
        hosts, origins = access_rules(
            8890, ['hack-growth.dev'], ['https://hack-growth.dev'])
        middleware = access_middleware(hosts, origins)
        request = types.SimpleNamespace(
            host='hack-growth.dev', path='/ws', method='GET',
            headers={'Origin': 'https://hack-growth.dev'})

        async def handler(_request):
            return web.Response(text='ok')

        response = await middleware(request, handler)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    async def test_mutating_routes_require_an_allowed_origin(self):
        hosts, origins = access_rules(
            8890, ['hack-growth.dev'], ['https://hack-growth.dev'])
        middleware = access_middleware(hosts, origins)

        async def handler(_request):
            return web.Response(text='ok')

        cases = [
            types.SimpleNamespace(host='hack-growth.dev', path='/ws', method='GET', headers={}),
            types.SimpleNamespace(
                host='hack-growth.dev', path='/restart', method='POST',
                headers={'Origin': 'https://attacker.example'}),
            types.SimpleNamespace(
                host='attacker.example', path='/state', method='GET', headers={}),
        ]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(web.HTTPForbidden):
                await middleware(request, handler)

        state_request = types.SimpleNamespace(
            host='hack-growth.dev', path='/state', method='GET', headers={})
        response = await middleware(state_request, handler)
        self.assertEqual(response.status, 200)


def service(continuous=True):
    value = LiveService.__new__(LiveService)
    value.continuous = continuous
    value.state = {'status': 'running' if continuous else 'ready', 'session_id': str(uuid.uuid4())}
    value.restarting = False
    value.requests = {}
    value.commands = RecordingQueue()
    value.command_epoch_seconds = COMMAND_EPOCH_SECONDS
    value.command_epoch = 'epoch-1' if continuous else None
    value.previous_command_epoch = None
    value.command_epoch_started = time.monotonic()
    value.epoch_admissions = ({value.command_epoch: 0} if continuous else {})
    value.heartbeat_seq = 0
    value.last_state_broadcast = time.monotonic()
    value.clients = set()
    value.client_sends = {}
    value.service_timings = {'snapshot_reads': 0, 'broadcasts': 0,
                             'json_encode_ms': 0.0, 'send_wait_ms': 0.0}
    value.service_samples = {key: deque(maxlen=4096)
                             for key in ('json_encode_ms', 'send_wait_ms')}
    return value


class FakeRequest:
    def __init__(self, body=None, origin=ORIGIN, query=None, **match_info):
        self.headers = {'Origin': origin} if origin else {}
        self.match_info = match_info
        self.query = query or {}
        self.body = body

    async def text(self):
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


def item_service(test, provider_mode='cached'):
    """One service with a real job store and an unstarted runner. No child ever spawns."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    value = service()
    value.item_jobs_root = Path(directory.name)
    value.item_jobs_provider = provider_mode
    value.provider_cache = value.item_jobs_root / 'provider-cache'
    # A path that never exists. Opening it would raise, so any read is visible.
    value.provider_env = value.item_jobs_root.parent / 'absent-secret.env'
    value.generator_root = item_jobs.GENERATOR_ROOT
    # The physics and training children receive the reviewed preset the service runs.
    value.preset = HERE / 'configs/continuous_demo.json'
    value.physics_replay = None
    value.item_jobs = item_jobs.ItemJobStore(value.item_jobs_root, provider_mode=provider_mode)
    test.addCleanup(value.item_jobs.close)
    value.runtime_lock = value.item_jobs_root / 'render.lock'
    value.item_jobs_unhealthy = False
    value.item_runner = item_jobs.ItemJobRunner(
        value.item_jobs, item_jobs.fake_commands(), runtime_lock_path=value.runtime_lock)
    value.catalog_root = object_catalog.PACKAGED_CATALOG_ROOT
    value.catalog_revision = CATALOG_REVISION
    value.active_type_ids = ['builtin.green_arabica.good']
    value.item_jobs_state = value._item_jobs_packet()
    return value


def item_arguments(**overrides):
    # Every field the real parser always supplies, so the validator sees a whole record.
    values = {'host': '127.0.0.1', 'item_jobs_provider': 'cached',
              'item_jobs_provider_cache': None,
              'item_jobs_provider_env': None, 'item_jobs_physics_replay': None,
              'item_jobs_generator_root': item_jobs.GENERATOR_ROOT,
              'item_jobs_root': None, 'object_catalog_root': None}
    return types.SimpleNamespace(**{**values, **overrides})


class StubParser:
    """argparse.error exits the process. The tests need the message instead."""

    def error(self, message):
        raise ValueError(message)


def item_request(description='A small brass star token', request_id=None, origin=ORIGIN, **extra):
    return FakeRequest({'request_id': request_id or str(uuid.uuid4()), 'description': description,
                        'expected_catalog_revision': CATALOG_REVISION, **extra}, origin=origin)


def command(value, command_id=None, epoch=None, class_name='stone'):
    result = {
        'type': 'inject',
        'command_id': command_id or str(uuid.uuid4()),
        'session_id': value.state['session_id'],
        'class_name': class_name,
    }
    if value.continuous:
        result['command_epoch'] = value.command_epoch if epoch is None else epoch
    return result


def policy_command(value, command_id=None, epoch=None, reject_classes=None,
                   expected_policy_version='policy-1'):
    return {
        'type': 'set_reject_policy',
        'command_id': command_id or str(uuid.uuid4()),
        'session_id': value.state['session_id'],
        'command_epoch': value.command_epoch if epoch is None else epoch,
        'expected_policy_version': expected_policy_version,
        'reject_classes': ['stone'] if reject_classes is None else reject_classes,
    }


class StartupValidationTest(unittest.TestCase):
    def test_continuous_preset_matches_selected_artifact_and_physics(self):
        preset = load_preset(HERE / 'configs/continuous_demo.json')
        self.assertEqual(preset['mode'], 'continuous')

    def test_live_only_fields_do_not_need_training_preset_equality(self):
        preset = load_preset(HERE / 'configs/continuous_demo.json')
        self.assertEqual(preset['name'], 'continuous-live-v2')
        self.assertEqual(preset['version'], 2)

    def test_physical_mismatch_fails_before_startup(self):
        preset = json.loads((HERE / 'configs/continuous_demo.json').read_text())
        preset['requested_rate'] += 1
        path = self._temp_preset(preset)
        try:
            with self.assertRaisesRegex(ValueError, 'feed rate'):
                load_preset(path)
        finally:
            path.unlink()

    def test_a_preset_rooted_model_path_resolves_beside_the_preset(self):
        """A bundle carries its model beside its preset, so it loads from there."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name).resolve()
        (directory / 'candidate.joblib').write_bytes(b'model')
        preset = {'model_path': 'candidate.joblib', 'model_path_root': 'preset'}
        path = directory / 'candidate.preset.json'

        resolved = resolve_model_path(preset, path, HERE)

        self.assertEqual(directory / 'candidate.joblib', resolved)

    def test_a_preset_rooted_model_path_cannot_leave_the_preset_directory(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'nested' / 'candidate.preset.json'
        path.parent.mkdir()
        cases = {
            'parent traversal': '../x.joblib',
            'deep traversal': '../../../etc/x.joblib',
            'absolute path': str(Path(folder.name) / 'x.joblib'),
        }
        for name, model_path in cases.items():
            with self.subTest(case=name):
                preset = {'model_path': model_path, 'model_path_root': 'preset'}
                with self.assertRaises(ValueError):
                    resolve_model_path(preset, path, HERE)

    def test_an_unknown_model_path_root_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'model_path_root'):
            resolve_model_path({'model_path': 'x.joblib', 'model_path_root': 'source'},
                               HERE / 'p.json', HERE)

    def test_without_the_field_the_model_path_stays_source_relative(self):
        self.assertEqual(HERE / 'models/live.joblib',
                         resolve_model_path({'model_path': 'models/live.joblib'},
                                            HERE / 'p.json', HERE))
        self.assertEqual(Path('/tmp/abs.joblib'),
                         resolve_model_path({'model_path': '/tmp/abs.joblib'},
                                            HERE / 'p.json', HERE))

    def _temp_preset(self, preset):
        path = HERE / f'.test-live-{uuid.uuid4()}.json'
        path.write_text(json.dumps(preset))
        self.addCleanup(path.unlink, missing_ok=True)
        return path


class ContinuousWorkerTest(unittest.TestCase):
    def test_worker_auto_starts_and_caches_scores_separately(self):
        stop = FakeStop()

        class FakeEngine:
            instance = None

            def __init__(self, preset):
                FakeEngine.instance = self
                self.continuous = True
                self.preset = {'limits': {'max_sim_seconds': None, 'max_wall_seconds': None}}
                self.session_id = str(uuid.uuid4())
                self.sim = types.SimpleNamespace(data=types.SimpleNamespace(time=0.0))
                self.source_revision = 'test'
                self.source_hashes = {}
                self.snapshot_calls = 0
                self.rolling_calls = 0

            def snapshot(self):
                self.snapshot_calls += 1
                return {'protocol_version': 2, 'mode': 'continuous',
                        'session_id': self.session_id, 'sim_time_s': self.sim.data.time}

            def rolling_scores(self):
                self.rolling_calls += 1
                return {'schema_version': 1, 'as_of_sim_time_s': self.sim.data.time}

            def step(self):
                self.sim.data.time += 0.1
                if self.sim.data.time >= 1.2:
                    stop.set()

            def report(self):
                return {}

            def close(self):
                pass

        clock = {'value': 0.0}

        def monotonic():
            clock['value'] += 0.11
            return clock['value']

        states = WorkerQueue()
        acks = WorkerQueue()
        commands = WorkerQueue()
        module = types.SimpleNamespace(Engine=FakeEngine)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'engine': module}), \
                    patch.dict('os.environ', {'CINTA_SOURCE_REVISION': 'a' * 40}), \
                    patch('live.signal.signal'), patch('live.time.monotonic', side_effect=monotonic):
                worker('unused.json', states, acks, commands, stop, directory)

        running = states.items[0]
        self.assertEqual(running['status'], 'running')
        self.assertEqual(running['source_revision'], 'a' * 40)
        self.assertEqual(FakeEngine.instance.source_revision, 'a' * 40)
        self.assertIsNone(running['limits']['sim_seconds'])
        self.assertIn('rolling_scores', running)
        self.assertGreater(FakeEngine.instance.sim.data.time, 0)
        self.assertLess(FakeEngine.instance.rolling_calls, FakeEngine.instance.snapshot_calls)

    def test_worker_applies_policy_and_rejects_stale_version(self):
        stop = FakeStop()

        class FakeEngine:
            def __init__(self, preset):
                self.continuous = True
                self.preset = {'limits': {'max_sim_seconds': None, 'max_wall_seconds': None}}
                self.session_id = 'session'
                self.sim = types.SimpleNamespace(data=types.SimpleNamespace(time=2.0))
                self.source_revision = 'test'
                self.source_hashes = {}
                self.policy_version = 'current-policy'
                self.score_epoch_id = 'epoch-1'
                self.steps = 0

            def snapshot(self):
                return {'session_id': self.session_id, 'sim_time_s': self.sim.data.time}

            def rolling_scores(self):
                return {'score_epoch_id': self.score_epoch_id}

            def reject_policy(self):
                return {'policy_version': self.policy_version, 'score_epoch_id': 'epoch-1'}

            def set_reject_classes(self, reject_classes):
                self.policy_version = 'next-policy'
                self.score_epoch_id = 'epoch-2'
                return {
                    'changed': True, 'reject_classes': reject_classes,
                    'policy_version': self.policy_version, 'score_epoch_id': 'epoch-2',
                    'score_epoch_started_sim_time_s': 2.0, 'applied_sim_time_s': 2.0,
                    'in_flight_excluded': 3,
                }

            def step(self):
                self.steps += 1
                if self.steps == 2:
                    stop.set()

            def report(self):
                return {}

            def close(self):
                pass

        class OneCommandQueue(WorkerQueue):
            def __init__(self, items):
                super().__init__()
                self.pending = list(items)

            def get_nowait(self):
                if not self.pending:
                    raise __import__('queue').Empty
                return self.pending.pop(0)

        payload = {
            'type': 'set_reject_policy', 'command_id': str(uuid.uuid4()),
            'session_id': 'session', 'command_epoch': 'epoch',
            'expected_policy_version': 'stale-policy', 'reject_classes': ['stone'],
        }
        accepted = {**payload, 'command_id': str(uuid.uuid4()),
                    'expected_policy_version': 'current-policy'}
        states = WorkerQueue()
        acknowledgments = WorkerQueue()
        commands = OneCommandQueue([payload, accepted])
        module = types.SimpleNamespace(Engine=FakeEngine)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'engine': module}), patch('live.signal.signal'):
                worker('unused.json', states, acknowledgments, commands, stop, directory)

        stale, applied = acknowledgments.items
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error_code'], 'policy_version_conflict')
        self.assertEqual(stale['reject_policy']['policy_version'], 'current-policy')
        self.assertTrue(applied['ok'])
        self.assertEqual(applied['policy_version'], 'next-policy')
        self.assertEqual(applied['score_epoch_id'], 'epoch-2')
        applied_state = next(
            item for item in reversed(states.items)
            if item.get('rolling_scores', {}).get('score_epoch_id') == 'epoch-2'
        )
        self.assertEqual(applied_state['rolling_scores']['score_epoch_id'], 'epoch-2')

    def test_injection_ack_includes_spawn_policy_expectation(self):
        stop = FakeStop()

        class FakeEngine:
            def __init__(self, preset):
                self.continuous = True
                self.preset = {'limits': {'max_sim_seconds': None, 'max_wall_seconds': None}}
                self.session_id = 'session'
                self.sim = types.SimpleNamespace(data=types.SimpleNamespace(time=2.0))
                self.source_revision = 'test'
                self.source_hashes = {}
                self.steps = 0

            def snapshot(self):
                return {'session_id': self.session_id, 'sim_time_s': self.sim.data.time}

            def rolling_scores(self):
                return {'score_epoch_id': 'epoch'}

            def inject(self, class_name):
                self.injected_class = class_name
                return 41

            def injection_position(self, object_id):
                return [-1.0, 0.1, 0.6]

            def injection_expectation(self, object_id):
                return {
                    'expected_outcome': 'reject',
                    'expectation_policy_version': 'spawn-policy',
                }

            def step(self):
                self.steps += 1
                stop.set()

            def report(self):
                return {}

            def close(self):
                pass

        class OneCommandQueue(WorkerQueue):
            def __init__(self, item):
                super().__init__()
                self.item = item

            def get_nowait(self):
                if self.item is None:
                    raise __import__('queue').Empty
                item, self.item = self.item, None
                return item

        payload = {
            'type': 'inject', 'command_id': str(uuid.uuid4()),
            'session_id': 'session', 'command_epoch': 'epoch', 'class_name': 'stone',
        }
        states = WorkerQueue()
        acknowledgments = WorkerQueue()
        commands = OneCommandQueue(payload)
        module = types.SimpleNamespace(Engine=FakeEngine)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'engine': module}), patch('live.signal.signal'):
                worker('unused.json', states, acknowledgments, commands, stop, directory)

        ack = acknowledgments.items[0]
        self.assertEqual(ack['expected_outcome'], 'reject')
        self.assertEqual(ack['expectation_policy_version'], 'spawn-policy')


class ContinuousCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_policy_apply_is_idempotent_and_exact_retry_returns_ack(self):
        value = service()
        ws = FakeWebSocket()
        payload = policy_command(value)

        await value._handle_command(payload, ws)
        await value._handle_command(payload, ws)

        self.assertEqual(len(value.commands.items), 1)
        self.assertEqual(ws.packets[-1]['type'], 'pending')
        ack = {
            'type': 'ack', 'command_id': payload['command_id'],
            'command_type': 'set_reject_policy', 'command_epoch': value.command_epoch,
            'session_id': value.state['session_id'], 'ok': True,
            'policy_version': 'policy-2', 'score_epoch_id': 'score-2',
        }
        value.requests[payload['command_id']]['ack'] = ack
        await value._handle_command(payload, ws)
        self.assertEqual(ws.packets[-1], ack)

    async def test_policy_ack_broadcast_reaches_two_clients(self):
        value = service()
        first = FakeClient()
        second = FakeClient()
        value.clients.update((first, second))
        ack = {
            'type': 'ack', 'command_id': str(uuid.uuid4()),
            'command_type': 'set_reject_policy', 'ok': True,
            'policy_version': 'policy-2', 'score_epoch_id': 'score-2',
        }

        await value.broadcast(ack)

        self.assertEqual(first.packets, [ack])
        self.assertEqual(second.packets, [ack])

    async def test_exact_duplicate_recovers_then_expires_without_spawn(self):
        value = service()
        ws = FakeWebSocket()
        payload = command(value)

        await value._handle_command(payload, ws)
        self.assertEqual(len(value.commands.items), 1)
        self.assertEqual(value.epoch_admissions[value.command_epoch], 1)

        await value._handle_command(payload, ws)
        self.assertEqual(ws.packets[-1]['type'], 'pending')
        ack = {'type': 'ack', 'command_id': payload['command_id'], 'command_epoch': value.command_epoch,
               'session_id': value.state['session_id'], 'ok': True, 'object_id': 42,
               'expected_outcome': 'reject', 'expectation_policy_version': 'spawn-policy'}
        value.requests[payload['command_id']]['ack'] = ack
        await value._handle_command(payload, ws)
        self.assertEqual(ws.packets[-1], ack)

        started = value.command_epoch_started
        value._advance_command_epoch(started + COMMAND_EPOCH_SECONDS + 0.1)
        self.assertIn(payload['command_id'], value.requests)
        value._advance_command_epoch(started + 2 * COMMAND_EPOCH_SECONDS + 0.1)
        self.assertNotIn(payload['command_id'], value.requests)
        await value._handle_command(payload, ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_epoch_expired')
        self.assertEqual(len(value.commands.items), 1)

    async def test_pending_duplicate_survives_epoch_expiry(self):
        value = service()
        ws = FakeWebSocket()
        payload = command(value)
        await value._handle_command(payload, ws)

        started = value.command_epoch_started
        value._advance_command_epoch(started + 2 * COMMAND_EPOCH_SECONDS + 0.1)
        self.assertIn(payload['command_id'], value.requests)
        await value._handle_command(payload, ws)
        self.assertEqual(ws.packets[-1]['type'], 'pending')
        self.assertEqual(len(value.commands.items), 1)

    async def test_changed_duplicate_conflicts(self):
        value = service()
        ws = FakeWebSocket()
        payload = command(value)
        await value._handle_command(payload, ws)
        changed = {**payload, 'class_name': 'stick'}
        await value._handle_command(changed, ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_conflict')
        self.assertEqual(len(value.commands.items), 1)

        changed_type = {**payload, 'type': 'restart'}
        await value._handle_command(changed_type, ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_conflict')
        self.assertEqual(len(value.commands.items), 1)

        changed_class = {**payload, 'class_name': None}
        await value._handle_command(changed_class, ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_conflict')
        self.assertEqual(len(value.commands.items), 1)

    async def test_idle_state_broadcasts_advance_application_heartbeat(self):
        value = service()
        client = FakeClient()
        value.clients.add(client)

        await value.broadcast(value._state_packet())
        await value.broadcast(value._state_packet())

        self.assertEqual([packet['heartbeat_seq'] for packet in client.packets], [1, 2])
        self.assertEqual({packet['command_epoch'] for packet in client.packets}, {value.command_epoch})
        self.assertTrue(all(packet['session_id'] == value.state['session_id']
                            for packet in client.packets))

    async def test_blocked_client_cannot_freeze_state_broadcasts(self):
        value = service()
        client = FakeClient(blocked=True)
        value.clients.add(client)

        await asyncio.wait_for(value.broadcast(value._state_packet()), timeout=1.0)
        await asyncio.sleep(0)

        self.assertNotIn(client, value.clients)
        self.assertTrue(client.force_closed)
        self.assertEqual(value.heartbeat_seq, 1)

    async def test_cancelled_broadcast_cleans_blocked_client_task(self):
        value = service()
        client = FakeClient(blocked=True)
        value.clients.add(client)
        broadcast = asyncio.create_task(value.broadcast(value._state_packet()))
        await asyncio.sleep(0)

        broadcast.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await broadcast

        self.assertNotIn(client, value.clients)
        self.assertNotIn(client, value.client_sends)
        self.assertTrue(client.force_closed)

    async def test_failed_send_closes_client(self):
        value = service()
        client = FakeClient(failed=True)
        value.clients.add(client)

        await value.broadcast(value._state_packet())
        await asyncio.sleep(0)

        self.assertNotIn(client, value.clients)
        self.assertTrue(client.force_closed)

    async def test_health_reports_a_stopped_state_pump(self):
        value = service()

        async def fail():
            raise RuntimeError('test pump failure')

        value.task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        response = await value.health(None)

        self.assertEqual(response.status, 503)
        self.assertIn('test pump failure', json.loads(response.text)['error'])

    async def test_health_reports_a_blocked_state_pump(self):
        value = service()
        value.state.update(status='running', session_id='test-session')
        value.last_state_broadcast = time.monotonic() - PUMP_STALE_SECONDS - 0.1
        value.task = asyncio.create_task(asyncio.Event().wait())
        self.addAsyncCleanup(value.task.cancel)

        response = await value.health(None)

        self.assertEqual(response.status, 503)
        self.assertIn('stopped publishing', json.loads(response.text)['error'])

    async def test_pending_and_epoch_limits_reject_without_admission(self):
        value = service()
        ws = FakeWebSocket()
        for _ in range(MAX_PENDING_COMMANDS):
            payload = command(value)
            value.requests[payload['command_id']] = {'payload': payload}
        await value._handle_command(command(value), ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_queue_full')
        self.assertEqual(value.commands.items, [])

        value.requests.clear()
        self.assertEqual(MAX_COMMANDS_PER_EPOCH, 256)
        value.epoch_admissions[value.command_epoch] = MAX_COMMANDS_PER_EPOCH
        await value._handle_command(command(value), ws)
        self.assertEqual(ws.packets[-1]['error_code'], 'command_epoch_full')
        self.assertEqual(value.commands.items, [])

    async def test_continuous_restart_returns_json_error(self):
        value = service()
        response = await value.restart(None)
        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.text)['error_code'], 'restart_unsupported')


class BoundedCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_is_retained_before_worker_can_acknowledge(self):
        value = service(continuous=False)
        ws = FakeWebSocket()
        payload = command(value)
        canonical_id = str(uuid.UUID(payload['command_id']))

        def check_retained(item):
            self.assertIn(canonical_id, value.requests)

        value.commands = RecordingQueue(before_put=check_retained)
        await value._handle_command(payload, ws)
        self.assertEqual(len(value.commands.items), 1)
        self.assertEqual(ws.packets, [])

    async def test_full_queue_rolls_back_retention(self):
        value = service(continuous=False)
        ws = FakeWebSocket()
        payload = command(value)
        canonical_id = str(uuid.UUID(payload['command_id']))
        value.commands = RecordingQueue(full=True)

        await value._handle_command(payload, ws)
        self.assertNotIn(canonical_id, value.requests)
        self.assertIn('queue is full', ws.packets[-1]['error'])


class BoundedSessionGuardTest(unittest.TestCase):
    """A bounded session refuses an injection that could not resolve before it ends.

    The bound is the one physical settling time that rolling_scores defines, so the
    guard and the score ledger agree on when an outcome is final.
    """

    SIM_LIMIT = 2.0

    def bounded_engine(self, stop, script, sim_limit=None):
        limit = self.SIM_LIMIT if sim_limit is None else sim_limit

        class BoundedEngine:
            instance = None

            def __init__(self, preset):
                BoundedEngine.instance = self
                self.continuous = False
                self.preset = {'limits': {'max_sim_seconds': limit, 'max_wall_seconds': 60.0}}
                self.session_id = 'session'
                self.sim = types.SimpleNamespace(data=types.SimpleNamespace(time=0.0))
                self.source_revision, self.source_hashes = 'test', {}
                self.injected = []

            def snapshot(self):
                return {'session_id': self.session_id, 'sim_time_s': self.sim.data.time}

            def inject(self, class_name):
                self.injected.append(class_name)
                return len(self.injected)

            def injection_position(self, object_id):
                return [0.0, 0.0, 0.0]

            def injection_expectation(self, object_id):
                return {'expected_outcome': 'accept', 'expectation_policy_version': 'v1'}

            def step(self):
                # One step carries the session to its last useful injection point.
                self.sim.data.time = limit - live.SETTLING_SECONDS
                if not script:
                    stop.set()

            def report(self):
                return {}

            def close(self):
                pass

        return BoundedEngine

    def run_worker(self, script, sim_limit=None):
        stop = FakeStop()
        states, acks = WorkerQueue(), WorkerQueue()

        class ScriptedQueue(WorkerQueue):
            def get(self, timeout=None):
                return self.get_nowait()

            def get_nowait(self):
                if not script:
                    raise Empty
                return script.pop(0)

        engine = self.bounded_engine(stop, script, sim_limit)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, {'engine': types.SimpleNamespace(Engine=engine)}), \
                patch('live.signal.signal'):
            worker('unused.json', states, acks, ScriptedQueue(), stop, directory)
        return engine.instance, states, acks

    def inject(self, class_name='stone'):
        return {'type': 'inject', 'command_id': str(uuid.uuid4()), 'session_id': 'session',
                'class_name': class_name}

    def test_an_injection_inside_one_settling_time_of_the_end_is_refused(self):
        engine, _, acks = self.run_worker([self.inject(), self.inject()])

        first, second = acks.items
        self.assertTrue(first['ok'])
        self.assertEqual((False, ['stone']), (second['ok'], engine.injected))
        self.assertIn('session is ending', second['error'])
        # The refusal begins exactly one settling time before the limit.
        self.assertEqual(self.SIM_LIMIT - live.SETTLING_SECONDS, engine.sim.data.time)

    def test_a_session_shorter_than_one_settling_time_never_starts(self):
        _, states, _ = self.run_worker([], sim_limit=live.SETTLING_SECONDS)

        failed = states.items[-1]
        self.assertEqual('failed', failed['status'])
        self.assertIn(f'{live.SETTLING_SECONDS:g} to 10', failed['error'])


class ParentDeathTest(unittest.TestCase):
    """The engine worker must not outlive a hard exit of the service process.

    These run the REAL live.worker in a spawned child of a service-like parent
    subprocess, so this test process is the grandparent and survives to observe it.
    A fake engine module is injected through that subprocess environment only.
    """

    # The fix exits in well under 0.1 s. This bound leaves headroom for a loaded machine,
    # and it stays clearly BELOW live.ORPHAN_EXIT_SECONDS. So the forced os._exit fallback
    # can never make this test pass when the feeder cancellation is broken.
    EXIT_BOUND_S = 4.0
    SCRIPT = HERE / 'tests/fakes/parent_death_service.py'
    FAKE_ENGINE = HERE / 'tests/fakes/parent_death_engine'

    def run_service(self, directory, mode=None):
        marker, out = Path(directory) / 'info.json', Path(directory) / 'out'
        out.mkdir()
        # Read the environment, never mutate it, and never print a value from it.
        environment = {**os.environ, 'PYTHONPATH': str(self.FAKE_ENGINE),
                       'CINTA_PARENT_DEATH_OUT': str(out)}
        command = [sys.executable, str(self.SCRIPT), str(marker), str(out)]
        if mode:
            command.append(mode)
        parent = subprocess.Popen(command, env=environment)
        try:
            code = parent.wait(timeout=120)
        except subprocess.TimeoutExpired:
            parent.kill()
            raise
        return code, json.loads(marker.read_text()), out

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def test_a_hard_parent_exit_stops_the_engine_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            code, info, _ = self.run_service(directory)
            worker_pid = info['worker_pid']
            try:
                self.assertEqual(code, 17)
                self.assertLessEqual(self.EXIT_BOUND_S * 2, live.ORPHAN_EXIT_SECONDS)
                # The states queue was never drained and its pipe is full, so a blocked
                # feeder thread could otherwise keep this process alive after stop.
                self.assertTrue(info['engine_filled'])
                deadline = time.monotonic() + self.EXIT_BOUND_S
                while time.monotonic() < deadline and self.alive(worker_pid):
                    time.sleep(0.02)
                self.assertFalse(self.alive(worker_pid),
                                 'the engine worker outlived its service process')
            finally:
                # A failing assertion must never leave an engine behind.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(worker_pid, signal.SIGKILL)

    def test_a_graceful_stop_still_delivers_a_final_acknowledgment(self):
        with tempfile.TemporaryDirectory() as directory:
            code, info, out = self.run_service(directory, mode='graceful')
            worker_pid = info['worker_pid']
            try:
                self.assertEqual(code, 0)
                result = json.loads((out / 'graceful.json').read_text())
                acknowledgment = result['acknowledgment']
                self.assertEqual(acknowledgment['type'], 'ack')
                self.assertEqual(acknowledgment['command_id'], 'graceful-command')
                self.assertTrue(acknowledgment['ok'])
                self.assertEqual(acknowledgment['object_id'], 1)
                self.assertEqual(result['exitcode'], 0)
                # The parent read the acknowledgment only AFTER the worker had handled the
                # command, received stop, and exited. So normal queue finalization flushed it.
                self.assertTrue(result['command_handled_before_stop'])
                self.assertTrue(result['worker_exited_before_read'])
                self.assertFalse(self.alive(worker_pid))
            finally:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(worker_pid, signal.SIGKILL)


class OneEngineTest(unittest.IsolatedAsyncioTestCase):
    """Exactly ONE engine exists after a restart and after an activation swap.

    Engines are counted by process id and liveness, never by log text. The fake engine
    module of the parent-death regression is reused through the child import path, so no
    mujoco, no model, and no catalog is needed. Every worker is killed in `finally`.
    """

    FAKE_ENGINE = ParentDeathTest.FAKE_ENGINE
    EXIT_BOUND_S = ParentDeathTest.EXIT_BOUND_S
    # One service-like parent. It starts the REAL live.worker and never drains the states
    # queue, exactly as the parent-death regression does.
    # The spawn sits under a main guard, or the spawned child re-executes this module and
    # never reaches the worker loop. The parent publishes readiness only after the child
    # PROVES it runs the real loop, and it exits with its own code if the child dies.
    READY = 'engine-filled'
    SERVICE = (
        'import json, os, sys, time\n'
        'from pathlib import Path\n'
        'import multiprocessing as mp\n'
        'import live\n'
        '\n'
        '\n'
        'def main():\n'
        '    marker, out, mode, lifetime = sys.argv[2:6]\n'
        '    Path(out).mkdir(parents=True, exist_ok=True)\n'
        '    ctx = mp.get_context("spawn")\n'
        '    states, acks, commands = ctx.Queue(maxsize=8), ctx.Queue(), ctx.Queue()\n'
        '    stop = ctx.Event()\n'
        '    process = ctx.Process(target=live.worker,\n'
        '                          args=("unused.json", states, acks, commands, stop, out))\n'
        '    process.start()\n'
        '    Path(marker).write_text(json.dumps({"worker_pid": process.pid}))\n'
        '    ready = Path(out) / "engine-filled"\n'
        '    deadline = time.monotonic() + 60\n'
        '    while time.monotonic() < deadline and not ready.exists():\n'
        '        if not process.is_alive():\n'
        '            os._exit(9)\n'
        '        time.sleep(0.02)\n'
        '    if not ready.exists():\n'
        '        os._exit(10)\n'
        '    if mode == "hard":\n'
        '        os._exit(17)\n'
        '    time.sleep(float(lifetime))\n'
        '    os._exit(0)\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n')

    @staticmethod
    def alive(pid):
        """LIVE, not merely a PID. A zombie has already exited and is never an engine.

        This reuses the queue's own group listing and its zombie rule, so one process
        state convention covers the runner and this test.
        """
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return False
        members = item_jobs._group_members(pgid)
        if members is None:
            # No listing available. The signal probe is the existing fallback.
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return False
            return True
        return any(member == pid and not state.startswith('Z') for member, state in members)

    def kill_later(self, *pids):
        def clean():
            for pid in pids:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
        self.addCleanup(clean)

    def wait_gone(self, pid, bound=None):
        deadline = time.monotonic() + (self.EXIT_BOUND_S if bound is None else bound)
        while time.monotonic() < deadline and self.alive(pid):
            time.sleep(0.02)
        return not self.alive(pid)

    def start_service(self, directory, mode, lifetime=0.0, script_text=None):
        """Start one service-like parent whose engine PROVED it runs the real loop."""
        marker, out = Path(directory) / 'info.json', Path(directory) / 'out'
        out.mkdir(parents=True, exist_ok=True)
        script = Path(directory) / 'service.py'
        script.write_text(self.SERVICE if script_text is None else script_text)
        # Read the environment, never mutate it, and never print a value from it. The
        # spawned grandchild re-imports this script, so its imports must come from the
        # path, never from an argv that multiprocessing replaces. The fake engine leads,
        # so `import engine` in the worker finds it and never the real one.
        environment = {**os.environ,
                       'PYTHONPATH': os.pathsep.join([str(self.FAKE_ENGINE), str(HERE)]),
                       'CINTA_PARENT_DEATH_OUT': str(out)}
        parent = subprocess.Popen(
            [sys.executable, str(script), str(HERE), str(marker), str(out), mode, str(lifetime)],
            env=environment)
        ready, deadline = out / self.READY, time.monotonic() + 90
        while time.monotonic() < deadline and not ready.exists():
            if parent.poll() is not None:
                break
            time.sleep(0.02)
        # A published PID is not an engine. The readiness marker is written by the real
        # worker loop, so it is the only proof that one exists.
        self.assertTrue(ready.exists(), 'the engine never proved that it runs')
        return parent, json.loads(marker.read_text())['worker_pid']

    def test_a_restart_after_a_hard_parent_death_leaves_exactly_one_engine(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            killed, orphan = self.start_service(first, 'hard')
            self.kill_later(orphan)
            self.assertEqual(17, killed.wait(timeout=120))

            # The service restarts while the abandoned engine may still be running.
            restarted, engine = self.start_service(second, 'live', lifetime=self.EXIT_BOUND_S * 3)
            self.kill_later(engine)
            self.addCleanup(restarted.kill)

            self.assertNotEqual(orphan, engine)
            self.assertTrue(self.wait_gone(orphan),
                            'the abandoned engine outlived its dead service')
            # Exactly one engine, counted by liveness of every pid this test started.
            self.assertTrue(self.alive(engine), 'the restarted engine is not running')
            self.assertEqual([engine], [pid for pid in (orphan, engine) if self.alive(pid)])

    def test_the_restart_proof_fails_without_the_spawn_main_guard(self):
        """The regression above must be able to go red. Remove the guard and it does."""
        unguarded = self.SERVICE.replace('if __name__ == "__main__":\n    main()\n', 'main()\n')

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AssertionError, 'never proved that it runs'):
                self.start_service(directory, 'hard', script_text=unguarded)

    async def running_service(self, directory):
        """One real service object with one real spawned worker and its real pump."""
        out = Path(directory) / 'out'
        out.mkdir(parents=True, exist_ok=True)
        # The constructor already built the first worker handle.
        value = LiveService(HERE / 'configs' / 'continuous_demo.json', out)
        value.process.start()
        value.task = asyncio.create_task(value.pump())
        await asyncio.wait_for(value.state_ready.wait(), timeout=30)
        return value

    async def test_one_real_swap_leaves_exactly_one_engine(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            # The spawned child imports the fake engine through its own path.
            os.environ['PYTHONPATH'] = str(self.FAKE_ENGINE)
            os.environ['CINTA_PARENT_DEATH_OUT'] = str(Path(directory) / 'out')
            value = await self.running_service(directory)
            before = value.process.pid
            self.kill_later(before)
            try:
                await value._swap_worker(value.state.get('session_id'))
                after = value.process.pid
                self.kill_later(after)

                self.assertNotEqual(before, after)
                self.assertTrue(self.wait_gone(before),
                                'the replaced engine outlived its swap')
                self.assertTrue(self.alive(after))
                self.assertEqual([after], [pid for pid in (before, after) if self.alive(pid)])
            finally:
                await value._cancel_pump()
                await value._stop_worker()
                value._close_queues()


class ItemJobRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_retry_returns_the_same_job_and_a_changed_payload_conflicts(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())

        created = await value.submit_item_job(item_request(request_id=request_id,
                                                           requester_name='Taras'))
        retried = await value.submit_item_job(item_request(request_id=request_id,
                                                           requester_name='Taras'))
        changed = await value.submit_item_job(item_request('A brass moon token',
                                                          request_id=request_id))

        self.assertEqual(created.status, 201)
        self.assertEqual(retried.status, 200)
        body = json.loads(retried.text)
        self.assertFalse(body['created'])
        self.assertEqual(body['job']['request_id'], request_id)
        self.assertEqual(body['job']['requester_name'], 'Taras')
        self.assertEqual(body['job']['state'], 'queued')
        self.assertEqual(changed.status, 409)
        self.assertEqual(json.loads(changed.text)['error_code'], 'request_conflict')
        self.assertEqual(len(value.item_jobs.jobs()), 1)

    async def test_queue_limit_stale_revision_and_invalid_description(self):
        value = item_service(self)
        for _ in range(item_jobs.MAX_QUEUED_JOBS):
            self.assertEqual((await value.submit_item_job(item_request())).status, 201)

        full = await value.submit_item_job(item_request())
        stale = await value.submit_item_job(
            FakeRequest({'request_id': str(uuid.uuid4()), 'description': 'A token',
                         'expected_catalog_revision': 'b' * 64}))
        empty = await value.submit_item_job(item_request(description='   '))

        self.assertEqual(full.status, 429)
        self.assertEqual(json.loads(full.text)['error_code'], 'queue_full')
        self.assertEqual(stale.status, 409)
        self.assertEqual(json.loads(stale.text)['error_code'], 'catalog_revision_conflict')
        self.assertEqual(empty.status, 400)
        self.assertEqual(json.loads(empty.text)['error_code'], 'invalid_description')

    async def test_item_job_posts_require_a_present_origin(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))

        for response in (await value.submit_item_job(item_request(origin=None)),
                         await value.resolve_item_provider(
                             FakeRequest({'action': 'use_cache'}, origin=None,
                                         request_id=request_id)),
                         await value.resolve_item_replacement(
                             FakeRequest(origin=None, request_id=request_id)),
                         await value.confirm_item_cleanup(
                             FakeRequest(origin=None, request_id=request_id))):
            self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.text)['error_code'], 'origin_required')
        self.assertEqual(len(value.item_jobs.jobs()), 1)

    async def test_unknown_job_and_preview_name_allowlist(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))

        missing = await value.get_item_job(FakeRequest(request_id=str(uuid.uuid4())))
        malformed = await value.get_item_job(FakeRequest(request_id='../../etc'))
        blocked = await value.item_job_preview(
            FakeRequest(request_id=request_id, name='object.blend'))
        too_early = await value.item_job_preview(
            FakeRequest(request_id=request_id, name='perspective.png'))

        self.assertEqual(missing.status, 404)
        self.assertEqual(json.loads(missing.text)['error_code'], 'unknown_job')
        self.assertEqual(malformed.status, 400)
        self.assertEqual(json.loads(blocked.text)['error_code'], 'unknown_preview')
        self.assertEqual(json.loads(too_early.text)['error_code'], 'preview_unavailable')

        previews = value.item_jobs.job_dir(request_id) / 'previews'
        (previews / 'perspective.png').write_bytes(b'fixture')
        value.item_jobs.transition(request_id, 'preview_ready', artifacts={'previews': {
            'perspective.png': {'sha256': hashlib.sha256(b'fixture').hexdigest(), 'bytes': 7}}})
        served = await value.item_job_preview(
            FakeRequest(request_id=request_id, name='perspective.png'))
        self.assertEqual(served.status, 200)
        self.assertEqual(served.body, b'fixture')

    async def test_get_item_job_never_publishes_the_worker_token(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))
        value.item_jobs.record(request_id, worker={
            'stage': 'render', 'pid': 1, 'pgid': 1, 'token': 'secret',
            'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
            'pid_start': None})

        response = await value.get_item_job(FakeRequest(request_id=request_id))

        self.assertEqual(response.status, 200)
        self.assertNotIn('secret', response.text)
        self.assertEqual(json.loads(response.text)['job']['worker']['stage'], 'render')

    async def test_replacement_resolution_stays_unavailable_in_this_phase(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))

        response = await value.resolve_item_replacement(FakeRequest(request_id=request_id))

        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.text)['error_code'], 'not_available')

    async def test_wall_of_fame_reports_bounded_pages_and_rejects_invalid_paging(self):
        value = item_service(self)

        page = await value.wall_of_fame(FakeRequest(query={'offset': '0', 'limit': '12'}))
        negative = await value.wall_of_fame(FakeRequest(query={'offset': '-1'}))
        text = await value.wall_of_fame(FakeRequest(query={'limit': 'all'}))

        body = json.loads(page.text)
        self.assertEqual(page.status, 200)
        self.assertEqual(body['limit'], 12)
        self.assertLessEqual(len(body['entries']), 12)
        self.assertEqual(negative.status, 400)
        self.assertEqual(text.status, 400)

    async def test_the_wall_of_fame_reads_the_history_root_of_an_active_bundle(self):
        from test_object_catalog import builtin_definition

        value = item_service(self)
        value.history_root = value.item_jobs_root / 'history'
        object_catalog.archive_type(value.history_root, builtin_definition('stone'),
                                    '2026-09-20T01:00:00Z')

        body = json.loads((await value.wall_of_fame(FakeRequest())).text)

        self.assertEqual(body['total'], 1)
        self.assertEqual(body['entries'][0]['object_type_id'], 'builtin.test.stone')

    async def test_new_request_is_refused_with_paid_mode_disabled(self):
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))
        value.item_jobs.transition(request_id, 'operator_required', error='provider_cache_miss')

        refused = await value.resolve_item_provider(
            FakeRequest({'action': 'new_request'}, request_id=request_id))
        cached = await value.resolve_item_provider(
            FakeRequest({'action': 'use_cache'}, request_id=request_id))

        self.assertEqual(refused.status, 403)
        self.assertEqual(json.loads(refused.text)['error_code'], 'paid_mode_disabled')
        self.assertEqual(cached.status, 200)
        self.assertEqual(json.loads(cached.text)['job']['state'], 'generating_recipe')
        self.assertIsNone(value.item_jobs.get(request_id)['provider_permission'])

    async def test_the_service_never_opens_the_credential_file(self):
        value = item_service(self, provider_mode='paid')
        names_before = sorted(os.environ)
        opened = []
        real_open = builtins.open

        def recording_open(path, *arguments, **keywords):
            opened.append(str(path))
            return real_open(path, *arguments, **keywords)

        with patch.object(builtins, 'open', recording_open):
            commands = value._item_commands()
            argv = commands['generation'](
                {'description': 'A token', 'attempts': {'generation': 1},
                 'provider_permission': None}, value.item_jobs_root / 'jobs' / 'x')

        credential = str(value.provider_env)
        self.assertNotIn(credential, opened)
        self.assertIn(credential, argv)
        self.assertNotIn('--live', argv)
        # Names only. The service adds no provider name to its own environment, and the
        # engine child inherits that environment. A value is never held or printed here.
        self.assertEqual(sorted(os.environ), names_before)
        self.assertNotIn('CINTA_PROVIDER_KEY', sorted(os.environ))

    def test_startup_validation_refuses_an_unsafe_provider_configuration(self):
        parser = StubParser()
        root = Path('/tmp/cinta-jobs').resolve()
        credential = Path(tempfile.mkdtemp()) / 'provider.env'
        credential.write_text('')
        self.addCleanup(credential.unlink)
        cases = [
            (item_arguments(host='0.0.0.0', item_jobs_provider='fake'),
             '--item-jobs-provider fake requires the loopback host'),
            (item_arguments(item_jobs_provider='paid', item_jobs_provider_env=None),
             'requires --item-jobs-provider-env'),
            (item_arguments(item_jobs_provider_cache=Path('/tmp/cinta-absent-cache')),
             '--item-jobs-provider-cache does not exist'),
            (item_arguments(item_jobs_generator_root=Path('/tmp/cinta-absent-generator')),
             '--item-jobs-generator-root does not exist'),
        ]
        for arguments, message in cases:
            with self.assertRaises(ValueError) as raised:
                live._validate_item_job_arguments(parser, arguments, root)
            self.assertIn(message, str(raised.exception))

        # T6: a real credential file inside a real job root must be refused for that
        # reason, not because the path happens to be absent.
        real_root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, real_root, ignore_errors=True)
        inside = real_root / 'provider.env'
        inside.write_text('')
        with self.assertRaises(ValueError) as raised:
            live._validate_item_job_arguments(
                parser, item_arguments(item_jobs_provider='paid',
                                       item_jobs_provider_env=inside), real_root)
        self.assertIn('must stay outside --item-jobs-root', str(raised.exception))

        # Compatible defaults keep starting: no cache path and no credential is not a contradiction.
        live._validate_item_job_arguments(parser, item_arguments(item_jobs_provider_cache=None), root)
        live._validate_item_job_arguments(
            parser, item_arguments(item_jobs_provider='fake', item_jobs_provider_cache=None), root)
        live._validate_item_job_arguments(
            parser, item_arguments(item_jobs_provider='paid',
                                   item_jobs_provider_env=credential), root)

    async def test_deployment_controlled_fields_are_refused_in_a_request_body(self):
        value = item_service(self)
        for name, field in (('source_revision', 'c' * 40), ('provider_mode', 'paid'),
                            ('live', True), ('env_file', '/etc/passwd')):
            response = await value.submit_item_job(item_request(**{name: field}))
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.text)['error_code'], 'invalid_request')
        self.assertEqual(value.item_jobs.jobs(), [])

    async def test_no_public_response_carries_a_host_path(self):
        """M1: worker.log and artifacts.runtime_lock are absolute host paths."""
        value = item_service(self)
        request_id = str(uuid.uuid4())
        await value.submit_item_job(item_request(request_id=request_id))
        value.item_jobs.record(request_id, worker={
            'stage': 'render', 'pid': 1, 'pgid': 1, 'token': 'secret',
            'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
            'pid_start': None, 'log': str(value.item_jobs_root / 'jobs' / request_id / 'render.log')})
        job_dir = value.item_jobs.job_dir(request_id)
        # F5: every Phase 3 record field, populated with realistic values that DO carry
        # absolute paths. A top-level deny-list cannot see inside nested child evidence.
        value.item_jobs.record(request_id, token='secret', artifacts={
            'runtime_lock': str(value.runtime_lock),
            'previews': {},
            'physics': {'physics_source': 'cached_llm_replay',
                        'physics_measurement_status': 'unmeasured_proxy_estimate',
                        'cache': str(job_dir / 'physics' / 'cache')},
            'physics_route': {'verdict': 'accept', 'reason': None,
                              'definition_sha256': 'a' * 64,
                              'detail': f'read {job_dir}/definition.json'},
            'candidate_validation': {
                'passed': True, 'failures': [],
                'source': {'sim.py': 'b' * 64},
                'policy': {'applied_reject_classes': ['stone']},
                'report': str(job_dir / 'training' / 'out' / 'candidate.report.json'),
                'runs': [{'model': str(job_dir / 'training' / 'out' / 'candidate.joblib')}]},
        })
        value.item_jobs.record(request_id, token='secret', reason='route_not_accepted',
                               progress=f'the validator wrote {job_dir}/physics/result.json',
                               blocked_stage='physics_proposal', training_baseline={
                                   'catalog_revision': 'c' * 64, 'victim_id': 'builtin.x',
                                   'policy_version': 'policy-1', 'reject_classes': ['stone'],
                                   'catalog_root': str(job_dir / 'training' / 'catalog')})
        value.item_jobs_state = value._item_jobs_packet()

        body = json.loads((await value.get_item_job(FakeRequest(request_id=request_id))).text)
        packet = value._state_packet()
        root = str(value.item_jobs_root)

        for label, document in (('job', body), ('state', packet)):
            for text in _string_values(document):
                self.assertNotIn(root, text, f'{label} leaked the job root')
                self.assertNotIn(str(value.runtime_lock), text, f'{label} leaked the lock path')
                if text.startswith('/'):
                    # The only permitted absolute value is a route, not a host path.
                    self.assertTrue(text.startswith('/item-jobs/'), f'{label}: {text}')
        self.assertNotIn('secret', json.dumps(body))
        self.assertIsNone(body['job']['worker'].get('log'))
        self.assertNotIn('runtime_lock', body['job']['artifacts'])
        # The evidence survives, with its paths redacted rather than the record dropped.
        self.assertEqual('accept', body['job']['artifacts']['physics_route']['verdict'])
        self.assertTrue(body['job']['artifacts']['candidate_validation']['passed'])
        self.assertEqual('route_not_accepted', body['job']['reason'])
        self.assertEqual('physics_proposal', body['job']['blocked_stage'])
        self.assertIn('<path>', body['job']['progress'])

    def test_the_preview_route_survives_the_host_path_redaction(self):
        """The summary preview is a route of this service. Redacting it blanks the thumbnail."""
        request_id = str(uuid.uuid4())
        route = f'/item-jobs/{request_id}/previews/perspective.png'

        public = live._without_host_paths(
            {'preview': route, 'progress': f'wrote /var/lib/jobs/{request_id}/perspective.png',
             'forged': f'{route}/../../../etc/passwd'})

        self.assertEqual(route, public['preview'])
        self.assertNotIn('/var/lib', public['progress'])
        self.assertNotIn('passwd', public['forged'])

    async def test_the_validated_request_text_is_published_verbatim(self):
        """A URL or a slash in the request text is content. Child text keeps its redaction."""
        value = item_service(self)
        request_id = str(uuid.uuid4())
        description = 'A star, see https://example.com/star at ratio 3 /4, /not/a/host/path in my text'
        requester = 'Taras https://example.com/me 3 /4'
        await value.submit_item_job(item_request(description, request_id,
                                                 requester_name=requester))
        job_dir = value.item_jobs.job_dir(request_id)
        value.item_jobs.record(request_id, progress=f'the renderer wrote {job_dir}/render.json')
        value.item_jobs_state = value._item_jobs_packet()

        job = json.loads((await value.get_item_job(FakeRequest(request_id=request_id))).text)['job']
        summary = value._state_packet()['item_jobs']['summaries'][0]

        for label, record in (('job', job), ('summary', summary)):
            with self.subTest(record=label):
                self.assertEqual(record['description'], description)
                self.assertEqual(record['requester_name'], requester)
                self.assertEqual(record['request_id'], request_id)
        self.assertEqual(job['admission_catalog_revision'], CATALOG_REVISION)
        self.assertIn('<path>', job['progress'])
        self.assertNotIn(str(value.item_jobs_root), json.dumps([job, summary]))

    def test_every_documented_launch_command_is_accepted(self):
        """A documented command the real parser refuses is a broken document."""
        repository = Path(live.HERE).parents[1]
        document = (Path(live.HERE) / 'LIVE.md').read_text()
        commands = [line.strip() for line in document.splitlines()
                    if 'live.py' in line and line.strip().startswith(('python', '.venv'))]
        self.assertGreaterEqual(len(commands), 4)
        parser = live.build_parser()
        previous = os.getcwd()
        os.chdir(repository)
        try:
            for command in commands:
                words = shlex.split(command)
                arguments = words[words.index('sim/coffee_sorter/live.py') + 1:]
                parsed = parser.parse_args(arguments)
                root = (parsed.item_jobs_root or parsed.out / 'item-jobs').resolve()
                live._validate_item_job_arguments(StubParser(), parsed, root)
        finally:
            os.chdir(previous)

    def test_a_plain_local_command_still_starts_with_compatible_defaults(self):
        parser = live.build_parser()
        parsed = parser.parse_args(['--preset', 'sim/coffee_sorter/configs/default_demo.json',
                                    '--out', '/tmp/cinta-plain'])

        self.assertEqual(parsed.item_jobs_provider, 'cached')
        self.assertIsNone(parsed.item_jobs_provider_cache)
        self.assertEqual(parsed.item_jobs_generator_root, item_jobs.GENERATOR_ROOT)
        self.assertEqual(parsed.item_jobs_runtime_lock, live.DEFAULT_RUNTIME_LOCK)
        # No contradiction, so the service starts and the queue uses the local defaults.
        live._validate_item_job_arguments(StubParser(), parsed, Path('/tmp/cinta-plain/item-jobs'))

    async def test_state_packet_carries_item_job_summaries_and_limits(self):
        value = item_service(self)
        await value.submit_item_job(item_request(requester_name='Taras'))
        value.item_jobs_state = value._item_jobs_packet()

        packet = value._state_packet()

        self.assertEqual(packet['item_jobs']['limits'],
                         {'max_queued_jobs': 4, 'max_retained_open_jobs': 32,
                          'max_retained_jobs': 256, 'max_summaries': 32,
                          'max_attempts': 2})
        self.assertEqual(packet['item_jobs']['provider_mode'], 'cached')
        self.assertEqual(packet['item_jobs']['catalog_revision'], CATALOG_REVISION)
        self.assertEqual(packet['item_jobs']['active_type_ids'], ['builtin.green_arabica.good'])
        summary = packet['item_jobs']['summaries'][0]
        self.assertEqual(summary['requester_name'], 'Taras')
        self.assertEqual(summary['state'], 'queued')
        self.assertIsNone(summary['preview'])
        self.assertIsNone(summary['primary_action'])
        self.assertNotIn('item_jobs', service()._state_packet())


class ActivationEngine:
    """A fake engine for the activation commands: a feed rate, a belt count, and a policy."""

    def __init__(self, preset=None, active=2, reject=('stone',)):
        self.continuous = True
        self.preset = {'limits': {'max_sim_seconds': None, 'max_wall_seconds': None}}
        self.session_id = 'session'
        self.source_revision, self.source_hashes = 'test', {}
        self.active = active
        self.sim = types.SimpleNamespace(rate=30.0, n_active=lambda: self.active,
                                         data=types.SimpleNamespace(time=1.0))
        self.score_epoch_id = 'epoch-1'
        self.set_reject_classes(list(reject))
        self.on_step = lambda: None

    @property
    def policy_version(self):
        return 'policy:' + ','.join(self.reject_classes)

    def reject_policy(self):
        return {'reject_classes': list(self.reject_classes),
                'policy_version': self.policy_version, 'score_epoch_id': self.score_epoch_id}

    def set_reject_classes(self, reject_classes):
        self.reject_classes = list(reject_classes)
        return {**self.reject_policy(), 'changed': True}

    def snapshot(self):
        return {'session_id': self.session_id, 'sim_time_s': self.sim.data.time}

    def rolling_scores(self):
        return {'score_epoch_id': self.score_epoch_id}

    def step(self):
        self.active = max(0, self.active - 1)
        self.on_step()

    def report(self):
        return {}

    def close(self):
        pass


def activation_command(kind, job_id='job-1', **fields):
    return {'type': kind, 'command_id': str(uuid.uuid4()), 'session_id': 'session',
            'command_epoch': 'epoch', 'job_id': job_id, **fields}


class ActivationWorkerTest(unittest.TestCase):
    """prepare, drain, commit with the policy fence, and cancel, inside the worker loop."""

    def setUp(self):
        self.revision = object_catalog.load_catalog(
            object_catalog.PACKAGED_CATALOG_ROOT)['catalog_revision']

    def prepare(self, victim='stick', revision=None, job_id='job-1'):
        return activation_command('prepare_activation', job_id, victim_label=victim,
                                  expected_catalog_revision=revision or self.revision)

    def apply(self, engine, command, activation):
        ack = {}
        return live._apply_activation_command(engine, command, activation, ack), ack

    def test_a_stale_catalog_or_a_rejected_victim_pauses_nothing(self):
        engine = ActivationEngine()
        cases = {'activation_conflict': self.prepare(revision='f' * 64),
                 'replacement_conflict': self.prepare(victim='stone')}
        for code, command in cases.items():
            with self.subTest(code=code):
                activation, ack = self.apply(engine, command, None)

                self.assertIsNone(activation)
                self.assertEqual((ack['ok'], ack['error_code']), (False, code))
                self.assertEqual(engine.sim.rate, 30.0)

    def test_a_commit_is_refused_while_an_object_is_on_the_belt(self):
        engine = ActivationEngine(active=1)
        activation, ack = self.apply(engine, self.prepare(), None)
        self.assertEqual((ack['ok'], ack['phase'], ack['active_objects']), (True, 'draining', 1))
        self.assertEqual(engine.sim.rate, 0.0)

        activation, ack = self.apply(engine, activation_command('commit_activation'), activation)

        self.assertEqual((ack['ok'], ack['error_code']), (False, 'activation_not_drained'))
        self.assertEqual(activation['phase'], 'draining')
        # Another job cannot take the drain over, and an unknown job commits nothing.
        _, other = self.apply(engine, self.prepare(job_id='job-2'), activation)
        self.assertEqual(other['error_code'], 'activation_in_progress')
        _, unknown = self.apply(engine, activation_command('commit_activation', 'job-2'), activation)
        self.assertEqual(unknown['error_code'], 'activation_not_prepared')

    def test_a_victim_rejected_during_the_drain_resumes_the_old_rate(self):
        engine = ActivationEngine(active=0)
        activation, _ = self.apply(engine, self.prepare(), None)
        engine.set_reject_classes(['stone', 'stick'])

        activation, ack = self.apply(engine, activation_command('commit_activation'), activation)

        self.assertIsNone(activation)
        self.assertEqual((ack['ok'], ack['error_code']), (False, 'replacement_conflict'))
        self.assertEqual(engine.sim.rate, 30.0)
        _, idle = self.apply(engine, activation_command('cancel_activation'), None)
        self.assertEqual((idle['ok'], idle['cancelled']), (True, False))

    def test_the_worker_loop_drains_fences_and_cancels(self):
        stop = FakeStop()
        created = []

        def policy(reject_classes, expected):
            return {'type': 'set_reject_policy', 'command_id': str(uuid.uuid4()),
                    'session_id': 'session', 'command_epoch': 'epoch',
                    'expected_policy_version': expected, 'reject_classes': reject_classes}

        script = [self.prepare(), policy(['stone', 'black'], 'policy:stone'),
                  activation_command('commit_activation'),
                  policy(['stone'], 'policy:stone,black'),
                  activation_command('commit_activation'),
                  activation_command('cancel_activation'),
                  policy(['stone'], 'policy:stone,black')]

        class ScriptedQueue(WorkerQueue):
            def get_nowait(self):
                if not script:
                    raise __import__('queue').Empty
                return script.pop(0)

        def build(preset):
            engine = ActivationEngine(preset)
            engine.on_step = lambda: script or stop.set()
            created.append(engine)
            return engine

        states, acks = WorkerQueue(), WorkerQueue()
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, {'engine': types.SimpleNamespace(Engine=build)}), \
                patch('live.signal.signal'):
            worker('unused.json', states, acks, ScriptedQueue(), stop, directory)

        prepared, drained, committed, fenced, retried, cancelled, reopened = acks.items
        self.assertEqual((prepared['ok'], prepared['active_objects']), (True, 2))
        # A survivor toggle during the drain applies, and the commit captures the LATEST policy.
        self.assertTrue(drained['ok'])
        self.assertEqual(committed['reject_classes'], ['stone', 'black'])
        self.assertEqual(committed['policy_version'], 'policy:stone,black')
        self.assertEqual((fenced['ok'], fenced['error_code']), (False, 'activation_in_progress'))
        self.assertEqual(retried['reject_classes'], committed['reject_classes'])
        self.assertEqual((cancelled['ok'], cancelled['cancelled']), (True, True))
        self.assertTrue(reopened['ok'])
        self.assertEqual(created[0].reject_classes, ['stone'])
        self.assertEqual(created[0].sim.rate, 30.0)
        phases = [state['activation']['phase'] for state in states.items if 'activation' in state]
        self.assertEqual(sorted(set(phases)), ['activating', 'draining'])
        self.assertEqual(states.items[1]['activation'],
                         {'job_id': 'job-1', 'phase': 'draining', 'active_objects': 2})
        self.assertNotIn('activation', states.items[-1])


class RecordingJobs:
    """The one injected reporting seam. The activator never touches a queue store."""

    def __init__(self):
        self.blocks = []

    def __call__(self, job_id, **fields):
        self.blocks.append((job_id, fields['activation']))

    @property
    def phases(self):
        return [block['phase'] for _, block in self.blocks]

    @property
    def last(self):
        return self.blocks[-1][1]


class ActivationLoopback:
    """The REAL worker command logic behind the REAL worker envelope.

    `live.worker` reads `session_id` from every command before it dispatches, so this
    applies the same check. A command that omits it raises here exactly as it would
    raise in the worker loop.
    """

    def __init__(self, service, engine, withhold=()):
        self.service, self.engine = service, engine
        self.activation = None
        self.sent = []
        self.withhold = set(withhold)

    def put_nowait(self, payload):
        self.sent.append(payload)
        if payload['session_id'] != self.engine.session_id:
            raise AssertionError('an internal command carries a foreign session id')
        if payload['type'] in self.withhold:
            return
        ack = {'type': 'ack', 'command_id': payload['command_id']}
        self.activation = live._apply_activation_command(
            self.engine, payload, self.activation, ack)
        self.service.requests[payload['command_id']]['ack'] = ack

    @property
    def kinds(self):
        return [payload['type'] for payload in self.sent]


class ActivatorTest(unittest.IsolatedAsyncioTestCase):
    """The activation sequence on a real seeded root, with the real worker command logic.

    No engine process, no server, and no port. `_swap_worker` is replaced by a fake that
    records one swap and publishes a new session and a new score epoch, exactly as a real
    one does. Slice B2 drives the real swap through the service path.
    """

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name).resolve()
        self.root = self.work / 'item-jobs'
        self.packaged = object_catalog.load_catalog(object_catalog.PACKAGED_CATALOG_ROOT)
        self.jobs = RecordingJobs()
        self.swaps = []
        self.preset = self.write_preset('packaged', self.packaged['catalog_revision'])
        # The real worker reads the exported catalog in its own child. The loopback runs
        # here, so the export is scoped to this test and never reaches the next one.
        self.enterContext(patch.dict(os.environ))
        self.service = self.build_service()
        os.environ[object_catalog.CATALOG_ROOT_ENV] = str(self.service.active_bundle / 'catalog')
        self.engine = ActivationEngine(active=0)
        self.engine.session_id = 'session-before'
        self.service.commands = ActivationLoopback(self.service, self.engine)
        self.service.state = {'status': 'running', 'session_id': 'session-before',
                              'reject_policy': self.engine.reject_policy()}

    def write_preset(self, name, revision, labels=None):
        """The real continuous preset beside a tiny model that records this revision."""
        from test_validate_bundle import CONTINUOUS_PRESET, continuous_model

        directory = self.work / name
        directory.mkdir()
        preset = {**json.loads(CONTINUOUS_PRESET.read_text()),
                  'model_path': str(directory / 'live_green_arabica.joblib')}
        model, manifest = continuous_model(
            labels or object_catalog.catalog_labels(self.packaged), preset, revision)
        (directory / 'live_green_arabica.joblib').write_bytes(model)
        (directory / 'live_green_arabica.manifest.json').write_bytes(manifest)
        (directory / 'preset.json').write_text(json.dumps(preset))
        return directory / 'preset.json'

    def build_service(self):
        """Seed bundle zero directly, so this process never rebinds its loaded profiles.

        Bundle zero holds the packaged catalog, which is the one this process already
        loaded, so the model label order still matches.
        """
        preset = json.loads(self.preset.read_text())
        model = self.preset.parent / 'live_green_arabica.joblib'
        files = object_catalog.seed_bundle_files(
            object_catalog.PACKAGED_CATALOG_ROOT, model, model.with_suffix('.manifest.json'),
            preset, {'reject_classes': live.default_reject_classes(self.packaged, preset)},
            live._bundle_sources())
        digest = object_catalog.publish_bundle(self.root / 'active' / 'bundles', files)
        object_catalog.write_active_pointer(self.root / 'active', digest)
        bundle = self.root / 'active' / 'bundles' / digest
        return LiveService(bundle / object_catalog.BUNDLE_PRESET, self.work / 'out',
                           item_jobs_root=self.root, item_jobs_provider='fake',
                           active_bundle=bundle, record=self.jobs)

    def candidate(self, victim_id=None, **changes):
        """The artifacts a finished training leaves: a candidate catalog, model, and preset."""
        from test_object_catalog import generated_definition
        from test_validate_bundle import CONTINUOUS_PRESET, continuous_model

        active = object_catalog.read_active(self.root / 'active')
        victim_id = victim_id or active['active_type_ids'][-1]
        catalog = object_catalog.candidate_catalog(
            active, generated_definition('star_token'), victim_id)
        directory = self.work / f'candidate-{uuid.uuid4().hex[:8]}'
        directory.mkdir()
        object_catalog.write_catalog(directory / 'catalog', catalog)
        labels = object_catalog.catalog_labels(catalog)
        preset = {**json.loads(CONTINUOUS_PRESET.read_text()), 'model_path': 'candidate.joblib',
                  'model_path_root': 'preset'}
        model, manifest = continuous_model(labels, preset, catalog['catalog_revision'])
        (directory / 'candidate.joblib').write_bytes(model)
        (directory / 'candidate.manifest.json').write_bytes(manifest)
        (directory / 'preset.json').write_text(json.dumps(preset))
        return {'catalog_root': directory / 'catalog', 'model': directory / 'candidate.joblib',
                'model_manifest': directory / 'candidate.manifest.json',
                'preset': directory / 'preset.json', 'victim_id': victim_id,
                'expected_catalog_revision': active['catalog_revision'],
                'evidence': {}, **changes}

    @contextlib.contextmanager
    def fake_swap(self, error=None, failing_swaps=()):
        """One swap that publishes a new session and a new score epoch, as a real one does.

        `failing_swaps` names the one-based swaps that raise, so a post-stop failure and
        its rollback can both run inside one activation.
        """
        async def swap(service, previous_session_id):
            self.swaps.append(previous_session_id)
            if error is not None or len(self.swaps) in failing_swaps:
                raise error or RuntimeError('the new engine did not start')
            self.engine.score_epoch_id = f'epoch-{len(self.swaps) + 1}'
            self.engine.session_id = f'session-{len(self.swaps)}'
            service.state = {'status': 'running', 'session_id': self.engine.session_id,
                             'previous_session_id': previous_session_id,
                             'reject_policy': self.engine.reject_policy()}

        with patch.object(LiveService, '_swap_worker', swap):
            yield

    def pointer(self):
        return object_catalog.read_active(self.root / 'active')['active_bundle_sha256']

    def identity(self):
        return (self.service.state.get('session_id'),
                (self.service.state.get('reject_policy') or {}).get('score_epoch_id'))

    async def test_a_clean_activation_swaps_one_type_and_records_every_phase(self):
        before, victim_id = self.pointer(), self.packaged['active_type_ids'][-1]
        candidate = self.candidate(victim_id)

        with self.fake_swap():
            result = await self.service._activate('job-1', candidate)

        self.assertEqual('active', result)
        self.assertEqual(['draining', 'activating', 'active'], self.jobs.phases)
        self.assertEqual(['prepare_activation', 'commit_activation'],
                         self.service.commands.kinds)
        self.assertEqual(['session-before'], self.swaps)
        # The pointer is the activation, and it carries the new bundle.
        self.assertNotEqual(before, self.pointer())
        after = object_catalog.read_active(self.root / 'active')
        self.assertEqual('generated.star_token', after['active_type_ids'][0])
        self.assertEqual(len(self.packaged['active_type_ids']), len(after['active_type_ids']))
        survivors = [item for item in self.packaged['active_type_ids'] if item != victim_id]
        self.assertEqual(survivors, after['active_type_ids'][1:])
        self.assertNotIn(victim_id, after['active_type_ids'])
        # The record block carries exactly the eleven frozen members.
        block = self.jobs.last
        self.assertEqual(sorted(live.ACTIVATION_MEMBERS), sorted(block))
        self.assertEqual(('active', 'active', False, 0), (block['phase'], block['result'],
                                                          block['rolled_back'],
                                                          block['active_objects']))
        self.assertEqual((self.pointer(), before),
                         (block['bundle_sha256'], block['previous_bundle_sha256']))
        self.assertEqual(live.DRAIN_TIMEOUT_S, block['drain_timeout_seconds'])
        self.assertEqual(('session-1', 'epoch-2'),
                         (block['session_id'], block['score_epoch_id']))
        self.assertEqual(self.engine.policy_version, block['activation_policy_version'])
        # The victim reaches the Wall of Fame with the retiring model manifest.
        archived = self.root / 'history' / 'wall-of-fame' / victim_id
        self.assertTrue((archived / 'definition.json').is_file())
        self.assertTrue((archived / 'model.manifest.json').is_file())
        history = (self.root / 'history' / 'activations.jsonl').read_text()
        rows = [json.loads(line) for line in history.splitlines()]
        self.assertEqual(1, len(rows))
        self.assertEqual((before, self.pointer(), victim_id),
                         (rows[0]['previous_bundle_sha256'], rows[0]['bundle_sha256'],
                          rows[0]['victim_type_id']))
        # The history is durable public evidence. No host path may reach it.
        for value in _string_values(rows[0]):
            with self.subTest(value=value):
                self.assertEqual(value, live._without_host_paths(value))
        self.assertNotIn(str(self.work), history)

    async def test_the_final_policy_drops_the_victim_and_is_written_once(self):
        victim_id = self.packaged['active_type_ids'][-1]
        victim_label = self.packaged['definitions'][-1]['classifier_label']
        self.engine.set_reject_classes(['stone', 'black'])
        self.service.state['reject_policy'] = self.engine.reject_policy()
        writes = []
        real_publish = object_catalog.publish_bundle

        def publish(bundles_root, files):
            digest = real_publish(bundles_root, files)
            writes.append(('publish', Path(bundles_root), digest))
            return digest

        def validate(bundle):
            writes.append(('verify', Path(bundle).parent, Path(bundle).name))
            return {'ok': True}

        with self.fake_swap(), patch.object(object_catalog, 'publish_bundle', publish), \
                patch.object(live, 'validate_bundle_child', validate):
            self.assertEqual('active', await self.service._activate(
                'job-1', self.candidate(victim_id)))

        bundle = self.root / 'active' / 'bundles' / self.pointer()
        self.assertEqual({'reject_classes': ['stone', 'black']},
                         json.loads((bundle / 'policy.json').read_text()))
        self.assertNotIn(victim_label, ['stone', 'black'])
        # The published bundle is staged whole, so policy.json is written exactly once,
        # and that one publish precedes its verification. Nothing is rewritten after it.
        bundles = self.root / 'active' / 'bundles'
        steps = [(kind, digest) for kind, parent, digest in writes if parent == bundles]
        self.assertEqual([('publish', self.pointer()), ('verify', self.pointer())], steps)

    async def test_a_stale_catalog_and_a_rejected_victim_keep_the_session_and_the_epoch(self):
        before, identity = self.pointer(), self.identity()
        victim_id = self.packaged['active_type_ids'][-1]
        victim_label = self.packaged['definitions'][-1]['classifier_label']
        cases = {
            'activation_conflict': self.candidate(victim_id, expected_catalog_revision='f' * 64),
            'replacement_conflict': self.candidate(victim_id),
        }

        for expected, candidate in cases.items():
            with self.subTest(result=expected):
                if expected == 'replacement_conflict':
                    self.engine.set_reject_classes(['stone', victim_label])
                with self.fake_swap():
                    result = await self.service._activate('job-1', candidate)

                self.assertEqual(expected, result)
                self.assertEqual(expected, self.jobs.last['result'])
                self.assertEqual('failed', self.jobs.last['phase'])
                # Nothing paused, nothing moved, and no session or epoch changed.
                self.assertEqual(30.0, self.engine.sim.rate)
                self.assertEqual([], self.swaps)
                self.assertEqual(before, self.pointer())
                self.assertEqual(identity, self.identity())

    async def test_a_drain_that_never_finishes_cancels_and_resumes_the_old_rate(self):
        before, identity = self.pointer(), self.identity()
        self.engine.active = 3

        with self.fake_swap(), patch.object(live, 'DRAIN_TIMEOUT_S', 0.0):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertEqual('cancel_activation', self.service.commands.kinds[-1])
        self.assertEqual(30.0, self.engine.sim.rate)
        self.assertEqual(3, self.jobs.last['active_objects'])
        self.assertIn('drain', self.jobs.last['message'])
        self.assertEqual([], self.swaps)
        self.assertEqual((before, identity), (self.pointer(), self.identity()))

    async def test_a_broken_candidate_never_causes_a_drain(self):
        before, identity = self.pointer(), self.identity()
        candidate = self.candidate()
        Path(candidate['model']).write_bytes(b'not a model')

        with self.fake_swap():
            result = await self.service._activate('job-1', candidate)

        self.assertEqual('activation_failed', result)
        self.assertEqual(['failed'], self.jobs.phases)
        # The pre-check runs before anything pauses, so no command ever reached the worker.
        self.assertEqual([], self.service.commands.kinds)
        self.assertEqual(30.0, self.engine.sim.rate)
        self.assertEqual((before, identity, []), (self.pointer(), self.identity(), self.swaps))
        self.assertNotIn(str(self.work), self.jobs.last['message'])

    async def test_a_bundle_that_fails_its_verification_cancels_and_keeps_the_session(self):
        before, identity = self.pointer(), self.identity()
        calls = []

        def validate(bundle):
            calls.append(Path(bundle))
            if len(calls) > 1:
                raise object_catalog.CatalogError('the final bundle is refused')
            return {'ok': True}

        with self.fake_swap(), patch.object(live, 'validate_bundle_child', validate):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertEqual('cancel_activation', self.service.commands.kinds[-1])
        self.assertEqual(30.0, self.engine.sim.rate)
        self.assertEqual([], self.swaps)
        self.assertEqual((before, identity), (self.pointer(), self.identity()))
        self.assertIn('refused', self.jobs.last['message'])

    async def test_an_unknown_victim_is_a_replacement_conflict_before_anything_runs(self):
        with self.fake_swap():
            result = await self.service._activate(
                'job-1', {**self.candidate(), 'victim_id': 'generated.absent'})

        self.assertEqual('replacement_conflict', result)
        self.assertEqual([], self.service.commands.kinds)

    async def test_every_internal_command_carries_the_active_session(self):
        """The worker loop reads session_id from every command before it dispatches."""
        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', self.candidate()))

        self.assertTrue(self.service.commands.sent)
        for payload in self.service.commands.sent:
            with self.subTest(command=payload['type']):
                self.assertEqual('session-before', payload['session_id'])
                self.assertIn(payload['type'], live.ACTIVATION_COMMANDS)

    async def test_an_unacknowledged_prepare_is_cancelled_before_it_reports(self):
        """A lost acknowledgment must never leave a paused engine behind."""
        before, identity = self.pointer(), self.identity()
        self.service.commands.withhold = {'prepare_activation'}

        with self.fake_swap(), patch.object(live, 'DRAIN_TIMEOUT_S', 0.05):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertEqual(['prepare_activation', 'cancel_activation'],
                         self.service.commands.kinds)
        self.assertEqual(30.0, self.engine.sim.rate)
        self.assertEqual((before, identity, []), (self.pointer(), self.identity(), self.swaps))

    async def test_an_unacknowledged_commit_is_cancelled_inside_the_drain_budget(self):
        """One budget bounds the drain AND every acknowledgment wait."""
        self.service.commands.withhold = {'commit_activation'}

        started = time.monotonic()
        with self.fake_swap(), patch.object(live, 'DRAIN_TIMEOUT_S', 0.2):
            result = await self.service._activate('job-1', self.candidate())
        elapsed = time.monotonic() - started

        self.assertEqual('activation_failed', result)
        self.assertEqual('cancel_activation', self.service.commands.kinds[-1])
        self.assertEqual(30.0, self.engine.sim.rate)
        # A per command wait would have run to ACTIVATION_ACK_TIMEOUT_S instead.
        self.assertLess(elapsed, live.ACTIVATION_ACK_TIMEOUT_S)

    async def test_a_cancel_that_is_never_acknowledged_takes_the_fatal_path(self):
        self.service.commands.withhold = {'commit_activation', 'cancel_activation'}

        with self.fake_swap(), patch.object(live, 'DRAIN_TIMEOUT_S', 0.05), \
                patch.object(live, 'ACTIVATION_CANCEL_TIMEOUT_S', 0.05):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertEqual('failed', self.service.state['status'])
        self.assertIn('unconfirmed', self.jobs.last['message'])

    async def test_a_second_activation_never_starts_a_second_drain(self):
        first, second = self.candidate(), self.candidate(
            self.packaged['active_type_ids'][-2])

        with self.fake_swap():
            results = await asyncio.gather(
                self.service._activate('job-1', first),
                self.service._activate('job-2', second))

        self.assertEqual(['active', 'activation_conflict'], results)
        self.assertEqual(1, len(self.swaps))
        self.assertEqual(1, self.service.commands.kinds.count('prepare_activation'))
        self.assertIsNone(self.service.activating)
        refused = [block for job_id, block in self.jobs.blocks if job_id == 'job-2']
        self.assertEqual(['activation_conflict'], [block['result'] for block in refused])

    async def retry_after(self, removals):
        """Activate, delete what a crash would have left undone, then retry exactly."""
        victim_id = self.packaged['active_type_ids'][-1]
        candidate = self.candidate(victim_id)
        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', candidate))
        activated, swaps, identity = self.pointer(), list(self.swaps), self.identity()
        history = self.root / 'history' / 'activations.jsonl'
        archived = self.root / 'history' / 'wall-of-fame' / victim_id
        if 'history' in removals:
            history.unlink()
        if 'archive' in removals:
            shutil.rmtree(archived)

        with self.fake_swap():
            result = await self.service._activate(
                'job-1', {**candidate, 'bundle_sha256': activated})

        self.assertEqual('active', result)
        # Never a second swap, never a second epoch, never a second pointer move.
        self.assertEqual((swaps, identity, activated),
                         (self.swaps, self.identity(), self.pointer()))
        rows = [json.loads(line) for line in history.read_text().splitlines()]
        return victim_id, archived, rows

    async def test_an_exact_retry_completes_a_commit_that_died_before_the_archive(self):
        victim_id, archived, rows = await self.retry_after({'archive', 'history'})

        self.assertTrue((archived / 'definition.json').is_file())
        self.assertEqual(1, len(rows))
        self.assertEqual((victim_id, self.pointer()),
                         (rows[0]['victim_type_id'], rows[0]['bundle_sha256']))
        # A repair never saw the capture, so it states no policy version.
        self.assertIsNone(rows[0]['activation_policy_version'])
        self.assertIsNone(self.jobs.last['message'])

    async def test_an_exact_retry_completes_a_commit_that_died_before_the_history_row(self):
        victim_id, archived, rows = await self.retry_after({'history'})

        self.assertTrue((archived / 'definition.json').is_file())
        self.assertEqual(1, len(rows))
        self.assertEqual(self.pointer(), rows[0]['bundle_sha256'])

    async def test_an_exact_retry_of_a_complete_commit_writes_nothing_twice(self):
        victim_id, archived, rows = await self.retry_after(set())

        self.assertEqual(1, len(rows))
        self.assertEqual(['archive.json', 'definition.json', 'model.manifest.json'],
                         sorted(path.name for path in archived.iterdir()))
        self.assertIsNone(self.jobs.last['message'])

    async def test_a_report_failure_after_prepare_never_strands_the_engine(self):
        """A paused engine with nobody to cancel it is the one unacceptable outcome."""
        before, identity = self.pointer(), self.identity()
        real = self.jobs

        def failing(job_id, **fields):
            if fields['activation']['phase'] == 'draining':
                raise RuntimeError('job record failed after prepare')
            return real(job_id, **fields)

        self.service.record = failing
        with self.fake_swap():
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        # The report sits inside the boundary, so the cancel always runs.
        self.assertEqual(['prepare_activation', 'cancel_activation'],
                         self.service.commands.kinds)
        self.assertEqual(30.0, self.engine.sim.rate)
        self.assertIsNone(self.service.commands.activation)
        self.assertEqual((before, identity, []), (self.pointer(), self.identity(), self.swaps))
        self.assertIsNone(self.service.activating)

    async def test_a_pointer_that_moved_despite_a_write_error_stays_active(self):
        """The pointer decides, never the exception. The two can never name two bundles."""
        before = self.pointer()
        real, failed = os.fsync, {'once': False}

        def fsync(descriptor):
            # The replace already happened. Only its durability step fails.
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed['once']:
                failed['once'] = True
                raise OSError('the directory fsync failed')
            return real(descriptor)

        with self.fake_swap(), patch.object(object_catalog.os, 'fsync', fsync):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('active', result)
        self.assertTrue(failed['once'])
        self.assertNotEqual(before, self.pointer())
        # Pointer, running worker, and the reported bundle all name ONE bundle.
        self.assertEqual(self.pointer(), self.service.active_bundle.name)
        self.assertEqual(self.pointer(), self.jobs.last['bundle_sha256'])
        self.assertEqual(1, len(self.swaps))
        self.assertIn('durability', self.jobs.last['message'])

    async def test_a_pointer_failure_after_the_swap_rolls_back_to_the_old_bundle(self):
        before = self.pointer()
        self.service._refresh_active_identity()
        revision = self.service.catalog_revision

        def refuse(active_root, bundle_sha256):
            raise object_catalog.CatalogError('the pointer cannot be written')

        with self.fake_swap(), patch.object(object_catalog, 'write_active_pointer', refuse):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertEqual(True, self.jobs.last['rolled_back'])
        # The pointer never moved, and the service runs the bundle it still names.
        self.assertEqual(before, self.pointer())
        self.assertEqual(before, self.service.active_bundle.name)
        self.assertEqual(revision, self.service.catalog_revision)
        self.assertEqual(2, len(self.swaps))
        self.assertFalse((self.root / 'history' / 'activations.jsonl').exists())

    async def test_an_audit_failure_after_the_pointer_keeps_the_activation_active(self):
        before = self.pointer()

        def refuse(root, definition, retired_at, evidence=None):
            raise object_catalog.CatalogError('the wall of fame is unwritable')

        with self.fake_swap(), patch.object(object_catalog, 'archive_type', refuse):
            result = await self.service._activate('job-1', self.candidate())

        # The pointer IS the activation, so an audit failure never undoes it.
        self.assertEqual('active', result)
        self.assertEqual('active', self.jobs.last['result'])
        self.assertNotEqual(before, self.pointer())
        self.assertEqual(self.pointer(), self.jobs.last['bundle_sha256'])
        self.assertEqual(self.pointer(), self.service.active_bundle.name)
        self.assertIn('audit trail failed', self.jobs.last['message'])
        self.assertEqual(1, len(self.swaps))

    async def test_a_rollback_that_does_not_come_back_is_not_a_rollback(self):
        before = self.pointer()

        with self.fake_swap(failing_swaps=(1, 2)):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        self.assertNotEqual(True, self.jobs.last['rolled_back'])
        self.assertEqual('failed', self.service.state['status'])
        # A compatibility claim follows a proof. Nothing verified anything here.
        health = json.loads((await self.service.health(None)).text)
        self.assertNotEqual(True, health['catalog_model_compatible'])
        self.assertEqual('failed', health['status'])
        self.assertEqual(before, self.pointer())
        self.assertIn('rollback failed', self.jobs.last['message'])

    async def test_an_activation_republishes_the_identity_the_pointer_names(self):
        self.service._refresh_active_identity()
        before = (self.service.catalog_revision, list(self.service.active_type_ids))

        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', self.candidate()))

        after = object_catalog.read_active(self.root / 'active')
        self.assertNotEqual(before[0], self.service.catalog_revision)
        self.assertEqual(after['catalog_revision'], self.service.catalog_revision)
        self.assertEqual(after['active_type_ids'], self.service.active_type_ids)
        self.assertIs(True, self.service.catalog_model_compatible)
        health = json.loads((await self.service.health(None)).text)
        self.assertEqual(self.pointer(), health['active_bundle_sha256'])
        self.assertIs(True, health['catalog_model_compatible'])

    async def test_a_post_stop_failure_rolls_back_and_never_claims_continuity(self):
        before = self.pointer()
        self.service._refresh_active_identity()
        revision = self.service.catalog_revision
        session_before, epoch_before = self.identity()

        with self.fake_swap(failing_swaps=(1,)):
            result = await self.service._activate('job-1', self.candidate())

        self.assertEqual('activation_failed', result)
        block = self.jobs.last
        self.assertEqual(('failed', 'activation_failed', True),
                         (block['phase'], block['result'], block['rolled_back']))
        # The pointer never moved, so the prior bundle is still the active one.
        self.assertEqual(before, self.pointer())
        self.assertEqual(before, self.service.active_bundle.name)
        self.assertEqual(revision, self.service.catalog_revision)
        self.assertFalse((self.root / 'history' / 'activations.jsonl').exists())
        # The old worker is gone, so this is a NEW session and a NEW score epoch.
        self.assertEqual(2, len(self.swaps))
        self.assertNotEqual(session_before, block['session_id'])
        self.assertNotEqual(epoch_before, block['score_epoch_id'])
        self.assertEqual(self.identity(), (block['session_id'], block['score_epoch_id']))

    async def test_an_exact_retry_starts_no_second_engine_and_no_second_epoch(self):
        candidate = self.candidate()
        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', candidate))
        activated, identity, swaps = self.pointer(), self.identity(), list(self.swaps)
        rows = (self.root / 'history' / 'activations.jsonl').read_text()

        # The same job retries with the bundle the pointer already carries.
        with self.fake_swap():
            result = await self.service._activate(
                'job-1', {**candidate, 'bundle_sha256': activated})

        self.assertEqual('active', result)
        self.assertEqual('active', self.jobs.last['result'])
        self.assertEqual(activated, self.jobs.last['bundle_sha256'])
        # No second restart, no second score epoch, and no second history row.
        self.assertEqual(swaps, self.swaps)
        self.assertEqual(identity, self.identity())
        self.assertEqual(activated, self.pointer())
        self.assertEqual(rows, (self.root / 'history' / 'activations.jsonl').read_text())

    async def test_the_activated_bundle_starts_a_plain_restart_with_its_changed_labels(self):
        """The root requirement, through the actual service startup path."""
        from test_validate_bundle import run_python

        victim_id = self.packaged['active_type_ids'][-1]
        with self.fake_swap():
            self.assertEqual('active', await self.service._activate(
                'job-1', self.candidate(victim_id)))
        activated = self.pointer()
        expected = object_catalog.catalog_labels(
            object_catalog.read_active(self.root / 'active'))

        seen = run_python(ActiveBundleStartupTest.CHILD, self.preset, self.root,
                          self.work / 'out', 'service order')

        self.assertNotEqual(object_catalog.catalog_labels(self.packaged), expected)
        self.assertEqual('star_token', expected[0])
        self.assertEqual(seen['bundle'], activated)
        self.assertEqual(seen['labels'], expected)
        self.assertTrue(seen['bundle_preset'])
        self.assertTrue(seen['exported_before_step_4'])
        self.assertFalse(seen['profiles_loaded_before_step_4'])
        self.assertTrue(seen['compatible'])

    async def test_the_activated_bundle_still_starts_after_the_whole_root_moves(self):
        """The bundle preset is relative, so no absolute path binds it to one parent."""
        from test_validate_bundle import run_python

        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', self.candidate()))
        activated = self.pointer()
        expected = object_catalog.catalog_labels(
            object_catalog.read_active(self.root / 'active'))

        moved_parent = self.work / 'moved'
        moved_parent.mkdir()
        moved = moved_parent / 'item-jobs'
        shutil.move(str(self.root), str(moved))
        seen = run_python(ActiveBundleStartupTest.CHILD, self.preset, moved,
                          self.work / 'out', 'service order')

        self.assertEqual(seen['bundle'], activated)
        self.assertEqual(seen['labels'], expected)
        self.assertTrue(seen['compatible'])
        # Only the activated bundle is loaded. No file of the retired one comes with it.
        loaded = object_catalog.read_active(moved / 'active')
        self.assertEqual(activated, loaded['active_bundle_sha256'])
        self.assertEqual(expected, object_catalog.catalog_labels(loaded))

    async def test_files_from_two_bundles_never_load_together(self):
        before = self.pointer()
        with self.fake_swap():
            self.assertEqual('active', await self.service._activate('job-1', self.candidate()))
        activated = self.pointer()

        bundles = sorted(path.name for path in (self.root / 'active' / 'bundles').iterdir())
        self.assertEqual(sorted([before, activated]), bundles)
        # Both bundles stay on disk, and each one verifies as one closed unit.
        for digest in bundles:
            with self.subTest(bundle=digest):
                listed = object_catalog.verify_bundle(
                    self.root / 'active' / 'bundles' / digest)['files']
                catalog = object_catalog.load_catalog(
                    self.root / 'active' / 'bundles' / digest / 'catalog')
                definitions = sum(name.startswith('catalog/definitions/') for name in listed)
                self.assertEqual(len(catalog['definitions']), definitions)
                self.assertEqual(catalog['active_type_ids'],
                                 [item['object_type_id'] for item in catalog['definitions']])
        # The pointer selects exactly one of them, and its definitions come from it alone.
        active = object_catalog.read_active(self.root / 'active')
        self.assertEqual(activated, active['active_bundle_sha256'])
        retired = object_catalog.load_catalog(
            self.root / 'active' / 'bundles' / before / 'catalog')
        self.assertNotEqual(retired['catalog_revision'], active['catalog_revision'])
        self.assertEqual([], [item for item in active['definitions']
                              if item['object_type_id'] not in
                              object_catalog.load_catalog(
                                  self.root / 'active' / 'bundles' / activated
                                  / 'catalog')['active_type_ids']])


class ActiveBundleStartupTest(unittest.TestCase):
    """Startup with --item-jobs-root: one verified active bundle, bound before profiles loads.

    An isolated service object only: no engine process, no server, no port. The model is a
    tiny picklable object that records the catalog revision, as the final model manifest does.
    """

    # One fresh interpreter runs the real startup order and reports what each step saw.
    CHILD = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "preset, root, out, early = sys.argv[1:5]\n"
        "if early == 'profiles first':\n"
        "    import profiles\n"
        "import live, object_catalog\n"
        "seen = {}\n"
        "real = live.load_preset\n"
        "def exported(bundle):\n"
        "    expected = str(Path(bundle) / 'catalog')\n"
        "    return os.environ.get(object_catalog.CATALOG_ROOT_ENV) == expected\n"
        "def load_preset(path):\n"
        "    seen['profiles_loaded_before_step_4'] = 'profiles' in sys.modules\n"
        "    seen['exported_before_step_4'] = exported(Path(path).parent)\n"
        "    return real(path)\n"
        "def create_worker(self, out):\n"
        "    seen['exported_before_the_worker'] = exported(self.active_bundle)\n"
        "parser = live.build_parser()\n"
        "args = parser.parse_args(['--preset', preset, '--item-jobs-root', root, '--out', out,\n"
        "                          '--item-jobs-provider', 'fake'])\n"
        "try:\n"
        "    with patch.object(live, 'load_preset', load_preset), \\\n"
        "            patch.object(live.LiveService, '_create_worker', create_worker):\n"
        "        service = live.build_service(parser, args)\n"
        "    import profiles\n"
        "    seen.update(bundle=service.active_bundle.name,\n"
        "                bundle_preset=service.preset == service.active_bundle / 'preset.json',\n"
        "                labels=profiles.PROFILES[service.preset_config['profile']].names,\n"
        "                compatible=service.catalog_model_compatible)\n"
        "except RuntimeError as error:\n"
        "    seen['refused'] = str(error)\n"
        "print(json.dumps(seen))\n")

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name).resolve()
        self.root = self.work / 'item-jobs'
        self.packaged = object_catalog.load_catalog(object_catalog.PACKAGED_CATALOG_ROOT)
        self.preset = self.write_inputs('packaged', self.packaged['catalog_revision'])

    def write_inputs(self, name, revision):
        """The real continuous preset beside a tiny model that records this revision."""
        from test_validate_bundle import CONTINUOUS_PRESET, continuous_model

        directory = self.work / name
        directory.mkdir()
        preset = {**json.loads(CONTINUOUS_PRESET.read_text()),
                  'model_path': str(directory / 'live_green_arabica.joblib')}
        model, manifest = continuous_model(object_catalog.catalog_labels(self.packaged),
                                           preset, revision)
        (directory / 'live_green_arabica.joblib').write_bytes(model)
        (directory / 'live_green_arabica.manifest.json').write_bytes(manifest)
        (directory / 'preset.json').write_text(json.dumps(preset))
        return directory / 'preset.json'

    def arguments(self, *extra, preset=None):
        return live.build_parser().parse_args([
            '--preset', str(preset or self.preset), '--out', str(self.work / 'out'),
            '--item-jobs-provider', 'fake', *map(str, extra)])

    @contextlib.contextmanager
    def fresh_process_state(self):
        """Every test module loads profiles, and startup refuses a process that did.

        profiles leaves sys.modules for the call and the export leaves os.environ after it,
        so no other test sees either one.
        """
        with patch.dict(sys.modules), patch.dict(os.environ), \
                patch.object(LiveService, '_create_worker', lambda self, out: None):
            sys.modules.pop('profiles', None)
            os.environ.pop(object_catalog.CATALOG_ROOT_ENV, None)
            yield

    def exported(self, bundle):
        return os.environ.get(object_catalog.CATALOG_ROOT_ENV) == str(bundle / 'catalog')

    def tree(self, *roots):
        return {str(path): path.read_bytes() for root in roots
                for path in sorted(Path(root).rglob('*')) if path.is_file()}

    def test_the_startup_order_binds_the_bundle_catalog_before_profiles_loads(self):
        """A changed-label bundle starts only when profiles loads the bundle catalog."""
        from test_validate_bundle import CHANGED_LABELS, continuous_bundle_files, run_python

        digest = object_catalog.publish_bundle(self.root / 'active' / 'bundles',
                                               continuous_bundle_files())
        object_catalog.write_active_pointer(self.root / 'active', digest)

        seen = run_python(self.CHILD, self.preset, self.root, self.work / 'out', 'service order')

        self.assertEqual(seen, {
            'profiles_loaded_before_step_4': False, 'exported_before_step_4': True,
            'exported_before_the_worker': True, 'bundle': digest, 'bundle_preset': True,
            'labels': list(CHANGED_LABELS), 'compatible': True})
        # The guard: a process that loaded profiles first can never bind a bundle catalog.
        early = run_python(self.CHILD, self.preset, self.root, self.work / 'out', 'profiles first')
        self.assertEqual(list(early), ['refused'])
        self.assertIn('profiles loaded before', early['refused'])

    def test_an_empty_mount_seeds_bundle_zero_and_the_packaged_tree_stays_identical(self):
        import engine
        from profiles import PROFILES

        sources = (object_catalog.PACKAGED_CATALOG_ROOT, HERE / 'configs', self.preset.parent)
        before = self.tree(*sources)
        with self.fresh_process_state():
            value = live.build_service(StubParser(), self.arguments('--item-jobs-root', self.root))
            self.assertTrue(self.exported(value.active_bundle))
        self.assertEqual(self.tree(*sources), before)

        bundle = value.active_bundle
        self.assertEqual(bundle.parent, self.root / 'active' / 'bundles')
        self.assertEqual(value.preset, bundle / 'preset.json')
        self.assertFalse((self.root / 'active' / object_catalog.SEED_MARKER).exists())
        catalog = object_catalog.read_active(self.root / 'active')
        self.assertEqual(catalog['active_bundle_sha256'], bundle.name)
        self.assertEqual(catalog['catalog_revision'], self.packaged['catalog_revision'])
        # Bundle zero states the reject set an engine derives without a bundle. Never Keep all.
        policy = json.loads(self.preset.read_text())['policy']
        derived = [item.name for item in PROFILES['green_arabica'].classes
                   if item.defect and item.severity in policy['reject_severities']]
        self.assertTrue(derived)
        self.assertEqual(json.loads((bundle / 'policy.json').read_text()),
                         {'reject_classes': derived})
        self.assertEqual(value.preset_config['policy'],
                         {**policy, 'initial_reject_classes': derived})
        recorded = json.loads((bundle / 'sources.json').read_text())
        self.assertEqual(sorted(recorded['files']), sorted(live.BUNDLE_SOURCE_FILES))
        self.assertLessEqual(set(engine.SOURCE_FILES), set(live.BUNDLE_SOURCE_FILES))

        health = json.loads(asyncio.run(value.health(None)).text)
        self.assertEqual(health['active_bundle_sha256'], bundle.name)
        self.assertIs(health['catalog_model_compatible'], True)

        # Job history lives apart from the unit that a rollback restores.
        with patch.object(item_jobs.ItemJobRunner, 'start'), \
                contextlib.redirect_stdout(io.StringIO()):
            value._open_item_jobs()
        self.addCleanup(value._close_item_jobs)
        self.assertTrue((self.root / 'history' / 'writer.lock').is_file())
        self.assertFalse((self.root / 'jobs').exists())
        self.assertEqual(value.catalog_revision, self.packaged['catalog_revision'])
        self.assertEqual(value.item_runner.catalog_provider()['active_bundle_sha256'], bundle.name)

    def test_a_model_without_a_recorded_catalog_revision_refuses_the_seed(self):
        preset = self.write_inputs('unrecorded', None)

        with self.fresh_process_state(), self.assertRaises(ValueError) as refused:
            live.build_service(StubParser(),
                               self.arguments('--item-jobs-root', self.root, preset=preset))

        self.assertIn('model_catalog_unrecorded', str(refused.exception))
        self.assertNotIn(str(self.work), str(refused.exception))
        self.assertFalse(self.root.exists())

    def test_a_rollback_returns_the_whole_bundle_and_newer_history_stays(self):
        from test_validate_bundle import CHANGED_LABELS, continuous_bundle_files

        def bind():
            with self.fresh_process_state():
                bundle = live.bind_active_bundle(self.preset, self.root)
                return bundle, self.exported(bundle)

        zero, _ = bind()
        newer = object_catalog.publish_bundle(self.root / 'active' / 'bundles',
                                              continuous_bundle_files())
        object_catalog.write_active_pointer(self.root / 'active', newer)
        record = self.root / 'history' / 'jobs' / 'newer.json'
        record.parent.mkdir(parents=True)
        record.write_bytes(b'{"after": "the snapshot"}')
        self.assertEqual(bind(), (zero.parent / newer, True))
        self.assertEqual(object_catalog.catalog_labels(
            object_catalog.read_active(self.root / 'active')), list(CHANGED_LABELS))

        object_catalog.rollback_active(self.root / 'active', zero.name)

        self.assertEqual(bind(), (zero, True))
        catalog = object_catalog.read_active(self.root / 'active')
        self.assertEqual(object_catalog.catalog_labels(catalog),
                         object_catalog.catalog_labels(self.packaged))
        # The catalog, its definitions, the model, the manifest, and the preset came back
        # as the one verified unit, and the newer bundle and the newer history both stay.
        listed = object_catalog.verify_bundle(zero)['files']
        for part in ('catalog/active/catalog.json', 'model/live_green_arabica.joblib',
                     'model/live_green_arabica.manifest.json', 'preset.json', 'policy.json'):
            self.assertIn(part, listed)
        self.assertEqual(sum(name.startswith('catalog/definitions/') for name in listed),
                         len(catalog['definitions']))
        self.assertEqual(record.read_bytes(), b'{"after": "the snapshot"}')
        self.assertEqual(sorted(path.name for path in zero.parent.iterdir()),
                         sorted([zero.name, newer]))

    def test_without_an_item_jobs_root_the_service_starts_as_before(self):
        with self.fresh_process_state():
            value = live.build_service(StubParser(), self.arguments())
            self.assertNotIn(object_catalog.CATALOG_ROOT_ENV, os.environ)

        self.assertIsNone(value.active_bundle)
        self.assertIsNone(value.history_root)
        self.assertEqual(value.preset, self.preset)
        self.assertEqual(value.item_jobs_root, self.work / 'out' / 'item-jobs')
        health = json.loads(asyncio.run(value.health(None)).text)
        self.assertIsNone(health['active_bundle_sha256'])
        self.assertIs(health['catalog_model_compatible'], True)
        with patch.object(item_jobs.ItemJobRunner, 'start'), \
                contextlib.redirect_stdout(io.StringIO()):
            value._open_item_jobs()
        self.addCleanup(value._close_item_jobs)
        self.assertTrue((value.item_jobs_root / 'writer.lock').is_file())
        self.assertEqual(sorted(path.name for path in value.item_jobs_root.iterdir()
                                if path.name in ('active', 'history')), [])
        self.assertEqual(value.catalog_root, object_catalog.PACKAGED_CATALOG_ROOT)

    def test_an_object_catalog_root_contradicts_an_item_jobs_root(self):
        arguments = self.arguments('--item-jobs-root', self.root,
                                   '--object-catalog-root', self.work / 'catalog')
        with self.assertRaisesRegex(ValueError, '--object-catalog-root cannot be used'):
            live.build_service(StubParser(), arguments)
        # The contradiction is refused with the other item job rules, before any startup.
        with self.assertRaisesRegex(ValueError, '--object-catalog-root cannot be used'):
            live._validate_item_job_arguments(StubParser(), arguments, self.root)
        self.assertFalse(self.root.exists())

    def test_the_source_revision_is_deployment_controlled_and_never_asked_of_the_host(self):
        """With the variable absent the field is None, and no child is ever spawned."""
        entries = ('run', 'Popen', 'call', 'check_call', 'check_output')

        with patch.dict(os.environ), contextlib.ExitStack() as stack:
            os.environ.pop('CINTA_SOURCE_REVISION', None)
            spawns = {name: stack.enter_context(patch.object(subprocess, name))
                      for name in entries}
            sources = live._bundle_sources()

        self.assertIsNone(sources['source_revision'])
        for name, spawn in spawns.items():
            self.assertFalse(spawn.called, f'subprocess.{name} was called')
        self.assertEqual(sorted(sources['files']), sorted(live.BUNDLE_SOURCE_FILES))
        self.assertTrue(all(len(digest) == 64 for digest in sources['files'].values()))

    def test_a_seeded_root_restarts_without_reading_the_preset(self):
        """LIVE.md: --preset matters again only for an empty root."""
        from test_validate_bundle import continuous_bundle_files

        digest = object_catalog.publish_bundle(self.root / 'active' / 'bundles',
                                               continuous_bundle_files())
        object_catalog.write_active_pointer(self.root / 'active', digest)
        absent = self.work / 'gone' / 'preset.json'
        malformed = self.work / 'malformed.json'
        malformed.write_text('{ this is not json')

        for unusable in (absent, malformed):
            with self.subTest(preset=unusable.name), self.fresh_process_state():
                bundle = live.bind_active_bundle(unusable, self.root)
                self.assertEqual(bundle, self.root / 'active' / 'bundles' / digest)
                self.assertTrue(self.exported(bundle))
        # An empty root still reads it, so the documented sentence stays true.
        with self.fresh_process_state(), self.assertRaises(OSError):
            live.bind_active_bundle(absent, self.work / 'empty-root')


def _string_values(document):
    if isinstance(document, str):
        yield document
    elif isinstance(document, dict):
        for key, value in document.items():
            yield from _string_values(value)
    elif isinstance(document, list):
        for value in document:
            yield from _string_values(value)



if __name__ == '__main__':
    unittest.main()
