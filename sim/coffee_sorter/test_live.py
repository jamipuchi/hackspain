import asyncio
import builtins
import subprocess
import signal
import contextlib
from collections import deque
import hashlib
import json
import shlex
import shutil
import os
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
    value.item_jobs = item_jobs.ItemJobStore(value.item_jobs_root, provider_mode=provider_mode)
    test.addCleanup(value.item_jobs.close)
    value.runtime_lock = value.item_jobs_root / 'render.lock'
    value.item_jobs_unhealthy = False
    value.item_runner = item_jobs.ItemJobRunner(
        value.item_jobs, item_jobs.fake_commands(), runtime_lock_path=value.runtime_lock)
    value.catalog_root = object_catalog.CATALOG_ROOT
    value.catalog_revision = CATALOG_REVISION
    value.active_type_ids = ['builtin.green_arabica.good']
    value.item_jobs_state = value._item_jobs_packet()
    return value


def item_arguments(**overrides):
    values = {'host': '127.0.0.1', 'item_jobs_provider': 'cached', 'item_jobs_provider_cache': None,
              'item_jobs_provider_env': None,
              'item_jobs_generator_root': item_jobs.GENERATOR_ROOT}
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
        value.item_jobs.record(request_id, token='secret', artifacts={
            'runtime_lock': str(value.runtime_lock), 'previews': {}})
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
