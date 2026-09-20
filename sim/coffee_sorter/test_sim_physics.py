"""Regressions for runtime physical properties of recycled MuJoCo bodies."""
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from profiles import GREEN_ARABICA
from scene import Layout
from scene import build_xml
from sim import SorterSim


class PooledPhysicsTests(unittest.TestCase):
    def make_sim(self, seed=7):
        return SorterSim(GREEN_ARABICA, Layout(
            n_ellipsoid=2, n_half=1, n_box=1, n_capsule=1, timestep=.001),
            rate=0, seed=seed)

    def airborne(self, sim, bean):
        qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
        sim.data.qpos[qa:qa + 3] = [0, 0, 3]
        sim.data.qpos[qa + 3:qa + 7] = [1, 0, 0, 0]
        sim.data.qvel[va:va + 6] = 0
        mujoco.mj_forward(sim.model, sim.data)
        return va

    def test_gravity_and_applied_force_follow_sampled_mass_after_reuse(self):
        sim = self.make_sim()
        for name in ('husk', 'good', 'stone', 'stick', 'broken', 'stone', 'husk'):
            with self.subTest(name=name):
                bean = sim.spawn(GREEN_ARABICA.by_name(name))
                va = self.airborne(sim, bean)
                np.testing.assert_allclose(sim.data.qacc[va:va + 3], [0, 0, -9.81], atol=1e-8)
                sim.data.xfrc_applied[bean.body, 2] = bean.mass * 2
                mujoco.mj_forward(sim.model, sim.data)
                np.testing.assert_allclose(sim.data.qacc[va:va + 3], [0, 0, -7.81], atol=1e-8)
                sim._park(bean.body)
                mujoco.mj_forward(sim.model, sim.data)
                np.testing.assert_allclose(sim.data.qacc[va:va + 3], 0, atol=1e-8)

    def test_refresh_matches_full_mujoco_constants_for_every_shape_and_reuse(self):
        sim = self.make_sim()
        fields = ('dof_M0', 'dof_invweight0', 'body_invweight0',
                  'body_subtreemass', 'dof_length')
        for name in ('good', 'stone', 'stick', 'broken', 'husk', 'stone', 'broken'):
            with self.subTest(name=name):
                bean = sim.spawn(GREEN_ARABICA.by_name(name))
                actual = {field: getattr(sim.model, field).copy() for field in fields}
                stats = {field: getattr(sim.model.stat, field) for field in
                         ('meanmass', 'meaninertia', 'meansize', 'extent')}
                centre = sim.model.stat.center.copy()
                mujoco.mj_setConst(sim.model, mujoco.MjData(sim.model))
                for field in fields:
                    np.testing.assert_allclose(actual[field], getattr(sim.model, field), rtol=1e-10, atol=1e-12,
                                               err_msg=field)
                for field, value in stats.items():
                    self.assertAlmostEqual(value, getattr(sim.model.stat, field), delta=1e-10, msg=field)
                np.testing.assert_allclose(centre, sim.model.stat.center, atol=1e-12)
                sim._park(bean.body)

    def test_changed_pool_topology_fails_before_spawning(self):
        layout = Layout(n_ellipsoid=0, n_half=0, n_box=1, n_capsule=0)
        xml = build_xml(GREEN_ARABICA, layout)
        changes = (
            xml.replace('<freejoint/>', '<joint type="slide"/>'),
            xml.replace('<freejoint/>', '<freejoint/><geom type="sphere" size=".001"/>'),
            xml.replace('<freejoint/>', '<freejoint/><body><geom type="sphere" size=".001"/></body>'),
        )
        for changed in changes:
            with self.subTest(xml=changed[-200:]), patch('sim.build_xml', return_value=changed):
                with self.assertRaisesRegex(ValueError, 'Pooled physics requires'):
                    SorterSim(GREEN_ARABICA, layout, rate=0)

    def test_spawn_preserves_other_live_state(self):
        sim = self.make_sim()
        first = sim.spawn(GREEN_ARABICA.by_name('good'))
        for _ in range(10):
            sim.step()
        qa, va = sim.body_qpos[first.body], sim.body_qvel[first.body]
        position = sim.data.qpos[qa:qa + 7].copy()
        velocity = sim.data.qvel[va:va + 6].copy()
        sim.data.xfrc_applied[first.body] = np.arange(6) * .001
        force = sim.data.xfrc_applied[first.body].copy()
        warmstart = sim.data.qacc_warmstart.copy()
        now = sim.data.time
        pulse = sim.fire(1, now + .1, .005, uid=123)
        sim.spawn(GREEN_ARABICA.by_name('stone'))
        np.testing.assert_array_equal(sim.data.qpos[qa:qa + 7], position)
        np.testing.assert_array_equal(sim.data.qvel[va:va + 6], velocity)
        np.testing.assert_array_equal(sim.data.xfrc_applied[first.body], force)
        np.testing.assert_array_equal(sim.data.qacc_warmstart, warmstart)
        self.assertEqual(sim.data.time, now)
        self.assertIs(sim.fires[0], pulse)
        self.assertEqual(sim.n_fired, 1)

    def test_ellipsoid_collision_bounds_use_capsule_local_axes(self):
        sim = self.make_sim()
        bean = sim.spawn(GREEN_ARABICA.by_name('husk'))
        collision = sim.body_col[bean.body]
        radius, half_length = sim.model.geom_size[collision, :2]
        np.testing.assert_allclose(sim.model.geom_aabb[collision, 3:],
                                   [radius, radius, half_length + radius])

    def test_ellipsoid_inertia_uses_body_shape_axes(self):
        sim = self.make_sim()
        bean = sim.spawn(GREEN_ARABICA.by_name('good'))
        x, y, z = bean.axes
        expected = bean.mass / 5 * np.array([y*y + z*z, x*x + z*z, x*x + y*y])
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, sim.model.body_iquat[bean.body])
        rotation = rotation.reshape(3, 3)
        tensor = rotation @ np.diag(sim.model.body_inertia[bean.body]) @ rotation.T
        np.testing.assert_allclose(tensor, np.diag(expected), atol=1e-20)

    def test_collision_leaf_matches_freshly_compiled_geometry(self):
        sim = self.make_sim()
        for name in ('good', 'stone', 'stick', 'husk', 'stone'):
            bean = sim.spawn(GREEN_ARABICA.by_name(name))
            m, b = sim.model, bean.body
            g = sim.body_col[b]
            values = lambda array: ' '.join(str(value) for value in array)
            kind = 'box' if name == 'stone' else 'capsule'
            size = m.geom_size[g] if kind == 'box' else m.geom_size[g, :2]
            xml = f'''<mujoco><worldbody><body><freejoint/>
              <inertial pos="{values(m.body_ipos[b])}" quat="{values(m.body_iquat[b])}"
                mass="{m.body_mass[b]}" diaginertia="{values(m.body_inertia[b])}"/>
              <geom type="{kind}" size="{values(size)}" pos="{values(m.geom_pos[g])}"
                quat="{values(m.geom_quat[g])}"/>
              </body></worldbody></mujoco>'''
            reference = mujoco.MjModel.from_xml_string(xml)
            with self.subTest(name=name):
                np.testing.assert_allclose(m.geom_aabb[g], reference.geom_aabb[0], atol=1e-12)
                np.testing.assert_allclose(m.bvh_aabb[m.body_bvhadr[b]],
                                           reference.bvh_aabb[0], atol=1e-12)
                self.assertGreaterEqual(m.geom_rbound[g], reference.geom_rbound[0] - 1e-12)
            sim._park(b)

    def test_stone_no_air_route_accepts_across_seeds_and_same_body_reuse(self):
        for seed in (7, 8, 9, 42):
            sim = self.make_sim(seed)
            previous_body = None
            for reuse in range(3):
                with self.subTest(seed=seed, reuse=reuse):
                    bean = sim.spawn(GREEN_ARABICA.by_name('stone'))
                    if previous_body is not None:
                        self.assertEqual(bean.body, previous_body)
                    previous_body = bean.body
                    start = sim.data.time
                    while bean.body in sim.bean_of and sim.data.time - start < 1.5:
                        sim.step()
                    self.assertEqual(bean.outcome, 'accept')
                    self.assertEqual(bean.jet_hits, 0)
                    self.assertEqual(sim.n_fired, 0)
                    self.assertNotIn(bean.body, sim.bean_of)

    def test_failed_spawn_refreshes_parked_body_before_retry(self):
        sim = self.make_sim()
        with patch.object(sim, '_free_spot', return_value=None):
            self.assertIsNone(sim.spawn(GREEN_ARABICA.by_name('stone')))
        body = sim.pools['box'][0]
        self.assertEqual(sim.model.body_gravcomp[body], 1)
        mujoco.mj_forward(sim.model, sim.data)
        va = sim.body_qvel[body]
        np.testing.assert_allclose(sim.data.qacc[va:va + 3], 0, atol=1e-8)
        bean = sim.spawn(GREEN_ARABICA.by_name('stone'))
        self.airborne(sim, bean)
        self.assertAlmostEqual(sim.data.qacc[va + 2], -9.81)


if __name__ == '__main__':
    unittest.main()
