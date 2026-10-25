import unittest
from pathlib import Path

from ripwave_human.mujoco_adapter import MuJoCoAdapter


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "MS-Human-700-main" / "MS-Human-700.xml"


class MuJoCoAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = MuJoCoAdapter(MODEL, ROOT)

    def test_full_body_loads_and_has_finite_state(self):
        status = self.adapter.status()
        self.assertTrue(status["enabled"], status.get("reason"))
        self.assertEqual(status["model_name"], "MS-Human-700.xml")
        self.assertTrue(status["state_finite"])
        self.assertGreater(status["nu"], 0)
        self.assertGreater(status["timestep"], 0)
        self.assertEqual(self.adapter.data.qpos[33], self.adapter.model.key_qpos[0][33])

    def test_step_preserves_finite_state(self):
        status = self.adapter.step(2)
        self.assertTrue(status["state_finite"])

    def test_right_arm_baseline_is_bounded(self):
        self.adapter._reset_state()
        target = self.adapter._mujoco.mj_name2id(
            self.adapter.model, self.adapter._mujoco.mjtObj.mjOBJ_JOINT, "shoulder_elv_r")
        target_qpos = int(self.adapter.model.jnt_qposadr[target])
        before = float(self.adapter.data.qpos[target_qpos])
        result = self.adapter.execute_intent("raise_right_arm")
        self.assertTrue(result["executed"])
        self.assertGreater(result["control_count"], 0)
        self.assertEqual(result["target_joint"], "shoulder_elv_r")
        self.assertGreater(result["target_joint_delta"], 0.01)
        self.assertTrue(self.adapter.status()["state_finite"])
        self.assertNotEqual(before, float(self.adapter.data.qpos[target_qpos]))
        self.assertGreater(result["hand_relative_displacement_m"], 0.01)
        repeated = self.adapter.execute_intent("raise_right_arm")
        self.assertAlmostEqual(repeated["movement_delta"], result["movement_delta"], places=9)
        self.assertAlmostEqual(repeated["hand_relative_displacement_m"], result["hand_relative_displacement_m"], places=9)

    def test_reset_and_neutral_restore_authored_keyframe(self):
        expected = self.adapter.model.key_qpos[0].copy()
        self.adapter.execute_intent("raise_right_arm")
        reset = self.adapter.execute_intent("reset")
        self.assertEqual(reset["execution_status"], "completed")
        self.assertTrue((self.adapter.data.qpos == expected).all())
        self.adapter.execute_intent("raise_right_arm")
        neutral = self.adapter.execute_intent("neutral")
        self.assertEqual(neutral["execution_status"], "completed")
        self.assertTrue((self.adapter.data.qpos == expected).all())

    def test_stop_zeros_all_controls(self):
        self.adapter.execute_intent("raise_right_arm")
        result = self.adapter.execute_intent("stop")
        self.assertEqual(result["execution_status"], "stopped")
        self.assertTrue((self.adapter.data.ctrl == 0).all())

    def test_unimplemented_arm_moves_are_explicitly_unsupported(self):
        for intent in ("lower_right_arm", "reach_forward"):
            result = self.adapter.execute_intent(intent)
            self.assertEqual(result["execution_status"], "unsupported")
            self.assertFalse(result["movement_observed"])

    def test_missing_model_is_explicit(self):
        missing = MuJoCoAdapter(ROOT / "missing.xml", ROOT)
        self.assertFalse(missing.status()["enabled"])
        result = missing.execute_intent("raise_right_arm")
        self.assertFalse(result["executed"])
        self.assertEqual(result["execution_status"], "unavailable")

    def test_walking_is_recorded_as_unsupported_without_motion(self):
        result = self.adapter.execute_intent("walking")
        self.assertFalse(result["executed"])
        self.assertFalse(result["movement_observed"])
        self.assertEqual(result["execution_status"], "unsupported")

    def test_runtime_scene_keeps_model_dimensions_and_adds_world_geometry(self):
        self.adapter._reset_state()
        self.adapter._apply_field_geometry()
        status = self.adapter.status()
        self.assertEqual((status["nq"], status["nv"], status["nu"]), (85, 85, 700))
        self.assertEqual(status["world"]["up_axis"], "+Z")
        self.assertEqual(status["world"]["ground"]["height_m"], 0.0)
        self.assertEqual(status["world"]["ground"]["visual_grid"], {
            "extent_m": 2.5, "spacing_m": 0.25, "collision_enabled": False})
        self.assertEqual(len(self.adapter._grid_geom_ids), 42)
        self.assertTrue(all(int(self.adapter.model.geom_contype[i]) == 0
                            and int(self.adapter.model.geom_conaffinity[i]) == 0
                            for i in self.adapter._grid_geom_ids))
        self.assertAlmostEqual(status["world"]["body_height_m"], 1.767018, places=4)
        self.assertAlmostEqual(status["world"]["foot_ground_clearance_m"], 0.003399, places=4)
        self.assertAlmostEqual(status["world"]["pelvis_position_m"][2], 0.95, places=5)
        self.assertAlmostEqual(status["world"]["sternum_position_m"][2], 1.4176, places=4)
        self.assertAlmostEqual(status["world"]["torso_bounds"]["size_m"][2], 0.721845, places=5)
        self.assertAlmostEqual(status["world"]["pelvis_bounds"]["size_m"][2], 0.281098, places=5)
        target_id = self.adapter._target_geom_id
        self.assertEqual(int(self.adapter.model.geom_type[target_id]), int(self.adapter._mujoco.mjtGeom.mjGEOM_SPHERE))
        self.assertEqual(int(self.adapter.model.geom_contype[target_id]), 1)
        self.assertEqual(int(self.adapter.model.geom_conaffinity[target_id]), 1)
        self.assertEqual(self.adapter.data.geom_xpos[target_id].tolist(), [0.65, 0.0, 0.05])
        # The world-anchored mocap target is a static body and is not expected
        # to generate static-static floor contacts. Verify its actual collision
        # geometry against a dynamic human body instead.
        pelvis_id = self.adapter._mujoco.mj_name2id(
            self.adapter.model, self.adapter._mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        target_body_id = int(self.adapter.model.geom_bodyid[target_id])
        mocap_id = int(self.adapter.model.body_mocapid[target_body_id])
        original_target_pos = self.adapter.data.mocap_pos[mocap_id].copy()
        self.adapter.data.mocap_pos[mocap_id] = self.adapter.data.xpos[pelvis_id].copy()
        self.adapter._mujoco.mj_forward(self.adapter.model, self.adapter.data)
        self.assertTrue(any(target_id in (int(self.adapter.data.contact[i].geom1),
                                          int(self.adapter.data.contact[i].geom2))
                            for i in range(self.adapter.data.ncon)))
        self.adapter.data.mocap_pos[mocap_id] = original_target_pos
        self.adapter._mujoco.mj_forward(self.adapter.model, self.adapter.data)

    def test_world_ripple_scales_in_meters_and_tracks_footprint(self):
        self.adapter.set_field({"radius": 0.42, "amplitude": 0.5, "active": True})
        world = self.adapter.status()["world"]
        self.assertAlmostEqual(world["field"]["radius_m"], 0.42)
        self.assertAlmostEqual(world["field"]["radius_to_body_height"], 0.42 / world["reference_body_height_m"])
        center = world["field"]["center_m"]
        bounds = world["footprint_bounds"]
        self.assertAlmostEqual(world["field"]["radius_to_footprint_length"],
                               0.42 / bounds["size_m"][0])
        self.assertAlmostEqual(world["field"]["radius_to_footprint_width"],
                               0.42 / bounds["size_m"][1])
        self.assertAlmostEqual(center[0], (bounds["min_m"][0] + bounds["max_m"][0]) / 2)
        self.assertAlmostEqual(center[1], (bounds["min_m"][1] + bounds["max_m"][1]) / 2)
        last_ring = self.adapter._field_geom_ids[-1]
        self.assertAlmostEqual(float(self.adapter.model.geom_size[last_ring, 0]), 0.42 * 1.66)
        self.assertEqual(int(self.adapter.model.geom_contype[last_ring]), 0)

    def test_passive_keyframe_contacts_then_loses_upright_stability(self):
        adapter = MuJoCoAdapter(MODEL, ROOT)
        initial = adapter.status()["world"]
        self.assertEqual(initial["contacts"]["foot_floor_count"], 0)
        self.assertEqual(initial["contacts"]["total"], 0)
        self.assertAlmostEqual(initial["pelvis_position_m"][2], 0.95, places=5)
        self.assertLess(initial["pelvis_to_sternum_upright_error_deg"], 2.0)
        adapter.step(100)
        early = adapter.status()["world"]
        self.assertGreater(early["contacts"]["foot_floor_count"], 0)
        adapter.step(400)
        settled = adapter.status()["world"]
        self.assertGreater(settled["pelvis_to_sternum_upright_error_deg"], 45.0)


if __name__ == "__main__":
    unittest.main()
