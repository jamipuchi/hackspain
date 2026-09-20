import asyncio
from collections import deque
import json
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

from live import (COMMAND_EPOCH_SECONDS, MAX_COMMANDS, MAX_COMMANDS_PER_EPOCH,
                  MAX_PENDING_COMMANDS, PUMP_STALE_SECONDS, LiveService,
                  _configured_source_revision, access_middleware, access_rules,
                  configured_access_rules, load_preset, worker)


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


if __name__ == '__main__':
    unittest.main()
