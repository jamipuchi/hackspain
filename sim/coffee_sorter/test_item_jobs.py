"""Phase 2 queue tests. Fake workers only: no network, no Blender, no mujoco.

Every test that starts a child process reaps it. The lease timings are tiny, so
the whole file runs in seconds while still exercising real process groups.
The provider tests drive the real runner and the real command builders, so an
argv assertion proves what the service would actually spawn.
"""
import ast
import contextlib
import datetime
import hashlib
import json
import shutil
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import item_job_physics
import item_jobs
import object_catalog
from item_jobs import (MAX_ATTEMPTS, MAX_QUEUED_JOBS, MAX_SUMMARIES, ItemJobError,
                       ItemJobRunner, ItemJobStore, fake_commands)

REVISION = 'a' * 64
PRESET = HERE / 'configs/continuous_demo.json'
OTHER_REVISION = 'b' * 64
RFC3339 = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
TINY_WAIT_S = 0.2
# A group leader that exits while its child keeps the process group alive.
ORPHAN_SOURCE = (
    "import subprocess, sys;"
    "child = subprocess.Popen([sys.executable, '-c',"
    " 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']);"
    "open(sys.argv[1], 'w').write(str(child.pid))"
)


class Clock:
    """A distinct RFC 3339 UTC second per call, so ordering stays deterministic."""

    def __init__(self):
        self.value = datetime.datetime(2026, 9, 20, 9, 0, 0, tzinfo=datetime.timezone.utc)
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.value += datetime.timedelta(seconds=1)
            return self.value.strftime('%Y-%m-%dT%H:%M:%SZ')


class QueueTest(unittest.TestCase):
    provider_mode = 'fake'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # The configured temporary directory: tempfile.gettempdir() honors this value. Each
        # test gets its own, so an ephemeral child root never outlives the test directory.
        self.configured = self.root / 'configured-tmp'
        self.configured.mkdir()
        patcher = unittest.mock.patch.object(tempfile, 'tempdir', str(self.configured))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.store = self.open_store()

    def open_store(self, provider_mode=None):
        store = ItemJobStore(self.root, clock=self.clock,
                             provider_mode=provider_mode or self.provider_mode)
        self.addCleanup(store.close)
        return store

    # The stages this harness runs. A Phase 3 test asks for the later stages explicitly,
    # so a Phase 2 assertion about preview_ready is never overtaken by the physics stage.
    STAGES = ('generation', 'render')

    def open_runner(self, store=None, lease_s=30.0, commands=None, stages=None,
                    catalog_provider=None, policy_provider=None, **injected):
        runner = ItemJobRunner(store or self.store,
                               commands or fake_commands(stages=stages or self.STAGES),
                               runtime_lock_path=self.root / 'render.lock', lease_s=lease_s,
                               catalog_provider=catalog_provider,
                               policy_provider=policy_provider, **injected)
        runner.term_wait_s = TINY_WAIT_S
        runner.kill_wait_s = TINY_WAIT_S
        runner.lock_backoff_s = (0.05, 0.1)
        self.addCleanup(runner.shutdown)
        return runner

    def recording_commands(self, log, mode=None):
        """Real builders decide the argv. Fake workers execute it, so nothing is billed."""
        fake = fake_commands()
        real = item_jobs.real_commands(
            mode=mode or self.provider_mode, provider_cache=self.root / 'provider-cache',
            runtime_lock=self.root / 'render.lock', preset=PRESET,
            generator_root=self.root / 'generator', env_file=self.root.parent / 'secret.env')

        def generation(job, job_dir):
            log.append(real['generation'](job, job_dir))
            return fake['generation'](job, job_dir)

        return {'generation': generation, 'render': fake['render']}

    def scenarios(self, table):
        (self.root / 'fake_scenario.json').write_text(json.dumps(table))

    def submit(self, description='A small brass star token', **extra):
        payload = {'request_id': str(uuid.uuid4()), 'description': description,
                   'expected_catalog_revision': REVISION, **extra}
        job, created = self.store.submit(payload, REVISION)
        self.assertTrue(created)
        return job

    def starts(self, stage):
        path = self.root / 'worker_runs.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        return [row for row in rows if row['stage'] == stage and row['event'] == 'start']

    def drive(self, runner, predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            runner.step()
            if predicate():
                return
            time.sleep(0.01)
        self.fail('the queue did not reach the expected condition in time')

    def reap(self, runner, predicate, timeout=20.0):
        """Settle the running child without letting the same pass launch the next one.

        One step() reaps and then schedules, so a settled state that the scheduler
        immediately replaces is invisible to drive(). These assertions need that state.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            runner._reap()
            if predicate():
                return
            time.sleep(0.01)
        self.fail('the owned worker did not settle in time')

    def settled(self, request_id):
        return self.store.get(request_id).get('worker') is None

    def wait_gone(self, pgid, timeout=20.0):
        """Wait for an observable condition, never for a fixed wall time.

        Termination of a signalled group is asynchronous. Under load it can outlast the
        state change that follows it, so the test waits for the group itself.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        self.fail('the owned process group did not leave in time')

    def expire_lease(self, runner, request_id):
        """Wait for the hung renderer, expire its lease now, and return its group.

        A tiny lease on the runner is a wall-clock budget for EVERY child. Under load a
        healthy child outlasts it too, the runner consumes the second attempt, and the job
        ends failed. So the test waits for the hung child and expires only that one.
        """
        def render_group():
            worker = self.store.get(request_id).get('worker') or {}
            return worker.get('pgid') if worker.get('stage') == 'render' else None

        # The start record is the child's own word that this run counts as the hung one.
        self.drive(runner, lambda: render_group() and self.starts('render'))
        pgid = render_group()
        with runner._guard:
            runner.children[request_id]['deadline'] = time.monotonic()
        return pgid

    def state(self, request_id):
        return self.store.get(request_id)['state']


class AdmissionTest(QueueTest):
    def test_exact_retry_returns_the_same_job_and_launches_nothing_new(self):
        payload = {'request_id': str(uuid.uuid4()), 'description': 'A brass star token',
                   'requester_name': 'Taras', 'expected_catalog_revision': REVISION}
        first, created = self.store.submit(payload, REVISION)
        self.assertTrue(created)
        retried, created_again = self.store.submit(dict(payload), REVISION)

        self.assertFalse(created_again)
        self.assertEqual(retried['request_id'], first['request_id'])
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(self.starts('generation'), [])

        with self.assertRaises(ItemJobError) as raised:
            self.store.submit({**payload, 'description': 'A brass moon token'}, REVISION)
        self.assertEqual(raised.exception.code, 'request_conflict')
        self.assertEqual(len(self.store.jobs()), 1)

    def test_queue_limit_and_bounded_newest_first_summaries(self):
        for _ in range(MAX_QUEUED_JOBS):
            self.submit()
        with self.assertRaises(ItemJobError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, 'queue_full')
        self.assertEqual(MAX_QUEUED_JOBS, 4)

        # A retained terminal job stops counting as queued work, so admission continues.
        submitted = []
        for job in self.store.jobs():
            self.store.transition(job['request_id'], 'failed', error='render_failed')
            submitted.append(job['request_id'])
        for _ in range(MAX_SUMMARIES + 3):
            request_id = self.submit()['request_id']
            self.store.transition(request_id, 'failed', error='render_failed')
            submitted.append(request_id)

        summaries = self.store.summaries()
        self.assertEqual(MAX_SUMMARIES, 32)
        self.assertEqual(len(summaries), MAX_SUMMARIES)
        self.assertEqual([item['request_id'] for item in summaries],
                         list(reversed(submitted[-MAX_SUMMARIES:])))

    def test_concurrent_submits_cannot_pass_the_queue_limit(self):
        """flock excludes another process. The store lock excludes another thread."""
        outcomes = []
        guard = threading.Lock()
        start = threading.Barrier(12)

        def attempt():
            start.wait()
            try:
                self.submit()
                code = 'admitted'
            except ItemJobError as error:
                code = error.code
            with guard:
                outcomes.append(code)

        threads = [threading.Thread(target=attempt) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertEqual(outcomes.count('admitted'), MAX_QUEUED_JOBS)
        self.assertEqual(outcomes.count('queue_full'), 12 - MAX_QUEUED_JOBS)
        self.assertEqual(len(self.store.jobs()), MAX_QUEUED_JOBS)

    def test_stale_catalog_revision_blocks_admission(self):
        with self.assertRaises(ItemJobError) as raised:
            self.store.submit({'request_id': str(uuid.uuid4()), 'description': 'A brass token',
                               'expected_catalog_revision': OTHER_REVISION}, REVISION)
        self.assertEqual(raised.exception.code, 'catalog_revision_conflict')

    def test_deployment_controlled_fields_are_refused_in_a_request(self):
        """The source revision and the provider mode never come from a browser."""
        for name, value in (('source_revision', 'c' * 40), ('provider_mode', 'paid'),
                            ('live', True), ('env_file', '/etc/passwd')):
            with self.assertRaises(ItemJobError) as raised:
                self.store.submit({'request_id': str(uuid.uuid4()), 'description': 'A token',
                                   'expected_catalog_revision': REVISION, name: value}, REVISION)
            self.assertEqual(raised.exception.code, 'invalid_request')
        self.assertEqual(self.store.jobs(), [])

    def test_timestamps_requester_name_and_request_id_validation(self):
        job = self.submit(requester_name='  Taras  ')
        self.assertEqual(job['requester_name'], 'Taras')
        self.store.transition(job['request_id'], 'waiting_for_render')
        stored = self.store.get(job['request_id'])

        for value in list(stored['timestamps'].values()) + [row['at'] for row in stored['history']]:
            self.assertRegex(value, RFC3339)
        self.assertEqual([row['state'] for row in stored['history']],
                         ['queued', 'waiting_for_render'])

        for request_id in ('../evil', 'jobs/../../evil', '', 'NOT-A-UUID',
                           str(uuid.uuid4()).upper(), f'{{{uuid.uuid4()}}}'):
            with self.assertRaises(ItemJobError) as raised:
                self.store.submit({'request_id': request_id, 'description': 'x',
                                   'expected_catalog_revision': REVISION}, REVISION)
            self.assertEqual(raised.exception.code, 'invalid_request')
        self.assertEqual(sorted(path.name for path in (self.root / 'jobs').iterdir()),
                         [job['request_id']])

        for description in ('', '   ', 'x' * (item_jobs.MAX_DESCRIPTION + 1), 7):
            with self.assertRaises(ItemJobError) as raised:
                self.store.submit({'request_id': str(uuid.uuid4()), 'description': description,
                                   'expected_catalog_revision': REVISION}, REVISION)
            self.assertEqual(raised.exception.code, 'invalid_description')

    def test_stale_token_cannot_publish_a_transition_or_artifacts(self):
        job = self.submit()
        request_id = job['request_id']
        self.store.record(request_id, worker={
            'stage': 'render', 'pid': 1, 'pgid': 1, 'token': 'owner',
            'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
            'pid_start': None, 'log': ''})

        for call in (lambda: self.store.transition(request_id, 'failed', token='stale',
                                                   error='render_failed'),
                     lambda: self.store.record(request_id, token='stale',
                                               artifacts={'previews': ['top.png']})):
            with self.assertRaises(ItemJobError) as raised:
                call()
            self.assertEqual(raised.exception.code, 'stale_token')

        stored = self.store.get(request_id)
        self.assertEqual(stored['state'], 'queued')
        self.assertEqual(stored['artifacts'], {})
        self.store.record(request_id, token='owner', artifacts={'previews': ['top.png']})
        self.assertEqual(self.store.get(request_id)['artifacts'], {'previews': ['top.png']})

    def test_second_store_on_one_root_reports_writer_locked(self):
        with self.assertRaises(ItemJobError) as raised:
            ItemJobStore(self.root, clock=self.clock)
        self.assertEqual(raised.exception.code, 'writer_locked')

    def test_provider_submission_memory_is_sticky(self):
        job = self.submit()
        request_id = job['request_id']
        self.store.transition(request_id, 'generating_recipe', provider_submission='in_flight')
        self.store.transition(request_id, 'generating_recipe', provider_submission='not_submitted')

        # Nothing may claim a request never reached the provider once it may have.
        self.assertEqual(self.store.get(request_id)['provider_submission'], 'uncertain')


class ProviderModeTest(QueueTest):
    """The structural rule: `--live` never appears without an operator grant in paid mode."""

    provider_mode = 'cached'

    def live_flags(self, log):
        return [argv.count('--live') for argv in log]

    def test_cache_miss_requires_an_operator_and_consumes_no_attempt(self):
        self.scenarios({'generation': ['fail_safe']})
        log = []
        runner = self.open_runner(commands=self.recording_commands(log))
        job = self.submit()
        request_id = job['request_id']

        self.drive(runner, lambda: self.state(request_id) == 'operator_required')

        stored = self.store.get(request_id)
        self.assertEqual(stored['error'], 'provider_cache_miss')
        self.assertEqual(stored['attempts']['generation'], 0)
        self.assertEqual(stored['provider_submission'], 'not_submitted')
        self.assertEqual(self.store.summary(stored)['primary_action'], 'resolve_provider')
        for _ in range(10):
            runner.step()
        self.assertEqual(len(self.starts('generation')), 1)
        self.assertEqual(self.live_flags(log), [0])

    def test_new_request_is_refused_when_paid_mode_is_disabled(self):
        self.scenarios({'generation': ['fail_safe']})
        runner = self.open_runner()
        job = self.submit()
        self.drive(runner, lambda: self.state(job['request_id']) == 'operator_required')

        with self.assertRaises(ItemJobError) as raised:
            runner.resolve_provider(job['request_id'], 'new_request')

        self.assertEqual(raised.exception.code, 'paid_mode_disabled')
        self.assertEqual(self.state(job['request_id']), 'operator_required')
        self.assertIsNone(self.store.get(job['request_id'])['provider_permission'])

    def test_use_cache_after_a_cache_miss_never_adds_live_on_any_later_attempt(self):
        """S1: the automatic retry after use_cache must not derive --live from anything."""
        self.scenarios({'generation': ['fail_safe', 'fail_hard', 'fail_hard']})
        log = []
        runner = self.open_runner(commands=self.recording_commands(log))
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'operator_required')

        runner.resolve_provider(request_id, 'use_cache')
        # The cache-only attempt fails hard, so the one automatic retry runs.
        self.drive(runner, lambda: self.state(request_id) == 'failed')
        for _ in range(10):
            runner.step()

        self.assertGreaterEqual(len(log), 3)
        self.assertEqual(self.live_flags(log), [0] * len(log))
        self.assertEqual(self.store.get(request_id)['error'], 'generation_failed')

    def test_uncertain_then_use_cache_then_cache_miss_never_bills(self):
        """F1: the exact reported sequence, across ten steps and a restart."""
        self.scenarios({'generation': ['uncertain', 'fail_safe']})
        log = []
        runner = self.open_runner(commands=self.recording_commands(log))
        job = self.submit()
        request_id = job['request_id']

        self.drive(runner, lambda: self.state(request_id) == 'interrupted_uncertain')
        self.assertEqual(self.store.get(request_id)['provider_submission'], 'uncertain')
        self.assertEqual(self.store.get(request_id)['attempts']['generation'], 0)

        runner.resolve_provider(request_id, 'use_cache')
        self.drive(runner, lambda: self.state(request_id) == 'operator_required')
        for _ in range(10):
            runner.step()

        runner.stop(timeout=1.0)
        self.store.close()
        self.store = self.open_store()
        restarted = self.open_runner(commands=self.recording_commands(log))
        restarted.recover()
        for _ in range(10):
            restarted.step()

        self.assertGreaterEqual(len(log), 2)
        self.assertEqual(self.live_flags(log), [0] * len(log))
        self.assertEqual(self.state(request_id), 'operator_required')
        # The uncertain memory survives every later cache-only outcome.
        self.assertEqual(self.store.get(request_id)['provider_submission'], 'uncertain')


class PaidModeTest(QueueTest):
    provider_mode = 'paid'

    def test_new_request_permits_exactly_one_live_call_and_never_the_retry(self):
        self.scenarios({'generation': ['fail_safe', 'fail_hard', 'fail_hard']})
        log = []
        runner = self.open_runner(commands=self.recording_commands(log))
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'operator_required')
        self.assertEqual([argv.count('--live') for argv in log], [0])

        runner.resolve_provider(request_id, 'new_request')
        self.assertEqual(self.store.get(request_id)['provider_permission'], 'new_request')
        self.drive(runner, lambda: self.state(request_id) == 'failed')
        for _ in range(10):
            runner.step()

        flags = [argv.count('--live') for argv in log]
        self.assertEqual(sum(flags), 1)
        self.assertEqual(flags[1], 1)
        self.assertEqual(flags[2:], [0] * len(flags[2:]))
        # The grant is consumed before the child can exist.
        self.assertIsNone(self.store.get(request_id)['provider_permission'])

    def test_paid_argv_carries_the_credential_path_and_the_service_never_opens_it(self):
        log = []
        runner = self.open_runner(commands=self.recording_commands(log))
        self.scenarios({})
        job = self.submit()
        self.drive(runner, lambda: self.state(job['request_id']) == 'preview_ready')

        argv = log[0]
        credential = str(self.root.parent / 'secret.env')
        self.assertIn('--env-file', argv)
        self.assertEqual(argv[argv.index('--env-file') + 1], credential)
        self.assertNotIn('--live', argv)
        self.assertFalse(Path(credential).exists())


class FakeModeTest(QueueTest):
    def test_fake_jobs_are_marked_and_can_never_reach_activation(self):
        self.scenarios({})
        runner = self.open_runner()
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'preview_ready')

        summary = self.store.summary(self.store.get(request_id))
        self.assertEqual(summary['provider_mode'], 'fake')
        self.assertIs(summary['provider_cache_hit'], True)

        with self.assertRaises(ItemJobError) as raised:
            self.store.transition(request_id, 'selecting_training_baseline')
        self.assertEqual(raised.exception.code, 'fake_provider_not_activatable')

        runner.select_training_baseline(request_id)
        stored = self.store.get(request_id)
        self.assertEqual(stored['state'], 'failed')
        self.assertEqual(stored['error'], 'generation_failed')
        self.assertEqual(stored['progress'], 'fake_provider_not_activatable')


class RenderRetryTest(QueueTest):
    def test_one_safe_render_failure_then_success_reaches_preview_ready(self):
        self.scenarios({'render': {'1': 'fail_safe', '2': 'ok'}})
        job = self.submit()
        runner = self.open_runner()

        self.drive(runner, lambda: self.state(job['request_id']) == 'preview_ready')

        stored = self.store.get(job['request_id'])
        self.assertEqual(stored['attempts']['render'], MAX_ATTEMPTS)
        self.assertIsNone(stored['error'])
        self.assertEqual(len(self.starts('render')), 2)
        self.assertEqual(self.store.summary(stored)['preview'],
                         f"/item-jobs/{job['request_id']}/previews/perspective.png")

    def test_two_safe_render_failures_retain_failed_and_a_later_job_still_starts(self):
        self.scenarios({'render': {'1': 'fail_safe', '2': 'fail_safe'}})
        first = self.submit('First token')
        runner = self.open_runner()
        self.drive(runner, lambda: self.state(first['request_id']) == 'failed')

        self.assertEqual(self.store.get(first['request_id'])['error'], 'render_failed')
        self.assertIsNone(runner.slots['render'])

        self.scenarios({})
        second = self.submit('Second token')
        self.drive(runner, lambda: self.state(second['request_id']) == 'preview_ready')
        self.assertEqual(self.state(first['request_id']), 'failed')

    def test_attempts_persist_across_a_store_and_runner_restart(self):
        self.scenarios({'render': {'1': 'fail_safe', '2': 'fail_safe'}})
        job = self.submit()
        request_id = job['request_id']
        first = self.open_runner()
        first.step()
        self.reap(first, lambda: self.state(request_id) == 'waiting_for_render')
        first.step()
        self.reap(first, lambda: self.settled(request_id))

        self.assertEqual(self.store.get(request_id)['attempts']['render'], 1)
        # A retried renderer waits for the shared lock again.
        self.assertEqual(self.state(request_id), 'waiting_for_render')
        self.assertEqual(len(self.starts('render')), 1)
        first.stop(timeout=1.0)
        self.store.close()

        self.store = self.open_store()
        second = self.open_runner()
        second.recover()
        self.drive(second, lambda: self.state(request_id) == 'failed')

        self.assertEqual(self.store.get(request_id)['attempts']['render'], MAX_ATTEMPTS)
        self.assertEqual(len(self.starts('render')), 2)

    def test_busy_render_lock_backs_off_without_consuming_an_attempt(self):
        self.scenarios({'render': {'1': 'lock_busy'}})
        job = self.submit()
        request_id = job['request_id']
        runner = self.open_runner()
        runner.step()
        self.reap(runner, lambda: self.state(request_id) == 'waiting_for_render')
        runner.step()
        self.reap(runner, lambda: self.settled(request_id))

        stored = self.store.get(request_id)
        self.assertEqual(stored['state'], 'waiting_for_render')
        self.assertEqual(stored['attempts']['render'], 0)
        self.assertEqual(stored['lock_busy_count'], {'render': 1})
        self.assertIn('runtime lock busy', stored['progress'])
        self.assertIn('retrying in', stored['progress'])
        self.assertIsNone(stored['error'])
        # The backoff replaces a relaunch spin: this pass must start nothing.
        runner.step()
        self.assertEqual(len(self.starts('render')), 1)

        self.scenarios({})
        self.drive(runner, lambda: self.state(request_id) == 'preview_ready')
        self.assertEqual(self.store.get(request_id)['attempts']['render'], 1)


class WorkerOwnershipTest(QueueTest):
    def test_expired_lease_terminates_the_whole_group_before_a_replacement(self):
        self.scenarios({'render': {'1': 'hang', '2': 'ok'}})
        job = self.submit()
        request_id = job['request_id']
        runner = self.open_runner()
        # The lease expiry is what this test exercises. Killing a group that ignores
        # SIGTERM is asynchronous, so the terminate budget waits for the observable exit
        # instead of assuming a fixed wall time: a short budget only makes the test flaky.
        runner.term_wait_s = runner.kill_wait_s = 1.0

        pgid = self.expire_lease(runner, request_id)
        self.drive(runner, lambda: self.state(request_id) == 'preview_ready', timeout=40.0)
        # The replacement record is written by the second worker, so wait for it too.
        self.drive(runner, lambda: len(self.starts('render')) == 2, timeout=40.0)

        self.wait_gone(pgid)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)
        starts = self.starts('render')
        self.assertEqual(len(starts), 2)
        self.assertGreater(starts[1]['at'], starts[0]['at'])
        self.assertIsNone(runner.slots['render'])

    def test_unconfirmed_exit_blocks_one_slot_until_cleanup_is_confirmed(self):
        self.scenarios({'render': {'1': 'hang', '2': 'ok'}})
        blocked = self.submit('Blocked token')
        runner = self.open_runner()
        # Generation must settle normally. The unconfirmed exit under test is the
        # renderer's, so only that one group refuses to confirm.
        hung = self.expire_lease(runner, blocked['request_id'])
        confirm = runner._group_gone
        runner._group_gone = (lambda pgid, pid=None:
                              False if pgid == hung else confirm(pgid, pid))

        self.drive(runner, lambda: self.state(blocked['request_id']) == 'worker_unavailable')

        stored = self.store.get(blocked['request_id'])
        self.assertEqual(stored['error'], 'worker_unavailable')
        self.assertEqual(self.store.summary(stored)['primary_action'], 'confirm_cleanup')
        self.assertEqual(runner.slots['render'], blocked['request_id'])
        for _ in range(5):
            runner.step()
        self.assertEqual(len(self.starts('render')), 1)

        # Unrelated stages keep running while one renderer slot stays blocked.
        other = self.submit('Other token')
        self.drive(runner, lambda: self.state(other['request_id']) == 'waiting_for_render')
        self.assertEqual(len(self.starts('render')), 1)

        with self.assertRaises(ItemJobError) as raised:
            runner.confirm_cleanup(blocked['request_id'])
        self.assertEqual(raised.exception.code, 'worker_unavailable')

        del runner._group_gone
        runner.confirm_cleanup(blocked['request_id'])
        self.assertIsNone(runner.slots['render'])
        # A cleaned renderer waits for the shared lock again.
        self.assertEqual(self.state(blocked['request_id']), 'waiting_for_render')
        self.drive(runner, lambda: self.state(blocked['request_id']) == 'preview_ready')

    def test_recovery_never_frees_a_slot_while_an_orphan_group_lives(self):
        """F2: the leader exited, so ownership is unprovable and the group is alive."""
        marker = self.root / 'orphan.pid'
        leader = subprocess.Popen([sys.executable, '-c', ORPHAN_SOURCE, str(marker)],
                                  start_new_session=True)
        leader.wait()
        pgid = leader.pid
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.01)
        self.addCleanup(self._kill_group, pgid)
        self.assertTrue(marker.is_file())
        self.assertFalse(self._group_gone(pgid))

        job = self.submit()
        request_id = job['request_id']
        self.store.record(request_id, worker={
            'stage': 'render', 'pid': leader.pid, 'pgid': pgid, 'token': 'orphan',
            'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
            'pid_start': 'Mon Jan  1 00:00:00 2024', 'log': ''})
        self.store.transition(request_id, 'rendering_previews', token='orphan')

        runner = self.open_runner()
        runner.recover()

        self.assertEqual(self.state(request_id), 'worker_unavailable')
        self.assertEqual(runner.slots['render'], request_id)
        runner.step()
        self.assertEqual(self.starts('render'), [])
        self.assertFalse(self._group_gone(pgid))

    def test_a_launch_intent_without_a_process_never_frees_a_slot(self):
        job = self.submit()
        request_id = job['request_id']
        self.store.record(request_id, worker={
            'stage': 'render', 'pid': None, 'pgid': None, 'token': 'intent',
            'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
            'pid_start': None, 'log': ''})
        self.store.transition(request_id, 'rendering_previews', token='intent')

        runner = self.open_runner()
        runner.recover()

        self.assertEqual(self.state(request_id), 'worker_unavailable')
        self.assertEqual(runner.slots['render'], request_id)

    def test_a_replacement_is_not_available_outside_its_two_states(self):
        runner = self.open_runner()
        with self.assertRaises(ItemJobError) as raised:
            runner.resolve_replacement(self.submit()['request_id'])
        self.assertEqual(raised.exception.code, 'not_available')
        with self.assertRaises(ItemJobError) as raised:
            runner.resolve_replacement(str(uuid.uuid4()), 'builtin.green_arabica.stick')
        self.assertEqual(raised.exception.code, 'unknown_job')

    def _group_gone(self, pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        return False

    def _kill_group(self, pgid):
        for number in (signal.SIGKILL,):
            try:
                os.killpg(pgid, number)
            except (ProcessLookupError, PermissionError):
                return


class ChildTempRootTest(QueueTest):
    """Child HOME, XDG, and TMPDIR state is ephemeral. Path relations only, never a value."""

    VARIABLES = ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'TMPDIR')

    def temp_root(self, request_id):
        return item_jobs._child_temp_root(request_id)

    def kill_group(self, pgid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)

    def hung_render(self, runner):
        self.scenarios({'render': {'1': 'hang', '2': 'ok'}})
        job = self.submit()
        return job['request_id'], self.expire_lease(runner, job['request_id'])

    def test_the_five_variables_resolve_below_one_owner_only_root(self):
        job_dir = self.store.job_dir(self.submit()['request_id'])
        environment = item_jobs._child_environment(job_dir)
        root = self.temp_root(job_dir.name).resolve()

        locations = [Path(environment[name]).resolve() for name in self.VARIABLES]
        self.assertEqual(len(set(locations)), len(self.VARIABLES))
        for name, location in zip(self.VARIABLES, locations):
            with self.subTest(variable=name):
                self.assertTrue(location.is_dir())
                self.assertTrue(location.is_relative_to(root))
                self.assertFalse(location.is_relative_to(job_dir.resolve()))
        self.assertEqual(root.parent, self.configured.resolve())
        self.assertFalse(root.is_relative_to(self.root.resolve() / 'jobs'))
        self.assertEqual(stat.S_IMODE(os.lstat(root).st_mode), 0o700)
        self.assertEqual(sorted(path.name for path in job_dir.iterdir() if path.name == 'home'), [])

    def test_two_jobs_never_share_a_root(self):
        first, second = (self.store.job_dir(self.submit(description=text)['request_id'])
                         for text in ('First token', 'Second token'))

        roots = {Path(item_jobs._child_environment(job_dir)['HOME']).resolve().parent
                 for job_dir in (first, second)}

        self.assertEqual(len(roots), 2)

    def test_a_foreign_entry_at_the_root_name_is_refused_and_never_followed(self):
        request_id = self.submit()['request_id']
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('keep')
        self.temp_root(request_id).symlink_to(outside, target_is_directory=True)

        with self.assertRaises(OSError):
            item_jobs._child_environment(self.store.job_dir(request_id))
        item_jobs._remove_child_temp(request_id)

        self.assertEqual(sorted(path.name for path in outside.iterdir()), ['keep.txt'])
        self.assertTrue(self.temp_root(request_id).is_symlink())

    def test_a_symlink_planted_at_a_child_name_is_refused_and_never_followed(self):
        """A leftover owned root is reused, so each fixed child takes the root's own rule."""
        for name in ('home', 'tmp', 'cache'):
            with self.subTest(child=name):
                request_id = self.submit(description=f'Token {name}')['request_id']
                outside = self.root / f'outside-{name}'
                outside.mkdir()
                root = self.temp_root(request_id)
                root.mkdir(mode=0o700)
                (root / name).symlink_to(outside, target_is_directory=True)

                with self.assertRaises(OSError):
                    item_jobs._child_environment(self.store.job_dir(request_id))

                self.assertTrue((root / name).is_symlink())
                self.assertEqual(list(outside.iterdir()), [])
        # A plain file at a child name is refused by the same rule.
        request_id = self.submit(description='Token file')['request_id']
        self.temp_root(request_id).mkdir(mode=0o700)
        (self.temp_root(request_id) / 'config').write_text('not a directory')
        with self.assertRaises(OSError):
            item_jobs._child_environment(self.store.job_dir(request_id))

    def test_a_confirmed_exit_cleans_the_root_on_success_and_on_failure(self):
        for scenario, final in (('ok', 'preview_ready'), ('fail_hard', 'failed')):
            with self.subTest(render=scenario):
                self.scenarios({'render': [scenario, scenario]})
                existed, remove = [], item_jobs._remove_child_temp

                def recording(request_id):
                    existed.append(self.temp_root(request_id).is_dir())
                    remove(request_id)

                runner = self.open_runner()
                job = self.submit(description=f'Token {scenario}')
                with unittest.mock.patch.object(item_jobs, '_remove_child_temp', recording):
                    self.drive(runner, lambda: self.state(job['request_id']) == final)

                # Every child had the root, and every confirmed exit removed it again.
                self.assertGreaterEqual(len(existed), 2)
                self.assertTrue(all(existed))
                self.assertFalse(self.temp_root(job['request_id']).exists())
                # No job record names the ephemeral root.
                self.assertNotIn('cinta-item-job-', json.dumps(self.store.get(job['request_id'])))

    def test_a_timeout_cleans_the_root_after_the_group_is_confirmed_gone(self):
        runner = self.open_runner()
        runner.term_wait_s = runner.kill_wait_s = 1.0
        request_id, pgid = self.hung_render(runner)
        self.assertTrue(self.temp_root(request_id).is_dir())

        self.drive(runner, lambda: self.state(request_id) == 'preview_ready', timeout=40.0)

        self.wait_gone(pgid)
        self.assertFalse(self.temp_root(request_id).exists())

    def test_the_root_stays_while_the_group_may_live_and_goes_after_the_cleanup(self):
        runner = self.open_runner()
        request_id, hung = self.hung_render(runner)
        confirm = runner._group_gone
        runner._group_gone = (lambda pgid, pid=None:
                              False if pgid == hung else confirm(pgid, pid))

        self.drive(runner, lambda: self.state(request_id) == 'worker_unavailable')
        self.assertTrue(self.temp_root(request_id).is_dir())

        del runner._group_gone
        runner.confirm_cleanup(request_id)
        self.assertFalse(self.temp_root(request_id).exists())

    def test_a_shutdown_cleans_the_root_of_a_terminated_child(self):
        runner = self.open_runner()
        runner.term_wait_s = runner.kill_wait_s = 1.0
        self.scenarios({'render': {'1': 'hang'}})
        request_id = self.submit()['request_id']
        self.drive(runner, lambda: (self.store.get(request_id).get('worker') or {}).get('stage')
                   == 'render' and self.starts('render'))
        self.assertTrue(self.temp_root(request_id).is_dir())

        runner.shutdown()

        self.assertFalse(self.temp_root(request_id).exists())

    def test_recovery_cleans_a_gone_group_and_keeps_the_root_of_a_live_one(self):
        marker = self.root / 'orphan.pid'
        leaders = {'gone': subprocess.Popen([sys.executable, '-c', 'pass'],
                                            start_new_session=True),
                   'alive': subprocess.Popen([sys.executable, '-c', ORPHAN_SOURCE, str(marker)],
                                             start_new_session=True)}
        jobs = {}
        for name, leader in leaders.items():
            leader.wait()
            request_id = self.submit(description=f'Token {name}')['request_id']
            item_jobs._child_environment(self.store.job_dir(request_id))
            self.store.record(request_id, worker={
                'stage': 'render', 'pid': leader.pid, 'pgid': leader.pid, 'token': name,
                'lease_deadline': '2026-09-20T09:00:00Z', 'started': '2026-09-20T09:00:00Z',
                'pid_start': 'Mon Jan  1 00:00:00 2024', 'log': ''})
            self.store.transition(request_id, 'rendering_previews', token=name)
            jobs[name] = request_id
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.01)
        self.addCleanup(self.kill_group, leaders['alive'].pid)
        self.assertTrue(marker.is_file())

        self.open_runner().recover()

        self.assertEqual(self.state(jobs['alive']), 'worker_unavailable')
        self.assertTrue(self.temp_root(jobs['alive']).is_dir())
        self.assertNotEqual(self.state(jobs['gone']), 'worker_unavailable')
        self.assertFalse(self.temp_root(jobs['gone']).exists())


class RetentionBoundTest(QueueTest):
    def test_blocked_jobs_fill_the_queue_and_the_bound_refuses_the_next(self):
        """I1: operator_required is blocked, so the queued-work bound alone never binds."""
        for index in range(item_jobs.MAX_RETAINED_OPEN_JOBS):
            job = self.submit(f'Token {index}')
            self.store.transition(job['request_id'], 'operator_required',
                                  error='provider_cache_miss')

        with self.assertRaises(ItemJobError) as raised:
            self.submit('One too many')
        self.assertEqual(raised.exception.code, 'queue_full')
        self.assertIn('operator', str(raised.exception))
        self.assertEqual(len(self.store.jobs()), item_jobs.MAX_RETAINED_OPEN_JOBS)

        # A terminal job stops being open, so admission continues.
        first = self.store.jobs()[0]['request_id']
        self.store.transition(first, 'failed', error='render_failed')
        self.submit('Now admitted')

    def test_the_index_survives_a_store_reopen(self):
        request_ids = sorted(self.submit(f'Token {index}')['request_id'] for index in range(3))
        self.store.close()

        self.store = self.open_store()

        self.assertEqual(sorted(job['request_id'] for job in self.store.jobs()), request_ids)
        self.assertEqual(len(self.store.summaries()), 3)

    def test_one_scheduling_pass_never_rescans_the_job_tree(self):
        """I1b: 250 ms rescans of a retained tree are unbounded work."""
        for index in range(4):
            self.submit(f'Token {index}')
        runner = self.open_runner()
        scans = []
        original = Path.iterdir

        def counted(self_path):
            scans.append(str(self_path))
            return original(self_path)

        with unittest.mock.patch.object(Path, 'iterdir', counted), \
                unittest.mock.patch.object(os, 'scandir', side_effect=AssertionError('scandir')):
            self.store.jobs()
            self.store.summaries()
            runner._reap()

        self.assertEqual(scans, [])


class ZombieGroupTest(QueueTest):
    def test_a_zombie_member_counts_as_exited(self):
        """I3: as PID 1 with no init, an unreaped member would pin the stage forever."""
        runner = self.open_runner()
        child = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(0)'],
                                 start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self._is_zombie(child.pid):
            time.sleep(0.02)
        self.assertTrue(self._is_zombie(child.pid), 'the child never became a zombie')

        self.assertTrue(runner._group_gone(child.pid, child.pid))

        # The zombie was reaped, so this handle is already collected.
        child.returncode = 0
        with self.assertRaises(ProcessLookupError):
            os.killpg(child.pid, 0)

    def test_a_live_member_still_counts_as_present(self):
        runner = self.open_runner()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'],
                                 start_new_session=True)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertFalse(runner._group_gone(child.pid, child.pid))

    @staticmethod
    def _is_zombie(pid):
        for member_pid, state in item_jobs._group_members(pid) or []:
            if member_pid == pid:
                return state.startswith('Z')
        return False


class StagingTest(QueueTest):
    def test_a_lock_busy_loop_leaves_no_staging_directory(self):
        """T2: render_work is recreated per attempt and must never survive one."""
        self.scenarios({'render': ['lock_busy', 'lock_busy']})
        job = self.submit()
        request_id = job['request_id']
        runner = self.open_runner()
        runner.step()
        self.reap(runner, lambda: self.state(request_id) == 'waiting_for_render')
        runner.step()
        self.reap(runner, lambda: self.settled(request_id))

        job_dir = self.store.job_dir(request_id)
        self.assertFalse((job_dir / 'render_work').exists())
        source = (HERE / 'item_job_render.py').read_text()
        self.assertIn('finally:', source)
        self.assertIn('shutil.rmtree(work, ignore_errors=True)', source)


# A wrapper that starts a descendant in its own group and exits cleanly, exactly like
# item_job_render.py starting render_suite.py, which starts Blender.
DESCENDANT_WRAPPER = (
    "import json, os, subprocess, sys\n"
    "from pathlib import Path\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "Path(sys.argv[1]).write_text(json.dumps({'wrapper': os.getpid(), 'child': child.pid}))\n"
    "sys.exit(0)\n"
)


class DescendantExitTest(QueueTest):
    """An ordinary wrapper exit must not free a stage while a descendant lives."""

    def wrapper_commands(self, marker):
        script = self.root / 'descendant_wrapper.py'
        script.write_text(DESCENDANT_WRAPPER)
        return {'render': lambda job, job_dir: [sys.executable, str(script), str(marker)]}

    def staged_job(self):
        job = self.submit()
        (self.store.job_dir(job['request_id']) / 'recipe.json').write_text('{}')
        self.store.transition(job['request_id'], 'waiting_for_render')
        return job['request_id']

    def descendant(self, marker, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.01)
        self.assertTrue(marker.is_file(), 'the wrapper never started a descendant')
        return json.loads(marker.read_text())['child']

    def test_a_live_descendant_blocks_the_stage_until_the_group_is_gone(self):
        marker = self.root / 'descendant.json'
        runner = self.open_runner(commands=self.wrapper_commands(marker))
        request_id = self.staged_job()
        runner.step()
        child_pid = self.descendant(marker)
        self.addCleanup(self._kill, child_pid)

        # Record whether the descendant was still alive at the moment the slot freed,
        # and how many render children were in flight on every pass.
        freed_with_live_descendant = []
        overlap = []
        original = runner._release

        def watched(request, entry, *, free_slot):
            if free_slot:
                freed_with_live_descendant.append(self._alive(child_pid))
            return original(request, entry, free_slot=free_slot)

        runner._release = watched
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            runner.step()
            overlap.append(len(runner.children))
            if self.state(request_id) in ('failed', 'preview_ready'):
                break
            time.sleep(0.01)

        self.assertFalse(self._alive(child_pid), 'the descendant outlived the stage')
        self.assertEqual(freed_with_live_descendant, [False] * len(freed_with_live_descendant))
        self.assertTrue(freed_with_live_descendant, 'the slot was never released')
        # One child per stage holds on every pass.
        self.assertLessEqual(max(overlap), 1)
        stored = self.store.get(request_id)
        # A terminated descendant means the artifacts may be incomplete: never a success.
        self.assertEqual(stored['state'], 'failed')
        self.assertEqual(stored['error'], 'render_failed')
        self.assertEqual(stored['progress'], 'a descendant outlived the stage wrapper')

        # The slot bounds are unchanged by this fix.
        self.assertEqual(item_jobs.MAX_QUEUED_JOBS, 4)
        self.assertEqual(item_jobs.MAX_RETAINED_OPEN_JOBS, 32)
        self.assertEqual(item_jobs.MAX_ATTEMPTS, 2)
        # One slot per configured stage, unchanged by this fix.
        self.assertEqual(sorted(runner.slots), sorted(runner.commands))

    def test_an_unconfirmed_descendant_blocks_the_stage_and_starts_no_replacement(self):
        marker = self.root / 'descendant.json'
        runner = self.open_runner(commands=self.wrapper_commands(marker))
        request_id = self.staged_job()
        runner.step()
        child_pid = self.descendant(marker)
        self.addCleanup(self._kill, child_pid)
        runner._group_gone = lambda pgid, pid=None: False

        self.drive(runner, lambda: self.state(request_id) == 'worker_unavailable')

        self.assertEqual(runner.slots['render'], request_id)
        self.assertEqual(runner.unavailable.get('render'), request_id)
        launches = len(self.store.get(request_id)['history'])
        for _ in range(5):
            runner.step()
        self.assertEqual(len(self.store.get(request_id)['history']), launches)
        self.assertEqual(self.store.get(request_id)['attempts']['render'], 1)

    @staticmethod
    def _alive(pid):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    @staticmethod
    def _kill(pid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


class PreviewConfinementTest(QueueTest):
    def test_a_parent_directory_swap_at_the_open_boundary_is_refused(self):
        """The job directory is replaced by a symlink between the first and second open."""
        self.scenarios({})
        runner = self.open_runner()
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'preview_ready')
        stored = self.store.get(request_id)
        job_dir = self.store.job_dir(request_id)

        outside = self.root / 'outside'
        (outside / 'previews').mkdir(parents=True)
        recorded = (job_dir / 'previews' / 'perspective.png').read_bytes()
        # The decoy matches the recorded size and hash, so only confinement can refuse it.
        (outside / 'previews' / 'perspective.png').write_bytes(recorded)

        original = os.open
        calls = []

        def hook(path, flags, *arguments, **keywords):
            calls.append(path)
            if len(calls) == 1:
                moved = job_dir.with_name(job_dir.name + '.moved')
                job_dir.rename(moved)
                job_dir.symlink_to(outside, target_is_directory=True)
            return original(path, flags, *arguments, **keywords)

        with unittest.mock.patch.object(os, 'open', hook):
            with self.assertRaises(ItemJobError) as raised:
                item_jobs.read_preview(stored, job_dir, 'perspective.png')

        self.assertEqual(raised.exception.code, 'preview_unavailable')
        self.assertGreaterEqual(len(calls), 2)
        # No path-based test may remain on this route.
        source = (HERE / 'item_jobs.py').read_text()
        route = source.split('def read_preview(')[1].split('\ndef ')[0]
        for banned in ('is_symlink', '.exists(', '.resolve(', '.is_dir(', '.is_file('):
            self.assertNotIn(banned, route)


class RetainedHistoryTest(QueueTest):
    def fill(self, count, state='failed', error='render_failed'):
        for index in range(count):
            request_id = self.submit(f'Token {index}')['request_id']
            self.store.transition(request_id, state, error=error)

    def test_a_full_history_refuses_admission_without_creating_a_directory(self):
        """Terminal jobs stop counting toward every other bound, so one total is needed."""
        self.fill(item_jobs.MAX_RETAINED_JOBS)
        before = sorted(path.name for path in (self.root / 'jobs').iterdir())

        request_id = str(uuid.uuid4())
        for _ in range(2):
            with self.assertRaises(ItemJobError) as raised:
                self.store.submit({'request_id': request_id, 'description': 'One too many',
                                   'expected_catalog_revision': REVISION}, REVISION)
            # The same request gets the same answer while the condition holds.
            self.assertEqual(raised.exception.code, 'history_full')
            self.assertIn('archive', str(raised.exception))
        self.assertEqual(sorted(path.name for path in (self.root / 'jobs').iterdir()), before)
        self.assertNotIn(request_id, before)

    def test_an_exact_retry_of_an_admitted_job_survives_a_full_history(self):
        payload = {'request_id': str(uuid.uuid4()), 'description': 'Admitted first',
                   'expected_catalog_revision': REVISION}
        admitted, created = self.store.submit(payload, REVISION)
        self.assertTrue(created)
        self.store.transition(admitted['request_id'], 'failed', error='render_failed')
        self.fill(item_jobs.MAX_RETAINED_JOBS - 1)

        retried, created_again = self.store.submit(dict(payload), REVISION)

        self.assertFalse(created_again)
        self.assertEqual(retried['request_id'], admitted['request_id'])

    def test_terminal_history_is_durable_across_a_store_reopen(self):
        self.fill(5)
        open_id = self.submit('Still open')['request_id']
        self.store.close()

        self.store = self.open_store()

        self.assertEqual(len(self.store.jobs()), 6)
        self.assertEqual([job['request_id'] for job in self.store.open_jobs()], [open_id])

    def test_one_scheduling_pass_reads_only_the_open_jobs(self):
        self.fill(40)
        open_ids = {self.submit(f'Open {index}')['request_id'] for index in range(3)}
        runner = self.open_runner()
        seen = []
        original = self.store.open_jobs

        def counted():
            found = original()
            seen.append(len(found))
            return found

        self.store.open_jobs = counted
        with unittest.mock.patch.object(type(self.store), 'jobs',
                                        side_effect=AssertionError('jobs() in a pass')):
            runner._reap()
            for job in self.store.open_jobs():
                pass
            runner.step()

        self.assertEqual({job['request_id'] for job in original() if is_open_state(job)}, open_ids)
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), item_jobs.MAX_RETAINED_OPEN_JOBS)
        self.assertLess(max(seen), 43)


def is_open_state(job):
    return job['state'] not in item_jobs.TERMINAL


class RunnerFaultTest(QueueTest):
    def test_faults_stay_bounded_while_the_total_stays_monotonic(self):
        runner = self.open_runner()
        for index in range(1000):
            runner._fault(f'injected_{index % 3}')

        health = runner.health()
        self.assertEqual(health['fault_total'], 1000)
        self.assertEqual(len(health['recent_faults']), item_jobs.MAX_RECENT_FAULTS)
        self.assertEqual(len(runner.recent_faults), 16)
        self.assertEqual(health['last_fault'], 'injected_0')
        self.assertTrue(all(code.startswith('injected_') for code in health['recent_faults']))

    def test_a_transient_fault_clears_after_clean_passes_but_the_total_stays(self):
        runner = self.open_runner()
        runner._fault('injected_once')
        self.assertEqual(runner.health()['last_fault'], 'injected_once')

        for _ in range(item_jobs.FAULT_CLEAR_PASSES):
            runner.step()

        health = runner.health()
        self.assertIsNone(health['last_fault'])
        self.assertEqual(health['recent_faults'], [])
        # The operator still sees that it happened.
        self.assertEqual(health['fault_total'], 1)


class ReleaseGateTest(QueueTest):
    def test_process_identity_reads_proc_then_falls_back_to_ps(self):
        """G3: a slim Linux image ships no ps. /proc always answers there."""
        # Field 2 can hold spaces and brackets, so the parser uses the LAST bracket.
        fields = ' '.join(str(index) for index in range(3, 53))
        # Each token equals its own field number, so field 22 must read back as '22'.
        self.assertEqual(item_jobs._proc_start(f'41 (odd ) name) {fields}'), '22')
        self.assertIsNone(item_jobs._proc_start('41 (short) 3 4'))

        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / '41').mkdir()
            (proc / '41' / 'stat').write_text(f'41 (python) {fields}\n')
            with unittest.mock.patch.object(item_jobs, 'PROC_ROOT', proc), \
                    unittest.mock.patch.object(item_jobs.subprocess, 'run') as ps:
                self.assertEqual(item_jobs._pid_start(41), '22')
                self.assertIsNone(item_jobs._pid_start(999))
                ps.assert_not_called()

        with unittest.mock.patch.object(item_jobs, 'PROC_ROOT', Path('/tmp/cinta-no-proc')):
            answer = unittest.mock.Mock(stdout='Mon Jan  1 00:00:00 2024\n')
            with unittest.mock.patch.object(item_jobs.subprocess, 'run', return_value=answer) as ps:
                self.assertEqual(item_jobs._pid_start(41), 'Mon Jan  1 00:00:00 2024')
                ps.assert_called_once()

    def test_real_child_identity_is_readable_on_this_platform(self):
        """G3: the recovery guard must hold for a real child here, not only for fakes."""
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        marker = item_jobs._pid_start(child.pid)
        self.assertTrue(marker)
        self.assertEqual(item_jobs._pid_start(child.pid), marker)

    def test_an_unconfirmed_runner_thread_is_an_unhealthy_shutdown(self):
        """G4: never report a clean stop while the thread may still write."""
        runner = self.open_runner()
        blocked = threading.Event()
        self.addCleanup(blocked.set)
        runner._thread = threading.Thread(target=blocked.wait, daemon=True)
        runner._thread.start()

        self.assertFalse(runner.stop(timeout=0.2))
        self.assertTrue(runner.unhealthy_shutdown)
        self.assertTrue(runner.health()['unhealthy_shutdown'])
        self.assertEqual(runner.last_fault, 'runner_thread_join_timeout')
        self.assertEqual(runner.health()['last_fault'], 'runner_thread_join_timeout')

        blocked.set()
        runner._thread.join(2)
        self.assertTrue(runner.stop(timeout=2))

    def test_health_reports_queue_liveness_and_blocked_stages(self):
        """G6: a dead runner thread must make health fail."""
        runner = self.open_runner()
        health = runner.health()
        self.assertEqual(health['fault_total'], 0)
        self.assertIsNone(health['last_fault'])
        self.assertEqual(health['recent_faults'], [])
        self.assertFalse(health['runner_thread_alive'])
        self.assertEqual(health['active_children'], {'generation': 0, 'render': 0})
        self.assertEqual(health['worker_unavailable_stages'], [])
        self.assertEqual(health['provider_mode'], 'fake')

        runner.unavailable['render'] = 'a-job'
        self.assertEqual(runner.health()['worker_unavailable_stages'], ['render'])
        runner.start()
        self.addCleanup(runner.stop)
        self.assertTrue(runner.health()['runner_thread_alive'])

    def test_preview_bytes_are_verified_and_confinement_holds(self):
        """G5: no symlink component, no second path lookup, no replaced bytes."""
        self.scenarios({})
        runner = self.open_runner()
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'preview_ready')
        stored = self.store.get(request_id)
        job_dir = self.store.job_dir(request_id)

        data, content_type = item_jobs.read_preview(stored, job_dir, 'perspective.png')
        self.assertEqual(content_type, 'image/png')
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         stored['artifacts']['previews']['perspective.png']['sha256'])

        for name in ('object.blend', 'render.json', '../recipe.json'):
            with self.assertRaises(ItemJobError) as raised:
                item_jobs.read_preview(stored, job_dir, name)
            self.assertEqual(raised.exception.code, 'unknown_preview')

        # A final-component symlink is refused even when it points at a real file.
        secret = self.root / 'secret.png'
        secret.write_bytes(b'not a preview')
        target = job_dir / 'previews' / 'top.png'
        target.unlink()
        target.symlink_to(secret)
        with self.assertRaises(ItemJobError) as raised:
            item_jobs.read_preview(stored, job_dir, 'top.png')
        self.assertEqual(raised.exception.code, 'preview_unavailable')

        # A parent-directory symlink is refused too.
        moved = self.root / 'moved-previews'
        (job_dir / 'previews').rename(moved)
        (job_dir / 'previews').symlink_to(moved)
        with self.assertRaises(ItemJobError):
            item_jobs.read_preview(stored, job_dir, 'perspective.png')
        (job_dir / 'previews').unlink()
        moved.rename(job_dir / 'previews')

        # Replacement after preview_ready fails the hash. An oversize record fails the cap.
        (job_dir / 'previews' / 'perspective.png').write_bytes(b'replaced bytes')
        with self.assertRaises(ItemJobError) as raised:
            item_jobs.read_preview(stored, job_dir, 'perspective.png')
        self.assertEqual(raised.exception.code, 'preview_unavailable')
        huge = {**stored, 'artifacts': {'previews': {'object.glb': {
            'sha256': '0' * 64, 'bytes': item_jobs.MAX_PREVIEW_BYTES + 1}}}}
        with self.assertRaises(ItemJobError):
            item_jobs.read_preview(huge, job_dir, 'object.glb')

    def test_a_job_writes_nothing_outside_its_root_and_the_temp_directory(self):
        """G7: durable writes stay in the job root and the packaged tree stays unchanged."""
        staging = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        fixtures = staging / 'fixtures'
        shutil.copytree(item_jobs.FIXTURE_ROOT, fixtures)
        for path in sorted(fixtures.rglob('*'), reverse=True):
            path.chmod(0o555 if path.is_dir() else 0o444)
        fixtures.chmod(0o555)
        before_generator = _tree_snapshot(item_jobs.HERE / 'generator')
        before_fixtures = _tree_snapshot(fixtures)

        runner = self.open_runner(commands=item_jobs.fake_commands(fixtures=fixtures,
                                                                   stages=self.STAGES))
        job = self.submit()
        self.drive(runner, lambda: self.state(job['request_id']) == 'preview_ready')

        self.assertEqual(_tree_snapshot(item_jobs.HERE / 'generator'), before_generator)
        self.assertEqual(_tree_snapshot(fixtures), before_fixtures)
        job_dir = self.store.job_dir(job['request_id'])
        # The child homes are ephemeral now: see ChildTempRootTest. Nothing of them may
        # stay in the persistent job directory. Names only, never an environment value.
        self.assertFalse((job_dir / 'home').exists())
        self.assertNotIn('XDG_CACHE_HOME', sorted(os.environ))

    def test_an_unknown_job_record_schema_is_refused(self):
        """G8: a downgrade refuses a newer record instead of misreading it."""
        job = self.submit()
        path = self.store.job_dir(job['request_id']) / 'job.json'
        record = json.loads(path.read_text())
        self.assertEqual(record['schema_version'], item_jobs.JOB_SCHEMA_VERSION)

        for version in (item_jobs.JOB_SCHEMA_VERSION + 1, None, 'one'):
            path.write_text(json.dumps({**record, 'schema_version': version}))
            with self.assertRaises(ItemJobError) as raised:
                self.store.get(job['request_id'])
            self.assertEqual(raised.exception.code, 'unsupported_job_schema')
        # A newer record must never look absent and be overwritten.
        with self.assertRaises(ItemJobError):
            self.store.submit({'request_id': job['request_id'], 'description': 'A token',
                               'expected_catalog_revision': REVISION}, REVISION)

    def test_state_and_error_labels_cover_every_code(self):
        """Drift guard: the browser must never drop a state or leave an error unexplained."""
        source = (HERE / 'live_web/timeline.mjs').read_text()
        states = set(re.findall(r'^  (\w+):', source.split('JOB_STATE_LABELS = {')[1]
                                .split('};')[0], re.M))
        errors = set(re.findall(r'^  (\w+):', source.split('JOB_ERROR_LABELS = {')[1]
                                .split('};')[0], re.M))

        import live
        # Every code the server can return, not only the stored job error codes.
        returnable = (set(item_jobs.ERRORS) | set(live.ITEM_JOB_STATUS)
                      | {'origin_required', 'cache_entry_invalid', 'stale_token'})
        self.assertEqual(states, set(item_jobs.STATES))
        self.assertEqual(sorted(returnable - errors), [])
        self.assertIn('operator_required', states)


def _tree_snapshot(root):
    return sorted((str(path.relative_to(root)), path.stat().st_size)
                  for path in Path(root).rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts)


class GeneratorInterfaceTest(unittest.TestCase):
    """The generator contract. These prove request identity and the deployment rules."""

    STAR_DIGEST = 'c5d88ad700875432803b4c9016746ff64e03740d65a373d73e2812af48ed27f8'
    JEV_DIGEST = 'fb801b56fe4855844e7d4a92d4df62054baf9e7213e97a04ae5fe69ac646d4a9'
    RECIPE_SHA256 = 'ab172827287041a6d98b836b37e7297ff9d5438e397ef7c213ed25f47293549b'
    # The service runs the source copy. The recorded cache stays in the research tree.
    GENERATOR = item_jobs.GENERATOR_ROOT
    EVIDENCE = (HERE.parents[1]
                / 'thoughts/taras/research/coffee-quality/object-generation/results')

    def probe(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('probe_under_test',
                                                      self.GENERATOR / 'probe.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules['probe_under_test'] = module
        spec.loader.exec_module(module)
        module.REQUESTS.clear()
        return module

    def test_cached_star_request_identity_is_unchanged(self):
        """Every existing cache entry must still hit after the generator edit."""
        import hashlib
        probe = self.probe()
        payload = json.loads((self.EVIDENCE / 'gemini/star'
                              / 'openrouter_request.json').read_text())
        digest = hashlib.sha256(
            json.dumps([probe.OPENROUTER_URL, payload], sort_keys=True).encode()).hexdigest()

        self.assertEqual(digest, self.STAR_DIGEST)
        self.assertTrue((self.EVIDENCE / 'cache' / f'{digest}.json').is_file())

    def test_cached_star_replay_hits_both_requests_without_a_credential(self):
        """The recipe prompt embeds the Jev answer, so BOTH requests must hit."""
        probe = self.probe()
        description = probe.CASES['star']
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'generation' / 'run'
            out.mkdir(parents=True)
            (out.parent / 'cache').symlink_to(self.EVIDENCE / 'cache')
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                for name in ('OPENROUTER_API_KEY', 'TYPESAFE_API_KEY'):
                    os.environ.pop(name, None)
                with unittest.mock.patch.object(
                        sys, 'argv',
                        ['probe.py', '--description', description, '--out', str(out),
                         '--env-file', str(Path(directory) / 'absent.env'),
                         '--source-revision', 'c' * 40]):
                    probe.main()
            recipe = out / 'custom' / 'recipe.json'
            self.assertEqual(hashlib.sha256(recipe.read_bytes()).hexdigest(), self.RECIPE_SHA256)

        digests = [row['request_sha256'] for row in probe.REQUESTS]
        self.assertEqual(digests, [self.JEV_DIGEST, self.STAR_DIGEST])
        self.assertEqual([row['outcome'] for row in probe.REQUESTS], ['hit', 'hit'])
        self.assertTrue(all(row['cached'] for row in probe.REQUESTS))

    def test_a_jev_cache_miss_never_degrades_to_a_different_recipe_request(self):
        probe = self.probe()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'generation' / 'run'
            out.mkdir(parents=True)
            (out.parent / 'cache').mkdir()
            with unittest.mock.patch.object(
                    sys, 'argv',
                    ['probe.py', '--description', 'An uncached description', '--out', str(out),
                     '--env-file', str(Path(directory) / 'absent.env'),
                     '--source-revision', 'c' * 40]):
                with self.assertRaises(probe.CacheMiss):
                    probe.main()
        # It stopped on the Jev request. It never built a second, different recipe request.
        self.assertEqual([row['outcome'] for row in probe.REQUESTS], ['cache_miss'])

    def test_source_revision_is_validated_and_never_calls_git(self):
        probe = self.probe()
        with unittest.mock.patch.object(probe.subprocess, 'check_output') as git:
            self.assertEqual(probe.source_revision('c' * 40), 'c' * 40)
            with self.assertRaises(SystemExit):
                probe.source_revision('not-a-sha')
            with self.assertRaises(SystemExit):
                probe.source_revision('C' * 40)
        git.assert_not_called()

    def test_render_suite_takes_the_lock_path_and_the_queue_never_does(self):
        source = (self.GENERATOR / 'render_suite.py').read_text()
        self.assertIn('"--lock"', source)
        self.assertIn('open(args.lock,', source)
        for name in ('item_jobs.py', 'item_job_render.py', 'item_job_generate.py'):
            self.assertNotIn('hackspain-coffee-runtime.lock', (HERE / name).read_text())
        self.assertNotIn('flock', (HERE / 'item_job_render.py').read_text())

    def test_wrapper_refuses_an_unpatched_generator_without_guessing(self):
        """F3: no exit code is ever derived from free provider or user text."""
        source = (HERE / 'item_job_generate.py').read_text()
        for pattern in ('request is not cached', 'diagnostic saved at', 'in message',
                        'in result.stderr', 'stderr.find'):
            self.assertNotIn(pattern, source)
        self.assertIn('REQUIRED_OUTCOMES', source)


class PhysicsAndTrainingFlowTest(QueueTest):
    """Phase 3 stages on the existing table: physics proposal, route, baseline, training.

    The children are fakes, but the provider mode is `cached`: a fake-provider job can
    never reach activation, so it could never exercise this flow.
    """

    provider_mode = 'cached'
    STAGES = tuple(item_jobs.STAGES)

    def catalog(self):
        return object_catalog.load_catalog()

    def labels(self):
        return [item['classifier_label'] for item in self.catalog()['definitions']]

    def policy(self, reject_classes=None):
        # Everything but `good` and one Keep survivor is rejected, so a victim exists.
        rejected = self.labels()[2:] if reject_classes is None else reject_classes
        return lambda: {'reject_classes': list(rejected), 'policy_version': 'policy-1'}

    def runner(self, reject_classes=None, catalog_provider=None):
        return self.open_runner(catalog_provider=catalog_provider or self.catalog,
                                policy_provider=self.policy(reject_classes))

    def test_a_clean_job_walks_the_whole_phase_three_flow(self):
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')
        stored = self.store.get(job['request_id'])

        self.assertEqual(['generation', 'render', 'physics_proposal', 'physics', 'training'],
                         [row['stage'] for row in self.starts_in_order()])
        self.assertTrue(stored['artifacts']['candidate_validation']['passed'])
        self.assertEqual('accept', stored['artifacts']['physics_route']['verdict'])
        baseline = stored['training_baseline']
        self.assertEqual(self.catalog()['catalog_revision'], baseline['catalog_revision'])
        self.assertEqual('policy-1', baseline['policy_version'])
        self.assertEqual(baseline['victim_id'], stored['victim'])
        # The summary shows the replacement and the active ordering before any activation.
        catalog = self.catalog()
        summary = self.store.summary(stored)
        replacement, identities = summary['replacement'], summary['identities']
        victim = next(item for item in catalog['definitions']
                      if item['object_type_id'] == replacement['victim_type_id'])
        self.assertEqual(victim['classifier_label'], replacement['victim_label'])
        self.assertEqual(catalog['active_type_ids'], replacement['active_type_ids_before'])
        survivors = [item for item in catalog['active_type_ids']
                     if item != replacement['victim_type_id']]
        self.assertEqual([replacement['new_type_id'], *survivors],
                         replacement['active_type_ids_after'])
        self.assertEqual(stored['artifacts']['candidate_validation']['new_label'],
                         replacement['new_label'])
        self.assertEqual(catalog['catalog_revision'], identities['baseline_catalog_revision'])
        self.assertRegex(identities['candidate_catalog_revision'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(identities['baseline_catalog_revision'],
                            identities['candidate_catalog_revision'])
        self.assertEqual('policy-1', identities['baseline_policy_version'])
        self.assertEqual('policy-1', identities['validation_policy_version'])
        self.assertEqual({None}, set(summary['activation'].values()))

    def test_the_validator_child_gets_the_job_asset_root_and_the_runtime_lock(self):
        job_dir = self.root / 'jobs' / 'x'
        argv = item_jobs.physics_command({'request_id': 'x'}, job_dir,
                                         preset=PRESET, runtime_lock=self.root / 'r.lock')

        self.assertIn('--asset-root', argv)
        self.assertEqual(str(job_dir), argv[argv.index('--asset-root') + 1])
        self.assertNotEqual(str(HERE), argv[argv.index('--asset-root') + 1])
        self.assertEqual(str(self.root / 'r.lock'), argv[argv.index('--runtime-lock') + 1])
        for flag in ('--no-air', '--seed', '--background-rate', '--json-out'):
            self.assertIn(flag, argv)

    def test_the_trainer_child_gets_the_catalog_the_preset_and_the_policy_file(self):
        job_dir = self.root / 'jobs' / 'x'
        argv = item_jobs.training_command({'request_id': 'x'}, job_dir,
                                          preset=PRESET, runtime_lock=self.root / 'r.lock')

        self.assertEqual(str(job_dir / 'training' / 'catalog'),
                         argv[argv.index('--catalog-root') + 1])
        self.assertEqual(str(job_dir / 'training' / 'policy.json'),
                         argv[argv.index('--policy') + 1])
        self.assertEqual(str(job_dir / 'training' / 'out'), argv[argv.index('--out') + 1])
        self.assertEqual(str(self.root / 'r.lock'), argv[argv.index('--runtime-lock') + 1])

    def test_the_queue_writes_the_policy_file_without_the_new_label(self):
        runner = self.runner()
        job = self.submit()
        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')
        written = json.loads(
            (self.store.job_dir(job['request_id']) / 'training/policy.json').read_text())

        self.assertEqual('policy-1', written['policy_version'])
        self.assertEqual(sorted(self.labels()[2:]), written['reject_classes'])
        self.assertNotIn('star_token', written['reject_classes'])

    def test_a_blocked_route_never_reaches_training(self):
        self.scenarios({'physics': ['fail_safe']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'physics_blocked')
        stored = self.store.get(job['request_id'])

        self.assertEqual('physics_unsupported', stored['error'])
        self.assertEqual('route_not_accepted', stored['reason'])
        self.assertEqual([], self.starts('training'))

    def test_a_physics_cache_miss_stops_at_operator_required_without_an_attempt(self):
        self.scenarios({'physics_proposal': ['fail_safe']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'operator_required')
        stored = self.store.get(job['request_id'])

        self.assertEqual('provider_cache_miss', stored['error'])
        self.assertEqual(0, stored['attempts']['physics_proposal'])

    def test_no_keep_victim_waits_for_a_replacement_and_releases_the_turn(self):
        # Every label except the anomaly reference is rejected, so no victim is eligible.
        runner = self.runner(reject_classes=self.labels()[1:])
        first, second = self.submit(), self.submit(description='Another token')

        self.drive(runner, lambda: self.state(first['request_id']) == 'waiting_for_replacement')
        self.drive(runner, lambda: self.state(second['request_id']) == 'waiting_for_replacement')

        self.assertEqual([], self.starts('training'))
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def test_a_queued_job_binds_the_catalog_that_exists_when_its_turn_begins(self):
        later = {**self.catalog(), 'catalog_revision': 'b' * 64}
        state = {'value': self.catalog()}
        runner = self.runner(catalog_provider=lambda: state['value'])
        job = self.submit()
        self.drive(runner, lambda: self.state(job['request_id']) == 'preview_ready'
                   or self.state(job['request_id']) == 'proposing_physics')
        state['value'] = later  # the catalog moves while the job is still upstream

        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')

        self.assertEqual('b' * 64,
                         self.store.get(job['request_id'])['training_baseline']['catalog_revision'])

    def test_a_stale_baseline_blocks_activation(self):
        """The baseline read and the activation re-check see different catalogs."""
        reads = {'count': 0}

        def moving_catalog():
            # The first read binds the baseline. Every later read reports a moved catalog.
            reads['count'] += 1
            base = self.catalog()
            return base if reads['count'] == 1 else {**base, 'catalog_revision': 'c' * 64}

        runner = self.runner(catalog_provider=moving_catalog)
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'activation_conflict')
        stored = self.store.get(job['request_id'])

        self.assertEqual('catalog_revision_conflict', stored['error'])
        self.assertEqual('stale_catalog_revision', stored['reason'])
        self.assertTrue(stored['artifacts']['candidate_validation']['passed'])

    def test_a_failed_candidate_validation_fails_the_job_with_its_evidence(self):
        self.scenarios({'training': ['fail_safe']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'failed')
        stored = self.store.get(job['request_id'])

        self.assertEqual('candidate_validation_failed', stored['error'])
        self.assertEqual('candidate_gate_failed', stored['reason'])
        self.assertIn('anomaly_fraction', stored['progress'])
        self.assertFalse(stored['artifacts']['candidate_validation']['passed'])

    def test_at_most_one_trainer_runs_across_jobs(self):
        runner = self.runner()
        first, second = self.submit(), self.submit(description='Another token')

        self.drive(runner, lambda: self.state(first['request_id']) == 'validating_candidate'
                   and self.state(second['request_id']) == 'validating_candidate', timeout=40.0)

        rows = [json.loads(line) for line
                in (self.root / 'worker_runs.jsonl').read_text().splitlines()
                if json.loads(line)['stage'] == 'training']
        running = 0
        for row in rows:
            running += 1 if row['event'] == 'start' else -1
            self.assertLessEqual(running, 1, 'two trainers overlapped')
        self.assertEqual(2, len({row['request_id'] for row in rows}))
        # The lease is released, so no owner is left behind.
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def test_a_busy_runtime_lock_keeps_the_visible_waiting_state(self):
        self.scenarios({'training': ['lock_busy']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'queued_for_training'
                   and self.settled(job['request_id']))
        stored = self.store.get(job['request_id'])

        self.assertIn('runtime lock busy', stored['progress'])
        self.assertEqual(0, stored['attempts']['training'])

    def test_the_physics_proposal_argv_carries_the_full_description_and_never_live(self):
        job_dir = self.root / 'jobs' / 'x'
        long_description = 'A small five-point gold star token with a polished rim'
        argv = item_jobs.physics_proposal_command(
            {'description': long_description, 'attempts': {'physics_proposal': 1},
             'provider_permission': None}, job_dir,
            mode='cached', provider_cache=self.root / 'provider-cache',
            replay_dir=self.root.parent / 'replay')

        self.assertEqual(long_description, argv[argv.index('--description') + 1])
        self.assertEqual(str(self.root.parent / 'replay'), argv[argv.index('--replay-dir') + 1])
        self.assertNotIn('--live', argv)
        self.assertNotIn('--env-file', argv)

    def test_the_policy_file_never_names_the_new_label(self):
        """F4: a live policy that names the new label must not reject it in training."""
        runner = self.runner(reject_classes=[*self.labels()[2:], 'star_token'])
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')
        written = json.loads(
            (self.store.job_dir(job['request_id']) / 'training/policy.json').read_text())

        self.assertNotIn('star_token', written['reject_classes'])
        self.assertEqual(sorted(self.labels()[2:]), written['reject_classes'])

    def test_training_never_starts_without_an_authoritative_policy(self):
        """S4: a recovered baseline job waits instead of binding Keep all."""
        runner = self.open_runner(stages=self.STAGES, catalog_provider=self.catalog,
                                  policy_provider=lambda: None)
        job = self.submit()

        self.drive(runner, lambda: self.store.get(job['request_id']).get('reason')
                   == 'waiting_for_engine_policy')
        stored = self.store.get(job['request_id'])

        self.assertEqual('selecting_training_baseline', stored['state'])
        self.assertEqual('waiting for the live policy', stored['progress'])
        self.assertEqual(0, stored['attempts']['training'])
        self.assertIsNone(stored['training_baseline'])
        self.assertEqual([], self.starts('training'))
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def moving_policy(self, first, later):
        """One policy for the baseline read, another for the validation read."""
        seen = {'count': 0}

        def provider():
            seen['count'] += 1
            reject, version = (first, 'policy-1') if seen['count'] == 1 else (later, 'policy-2')
            return {'reject_classes': list(reject), 'policy_version': version}

        return provider

    def test_a_survivor_toggle_during_training_never_blocks_activation(self):
        """A reject toggle stays usable while a candidate trains and while it drains."""
        labels = self.labels()
        # `faded` is a survivor that is not the victim, so the victim cannot move.
        runner = self.open_runner(
            stages=self.STAGES, catalog_provider=self.catalog,
            policy_provider=self.moving_policy(labels[4:], [labels[1], *labels[4:]]))
        job = self.submit()

        self.drive(runner, lambda: 'validation_policy'
                   in self.store.get(job['request_id'])['artifacts'])
        stored = self.store.get(job['request_id'])

        self.assertEqual('validating_candidate', stored['state'])
        self.assertIsNone(stored['error'])
        self.assertTrue(stored['artifacts']['candidate_validation']['passed'])
        # Both policies are recorded as evidence, and they are allowed to differ.
        self.assertEqual('policy-1', stored['training_baseline']['policy_version'])
        self.assertEqual(sorted(labels[4:]), stored['training_baseline']['reject_classes'])
        self.assertEqual('policy-2', stored['artifacts']['validation_policy']['policy_version'])
        self.assertEqual(sorted([labels[1], *labels[4:]]),
                         stored['artifacts']['validation_policy']['reject_classes'])

    def test_a_now_rejected_victim_still_blocks_activation(self):
        """The victim must still be a Keep type that the replacement can take over."""
        labels = self.labels()
        runner = self.open_runner(
            stages=self.STAGES, catalog_provider=self.catalog,
            policy_provider=self.moving_policy(labels[4:], labels[3:]))
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'activation_conflict')
        stored = self.store.get(job['request_id'])

        self.assertEqual('victim_no_longer_eligible', stored['reason'])
        self.assertEqual('replacement_conflict', stored['error'])
        self.assertTrue(stored['artifacts']['candidate_validation']['passed'])

    def test_a_launch_failure_gives_the_training_turn_back_at_once(self):
        """F1 and S5: no later job waits out the lease for a trainer that never ran."""
        def broken(job, job_dir):
            return [str(self.root / 'absent-binary')]

        commands = {**fake_commands(stages=self.STAGES), 'training': broken}
        runner = self.open_runner(commands=commands, catalog_provider=self.catalog,
                                  policy_provider=self.policy())
        first = self.submit()

        self.drive(runner, lambda: self.state(first['request_id']) == 'failed')

        self.assertEqual('training_failed', self.store.get(first['request_id'])['error'])
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())
        # A later job takes the turn immediately, not after the lease deadline.
        runner.commands['training'] = fake_commands(stages=('training',))['training']
        second = self.submit(description='Another token')
        self.drive(runner, lambda: self.state(second['request_id']) == 'validating_candidate')

    def test_a_training_builder_fault_gives_the_training_turn_back(self):
        """P1: a persistent pre-child fault is bounded and cannot starve a later job."""
        first = self.submit()
        later = self.submit(description='Another token')
        training = fake_commands(stages=('training',))['training']

        def broken(job, job_dir):
            if job['request_id'] == first['request_id']:
                raise RuntimeError('persistent training command-builder fault')
            return training(job, job_dir)

        commands = {**fake_commands(stages=self.STAGES), 'training': broken}
        runner = self.open_runner(commands=commands, catalog_provider=self.catalog,
                                  policy_provider=self.policy())

        self.drive(runner, lambda: self.state(first['request_id']) == 'failed'
                   and self.state(later['request_id']) == 'validating_candidate')

        stored = self.store.get(first['request_id'])
        self.assertEqual(MAX_ATTEMPTS, stored['attempts']['training'])
        self.assertEqual('training_failed', stored['error'])
        self.assertEqual('training_command_failed', stored['reason'])
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def test_a_validation_fault_gives_the_training_turn_back(self):
        """P1: a persistent post-exit fault is bounded and cannot strand validation."""
        runner = self.runner()
        first = self.submit()
        later = self.submit(description='Another token')
        validate = runner._validate_candidate

        def broken(job, job_dir, token):
            if job['request_id'] == first['request_id']:
                raise RuntimeError('persistent validation fault')
            return validate(job, job_dir, token)

        runner._validate_candidate = broken
        self.drive(runner, lambda: self.state(first['request_id']) == 'failed'
                   and self.state(later['request_id']) == 'validating_candidate')

        stored = self.store.get(first['request_id'])
        self.assertEqual(MAX_ATTEMPTS, stored['attempts']['training'])
        self.assertEqual('training_failed', stored['error'])
        self.assertEqual('candidate_validation_error', stored['reason'])
        self.assertIsNone(stored['worker'])
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def test_a_policy_provider_fault_is_bounded_and_releases_the_turn(self):
        """P1: a persistent policy read fault cannot retain the lease or starve later work."""
        first = self.submit()
        later = self.submit(description='Another token')
        healthy = self.policy()

        def broken_until_first_fails():
            if self.state(first['request_id']) != 'failed':
                raise RuntimeError('persistent policy provider fault')
            return healthy()

        runner = self.open_runner(stages=self.STAGES, catalog_provider=self.catalog,
                                  policy_provider=broken_until_first_fails)

        self.drive(runner, lambda: self.state(first['request_id']) == 'failed'
                   and self.state(later['request_id']) == 'validating_candidate')

        stored = self.store.get(first['request_id'])
        self.assertEqual(MAX_ATTEMPTS, stored['attempts']['training'])
        self.assertEqual('training_failed', stored['error'])
        self.assertEqual('training_baseline_failed', stored['reason'])
        self.assertFalse((self.root / item_jobs.TRAINING_LEASE).exists())

    def test_the_lock_backoff_counts_per_stage(self):
        """F7: a render lock-busy must not lengthen the first physics backoff."""
        self.scenarios({'render': ['lock_busy', 'ok'], 'physics': ['lock_busy', 'ok']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')
        counts = self.store.get(job['request_id'])['lock_busy_count']

        self.assertEqual({'render': 1, 'physics': 1}, counts)

    def test_a_hard_physics_failure_is_retried_once_then_retained(self):
        """Minor: fail_hard was untested and silently passed before."""
        self.scenarios({'physics_proposal': ['fail_hard', 'fail_hard']})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'failed')
        stored = self.store.get(job['request_id'])

        self.assertEqual('physics_proposal_failed', stored['error'])
        self.assertEqual(MAX_ATTEMPTS, stored['attempts']['physics_proposal'])

    def proposing(self, submission, attempt=1):
        """One job whose physics proposal child just exited. Its status file is a fake."""
        job = self.submit()
        job = self.store.transition(job['request_id'], 'proposing_physics',
                                    attempts={**job['attempts'], 'physics_proposal': attempt},
                                    provider_submission=submission)
        return job, self.store.job_dir(job['request_id'])

    def test_a_finished_paid_physics_call_never_stays_in_flight(self):
        """The same settlement as generation. A cache replay after a flight stays sticky."""
        runner = self.open_runner()
        for reported, settled in {'completed': 'completed', 'not_submitted': 'uncertain'}.items():
            with self.subTest(status=reported):
                job, job_dir = self.proposing('in_flight')
                (job_dir / 'definition.json').write_text('{}')
                (job_dir / 'physics.json').write_text(json.dumps({'physics_source': 'paid_llm_call'}))
                (job_dir / 'provider_status.json').write_text(json.dumps(
                    {'provider_submission': reported, 'cache_hit': False}))

                runner._settle_physics_proposal(job, job_dir, None, item_jobs.EXIT_OK)

                stored = self.store.get(job['request_id'])
                self.assertEqual('validating_physics', stored['state'])
                self.assertEqual(settled, stored['provider_submission'])
                self.assertNotEqual('in_flight', stored['provider_submission_history'][-1])

    def test_a_failed_stage_after_a_completed_call_settles_completed(self):
        """The provider answered and a later step failed. Known evidence wins."""
        runner = self.open_runner()
        for code in (item_jobs.EXIT_FAILED, item_jobs.EXIT_CACHE_ENTRY_INVALID):
            with self.subTest(exit_code=code):
                job, job_dir = self.proposing('in_flight', attempt=MAX_ATTEMPTS)
                (job_dir / 'physics.json').write_text(json.dumps(
                    {'physics_source': 'paid_llm_call', 'physics_request_sha256': 'd' * 64}))
                (job_dir / 'provider_status.json').write_text(json.dumps(
                    {'provider_submission': 'completed', 'request_sha256': 'd' * 64,
                     'reason': 'the draft definition is invalid: no proxy fits'}))

                runner._settle_physics_proposal(job, job_dir, None, code)

                stored = self.store.get(job['request_id'])
                self.assertEqual(('failed', 'physics_proposal_failed'),
                                 (stored['state'], stored['error']))
                self.assertEqual('completed', stored['provider_submission'])
                self.assertEqual('completed', stored['provider_submission_history'][-1])
                self.assertNotIn('uncertain', stored['provider_submission_history'])
                # This path neither returns the attempt nor touches a grant.
                self.assertEqual(job['attempts'], stored['attempts'])
                self.assertIsNone(stored['provider_permission'])
                self.assertIsNone(stored['provider_permission_stage'])
                self.assertEqual('d' * 64, stored['artifacts']['physics']['physics_request_sha256'])
                self.assertTrue(stored['progress'].startswith('the draft definition is invalid'))

    def test_a_cache_replay_after_a_completed_call_keeps_completed(self):
        """The retry reports not_submitted about ITSELF. The job keeps its billed fact."""
        runner = self.open_runner()
        job, job_dir = self.proposing('in_flight')
        (job_dir / 'physics.json').write_text(json.dumps({'physics_source': 'paid_llm_call'}))
        (job_dir / 'provider_status.json').write_text(json.dumps(
            {'provider_submission': 'completed', 'reason': 'the draft definition is invalid'}))
        runner._settle_physics_proposal(job, job_dir, None, item_jobs.EXIT_FAILED)
        retried = self.store.get(job['request_id'])
        self.assertEqual(('proposing_physics', 'completed'),
                         (retried['state'], retried['provider_submission']))

        # The one automatic retry finds the saved answer in the cache and succeeds.
        (job_dir / 'definition.json').write_text('{}')
        (job_dir / 'provider_status.json').write_text(json.dumps(
            {'provider_submission': 'not_submitted', 'cache_hit': True}))
        runner._settle_physics_proposal(retried, job_dir, None, item_jobs.EXIT_OK)

        stored = self.store.get(job['request_id'])
        self.assertEqual('validating_physics', stored['state'])
        self.assertEqual('completed', stored['provider_submission'])
        self.assertEqual(['not_submitted', 'in_flight', 'completed'],
                         stored['provider_submission_history'])

    def test_an_unsaved_live_answer_is_a_consumed_failure_that_stays_completed(self):
        """Exit 7: the answer is confirmed, so no path may read it as uncertain or unused."""
        for scenario, reason in (
                ('answer_unsaved', 'fake answer confirmed'),
                ('answer_unsaved_without_status', 'the provider answered')):
            with self.subTest(scenario=scenario):
                self.scenarios({'physics_proposal': {'1': scenario, '2': scenario}})
                runner = self.runner()
                job = self.submit(description=f'Token {scenario}')
                request_id = job['request_id']

                self.drive(runner, lambda: self.state(request_id) == 'failed')

                stored = self.store.get(request_id)
                self.assertEqual('physics_proposal_failed', stored['error'])
                self.assertEqual('completed', stored['provider_submission'])
                self.assertEqual('completed', stored['provider_submission_history'][-1])
                self.assertNotIn('uncertain', stored['provider_submission_history'])
                # Both attempts count as used, and no grant comes back.
                self.assertEqual(MAX_ATTEMPTS, stored['attempts']['physics_proposal'])
                self.assertEqual(2, len(self.starts('physics_proposal')))
                self.assertIsNone(stored['provider_permission'])
                self.assertIsNone(stored['blocked_stage'])
                self.assertTrue(stored['progress'].startswith(reason))
                runner.shutdown()
                (self.root / 'worker_runs.jsonl').unlink()

    def test_no_paid_retry_and_no_grant_follow_a_received_response(self):
        """Exit 7 after a granted paid call: the retry may replay the cache, never pay again."""
        runner = self.open_runner()
        job, job_dir = self.proposing('in_flight')
        self.assertIsNone(job['provider_permission'])  # the launch consumed the grant

        runner._settle_physics_proposal(job, job_dir, None, item_jobs.EXIT_RESPONSE_RECEIVED)

        retried = self.store.get(job['request_id'])
        self.assertEqual(('proposing_physics', 'completed'),
                         (retried['state'], retried['provider_submission']))
        self.assertEqual(job['attempts'], retried['attempts'])
        self.assertIsNone(retried['provider_permission'])
        self.assertIsNone(retried['provider_permission_stage'])
        self.assertFalse(item_jobs.live_permitted(retried, 'paid', 'physics_proposal'))
        argv = item_jobs.physics_proposal_command(
            retried, job_dir, mode='paid', provider_cache=self.root / 'provider-cache')
        self.assertNotIn('--live', argv)
        # No operator prompt either: nothing here can lead to a new paid request.
        self.assertIsNone(self.store.summary(retried)['primary_action'])

    def test_the_retry_after_an_unsaved_answer_keeps_completed(self):
        self.scenarios({'physics_proposal': {'1': 'answer_unsaved_without_status', '2': 'ok'}})
        runner = self.runner()
        job = self.submit()

        self.drive(runner, lambda: self.state(job['request_id']) == 'validating_candidate')

        stored = self.store.get(job['request_id'])
        self.assertEqual(2, stored['attempts']['physics_proposal'])
        self.assertEqual('completed', stored['provider_submission'])
        self.assertNotIn('uncertain', stored['provider_submission_history'])

    def test_a_known_provider_failure_shows_its_short_path_free_reason(self):
        runner = self.open_runner()
        job, job_dir = self.proposing('not_submitted')
        reason = f'physics proposal verification failed: unreadable {job_dir}/physics/out.json'
        (job_dir / 'provider_status.json').write_text(json.dumps(
            {'provider_submission': 'not_submitted', 'reason': reason + ' ' + 'x' * 400}))

        runner._settle_physics_proposal(job, job_dir, None, 1)

        stored = self.store.get(job['request_id'])
        # No new code: the generic error stays, and the short reason rides in progress.
        self.assertEqual('physics_proposal_failed', stored['error'])
        self.assertEqual('physics_proposal_failed', stored['reason'])
        self.assertTrue(stored['progress'].startswith(
            'physics proposal verification failed: unreadable <path>'))
        self.assertLessEqual(len(stored['progress']), 160)
        self.assertNotIn(str(self.root), json.dumps(stored))

    def starts_in_order(self):
        path = self.root / 'worker_runs.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        seen, ordered = set(), []
        for row in rows:
            if row['event'] == 'start' and row['stage'] not in seen:
                seen.add(row['stage'])
                ordered.append(row)
        return ordered


class ActivationRunnerTest(QueueTest):
    """The runner side of an activation, with a fake activator and a fake pointer reader."""

    provider_mode = 'cached'
    STAGES = tuple(item_jobs.STAGES)

    def setUp(self):
        super().setUp()
        self.catalog = object_catalog.load_catalog()
        self.calls = []
        self.answer = 'active'
        self.pointer = None

    def activator(self, job_id, candidate):
        """A fake service: it reports `activating` through the runner, then answers."""
        self.calls.append((job_id, dict(candidate), self.state(job_id)))
        if isinstance(self.answer, Exception):
            raise self.answer
        self.runner.record_activation(job_id, activation={
            'phase': 'activating', 'active_objects': 0, 'message': 'restarting the engine'})
        self.calls[-1] += (self.state(job_id),)
        return self.answer

    def open(self, **extra):
        policy = {'reject_classes': [item['classifier_label']
                                     for item in self.catalog['definitions']][2:],
                  'policy_version': 'policy-1'}
        self.runner = self.open_runner(
            catalog_provider=lambda: self.catalog, policy_provider=lambda: policy,
            activator=self.activator, active_bundle_sha256=lambda: self.pointer, **extra)
        return self.runner

    def validated(self, state='validating_candidate', **fields):
        """One job whose candidate passed validation, written as the training turn leaves it."""
        victim = self.catalog['active_type_ids'][1]
        job = self.submit(description=f'Token {uuid.uuid4()}')
        return self.store.transition(
            job['request_id'], state,
            training_baseline={'catalog_revision': self.catalog['catalog_revision'],
                               'victim_id': victim, 'policy_version': 'policy-1'},
            artifacts={'validation_policy': {'policy_version': 'policy-1'},
                       'candidate_validation': {'passed': True}}, **fields)

    def lease_owner(self):
        lease = self.root / item_jobs.TRAINING_LEASE
        return json.loads(lease.read_text())['owner'] if lease.is_file() else None

    def test_a_clean_job_reaches_active_through_the_fake_activator(self):
        runner = self.open()
        job = self.submit()
        request_id = job['request_id']

        self.drive(runner, lambda: self.state(request_id) == 'active')

        (job_id, candidate, during_call, after_report), = self.calls
        stored = self.store.get(request_id)
        job_dir = self.store.job_dir(request_id)
        # The runner drains first, and `activating` comes from the activator's record block.
        self.assertEqual(('draining_for_activation', 'activating'), (during_call, after_report))
        self.assertEqual(request_id, job_id)
        self.assertEqual({'catalog_root': job_dir / 'training' / 'catalog',
                          'model': job_dir / 'training' / 'out' / 'candidate.joblib',
                          'model_manifest': job_dir / 'training' / 'out' / 'candidate.manifest.json',
                          'preset': job_dir / 'training' / 'out' / 'candidate.preset.json',
                          'victim_id': stored['training_baseline']['victim_id'],
                          'expected_catalog_revision': self.catalog['catalog_revision'],
                          'evidence': {}}, candidate)
        self.assertIsNone(stored['error'])
        self.assertEqual('activating', self.store.summary(stored)['activation']['phase'])
        self.assertIsNone(self.lease_owner())
        # A terminal job is never handed over twice.
        for _ in range(3):
            runner.step()
        self.assertEqual(1, len(self.calls))

    def test_every_activator_answer_maps_to_its_state_and_releases_the_turn(self):
        runner = self.open()
        table = {
            'active': ('active', None),
            'replacement_conflict': ('replacement_conflict', 'replacement_conflict'),
            'activation_conflict': ('activation_conflict', 'catalog_revision_conflict'),
            'activation_failed': ('failed', 'activation_failed'),
            'an unknown answer': ('failed', 'activation_failed'),
            None: ('failed', 'activation_failed'),
        }
        for answer, expected in table.items():
            with self.subTest(answer=answer):
                self.answer = answer
                request_id = self.validated()['request_id']

                runner.step()

                stored = self.store.get(request_id)
                self.assertEqual(expected, (stored['state'], stored['error']))
                self.assertEqual('restarting the engine', stored['progress'])
                self.assertIsNone(self.lease_owner())
        self.assertEqual('resolve_replacement', self.store.summary(self.store.get(
            self.calls[1][0]))['primary_action'])

    def test_an_activator_that_raises_is_a_failed_activation_and_never_a_stuck_job(self):
        runner = self.open()
        self.answer = RuntimeError(f'the engine did not start in {self.root}/run')
        request_id = self.validated()['request_id']

        runner.step()

        stored = self.store.get(request_id)
        self.assertEqual(('failed', 'activation_failed'), (stored['state'], stored['error']))
        self.assertIn('RuntimeError', stored['progress'])
        self.assertNotIn(str(self.root), stored['progress'])
        self.assertIsNone(self.lease_owner())
        self.assertEqual('activation_failed_RuntimeError', runner.health()['last_fault'])

    def test_the_activator_runs_outside_the_store_lock_and_holds_the_training_turn(self):
        runner = self.open()
        seen = {}

        def activator(job_id, candidate):
            def probe():
                seen['store_lock_free'] = self.store.lock.acquire(blocking=False)
                if seen['store_lock_free']:
                    self.store.lock.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(10)
            seen['turn_owner'] = self.lease_owner()
            return 'active'

        runner.activator = activator
        request_id = self.validated()['request_id']

        runner.step()

        self.assertEqual({'store_lock_free': True, 'turn_owner': request_id}, seen)
        self.assertIsNone(self.lease_owner())

    def test_one_activation_at_a_time_waits_for_the_training_turn(self):
        runner = self.open()
        request_id = self.validated()['request_id']
        lease = self.root / item_jobs.TRAINING_LEASE
        lease.write_text(json.dumps({'owner': 'another-job', 'pgid': None, 'pid': None,
                                     'deadline': time.time() + 600}))

        runner.step()
        self.assertEqual([], self.calls)
        waiting = self.store.get(request_id)
        self.assertEqual(('validating_candidate', 'waiting_for_training_turn'),
                         (waiting['state'], waiting['reason']))

        lease.unlink()
        runner.step()
        self.assertEqual('active', self.state(request_id))

    def test_without_an_activator_a_validated_candidate_waits(self):
        runner = self.open()
        runner.activator = None
        request_id = self.validated()['request_id']

        runner.step()

        self.assertEqual('validating_candidate', self.state(request_id))

    def test_a_job_found_mid_activation_follows_the_pointer_and_never_restarts(self):
        committed = self.validated('activating', activation={'bundle_sha256': 'b' * 64})
        interrupted = self.validated('activating', activation={'bundle_sha256': 'c' * 64})
        draining = self.validated('draining_for_activation')
        (self.root / item_jobs.TRAINING_LEASE).write_text(json.dumps(
            {'owner': committed['request_id'], 'pgid': None, 'pid': None,
             'deadline': time.time() + 600}))
        self.pointer = 'b' * 64

        self.open().recover()

        self.assertEqual(('active', None), (self.state(committed['request_id']),
                                            self.store.get(committed['request_id'])['error']))
        for job in (interrupted, draining):
            stored = self.store.get(job['request_id'])
            self.assertEqual(('failed', 'activation_failed', 'activation_interrupted'),
                             (stored['state'], stored['error'], stored['reason']))
        self.assertEqual([], self.calls)
        self.assertIsNone(self.lease_owner())

    def test_an_unreadable_pointer_fails_the_interrupted_activation(self):
        job = self.validated('activating', activation={'bundle_sha256': 'b' * 64})
        runner = self.open()

        def unreadable():
            raise OSError('the pointer cannot be read')

        runner.active_bundle_sha256 = unreadable
        runner.recover()

        self.assertEqual('failed', self.state(job['request_id']))
        self.assertEqual('active_pointer_unreadable_OSError', runner.health()['last_fault'])

    def test_an_exact_retry_passes_the_recorded_bundle(self):
        runner = self.open()
        self.validated(activation={'bundle_sha256': 'b' * 64})

        runner.step()

        self.assertEqual('b' * 64, self.calls[0][1]['bundle_sha256'])

    def test_a_confirmed_replacement_retrains_without_any_provider_work(self):
        runner = self.open()
        job = self.submit()
        request_id = job['request_id']
        self.drive(runner, lambda: self.state(request_id) == 'active')
        automatic = self.store.get(request_id)['training_baseline']['victim_id']
        confirmed = next(item for item in reversed(self.catalog['active_type_ids'])
                         if item != automatic)
        good = next(item['object_type_id'] for item in self.catalog['definitions']
                    if item['classifier_label'] == object_catalog.ANOMALY_REFERENCE_LABEL)
        before = self.store.transition(request_id, 'replacement_conflict',
                                       error='replacement_conflict')
        starts = {stage: len(self.starts(stage)) for stage in self.STAGES}

        for refused in (good, 'builtin.absent.type', None, ['not', 'text']):
            with self.assertRaises(ItemJobError) as raised:
                runner.resolve_replacement(request_id, refused)
            self.assertEqual('invalid_request', raised.exception.code)
        resolved = runner.resolve_replacement(request_id, confirmed)

        self.assertEqual(('selecting_training_baseline', None, 0),
                         (resolved['state'], resolved['error'], resolved['attempts']['training']))
        self.assertNotIn('validation_policy', resolved['artifacts'])
        self.assertEqual(before['artifacts']['previews'], resolved['artifacts']['previews'])
        self.assertEqual(before['provider_evidence'], resolved['provider_evidence'])

        self.drive(runner, lambda: self.state(request_id) == 'active')

        stored = self.store.get(request_id)
        self.assertEqual((confirmed, True), (stored['training_baseline']['victim_id'],
                                             stored['training_baseline']['victim_confirmed']))
        self.assertEqual(confirmed, self.calls[-1][1]['victim_id'])
        # Only the training turn ran again. No generation, render, or physics work repeats.
        again = {stage: len(self.starts(stage)) - starts[stage] for stage in self.STAGES}
        self.assertEqual({'generation': 0, 'render': 0, 'physics_proposal': 0, 'physics': 0,
                          'training': 1}, again)
        self.assertEqual(before['attempts']['generation'], stored['attempts']['generation'])
        # A generated victim hands its rendered files to the archive of a later activation.
        evidence = runner._victim_evidence(stored['training_baseline']['new_type_id'])
        self.assertEqual(sorted(item_jobs.PREVIEW_DOWNLOADS), sorted(evidence))

    def test_a_replacement_also_leaves_waiting_for_replacement(self):
        runner = self.open()
        waiting = self.validated('waiting_for_replacement')
        victim = self.catalog['active_type_ids'][-1]

        resolved = runner.resolve_replacement(waiting['request_id'], victim)

        self.assertEqual(('selecting_training_baseline', victim),
                         (resolved['state'], resolved['replacement_victim']))


# The tracked, byte-identical copy of the frozen server to UI contract.
CONTRACT = HERE / 'ITEM_SUMMARY_CONTRACT.md'
EXISTING_SUMMARY_KEYS = [
    'request_id', 'display_name', 'description', 'requester_name', 'state', 'error',
    'updated_at', 'created_at', 'preview', 'attempts', 'progress', 'reason', 'blocked_stage',
    'primary_action', 'provider_mode', 'provider_cache_hit']


def contract_tables(text):
    """Block name to member names, read from the markdown tables of the frozen contract."""
    blocks, current = {}, None
    for line in text.splitlines():
        if line.startswith('#'):
            heading = re.match(r'^## `(\w+)`', line)
            current = heading.group(1) if heading else None
            if current is not None:
                blocks[current] = []
            continue
        member = re.match(r'^\| `(\w+)` \|', line)
        if current is not None and member:
            blocks[current].append(member.group(1))
    return blocks


class SummaryContractTest(QueueTest):
    """The four additive summary blocks of the frozen server to UI contract."""

    provider_mode = 'cached'
    ACTIVATION = {'phase': 'active', 'active_objects': 0, 'drain_timeout_seconds': 20.0,
                  'result': 'active', 'rolled_back': False, 'message': 'activated',
                  'bundle_sha256': 'b' * 64, 'previous_bundle_sha256': 'a' * 64,
                  'activation_policy_version': 'policy-3', 'session_id': 'session-2',
                  'score_epoch_id': 'epoch-2'}

    def test_a_new_job_has_every_block_and_only_null_members(self):
        summary = self.store.summary(self.submit())

        self.assertEqual(EXISTING_SUMMARY_KEYS + list(item_jobs.SUMMARY_BLOCKS), list(summary))
        for name, members in item_jobs.SUMMARY_BLOCKS.items():
            with self.subTest(block=name):
                self.assertEqual(list(members), list(summary[name]))
        unknown = {(name, member) for name in item_jobs.SUMMARY_BLOCKS
                   for member, value in summary[name].items() if value is not None}
        # The submission state is a string from the first moment. Everything else is unknown.
        self.assertEqual({('evidence', 'provider_submission')}, unknown)
        self.assertEqual('not_submitted', summary['evidence']['provider_submission'])

    def test_the_block_and_member_names_equal_the_contract_tables(self):
        """The file and the code cannot drift silently: the tables are parsed, not copied."""
        text = CONTRACT.read_text()
        tables = contract_tables(text)
        summary = self.store.summary(self.submit())

        self.assertEqual(['replacement', 'activation', 'identities', 'evidence'], list(tables))
        self.assertEqual(tables, {name: list(summary[name]) for name in tables})
        self.assertEqual(tables, {name: list(members)
                                  for name, members in item_jobs.SUMMARY_BLOCKS.items()})
        # The activator writes ONE record block: the six activation members plus the last
        # five identities. live.py is read as text, so this test never imports the service.
        source = (HERE / 'live.py').read_text()
        declared = re.search(r'^ACTIVATION_MEMBERS = (\([^)]*\))', source, re.MULTILINE)
        members = ast.literal_eval(declared.group(1))
        self.assertEqual(11, len(members))
        self.assertEqual(tables['activation'], list(members[:6]))
        self.assertLessEqual(set(members[6:]), set(tables['identities']))
        # Every value the code can produce is a value the contract allows.
        row = next(line for line in text.splitlines() if line.startswith('| `evidence_kind` |'))
        allowed = set(re.findall(r'`"(\w+)"`', row.split('|')[2])) | {None}
        produced = {item_jobs.evidence_kind(mode, hit, source_name)
                    for mode in ('fake', 'cached', 'paid', None)
                    for hit in (True, False, None)
                    for source_name in (*item_jobs.PHYSICS_SOURCES, 'fake', None)}
        self.assertEqual(allowed, produced)

    def test_the_evidence_kind_follows_the_derivation_table(self):
        replay, paid = item_jobs.PHYSICS_SOURCES
        table = [
            (('fake', None, None), 'fake'),
            (('fake', False, paid), 'fake'),
            (('cached', True, replay), 'cached_generation_cached_physics'),
            (('paid', True, replay), 'cached_generation_cached_physics'),
            (('paid', False, replay), 'paid_generation_cached_physics'),
            # A paid physics call never gets a kind that names cached physics.
            (('paid', False, paid), None),
            (('paid', True, paid), None),
            (('cached', True, paid), None),
            # Anything else, or a stage that has not run.
            (('cached', False, replay), None),
            (('cached', True, None), None),
            (('paid', None, replay), None),
            ((None, True, replay), None),
        ]
        for arguments, expected in table:
            with self.subTest(arguments=arguments):
                self.assertEqual(expected, item_jobs.evidence_kind(*arguments))

    def test_the_record_blocks_fill_the_summary_and_survive_a_reopened_store(self):
        request_id = self.submit()['request_id']
        physics = {'physics_source': 'cached_llm_replay',
                   'physics_measurement_status': 'unmeasured_proxy_estimate',
                   'recipe_sha256': 'c' * 64, 'glb_sha256': 'd' * 64,
                   'physics_request_sha256': 'e' * 64, 'physics_cache_entry_sha256': 'f' * 64}
        self.store.record(
            request_id, generation_cache_hit=True,
            provider_evidence={'request_sha256': '1' * 64, 'recipe_sha256': 'c' * 64},
            artifacts={'physics': physics, 'validation_policy': {'policy_version': 'policy-2'},
                       'candidate_validation': {'artifact_sha256': '2' * 64}},
            training_baseline={'catalog_revision': '3' * 64, 'policy_version': 'policy-1',
                               'candidate_catalog_revision': '4' * 64})
        # The activator reports through the public record, with its eleven members.
        self.store.record(request_id, activation=dict(self.ACTIVATION))
        self.store.close()
        reopened = self.open_store()

        summary = reopened.summary(reopened.get(request_id))

        self.assertEqual({name: self.ACTIVATION[name]
                          for name in item_jobs.SUMMARY_BLOCKS['activation']},
                         summary['activation'])
        self.assertEqual({
            'baseline_catalog_revision': '3' * 64, 'candidate_catalog_revision': '4' * 64,
            'model_artifact_sha256': '2' * 64, 'bundle_sha256': 'b' * 64,
            'previous_bundle_sha256': 'a' * 64, 'baseline_policy_version': 'policy-1',
            'validation_policy_version': 'policy-2', 'activation_policy_version': 'policy-3',
            'session_id': 'session-2', 'score_epoch_id': 'epoch-2'}, summary['identities'])
        self.assertEqual({'evidence_kind': 'cached_generation_cached_physics',
                          'provider_submission': 'not_submitted',
                          'recipe_request_sha256': '1' * 64, **physics}, summary['evidence'])
        # Existing top-level keys keep their values.
        self.assertEqual('cached', summary['provider_mode'])


class ValidationCompletenessTest(unittest.TestCase):
    """F3: a partial trainer record must never count as passed."""

    def validation(self, **changes):
        value = {
            'passed': True, 'failures': [], 'label_order_ok': True,
            'classifier': {'holdout_accuracy': 0.97},
            'anomaly': {'fraction_above_threshold': 0.0},
            'keep_outcome': {'runs': [{'resolved': 34, 'accept_fraction': 1.0}]},
            'pulses': {'runs': []},
            'policy': {'applied_reject_classes': [], 'new_label_policy': 'keep'},
            'preset_compatibility': {'loaded': True},
        }
        value.update(changes)
        return value

    def test_a_complete_record_is_accepted(self):
        self.assertEqual([], item_jobs.incomplete_validation(self.validation()))

    def test_every_missing_evidence_block_is_named(self):
        for name in item_jobs.VALIDATION_EVIDENCE:
            with self.subTest(missing=name):
                value = self.validation()
                del value[name]
                self.assertEqual([name], item_jobs.incomplete_validation(value))

    def test_a_bare_passed_record_is_incomplete(self):
        missing = item_jobs.incomplete_validation({'passed': True})

        self.assertEqual(sorted((*item_jobs.VALIDATION_EVIDENCE, 'failures')), missing)

    def test_a_complete_record_that_reports_failures_is_not_incomplete(self):
        """A failed gate is a different outcome from a partial file."""
        self.assertEqual([], item_jobs.incomplete_validation(self.validation(
            passed=False, failures=['anomaly_fraction'])))


class PhysicsReplayTest(unittest.TestCase):
    """Cached physics replay. No credential, no network, and no provider call."""

    STAR_BOUNDS_M = [0.01694960594177246, 0.016120851516723635, 0.002]
    SHORT = 'A small five-point gold star token, about 18 mm wide and 2 mm thick.'
    # The original star job description, unchanged. The generation request keeps it whole.
    FULL = ('A small five-point gold star token, about 18 mm wide and 2 mm thick. Make one '
            'continuous solid with five clear points and five concave valleys. Lay the '
            'silhouette in the XY plane.')
    SHORT_DIGEST = '0778635c802a96bd0bca95805dec15812fb4bd239ad777aa41f45a1177e7d5dc'
    FULL_DIGEST = 'ea53b1ebd9c76560277528c7135a9cd26c4b70db6d42b4c5767563812f78fcc3'
    CACHE_SHA256 = '19d3195ff370978bee73d957326668d4239b5e05fed28a2e544e51089e23d4cd'
    AUTHENTIC = Path('/private/tmp/cache') / f'{SHORT_DIGEST}.json'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.replay = self.root / 'replay'
        self.replay.mkdir()

    def metadata(self, recipe_sha256='a' * 64, glb_sha256='b' * 64, **changes):
        value = {'schema_version': 1,
                 'binding': {'recipe_sha256': recipe_sha256, 'glb_sha256': glb_sha256},
                 'physics_description': self.SHORT,
                 'expected_request_sha256': self.SHORT_DIGEST,
                 'cache_entry_sha256': self.CACHE_SHA256}
        value.update(changes)
        path = self.replay / f'{value["binding"]["recipe_sha256"]}.json'
        path.write_text(json.dumps(value, indent=2, sort_keys=True))
        return path

    def test_the_bound_replay_description_reproduces_the_recorded_request_digest(self):
        """Identity, with no credential and no network."""
        from object_definitions import physics_request

        _, digest = physics_request(description=self.SHORT,
                                    visual_dimensions_m=self.STAR_BOUNDS_M)

        self.assertEqual(self.SHORT_DIGEST, digest)

    def test_the_full_description_reproduces_a_different_uncached_digest(self):
        """A user prompt is never truncated, so the default request is the full one."""
        from object_definitions import physics_request

        _, digest = physics_request(description=self.FULL,
                                    visual_dimensions_m=self.STAR_BOUNDS_M)

        self.assertEqual(self.FULL_DIGEST, digest)
        self.assertNotEqual(self.SHORT_DIGEST, digest)

    def test_metadata_bound_to_this_job_is_used(self):
        self.metadata(recipe_sha256='c' * 64, glb_sha256='d' * 64)

        found = item_job_physics.load_replay(self.replay, 'c' * 64, 'd' * 64)

        self.assertEqual(self.SHORT, found['physics_description'])
        self.assertEqual(64, len(found['metadata_sha256']))

    def test_metadata_bound_to_another_asset_is_ignored_without_an_error(self):
        self.metadata(recipe_sha256='c' * 64, glb_sha256='d' * 64)

        self.assertIsNone(item_job_physics.load_replay(self.replay, 'c' * 64, 'e' * 64))
        self.assertIsNone(item_job_physics.load_replay(self.replay, 'f' * 64, 'd' * 64))
        self.assertIsNone(item_job_physics.load_replay(None, 'c' * 64, 'd' * 64))

    def test_malformed_bound_metadata_is_refused(self):
        cases = {
            'extra field': {'note': 'x'},
            'missing binding field': {'binding': {'recipe_sha256': 'c' * 64}},
            'bad digest': {'expected_request_sha256': 'nope'},
            'unknown version': {'schema_version': 2},
            'empty description': {'physics_description': '  '},
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.metadata(recipe_sha256='c' * 64, glb_sha256='d' * 64, **change)
                with self.assertRaises(item_job_physics.ReplayError):
                    item_job_physics.load_replay(self.replay, 'c' * 64, 'd' * 64)

    def test_a_changed_cache_file_is_cache_entry_invalid_and_never_a_miss(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / f'{self.SHORT_DIGEST}.json').write_text('{"tampered": true}')

        with self.assertRaisesRegex(item_job_physics.ReplayError, 'cache entry bytes'):
            item_job_physics.verify_cache_entry(cache, self.SHORT_DIGEST, self.CACHE_SHA256)

    @unittest.skipUnless(AUTHENTIC.is_file(),
                         'the authentic cached physics entry is a local artifact')
    def test_the_authentic_cache_entry_verifies_against_the_recorded_hash(self):
        """Read only. The entry is never modified and never copied into the repo."""
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / f'{self.SHORT_DIGEST}.json').write_bytes(self.AUTHENTIC.read_bytes())

        actual = item_job_physics.verify_cache_entry(
            cache, self.SHORT_DIGEST, self.CACHE_SHA256)

        self.assertEqual(self.CACHE_SHA256, actual)

    def star_job(self):
        """One job directory holding the real star recipe and its rendered asset."""
        star = HERE.parents[1] / ('thoughts/taras/research/coffee-quality/'
                                  'object-generation/results/gemini/star')
        job = self.root / 'jobs' / 'star'
        (job / 'previews').mkdir(parents=True)
        shutil.copy(star / 'recipe.json', job / 'recipe.json')
        shutil.copy(star / 'render/object.glb', job / 'previews/object.glb')
        record = json.loads((star / 'render/render.json').read_text())
        (job / 'previews/render.json').write_text(json.dumps(record))
        return job, item_job_physics.sha256_file(job / 'recipe.json'), \
            item_job_physics.sha256_file(job / 'previews/object.glb')

    def run_wrapper(self, job, replay_dir, cache_root):
        return item_job_physics.main([
            '--job-dir', str(job), '--description', self.FULL, '--mode', 'cached',
            '--provider-cache', str(cache_root),
            *(['--replay-dir', str(replay_dir)] if replay_dir else [])])

    @unittest.skipUnless(AUTHENTIC.is_file(),
                         'the authentic cached physics entry is a local artifact')
    def test_a_bound_replay_rebuilds_the_definition_from_the_cached_entry(self):
        """The whole path, with no credential and no network."""
        job, recipe_sha256, glb_sha256 = self.star_job()
        self.metadata(recipe_sha256=recipe_sha256, glb_sha256=glb_sha256)
        cache_root = self.root / 'provider-cache'
        (cache_root / 'cache').mkdir(parents=True)
        (cache_root / 'cache' / f'{self.SHORT_DIGEST}.json').write_bytes(
            self.AUTHENTIC.read_bytes())

        code = self.run_wrapper(job, self.replay, cache_root)
        physics = json.loads((job / 'physics.json').read_text())
        definition = json.loads((job / 'definition.json').read_text())

        self.assertEqual(0, code)
        self.assertEqual('cached_llm_replay', physics['physics_source'])
        self.assertEqual('operator_replay_metadata', physics['physics_description_source'])
        self.assertEqual(self.SHORT_DIGEST, physics['physics_request_sha256'])
        self.assertEqual(self.CACHE_SHA256, physics['physics_cache_entry_sha256'])
        self.assertEqual('unmeasured_proxy_estimate', physics['physics_measurement_status'])
        # Provenance is rebuilt through the real adapter on this job's own artifacts.
        self.assertEqual('llm', definition['physics']['proposal_provenance']['kind'])
        self.assertEqual(self.SHORT_DIGEST,
                         definition['physics']['proposal_provenance']['request_sha256'])
        self.assertEqual('previews/object.glb', definition['visual']['uri'])
        self.assertEqual(f'sha256:{glb_sha256}', definition['visual']['visual_asset_id'])

    @unittest.skipUnless(AUTHENTIC.is_file(),
                         'the authentic cached physics entry is a local artifact')
    def test_without_metadata_the_full_description_misses_and_never_submits(self):
        job, _, _ = self.star_job()
        cache_root = self.root / 'provider-cache'
        (cache_root / 'cache').mkdir(parents=True)
        (cache_root / 'cache' / f'{self.SHORT_DIGEST}.json').write_bytes(
            self.AUTHENTIC.read_bytes())

        code = self.run_wrapper(job, None, cache_root)
        status = json.loads((job / 'provider_status.json').read_text())

        self.assertEqual(item_jobs.EXIT_NOT_SUBMITTED, code)
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertEqual(self.FULL_DIGEST, status['request_sha256'])
        self.assertEqual('job_description', status['physics_description_source'])
        self.assertFalse((job / 'definition.json').exists())

    @unittest.skipUnless(AUTHENTIC.is_file(),
                         'the authentic cached physics entry is a local artifact')
    def test_a_wrong_expected_digest_is_cache_entry_invalid(self):
        job, recipe_sha256, glb_sha256 = self.star_job()
        self.metadata(recipe_sha256=recipe_sha256, glb_sha256=glb_sha256,
                      expected_request_sha256='9' * 64)
        cache_root = self.root / 'provider-cache'
        (cache_root / 'cache').mkdir(parents=True)

        code = self.run_wrapper(job, self.replay, cache_root)
        status = json.loads((job / 'provider_status.json').read_text())

        self.assertEqual(item_jobs.EXIT_CACHE_ENTRY_INVALID, code)
        self.assertIn('recomputed request digest', status['reason'])
        self.assertFalse((job / 'definition.json').exists())

    @unittest.skipUnless(AUTHENTIC.is_file(),
                         'the authentic cached physics entry is a local artifact')
    def test_use_cache_resumes_the_physics_proposal_not_generation(self):
        """F6 and S6: the real wrapper, a real miss, then a real recovery.

        The operator sees a PHYSICS block and answers it. The job must resume the physics
        proposal with the same request, never fall back to generation.
        """
        store = ItemJobStore(self.root / 'store', provider_mode='cached')
        self.addCleanup(store.close)
        revision = object_catalog.load_catalog()['catalog_revision']
        job, _ = store.submit({'request_id': str(uuid.uuid4()), 'description': self.FULL,
                               'expected_catalog_revision': revision}, revision)
        request_id = job['request_id']
        job_dir = store.job_dir(request_id)
        star = HERE.parents[1] / ('thoughts/taras/research/coffee-quality/'
                                  'object-generation/results/gemini/star')
        (job_dir / 'previews').mkdir(parents=True, exist_ok=True)
        shutil.copy(star / 'recipe.json', job_dir / 'recipe.json')
        shutil.copy(star / 'render/object.glb', job_dir / 'previews/object.glb')
        shutil.copy(star / 'render/render.json', job_dir / 'previews/render.json')
        recipe_sha256 = item_job_physics.sha256_file(job_dir / 'recipe.json')
        glb_sha256 = item_job_physics.sha256_file(job_dir / 'previews/object.glb')
        cache_root = self.root / 'provider-cache'
        (cache_root / 'cache').mkdir(parents=True)
        store.transition(request_id, 'preview_ready')

        runner = ItemJobRunner(
            store,
            {'physics_proposal': lambda value, directory: item_jobs.physics_proposal_command(
                value, directory, mode='cached', provider_cache=cache_root,
                replay_dir=self.replay)},
            runtime_lock_path=self.root / 'r.lock')
        self.addCleanup(runner.shutdown)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and store.get(request_id)['state'] != 'operator_required':
            runner.step()
            time.sleep(0.02)
        blocked = store.get(request_id)

        self.assertEqual('operator_required', blocked['state'])
        self.assertEqual('physics_proposal', blocked['blocked_stage'])
        self.assertEqual('provider_cache_miss', blocked['error'])
        self.assertEqual(0, blocked['attempts']['generation'])

        # The operator stages the reviewed replay and its cache entry, then answers.
        self.metadata(recipe_sha256=recipe_sha256, glb_sha256=glb_sha256)
        (cache_root / 'cache' / f'{self.SHORT_DIGEST}.json').write_bytes(
            self.AUTHENTIC.read_bytes())
        resumed = runner.resolve_provider(request_id, 'use_cache')

        self.assertEqual('preview_ready', resumed['state'])
        self.assertNotEqual('generating_recipe', resumed['state'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and store.get(request_id)['state'] != 'validating_physics':
            runner.step()
            time.sleep(0.02)
        done = store.get(request_id)
        physics = json.loads((job_dir / 'physics.json').read_text())

        self.assertEqual('validating_physics', done['state'])
        self.assertEqual(0, done['attempts']['generation'])
        self.assertEqual(recipe_sha256, physics['recipe_sha256'])
        self.assertEqual(glb_sha256, physics['glb_sha256'])
        self.assertEqual(self.SHORT_DIGEST, physics['physics_request_sha256'])

    def test_a_request_body_can_never_carry_a_physics_description_or_a_replay(self):
        store = ItemJobStore(self.root / 'jobs', provider_mode='cached')
        self.addCleanup(store.close)
        for field in ('physics_description', 'replay_dir', 'physics_replay', 'live'):
            with self.subTest(field=field):
                with self.assertRaises(ItemJobError) as raised:
                    store.submit({'request_id': str(uuid.uuid4()), 'description': 'A token',
                                  'expected_catalog_revision': REVISION, field: 'x'}, REVISION)
                self.assertEqual('invalid_request', raised.exception.code)


if __name__ == '__main__':
    unittest.main()
