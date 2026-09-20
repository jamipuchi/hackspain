import unittest

import mujoco

from profiles import ELLIPSOID, GREEN_ARABICA
from scene import Layout
from sim import RESOLVED_RETIRE_GRACE, SorterSim


class ResolvedBodyRetirementTests(unittest.TestCase):
    def setUp(self):
        layout = Layout(n_ellipsoid=1, n_half=0, n_box=0, n_capsule=0)
        self.sim = SorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=7)
        self.sim.continuous = True

    def position(self, bean, *, x, z):
        qa = self.sim.body_qpos[bean.body]
        va = self.sim.body_qvel[bean.body]
        self.sim.data.qpos[qa:qa + 3] = [x, 0, z]
        self.sim.data.qvel[va:va + 6] = 0
        mujoco.mj_forward(self.sim.model, self.sim.data)

    def test_resolved_body_settled_in_reject_bin_releases_slot_after_grace(self):
        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        self.position(bean, x=0.40, z=0.19)

        self.sim.data.time = 1.0
        self.sim.step()
        self.assertEqual(bean.outcome, "reject")
        self.assertEqual(bean.resolved_t, 1.0)
        self.assertEqual(
            self.sim.retire_at[self.sim.body_index[bean.body]],
            1.0 + RESOLVED_RETIRE_GRACE,
        )
        self.assertEqual(self.sim.n_active(), 1)

        self.position(bean, x=0.20, z=0.19)
        self.sim.data.time = 1.0 + RESOLVED_RETIRE_GRACE - self.sim.dt
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 1)

        self.position(bean, x=0.20, z=0.19)
        self.sim.data.time = 1.0 + RESOLVED_RETIRE_GRACE
        self.sim.step()
        self.assertEqual(self.sim.n_active(), 0)
        self.assertIn(bean.body, self.sim.free[ELLIPSOID])
        self.assertEqual(
            self.sim.retire_at[self.sim.body_index[bean.body]], float("inf")
        )

        _, outcomes, retired, _, _ = self.sim.drain_continuous_events()
        self.assertEqual([outcome_bean.uid for outcome_bean in outcomes], [bean.uid])
        self.assertEqual([retired_bean.uid for retired_bean in retired], [bean.uid])
        self.assertEqual(retired[0].outcome, "reject")

        replacement = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        self.assertEqual(replacement.body, bean.body)
        self.assertIsNone(replacement.outcome)
        self.assertIsNone(replacement.resolved_t)
        self.assertEqual(
            self.sim.retire_at[self.sim.body_index[replacement.body]], float("inf")
        )

    def test_old_unresolved_body_is_not_retired(self):
        bean = self.sim.spawn(GREEN_ARABICA.by_name("good"))
        self.position(bean, x=0.0, z=self.sim.L.belt_z + bean.axes[2])
        self.sim.data.time = RESOLVED_RETIRE_GRACE * 10

        self.sim.step()

        self.assertEqual(self.sim.n_active(), 1)
        self.assertIsNone(bean.outcome)
        self.assertIsNone(bean.resolved_t)


if __name__ == "__main__":
    unittest.main()
