"""Bounded, field-first Ripwave medium with optional local signaling.

The model is deliberately independent from rendering and MuJoCo. It keeps a
uniform spatial grid in compact structure-of-arrays buffers and advances from
the application simulation clock.
"""
from __future__ import annotations

from array import array
from collections import deque
import math
import random
import threading
import time
import uuid


CORE, MID, SHELL = 0, 1, 2
ROLE_NAMES = ("CORE", "MID", "SHELL")
MAX_CELLS = 65_536
MAX_TASKS = 32
MAX_RIPPLES = 256
MAX_MESSAGES = 8_192
MAX_TRACES = 65_536
MAX_TRACES_PER_CELL = 4
MAX_EVENT_IDS = 2_048
MAX_VIEW_CELLS = 2_304
R_CORE, R_MID = 10, 28
LAMBDA, MU, OMEGA, WAVE_NUMBER = 0.08, 0.025, 0.12, 0.18
# The upstream demo's 0.12 write threshold is unreachable for SHELL cells:
# exp(-0.08 * R_MID) is only 0.106 before temporal decay. 0.03 restores a
# non-empty, decaying shell write band without changing the ripple equation.
ALPHA, THETA_WRITE, THETA_ROUTE = 0.12, 0.03, 0.38
DELTA_FAST, DELTA_SLOW, THETA_FORGET = 0.009, 0.00004, 0.25


class DistributedMedium:
    """Local distributed field. Shared ripples are primary; neighbor messages are opt-in."""

    def __init__(self, blueprint: dict | None = None):
        self._lock = threading.RLock()
        self.configure(blueprint or {})

    @staticmethod
    def _finite_number(value, name):
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a finite number.") from None
        if not math.isfinite(result):
            raise ValueError(f"{name} must be a finite number.")
        return result

    def configure(self, blueprint: dict):
        if not isinstance(blueprint, dict):
            raise ValueError("blueprint must be a JSON object.")
        width = blueprint.get("width", 96)
        height = blueprint.get("height", 96)
        if isinstance(width, bool) or not isinstance(width, int) or not 8 <= width <= 256:
            raise ValueError("width must be an integer from 8 through 256 cells.")
        if isinstance(height, bool) or not isinstance(height, int) or not 8 <= height <= 256:
            raise ValueError("height must be an integer from 8 through 256 cells.")
        if width * height > MAX_CELLS:
            raise ValueError(f"blueprint is limited to {MAX_CELLS} cells.")
        cell_size = self._finite_number(blueprint.get("cell_size_m", 0.05), "cell_size_m")
        if not 0.005 <= cell_size <= 1.0:
            raise ValueError("cell_size_m must be between 0.005 and 1 meter.")
        seed = blueprint.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed must be an integer from 0 through 4294967295.")
        messaging = blueprint.get("messaging_enabled", False)
        if not isinstance(messaging, bool):
            raise ValueError("messaging_enabled must be a boolean.")
        stressors = blueprint.get("stressors", [])
        if not isinstance(stressors, list) or len(stressors) > MAX_TASKS:
            raise ValueError(f"stressors must be a list with at most {MAX_TASKS} entries.")

        with self._lock:
            self.width, self.height, self.cell_size_m, self.seed = width, height, cell_size, seed
            self.count = width * height
            rng = random.Random(seed)
            self.role = array("B", [SHELL]) * self.count
            self.energy = array("f", (0.6 + rng.random() * 0.4 for _ in range(self.count)))
            self.memory = array("f", [0.0]) * self.count
            self.memory_task = array("i", [-1]) * self.count
            self.memory_outcome = array("f", [0.0]) * self.count
            self.memory_access = array("i", [0]) * self.count
            self.task_load = array("f", [0.0]) * self.count
            self.signal = array("f", [0.0]) * self.count
            self.ripple_intensity = array("f", [0.0]) * self.count
            self.ripple_task = array("i", [-1]) * self.count
            self.cell_task = array("i", [-1]) * self.count
            self.tick = 0
            self.tasks: list[dict] = []
            self.ripples: list[dict] = []
            self.messages: deque[tuple] = deque()
            self.messaging_enabled = messaging
            self._traces: dict[tuple[int, int], list] = {}
            self._trace_ids: list[list[int]] = [[] for _ in range(self.count)]
            self._trace_slots: dict[tuple[int, int], int] = {}
            self._trace_keys: list[tuple[int, int] | None] = []
            self._free_trace_slots: list[int] = []
            self._trace_cursor = 0
            self._event_ids: dict[str, int] = {}
            self._event_id_order: deque[str] = deque()
            self._history: deque[dict] = deque(maxlen=32)
            self._next_task_id = 1
            self._next_message_id = 1
            self._seen_message_ids: set[int] = set()
            self._seen_message_order: deque[int] = deque()
            self._last_ms = 0.0
            self._mean_ms = 0.0
            self._total_ms = 0.0
            self._updates = 0
            self._cells_updated = 0
            self._message_counts = {"sent": 0, "delivered": 0, "expired": 0,
                                    "dropped": 0, "duplicate": 0, "invalid": 0}
            self._message_cursor = 0
            self._ripples_emitted = 0
            self._ripples_dropped = 0
            self._tasks_dropped = 0
            self._traces_dropped = 0
            self._humanoid_ingress = 0
            self._last_humanoid_event = None
        for stressor in stressors:
            if not isinstance(stressor, dict):
                raise ValueError("Each blueprint stressor must be an object.")
            config = dict(stressor)
            if "type" in config and "stressor_type" not in config:
                config["stressor_type"] = config.pop("type")
            self.inject_stressor(**config)
        return self.snapshot()

    def _cell_from_input(self, origin_cell=None, origin_m=None):
        if origin_cell is not None and origin_m is not None:
            raise ValueError("Provide origin_cell or origin_m, not both.")
        if origin_m is not None:
            if not isinstance(origin_m, (list, tuple)) or len(origin_m) != 2:
                raise ValueError("origin_m must contain two coordinates.")
            mx, my = (self._finite_number(v, "origin_m") for v in origin_m)
            x = round(mx / self.cell_size_m + (self.width - 1) * 0.5)
            y = round(my / self.cell_size_m + (self.height - 1) * 0.5)
        elif origin_cell is not None:
            if not isinstance(origin_cell, (list, tuple)) or len(origin_cell) != 2:
                raise ValueError("origin_cell must contain two coordinates.")
            x, y = origin_cell
            if isinstance(x, bool) or not isinstance(x, int) or isinstance(y, bool) or not isinstance(y, int):
                raise ValueError("origin_cell coordinates must be integers.")
        else:
            x, y = self.width // 2, self.height // 2
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ValueError("event origin is outside the configured field.")
        return x, y

    def _route_for(self, x, y):
        best_score, best_cell = 0.0, None
        for cy in range(0, self.height, 3):
            for cx in range(0, self.width, 3):
                cell = cy * self.width + cx
                if self.role[cell] != SHELL or not self._trace_ids[cell]:
                    continue
                distance = math.hypot(cx - x, cy - y)
                for task_id in self._trace_ids[cell]:
                    trace = self._traces.get((cell, task_id))
                    if not trace:
                        continue
                    score = trace[0] * trace[1] / (1.0 + distance * 0.01)
                    if score > best_score:
                        best_score, best_cell = score, cell
        return {"kind": "experienced" if best_score > THETA_ROUTE else "novel",
                "score": round(best_score, 6), "cell_id": best_cell}

    def inject_stressor(self, event_id=None, stressor_type="disturbance", intensity=0.6,
                        duration=200, origin_cell=None, origin_m=None, kind="stressor"):
        with self._lock:
            if not isinstance(stressor_type, str) or not stressor_type or len(stressor_type) > 32:
                raise ValueError("stressor_type must be a non-empty string of at most 32 characters.")
            if kind not in {"stressor", "command", "humanoid"}:
                raise ValueError("kind must be stressor, command, or humanoid.")
            strength = self._finite_number(intensity, "intensity")
            if not 0.0 <= strength <= 1.0:
                raise ValueError("intensity must be between 0 and 1.")
            if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 6000:
                raise ValueError("duration must be an integer from 1 through 6000 updates.")
            x, y = self._cell_from_input(origin_cell, origin_m)
            event_id = str(event_id or uuid.uuid4())
            if not event_id or len(event_id) > 96:
                raise ValueError("event_id must contain 1 through 96 characters.")
            if event_id in self._event_ids:
                return {"accepted": True, "duplicate": True, "event_id": event_id,
                        "task_id": self._event_ids[event_id]}
            if len(self.tasks) >= MAX_TASKS:
                self._tasks_dropped += 1
                raise ValueError(f"field is at the {MAX_TASKS}-task limit.")
            task_id = self._next_task_id
            self._next_task_id += 1
            task = {"id": task_id, "event_id": event_id, "type": stressor_type,
                    "kind": kind, "x": x, "y": y, "intensity": strength,
                    "remaining": duration, "duration": duration,
                    "emit_interval": 25, "route": self._route_for(x, y)}
            if len(self.tasks) == 0:
                self._history = deque(self._history, maxlen=32)
            self.tasks.append(task)
            self._history.append({"event_id": event_id, "task_id": task_id, "type": stressor_type,
                                  "kind": kind, "origin_cell": [x, y], "intensity": strength,
                                  "state": "active", "tick": self.tick})
            self._event_ids[event_id] = task_id
            self._event_id_order.append(event_id)
            while len(self._event_id_order) > MAX_EVENT_IDS:
                old_id = self._event_id_order.popleft()
                self._event_ids.pop(old_id, None)
            self._emit_ripple(task, strength)
            return {"accepted": True, "duplicate": False, "event_id": event_id,
                    "task_id": task_id, "route": task["route"], "origin_cell": [x, y]}

    def inject_humanoid_motion(self, event_id, origin_m, magnitude_m, source="measured_mujoco"):
        magnitude = self._finite_number(magnitude_m, "magnitude_m")
        if magnitude <= 0.0001:
            return {"accepted": False, "reason": "measured displacement is below threshold."}
        strength = min(1.0, max(0.05, magnitude / 0.25))
        result = self.inject_stressor(event_id=event_id, stressor_type="humanoid_motion",
                                      kind="humanoid", origin_m=origin_m,
                                      intensity=strength, duration=120)
        if not result.get("duplicate"):
            with self._lock:
                self._humanoid_ingress += 1
                self._last_humanoid_event = {"event_id": result["event_id"], "source": source,
                                             "magnitude_m": magnitude, "origin_m": list(origin_m),
                                             "task_id": result["task_id"], "tick": self.tick}
        return result

    def set_messaging(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("messaging_enabled must be a boolean.")
        with self._lock:
            self.messaging_enabled = enabled
            if not enabled:
                self._message_counts["dropped"] += len(self.messages)
                self.messages.clear()
            return self._diagnostics()

    def stop(self):
        with self._lock:
            for task in self.tasks:
                task["remaining"] = min(task["remaining"], 1)
            return self._diagnostics()

    def _emit_ripple(self, task, intensity):
        if len(self.ripples) >= MAX_RIPPLES:
            self.ripples.pop(0)
            self._ripples_dropped += 1
        self.ripples.append({"x": task["x"], "y": task["y"], "task_id": task["id"],
                             "intensity": min(1.0, max(0.0, intensity)), "age": 0, "max_age": 200})
        self._ripples_emitted += 1

    def enqueue_message(self, source, destination, task_id, intensity, ttl=8):
        """Validate and enqueue a testable one-hop message; delivery is on a later tick."""
        with self._lock:
            if not self.messaging_enabled:
                return {"accepted": False, "reason": "direct neighbor messaging is disabled"}
            if isinstance(source, bool) or not isinstance(source, int) or isinstance(destination, bool) or not isinstance(destination, int):
                self._message_counts["invalid"] += 1
                raise ValueError("source and destination must be integer cell IDs.")
            if not 0 <= source < self.count or not 0 <= destination < self.count:
                self._message_counts["invalid"] += 1
                raise ValueError("message endpoint is outside the field.")
            sx, sy = source % self.width, source // self.width
            dx, dy = destination % self.width, destination // self.width
            if abs(sx - dx) + abs(sy - dy) != 1:
                self._message_counts["invalid"] += 1
                raise ValueError("messages may be delivered only to a cardinal neighbor.")
            if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 0:
                self._message_counts["invalid"] += 1
                raise ValueError("task_id must be a non-negative integer.")
            strength = self._finite_number(intensity, "intensity")
            if not 0.0 <= strength <= 1.0:
                self._message_counts["invalid"] += 1
                raise ValueError("message intensity must be between 0 and 1.")
            if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 32:
                self._message_counts["invalid"] += 1
                raise ValueError("message ttl must be an integer from 1 through 32 ticks.")
            if len(self.messages) >= MAX_MESSAGES:
                self._message_counts["dropped"] += 1
                return {"accepted": False, "reason": "message capacity reached"}
            message_id = self._next_message_id
            self._next_message_id += 1
            self.messages.append((message_id, source, destination, task_id, strength,
                                  self.tick, self.tick + ttl))
            self._message_counts["sent"] += 1
            return {"accepted": True, "message_id": message_id}

    def _role_topology(self):
        self.role = array("B", [SHELL]) * self.count
        self.cell_task = array("i", [-1]) * self.count
        closest = array("f", [float("inf")]) * self.count
        core_sq, mid_sq = R_CORE * R_CORE, R_MID * R_MID
        for task in self.tasks:
            tx, ty, tid = task["x"], task["y"], task["id"]
            x0, x1 = max(0, tx - R_MID), min(self.width - 1, tx + R_MID)
            y0, y1 = max(0, ty - R_MID), min(self.height - 1, ty + R_MID)
            for y in range(y0, y1 + 1):
                dy = y - ty
                for x in range(x0, x1 + 1):
                    dx = x - tx
                    d2 = dx * dx + dy * dy
                    if d2 < mid_sq:
                        idx = y * self.width + x
                        if d2 < core_sq:
                            self.role[idx] = CORE
                            if d2 < closest[idx]:
                                closest[idx] = d2
                                self.cell_task[idx] = tid
                        elif self.role[idx] == SHELL:
                            self.role[idx] = MID

    def _deliver_messages(self):
        if not self.messages:
            return
        started = time.perf_counter()
        budgets = {}
        remaining = deque()
        while self.messages:
            mid, source, dest, task_id, strength, created, expires = self.messages.popleft()
            if mid in self._seen_message_ids:
                self._message_counts["duplicate"] += 1
                continue
            if self.tick > expires:
                self._message_counts["expired"] += 1
                continue
            if self.tick <= created or budgets.get(dest, 0) >= 2:
                remaining.append((mid, source, dest, task_id, strength, created, expires))
                continue
            budgets[dest] = budgets.get(dest, 0) + 1
            self._seen_message_ids.add(mid)
            self._seen_message_order.append(mid)
            if len(self._seen_message_order) > MAX_MESSAGES * 2:
                self._seen_message_ids.discard(self._seen_message_order.popleft())
            current = self.ripple_intensity[dest]
            self.ripple_intensity[dest] = min(1.0, current + strength - current * strength)
            self.signal[dest] = min(1.0, self.signal[dest] + strength)
            self.ripple_task[dest] = task_id
            self._message_counts["delivered"] += 1
        self.messages = remaining
        self._message_process_ms = (time.perf_counter() - started) * 1000.0

    def _send_local_messages(self, global_budget):
        if not self.messaging_enabled or global_budget <= 0 or self.tick % 5:
            return 0
        sent = 0
        active_by_id = {task["id"]: task for task in self.tasks}
        for offset in range(self.count):
            if sent >= global_budget:
                break
            idx = (self._message_cursor + offset) % self.count
            if self.role[idx] != CORE or self.task_load[idx] < 0.4:
                continue
            task = active_by_id.get(self.cell_task[idx])
            if not task:
                continue
            x, y = idx % self.width, idx // self.width
            dx, dy = x - task["x"], y - task["y"]
            if abs(dx) >= abs(dy) and dx:
                nx, ny = x + (1 if dx > 0 else -1), y
            elif dy:
                nx, ny = x, y + (1 if dy > 0 else -1)
            elif x + 1 < self.width:
                nx, ny = x + 1, y
            else:
                nx, ny = x - 1, y
            if not (0 <= nx < self.width and 0 <= ny < self.height):
                self._message_counts["dropped"] += 1
                continue
            if len(self.messages) >= MAX_MESSAGES:
                self._message_counts["dropped"] += 1
                break
            mid = self._next_message_id
            self._next_message_id += 1
            self.messages.append((mid, idx, ny * self.width + nx, task["id"],
                                  min(1.0, self.task_load[idx] * self.energy[idx]),
                                  self.tick, self.tick + 8))
            self._message_counts["sent"] += 1
            sent += 1
        if sent:
            self._message_cursor = (self._message_cursor + sent * 17) % self.count
        return sent

    @staticmethod
    def _utility(trace, tick):
        memory, outcome, last_access, _ = trace
        recency = max(0.0, 1.0 - (tick - last_access) * 0.0005)
        return 0.4 * outcome + 0.4 * recency + 0.2 * outcome

    def _remove_trace(self, cell, task_id):
        key = (cell, task_id)
        self._traces.pop(key, None)
        slot = self._trace_slots.pop(key, None)
        if slot is not None:
            self._trace_keys[slot] = None
            self._free_trace_slots.append(slot)
        try:
            self._trace_ids[cell].remove(task_id)
        except ValueError:
            pass
        self._refresh_memory_summary(cell)

    def _refresh_memory_summary(self, cell):
        best_memory, best_task, best_outcome, last_access = 0.0, -1, 0.0, 0
        for task_id in self._trace_ids[cell]:
            trace = self._traces.get((cell, task_id))
            if trace and trace[0] > best_memory:
                best_memory, best_task, best_outcome, last_access = trace[0], task_id, trace[1], trace[2]
        self.memory[cell] = best_memory
        self.memory_task[cell] = best_task
        self.memory_outcome[cell] = best_outcome
        self.memory_access[cell] = last_access

    def _write_memory(self, cell, task_id, intensity):
        if task_id < 0:
            return
        key = (cell, task_id)
        trace = self._traces.get(key)
        if trace is None:
            if len(self._trace_ids[cell]) >= MAX_TRACES_PER_CELL:
                victim = min(self._trace_ids[cell], key=lambda tid: self._traces[(cell, tid)][0] *
                             self._utility(self._traces[(cell, tid)], self.tick))
                victim_trace = self._traces[(cell, victim)]
                if intensity <= victim_trace[0] * self._utility(victim_trace, self.tick):
                    self._traces_dropped += 1
                    return
                self._remove_trace(cell, victim)
            if len(self._traces) >= min(MAX_TRACES, self.count * MAX_TRACES_PER_CELL):
                self._traces_dropped += 1
                return
            trace = [0.0, 0.0, self.tick, self.tick]
            self._traces[key] = trace
            self._trace_ids[cell].append(task_id)
            if self._free_trace_slots:
                slot = self._free_trace_slots.pop()
                self._trace_keys[slot] = key
            else:
                slot = len(self._trace_keys)
                self._trace_keys.append(key)
            self._trace_slots[key] = slot
        trace[0] += ALPHA * intensity * (1.0 - trace[0])
        trace[1] = min(1.0, trace[1] + 0.02)
        trace[2] = self.tick
        self._refresh_memory_summary(cell)

    def _decay_memory(self, budget=1024):
        if not self._trace_keys:
            return
        visits = min(budget, len(self._trace_keys))
        for _ in range(visits):
            if self._trace_cursor >= len(self._trace_keys):
                self._trace_cursor = 0
            key = self._trace_keys[self._trace_cursor]
            self._trace_cursor += 1
            if key is None:
                continue
            trace = self._traces.get(key)
            if not trace:
                continue
            elapsed = self.tick - trace[3]
            if elapsed <= 0:
                continue
            delta = DELTA_FAST if self._utility(trace, self.tick) < THETA_FORGET else DELTA_SLOW
            trace[0] = max(0.0, trace[0] * ((1.0 - delta) ** elapsed))
            trace[3] = self.tick
            if trace[0] < 1e-7:
                self._remove_trace(key[0], key[1])
            else:
                self._refresh_memory_summary(key[0])

    def _complete_task(self, task):
        self._emit_ripple(task, task["intensity"])
        x0, x1 = max(0, task["x"] - R_MID * 2), min(self.width - 1, task["x"] + R_MID * 2)
        y0, y1 = max(0, task["y"] - R_MID * 2), min(self.height - 1, task["y"] + R_MID * 2)
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                cell = y * self.width + x
                trace = self._traces.get((cell, task["id"]))
                if self.role[cell] == SHELL and trace and trace[0] > 0.1:
                    trace[1] = min(1.0, trace[1] + 0.05)
                    self._refresh_memory_summary(cell)
        for item in reversed(self._history):
            if item["task_id"] == task["id"]:
                item["state"] = "completed"
                break

    def advance(self, steps=1):
        if isinstance(steps, bool):
            raise ValueError("steps must be an integer from 1 through 1000.")
        try:
            steps = int(steps)
        except (TypeError, ValueError):
            raise ValueError("steps must be an integer from 1 through 1000.") from None
        if not 1 <= steps <= 1000:
            raise ValueError("steps must be an integer from 1 through 1000.")
        with self._lock:
            started = time.perf_counter()
            for _ in range(steps):
                self.tick += 1
                self._role_topology()
                self.ripple_intensity = array("f", [0.0]) * self.count
                self.ripple_task = array("i", [-1]) * self.count
                self.signal = array("f", [0.0]) * self.count
                dominant = array("f", [0.0]) * self.count
                for ripple in self.ripples:
                    age = ripple["age"]
                    scan = min(max(self.width, self.height), age * 1.5 + 20)
                    x0, x1 = max(0, int(ripple["x"] - scan)), min(self.width - 1, int(ripple["x"] + scan))
                    y0, y1 = max(0, int(ripple["y"] - scan)), min(self.height - 1, int(ripple["y"] + scan))
                    temporal = ripple["intensity"] * math.exp(-MU * age)
                    for y in range(y0, y1 + 1):
                        dy = y - ripple["y"]
                        for x in range(x0, x1 + 1):
                            dx = x - ripple["x"]
                            distance = math.sqrt(dx * dx + dy * dy)
                            strength = temporal * math.exp(-LAMBDA * distance) * max(
                                0.0, math.cos(OMEGA * age - WAVE_NUMBER * distance))
                            if strength <= 0.01:
                                continue
                            idx = y * self.width + x
                            current = self.ripple_intensity[idx]
                            self.ripple_intensity[idx] = min(1.0, current + strength - current * strength)
                            if strength > dominant[idx]:
                                dominant[idx] = strength
                                self.ripple_task[idx] = ripple["task_id"]
                    ripple["age"] += 1
                self._deliver_messages()
                previous_load = array("f", self.task_load)
                for idx in range(self.count):
                    role = self.role[idx]
                    if role == CORE:
                        task_id = self.cell_task[idx]
                        if task_id >= 0:
                            load = min(1.0, self.task_load[idx] + 0.08)
                            self.task_load[idx] = load
                            self.energy[idx] -= load * 0.006
                        else:
                            self.task_load[idx] = max(0.0, self.task_load[idx] - 0.05)
                        self.signal[idx] = max(self.signal[idx], self.ripple_intensity[idx])
                        self.energy[idx] = min(1.0, max(0.0, self.energy[idx] + 0.002))
                    elif role == MID:
                        self.signal[idx] = self.ripple_intensity[idx] * 0.7
                        x, y = idx % self.width, idx // self.width
                        core_signal = 0.0
                        if x + 1 < self.width and self.role[idx + 1] == CORE: core_signal = max(core_signal, previous_load[idx + 1])
                        if x and self.role[idx - 1] == CORE: core_signal = max(core_signal, previous_load[idx - 1])
                        if y + 1 < self.height and self.role[idx + self.width] == CORE: core_signal = max(core_signal, previous_load[idx + self.width])
                        if y and self.role[idx - self.width] == CORE: core_signal = max(core_signal, previous_load[idx - self.width])
                        self.task_load[idx] = max(self.task_load[idx] * 0.92, core_signal * 0.6)
                        self.energy[idx] = min(1.0, max(0.0, self.energy[idx] - self.signal[idx] * 0.001 + 0.004))
                    else:
                        self.signal[idx] = self.ripple_intensity[idx]
                        task_id = self.ripple_task[idx]
                        if self.ripple_intensity[idx] > THETA_WRITE and task_id >= 0:
                            self._write_memory(idx, task_id, self.ripple_intensity[idx])
                        self.task_load[idx] *= 0.98
                        self.energy[idx] = min(1.0, max(0.0, self.energy[idx] + 0.005))
                self._decay_memory()
                self._send_local_messages(128)
                still_active = []
                for task in self.tasks:
                    task["remaining"] -= 1
                    if task["remaining"] <= 0:
                        self._complete_task(task)
                    else:
                        still_active.append(task)
                    if task["remaining"] > 0 and self.tick % task["emit_interval"] == 0:
                        self._emit_ripple(task, task["intensity"])
                self.tasks = still_active
                self.ripples = [r for r in self.ripples if r["age"] < r["max_age"]]
            elapsed = (time.perf_counter() - started) * 1000.0
            self._last_ms = elapsed / steps
            self._total_ms += elapsed
            self._updates += steps
            self._mean_ms = self._total_ms / self._updates
            self._cells_updated += self.count * steps
            return self._diagnostics()

    def outbound_to_humanoid(self):
        """Expose a small field signal sample; no MuJoCo controller consumes it today."""
        with self._lock:
            strongest = -1
            for idx, value in enumerate(self.ripple_intensity):
                if value > 0.2 and (strongest < 0 or value > self.ripple_intensity[strongest]):
                    strongest = idx
            signal = None
            if strongest >= 0:
                signal = {"cell_id": strongest, "task_id": self.ripple_task[strongest],
                          "intensity": round(self.ripple_intensity[strongest], 4),
                          "origin_m": self._cell_to_m(strongest % self.width, strongest // self.width)}
            return {"available": signal is not None, "signal": signal,
                    "consumer": None, "consumed": False,
                    "note": "Exposed for inspection; no autonomous MuJoCo controller consumes field signals."}

    def _cell_to_m(self, x, y):
        return [round((x - (self.width - 1) * 0.5) * self.cell_size_m, 4),
                round((y - (self.height - 1) * 0.5) * self.cell_size_m, 4)]

    def _diagnostics(self):
        roles = [0, 0, 0]
        mem_sum, signal_sum = 0.0, 0.0
        for idx in range(self.count):
            roles[self.role[idx]] += 1
            mem_sum += self.memory[idx]
            signal_sum += self.signal[idx]
        stressor_count = sum(task["kind"] == "stressor" for task in self.tasks)
        ripple_summary = {"count": len(self.ripples), "emitted": self._ripples_emitted,
                          "dropped": self._ripples_dropped}
        return {"cell_count": self.count, "active_task_count": len(self.tasks),
                "active_stressor_count": stressor_count, "active_ripple_count": len(self.ripples),
                "ripple": ripple_summary, "role_counts": dict(zip(ROLE_NAMES, roles)),
                "memory": {"trace_count": len(self._traces), "mean_cell_memory": round(mem_sum / self.count, 6),
                           "dropped_traces": self._traces_dropped},
                "signal": {"mean_intensity": round(signal_sum / self.count, 6)},
                "messages": {**self._message_counts, "pending": len(self.messages),
                             "enabled": self.messaging_enabled},
                "update": {"ticks": self._updates, "cells_updated": self._cells_updated,
                           "last_ms_per_tick": round(self._last_ms, 3),
                           "mean_ms_per_tick": round(self._mean_ms, 3),
                           "measured_hz": round(1000.0 / self._last_ms, 2) if self._last_ms > 0 else 0.0},
                "humanoid_ingress": {"measured_events": self._humanoid_ingress,
                                     "last_event": self._last_humanoid_event}}

    def diagnostics(self):
        with self._lock:
            return self._diagnostics()

    def snapshot(self):
        with self._lock:
            stride = max(1, math.ceil(math.sqrt(self.count / MAX_VIEW_CELLS)))
            view_w = math.ceil(self.width / stride)
            view_h = math.ceil(self.height / stride)
            cells = []
            for y in range(0, self.height, stride):
                for x in range(0, self.width, stride):
                    idx = y * self.width + x
                    cells.append([int(self.role[idx]), round(self.signal[idx], 3),
                                  round(self.memory[idx], 3), int(self.ripple_task[idx]),
                                  int(self.memory_task[idx])])
            tasks = [{"id": t["id"], "event_id": t["event_id"], "type": t["type"],
                      "kind": t["kind"], "cell": [t["x"], t["y"]],
                      "origin_m": self._cell_to_m(t["x"], t["y"]),
                      "intensity": round(t["intensity"], 3), "remaining": t["remaining"],
                      "route": t["route"]} for t in self.tasks]
            return {"blueprint": {"width": self.width, "height": self.height,
                                  "cell_size_m": self.cell_size_m, "seed": self.seed,
                                  "coordinate_origin": "grid center = (0,0) meters",
                                  "messaging_enabled": self.messaging_enabled},
                    "diagnostics": self._diagnostics(), "tasks": tasks,
                    "events": list(self._history),
                    "view": {"width": view_w, "height": view_h, "stride": stride,
                             "cells": cells, "encoding": "[role,signal,memory,ripple_task_id,memory_task_id]"},
                    "humanoid_egress": self.outbound_to_humanoid()}
