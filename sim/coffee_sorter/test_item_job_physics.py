"""Wrapper rules of item_job_physics.py. No credential, no network, no provider call.

Every provider result here is a local cache file or a fake, so no test can submit
anything. The typed outcome mapping is checked against the real probe classes, because
that mapping is the only thing standing between a recorded failure and a false claim
that a request may have reached the provider.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import item_job_physics
import item_jobs
from generator import probe
from object_definitions import physics_request


class WrapperTest(unittest.TestCase):
    """One job directory with a recipe, a rendered asset, and an empty provider cache."""

    DESCRIPTION = 'a small brass token'
    BOUNDS_MM = [10.0, 10.0, 10.0]

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.job = self.root / 'job'
        self.replay = self.root / 'replay'
        (self.job / 'previews').mkdir(parents=True)
        (self.job / 'recipe.json').write_text('{}')
        (self.job / 'previews/object.glb').write_bytes(b'glb')
        (self.job / 'previews/render.json').write_text(
            json.dumps({'bounding_dimensions_mm': self.BOUNDS_MM}))
        self.cache_root = self.root / 'provider-cache'
        (self.cache_root / 'cache').mkdir(parents=True)

    def digest(self):
        _, value = physics_request(
            description=self.DESCRIPTION,
            visual_dimensions_m=[value * 0.001 for value in self.BOUNDS_MM])
        return value

    def status(self, job=None):
        return json.loads(((job or self.job) / 'provider_status.json').read_text())

    def run_wrapper(self, *extra, mode='cached'):
        return item_job_physics.main([
            '--job-dir', str(self.job), '--description', self.DESCRIPTION,
            '--mode', mode, '--provider-cache', str(self.cache_root), *extra])

    def metadata(self, **changes):
        """Reviewed replay metadata bound to THIS job's own artifacts."""
        recipe_sha256 = item_job_physics.sha256_file(self.job / 'recipe.json')
        glb_sha256 = item_job_physics.sha256_file(self.job / 'previews/object.glb')
        value = {'schema_version': 1,
                 'binding': {'recipe_sha256': recipe_sha256, 'glb_sha256': glb_sha256},
                 'physics_description': self.DESCRIPTION,
                 'expected_request_sha256': self.digest(),
                 'cache_entry_sha256': 'c' * 64}
        value.update(changes)
        self.replay.mkdir(exist_ok=True)
        (self.replay / f'{recipe_sha256}.json').write_text(json.dumps(value, sort_keys=True))
        return value


class LiveModeGuardTest(WrapperTest):

    def test_live_outside_paid_mode_is_refused_before_any_work(self):
        """The job directory is empty, so a refusal proves no artifact was read."""
        empty = self.root / 'empty-job'

        code = item_job_physics.main([
            '--job-dir', str(empty), '--description', self.DESCRIPTION, '--mode', 'cached',
            '--provider-cache', str(self.cache_root), '--live'])
        status = self.status(empty)

        self.assertEqual(item_jobs.EXIT_FAILED, code)
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertTrue(status['live_requested'])
        self.assertIn('paid mode', status['reason'])
        # No cache link and no evidence directory: nothing started.
        self.assertFalse((empty / 'physics').exists())

    def test_cached_mode_without_live_still_reaches_its_ordinary_miss(self):
        code = self.run_wrapper()

        self.assertEqual(item_jobs.EXIT_NOT_SUBMITTED, code)
        self.assertFalse(self.status()['live_requested'])


class CacheLinkTest(WrapperTest):

    def stale_link(self):
        other = self.root / 'old-cache'
        (other / 'cache').mkdir(parents=True)
        physics_dir = self.job / 'physics'
        physics_dir.mkdir(parents=True)
        (physics_dir / 'cache').symlink_to(other / 'cache', target_is_directory=True)
        return physics_dir

    def test_a_link_into_another_cache_root_is_refused(self):
        physics_dir = self.stale_link()

        with self.assertRaisesRegex(item_job_physics.ReplayError, 'another provider cache'):
            item_job_physics.link_cache(physics_dir, self.cache_root)

    def test_a_stale_link_makes_the_whole_run_cache_entry_invalid(self):
        self.stale_link()

        code = self.run_wrapper()

        self.assertEqual(item_jobs.EXIT_CACHE_ENTRY_INVALID, code)
        self.assertEqual('not_submitted', self.status()['provider_submission'])
        self.assertIn('another provider cache', self.status()['reason'])

    def test_the_configured_link_is_reused_and_a_real_directory_is_refused(self):
        physics_dir = self.job / 'physics'

        first = item_job_physics.link_cache(physics_dir, self.cache_root)
        again = item_job_physics.link_cache(physics_dir, self.cache_root)

        self.assertEqual(first, again)
        self.assertTrue(again.is_symlink())
        occupied = self.job / 'occupied'
        (occupied / 'cache').mkdir(parents=True)
        with self.assertRaisesRegex(item_job_physics.ReplayError, 'real directory'):
            item_job_physics.link_cache(occupied, self.cache_root)


class BoundCacheEntryTest(WrapperTest):

    def test_an_absent_declared_cache_entry_is_cache_entry_invalid(self):
        """A declared entry that is gone is invalid state, never a miss."""
        self.metadata()

        code = self.run_wrapper('--replay-dir', str(self.replay))
        status = self.status()

        self.assertEqual(item_jobs.EXIT_CACHE_ENTRY_INVALID, code)
        self.assertNotEqual(item_jobs.EXIT_NOT_SUBMITTED, code)
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertIn('declared cache entry is absent', status['reason'])
        self.assertFalse((self.job / 'definition.json').exists())

    def test_the_unit_refuses_an_absent_entry_and_a_changed_one(self):
        cache = self.root / 'unit-cache'
        cache.mkdir()

        with self.assertRaisesRegex(item_job_physics.ReplayError, 'absent'):
            item_job_physics.verify_cache_entry(cache, 'a' * 64, 'b' * 64)
        (cache / f'{"a" * 64}.json').write_text('{"tampered": true}')
        with self.assertRaisesRegex(item_job_physics.ReplayError, 'cache entry bytes'):
            item_job_physics.verify_cache_entry(cache, 'a' * 64, 'b' * 64)

    def test_an_unbound_miss_is_still_an_ordinary_miss(self):
        """Nothing is declared without metadata, so a miss must stay a miss."""
        code = self.run_wrapper()
        status = self.status()

        self.assertEqual(item_jobs.EXIT_NOT_SUBMITTED, code)
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertEqual(self.digest(), status['request_sha256'])
        self.assertEqual('job_description', status['physics_description_source'])


class TypedOutcomeTest(WrapperTest):

    def test_every_typed_probe_outcome_maps_to_its_exit_code(self):
        expected = {
            probe.CacheMiss: (item_jobs.EXIT_NOT_SUBMITTED, 'not_submitted'),
            probe.CredentialMissing: (item_jobs.EXIT_CREDENTIALS, 'not_submitted'),
            probe.SubmissionUncertain: (item_jobs.EXIT_UNCERTAIN, 'uncertain'),
            probe.CacheEntryInvalid: (item_jobs.EXIT_CACHE_ENTRY_INVALID, 'not_submitted'),
            probe.CachedProviderFailure: (item_jobs.EXIT_FAILED, 'not_submitted'),
        }

        for outcome, mapped in expected.items():
            with self.subTest(outcome=outcome.__name__):
                self.assertEqual(mapped, item_job_physics.probe_outcome(outcome('x'))[:2])
        # An unrecognized fault stays unmapped, so the caller keeps it uncertain.
        self.assertIsNone(item_job_physics.probe_outcome(RuntimeError('x')))
        self.assertIsNone(item_job_physics.probe_outcome(probe.ProbeOutcome('x')))

    def test_a_cached_provider_failure_is_a_known_failure_not_an_uncertain_call(self):
        """The cached entry records a provider failure. No call happened here."""
        digest = self.digest()
        (self.cache_root / 'cache' / f'{digest}.json').write_text(json.dumps({
            'endpoint': probe.OPENROUTER_URL, 'request_sha256': digest,
            'error': {'code': 500}}))

        code = self.run_wrapper()
        status = self.status()

        self.assertEqual(item_jobs.EXIT_FAILED, code)
        self.assertNotEqual(item_jobs.EXIT_UNCERTAIN, code)
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertIn('records a provider failure', status['reason'])


class SubmissionEvidenceTest(WrapperTest):

    PROPOSAL = {'shape': 'box', 'dimensions_m': [.01, .01, .01], 'density_kg_m3': 1000,
                'material_assumption': 'test', 'limitations': 'test',
                'provenance': {'kind': 'llm'}}
    DEFINITION = {'physics': {'proxy': None}, 'object_key': 'token',
                  'visual': {'uri': 'previews/object.glb'}}

    def run_with_fake_provider(self, *extra, mode='cached', side_effect=None,
                               proposal=None):
        """A fake provider result. Nothing is sent, and nothing can be billed."""
        patch = unittest.mock.patch.object
        with patch(item_job_physics, 'propose_physics', side_effect=side_effect,
                   return_value=proposal or self.PROPOSAL), \
                patch(item_job_physics, 'build_object_definition',
                      return_value=self.DEFINITION):
            return self.run_wrapper(*extra, mode=mode)

    def test_a_paid_live_success_records_a_completed_submission(self):
        entry = self.cache_root / 'cache' / f'{self.digest()}.json'

        def answered(**kwargs):
            # The provider answered and the call wrote its cache entry.
            entry.write_text('{}')
            return self.PROPOSAL

        code = self.run_with_fake_provider('--live', mode='paid', side_effect=answered)
        status = self.status()

        self.assertEqual(item_jobs.EXIT_OK, code)
        self.assertTrue(status['live_requested'])
        self.assertEqual('completed', status['provider_submission'])
        self.assertFalse(status['cache_hit'])

    def test_an_exact_cache_hit_never_claims_a_submission(self):
        (self.cache_root / 'cache' / f'{self.digest()}.json').write_text('{}')

        code = self.run_with_fake_provider()
        status = self.status()

        self.assertEqual(item_jobs.EXIT_OK, code)
        self.assertFalse(status['live_requested'])
        self.assertEqual('not_submitted', status['provider_submission'])
        self.assertTrue(status['cache_hit'])


if __name__ == '__main__':
    unittest.main()
