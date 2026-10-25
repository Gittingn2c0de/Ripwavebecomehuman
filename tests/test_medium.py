import time
import unittest
import math

from ripwave_human.medium import CORE, MID, SHELL, MAX_MESSAGES, DistributedMedium


class DistributedMediumTests(unittest.TestCase):
    def make_medium(self, **kwargs):
        return DistributedMedium({"width": 64, "height": 64, "seed": 17, **kwargs})

    def test_field_ripple_and_roles_are_spatial_and_task_relative(self):
        medium = self.make_medium()
        result = medium.inject_stressor(event_id="event-a", origin_cell=[32, 32], intensity=1.0, duration=100)
        medium.advance(1)
        self.assertTrue(result["accepted"])
        self.assertEqual(medium.role[32 * 64 + 32], CORE)
        self.assertEqual(medium.role[32 * 64 + 47], MID)
        self.assertEqual(medium.role[32 * 64 + 61], SHELL)
        self.assertGreater(medium.ripple_intensity[32 * 64 + 32], 0.0)
        self.assertEqual(medium.ripple_task[32 * 64 + 32], result["task_id"])
        self.assertEqual(medium.snapshot()["diagnostics"]["active_stressor_count"], 1)

    def test_shell_memory_reinforces_and_decays_with_task_identity(self):
        medium = self.make_medium()
        task = medium.inject_stressor(event_id="memory-a", origin_cell=[32, 32], intensity=1.0, duration=100)
        medium.advance(58)
        shell = 32 * 64 + 61
        trace = medium._traces.get((shell, task["task_id"]))
        self.assertIsNotNone(trace, "reduced threshold must allow the ripple to reach a shell cell")
        trace[1] = 0.0
        trace[2] = medium.tick - 1000
        trace[3] = medium.tick
        medium.tasks.clear()
        medium.ripples.clear()
        before = trace[0]
        medium.advance(1)
        after = medium._traces[(shell, task["task_id"])][0]
        self.assertLess(after, before)
        self.assertGreater(after, 0.0)

    def test_overlapping_stressors_keep_distinct_ids_and_traces(self):
        medium = self.make_medium()
        first = medium.inject_stressor(event_id="stress-1", origin_cell=[20, 32], intensity=0.8)
        second = medium.inject_stressor(event_id="stress-2", origin_cell=[44, 32], intensity=0.7)
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertEqual([t["event_id"] for t in medium.tasks], ["stress-1", "stress-2"])
        medium.advance(2)
        self.assertNotEqual(medium.ripple_task[32 * 64 + 20], medium.ripple_task[32 * 64 + 44])

    def test_duplicate_event_id_is_idempotent(self):
        medium = self.make_medium()
        first = medium.inject_stressor(event_id="duplicate", origin_cell=[20, 20])
        again = medium.inject_stressor(event_id="duplicate", origin_cell=[40, 40])
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["task_id"], again["task_id"])
        self.assertEqual(len(medium.tasks), 1)

    def test_optional_neighbor_delivery_is_local_and_bounded(self):
        medium = self.make_medium(messaging_enabled=True)
        source, destination = 10 * 64 + 10, 10 * 64 + 11
        queued = medium.enqueue_message(source, destination, 3, 0.7, ttl=5)
        self.assertTrue(queued["accepted"])
        medium.advance(1)
        self.assertEqual(medium._message_counts["delivered"], 1)
        self.assertGreater(medium.signal[destination], 0.0)
        with self.assertRaisesRegex(ValueError, "cardinal neighbor"):
            medium.enqueue_message(source, source + 2, 3, 0.7)
        self.assertEqual(medium._message_counts["invalid"], 1)

    def test_per_cell_delivery_budget_defers_excess_until_a_later_tick(self):
        medium = self.make_medium(messaging_enabled=True)
        source, destination = 10 * 64 + 10, 10 * 64 + 11
        for _ in range(3):
            self.assertTrue(medium.enqueue_message(source, destination, 8, 0.3, ttl=5)["accepted"])
        medium.advance(1)
        self.assertEqual(medium._message_counts["delivered"], 2)
        self.assertEqual(len(medium.messages), 1)
        medium.advance(1)
        self.assertEqual(medium._message_counts["delivered"], 3)
        self.assertFalse(medium.messages)

    def test_expired_duplicate_disabled_and_budget_overflow_messages(self):
        medium = self.make_medium(messaging_enabled=True)
        source, destination = 10 * 64 + 10, 10 * 64 + 11
        mid = medium._next_message_id
        medium.messages.append((mid, source, destination, 2, 0.5, -10, -1))
        medium._next_message_id += 1
        medium.advance(1)
        self.assertEqual(medium._message_counts["expired"], 1)
        duplicate_id = medium._next_message_id
        message = (duplicate_id, source, destination, 2, 0.5, medium.tick, medium.tick + 4)
        medium._seen_message_ids.add(duplicate_id)
        medium.messages.append(message)
        medium.advance(1)
        self.assertEqual(medium._message_counts["duplicate"], 1)
        medium.messages.clear()
        for _ in range(MAX_MESSAGES):
            medium.messages.append((medium._next_message_id, source, destination, 2, 0.2,
                                    medium.tick, medium.tick + 1))
            medium._next_message_id += 1
        refused = medium.enqueue_message(source, destination, 2, 0.2)
        self.assertFalse(refused["accepted"])
        self.assertEqual(len(medium.messages), MAX_MESSAGES)
        medium.set_messaging(False)
        self.assertEqual(len(medium.messages), 0)
        self.assertFalse(medium.enqueue_message(source, destination, 2, 0.2)["accepted"])

    def test_field_only_mode_runs_without_direct_messages(self):
        medium = self.make_medium(messaging_enabled=False)
        medium.inject_stressor(event_id="field-only", origin_cell=[32, 32], intensity=1.0)
        medium.advance(8)
        self.assertGreater(max(medium.ripple_intensity), 0.0)
        self.assertEqual(medium._message_counts["sent"], 0)
        self.assertGreater(medium._updates, 0)

    def test_measured_humanoid_ingress_and_unconsumed_egress_are_explicit(self):
        medium = self.make_medium()
        rejected = medium.inject_humanoid_motion("tiny", [0, 0], 0.00001)
        self.assertFalse(rejected["accepted"])
        accepted = medium.inject_humanoid_motion("measured", [0.15, -0.1], 0.08)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(medium._humanoid_ingress, 1)
        egress = medium.outbound_to_humanoid()
        self.assertFalse(egress["consumed"])
        self.assertIsNone(egress["consumer"])

    def test_blueprint_bounds_and_determinism(self):
        blueprint = {"width": 24, "height": 24, "seed": 77,
                     "stressors": [{"event_id": "seeded", "type": "load",
                                    "origin_cell": [8, 8], "intensity": 0.8}]}
        left, right = DistributedMedium(blueprint), DistributedMedium(blueprint)
        left.advance(10); right.advance(10)
        self.assertEqual(list(left.role), list(right.role))
        self.assertEqual(list(left.ripple_task), list(right.ripple_task))
        self.assertEqual(list(left.energy), list(right.energy))
        with self.assertRaisesRegex(ValueError, "width must"):
            self.make_medium(width=257, height=256)

    def test_128_square_performance_smoke(self):
        medium = DistributedMedium({"width": 128, "height": 128, "seed": 9})
        medium.inject_stressor(event_id="perf-a", origin_cell=[40, 64], intensity=0.9, duration=400)
        medium.inject_stressor(event_id="perf-b", origin_cell=[88, 64], intensity=0.8, duration=400)
        started = time.perf_counter()
        diagnostics = medium.advance(20)
        wall_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(diagnostics["cell_count"], 16_384)
        self.assertEqual(diagnostics["update"]["ticks"], 20)
        self.assertLess(wall_ms, 10_000)
        self.assertLessEqual(len(medium.messages), MAX_MESSAGES)
        for values in (medium.energy, medium.memory, medium.task_load, medium.signal, medium.ripple_intensity):
            self.assertTrue(all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values))
        self.perf_wall_ms = wall_ms


if __name__ == "__main__":
    unittest.main()
