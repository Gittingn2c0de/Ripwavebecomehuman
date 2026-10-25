import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app = app_module
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.app.medium.configure({"width": 48, "height": 48, "seed": 5})

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                          headers={"Content-Type": "application/json"} if data else {})
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            return response.code, json.loads(response.read())

    def get_bytes(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as response:
            return response.status, response.read()

    def test_health_field_blueprint_and_stressor_endpoints(self):
        status, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertIn("medium", health)
        status, blueprint = self.request("/api/field/blueprint", {
            "width": 32, "height": 24, "cell_size_m": 0.08, "seed": 19,
            "stressors": [{"event_id": "blueprint-load", "type": "disturbance",
                           "origin_cell": [8, 12], "intensity": 0.8, "duration": 30}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(blueprint["medium"]["diagnostics"]["cell_count"], 768)
        self.assertEqual(blueprint["medium"]["tasks"][0]["event_id"], "blueprint-load")
        status, injected = self.request("/api/stressor", {
            "event_id": "api-stressor", "type": "attention", "origin_cell": [18, 12],
            "intensity": 0.5, "duration": 20,
        })
        self.assertEqual(status, 200)
        self.assertEqual(injected["result"]["event_id"], "api-stressor")
        status, invalid = self.request("/api/stressor", {"origin_cell": [1000, 1000]})
        self.assertEqual(status, 400)
        self.assertFalse(invalid["ok"])

    def test_neighbor_toggle_field_step_and_state_compatibility(self):
        status, result = self.request("/api/field/config", {"messaging_enabled": True})
        self.assertEqual(status, 200)
        self.assertTrue(result["diagnostics"]["messages"]["enabled"])
        status, injected = self.request("/api/stressor", {"event_id": "step", "origin_cell": [24, 24]})
        self.assertEqual(status, 200)
        status, stepped = self.request("/api/field/step", {"steps": 4})
        self.assertEqual(status, 200)
        self.assertEqual(stepped["diagnostics"]["update"]["ticks"], 4)
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("field", state)
        self.assertIn("medium", state)
        self.assertIn("mujoco", state)
        status, stopped = self.request("/api/event", {"transcript": "stop", "source": "text"})
        self.assertEqual(status, 200)
        self.assertEqual(stopped["result"]["execution_status"], "stopped")
        status, sim_step = self.request("/api/sim/step", {"steps": 2})
        self.assertEqual(status, 200)
        self.assertIn("mujoco", sim_step)

    def test_measured_humanoid_motion_is_ingested_but_field_egress_is_unconsumed(self):
        self.request("/api/event", {"transcript": "return to neutral", "source": "text"})
        status, raised = self.request("/api/event", {"transcript": "raise your right arm", "source": "text"})
        self.assertEqual(status, 200)
        simulation = raised["result"]["simulation"]
        self.assertTrue(simulation["movement_observed"])
        self.assertTrue(simulation["hand_world_position_m"])
        ingress = raised["result"]["humanoid_field_ingress"]
        self.assertTrue(ingress["accepted"])
        self.assertEqual(raised["medium"]["diagnostics"]["humanoid_ingress"]["measured_events"], 1)
        status, egress = self.request("/api/field/egress")
        self.assertEqual(status, 200)
        self.assertFalse(egress["consumed"])
        self.assertIsNone(egress["consumer"])

    def test_unsupported_walk_intent_is_not_reported_as_humanoid_motion(self):
        status, result = self.request("/api/event", {"transcript": "start walking", "source": "text"})
        self.assertEqual(status, 200)
        self.assertEqual(result["result"]["simulation"]["execution_status"], "unsupported")
        self.assertFalse(result["result"]["simulation"]["movement_observed"])
        self.assertIsNone(result["result"]["humanoid_field_ingress"])
        self.assertEqual(result["medium"]["diagnostics"]["humanoid_ingress"]["measured_events"], 0)

    def test_field_view_and_interaction_assets_are_served(self):
        for path in ("/", "/app.js", "/style.css"):
            status, payload = self.get_bytes(path)
            self.assertEqual(status, 200)
            if path == "/":
                self.assertIn(b'id="mediumMap"', payload)


if __name__ == "__main__":
    unittest.main()
