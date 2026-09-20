import unittest
from unittest.mock import patch

import mujoco

from profiles import BOX, CAPSULE, ELLIPSOID, HALF, GREEN_ARABICA
from scene import Layout
from sim import SorterSim


class CollectionLifecycleTests(unittest.TestCase):
    def make_sim(self):
        return SorterSim(
            GREEN_ARABICA,
            layout=Layout(n_ellipsoid=1, n_half=0, n_box=0, n_capsule=0),
            rate=0,
            seed=7,
        )

    def setUp(self):
        self.sim = self.make_sim()
        self.sim.continuous = True

    def position(self, bean, *, x, z, y=0):
        qa = self.sim.body_qpos[bean.body]
        va = self.sim.body_qvel[bean.body]
        self.sim.data.qpos[qa:qa + 3] = [x, y, z]
        self.sim.data.qvel[va:va + 6] = 0
        mujoco.mj_forward(self.sim.model, self.sim.data)

    def bin_position(self, name):
        geom = mujoco.mj_name2id(self.sim.model, mujoco.mjtObj.mjOBJ_GEOM, name)
        return self.sim.model.geom_pos[geom, 0], self.sim.model.geom_pos[geom, 2] + .004

    def relevant_contacts(self, bean):
        targets = {*self.sim.collection_geom, self.sim.ground_geom}
        collision = self.sim.body_col[bean.body]
        return sum(
            collision in (contact.geom1, contact.geom2)
            and any(target in (contact.geom1, contact.geom2) for target in targets)
            for contact in self.sim.data.contact[:self.sim.data.ncon]
        )

    def test_splitter_and_old_retirement_bound_do_not_resolve_a_flying_body(self):
        for x, z in ((.34, .50), (.51, .55)):
            with self.subTest(x=x, z=z):
                self.sim = self.make_sim()
                bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
                self.position(bean, x=x, z=z)
                self.sim.step()
                self.assertEqual(self.sim.n_active(), 1)
                self.assertIsNone(bean.outcome)

    def test_airborne_lateral_motion_after_belt_stays_unresolved(self):
        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        self.position(bean, x=.10, y=.281, z=.50)
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 1)
        self.assertIsNone(bean.outcome)

    def test_positive_named_bin_contact_resolves_once_and_retires_once(self):
        for name, outcome in (("bin_accept", "accept"), ("bin_reject", "reject")):
            with self.subTest(name=name):
                bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
                x, z = self.bin_position(name)
                self.position(bean, x=x, z=z)
                t = self.sim.data.time
                pose = self.sim.data.qpos[self.sim.body_qpos[bean.body]:self.sim.body_qpos[bean.body] + 7].copy()
                self.sim.step()
                self.assertGreater(self.relevant_contacts(bean), 0)
                self.assertEqual(bean.outcome, outcome)
                self.assertEqual(bean.resolved_t, t)
                self.assertEqual(bean.last_pos, tuple(round(value, 3) for value in pose[:3]))
                self.assertEqual(bean.last_quat, tuple(pose[3:]))
                self.assertEqual(self.sim.n_active(), 0)
                _, outcomes, retired, _, _ = self.sim.drain_continuous_events()
                self.assertEqual([item.uid for item in outcomes], [bean.uid])
                self.assertEqual([item.uid for item in retired], [bean.uid])

    def test_zero_force_bin_contact_stays_unresolved(self):
        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        x, z = self.bin_position("bin_accept")
        self.position(bean, x=x, z=z)
        with patch("sim.mujoco.mj_contactForce", side_effect=lambda *args: args[-1].fill(0)):
            self.sim.step()
        self.assertGreater(self.relevant_contacts(bean), 0)
        self.assertEqual(self.sim.n_active(), 1)
        self.assertIsNone(bean.outcome)

    def test_ground_and_downstream_escape_spill(self):
        for x, z in ((0, .001), (self.sim.collection_end_x + .02, .30)):
            with self.subTest(x=x, z=z):
                self.sim = self.make_sim()
                bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
                self.position(bean, x=x, z=z)
                self.sim.step()
                self.assertEqual(bean.outcome, "spilled")
                self.assertEqual(self.sim.n_active(), 0)

    def test_idle_steps_are_safe_before_spawn_and_after_retirement(self):
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 0)

        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        x, z = self.bin_position("bin_accept")
        self.position(bean, x=x, z=z)
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 0)

        self.sim.step()
        self.assertEqual(self.sim.n_active(), 0)

    def test_body_straddling_the_accept_wall_stays_active(self):
        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        radius = self.sim.model.geom_rbound[self.sim.body_col[bean.body]]
        self.position(bean, x=self.sim.collection_end_x + radius / 2, z=.50)
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 1)
        self.assertIsNone(bean.outcome)

    def test_multiple_bin_contact_points_emit_one_outcome_and_retirement(self):
        layout = Layout(n_ellipsoid=0, n_half=0, n_box=1, n_capsule=0)
        sim = SorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=7)
        sim.continuous = True
        bean = sim.spawn(GREEN_ARABICA.by_name("stone"))
        geom = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_GEOM, "bin_accept")
        qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
        sim.data.qpos[qa:qa + 3] = [sim.model.geom_pos[geom, 0], 0, sim.model.geom_pos[geom, 2] + .002]
        sim.data.qpos[qa + 3:qa + 7] = [1, 0, 0, 0]
        sim.data.qvel[va:va + 6] = 0
        mujoco.mj_forward(sim.model, sim.data)
        sim.step()
        contacts = sum(
            sim.body_col[bean.body] in (contact.geom1, contact.geom2)
            and geom in (contact.geom1, contact.geom2)
            for contact in sim.data.contact[:sim.data.ncon]
        )
        self.assertGreater(contacts, 1)
        _, outcomes, retired, _, _ = sim.drain_continuous_events()
        self.assertEqual([item.uid for item in outcomes], [bean.uid])
        self.assertEqual([item.uid for item in retired], [bean.uid])

    def test_slot_reuse_clears_collection_state_for_every_shape(self):
        specs = ((ELLIPSOID, "good"), (HALF, "broken"), (BOX, "stone"), (CAPSULE, "stick"))
        for shape, name in specs:
            with self.subTest(shape=shape):
                layout = Layout(n_ellipsoid=shape == ELLIPSOID, n_half=shape == HALF,
                                n_box=shape == BOX, n_capsule=shape == CAPSULE)
                sim = SorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=7)
                bean = sim.spawn(GREEN_ARABICA.by_name(name))
                qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
                sim.data.qpos[qa:qa + 3] = [0, 0, -.005]
                sim.data.qvel[va:va + 6] = 0
                mujoco.mj_forward(sim.model, sim.data)
                sim.step()
                self.assertEqual(bean.outcome, "spilled")
                replacement = sim.spawn(GREEN_ARABICA.by_name(name))
                self.assertEqual(replacement.body, bean.body)
                self.assertIsNone(replacement.outcome)
                self.assertIsNone(replacement.resolved_t)
                self.assertIsNone(replacement.last_quat)


if __name__ == "__main__":
    unittest.main()
