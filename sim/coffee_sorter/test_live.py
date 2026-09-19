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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from live import COMMAND_EPOCH_SECONDS, MAX_COMMANDS, MAX_PENDING_COMMANDS, LiveService, load_preset, worker


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
    def __init__(self):
        self.packets = []

    async def send_str(self, packet):
        self.packets.append(json.loads(packet))


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
                    patch('live.signal.signal'), patch('live.time.monotonic', side_effect=monotonic):
                worker('unused.json', states, acks, commands, stop, directory)

        running = states.items[0]
        self.assertEqual(running['status'], 'running')
        self.assertIsNone(running['limits']['sim_seconds'])
        self.assertIn('rolling_scores', running)
        self.assertGreater(FakeEngine.instance.sim.data.time, 0)
        self.assertLess(FakeEngine.instance.rolling_calls, FakeEngine.instance.snapshot_calls)


class ContinuousCommandTest(unittest.IsolatedAsyncioTestCase):
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
               'session_id': value.state['session_id'], 'ok': True, 'object_id': 42}
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
        value.epoch_admissions[value.command_epoch] = MAX_COMMANDS
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
