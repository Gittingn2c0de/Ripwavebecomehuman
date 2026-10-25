"""MuJoCo model discovery and bounded simulation control adapter."""
from __future__ import annotations
import threading
import math
import struct
import zlib
from pathlib import Path

class MuJoCoAdapter:
    def __init__(self, xml_path=None, project_root=None):
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.xml_path = None
        self.model = None
        self.data = None
        self.error = None
        self._mujoco = None
        self._lock = threading.RLock()
        self._render_lock = threading.Lock()
        self._renderer = None
        self._render_ms = None
        self._last_action = "none"
        self._last_control_count = 0
        self._field_geom_ids = []
        self._grid_geom_ids = []
        self._target_geom_id = -1
        self._target_mocap_id = -1
        self._field_radius_m = 0.28
        self._field_amplitude = 0.35
        self._field_active = False
        self._reference_body_height_m = None
        self.load(xml_path) if xml_path else None

    def discover(self):
        excluded = {'.git', '__pycache__', '.venv', 'venv', 'node_modules'}
        candidates = []
        for p in self.project_root.rglob('*.xml'):
            try:
                rel = p.relative_to(self.project_root)
            except ValueError:
                continue
            if any(part in excluded for part in rel.parts):
                continue
            # Exclude common asset/config XML files when a more likely model exists.
            name = p.name.lower()
            score = 0
            if 'ms-human' in name or 'mshuman' in name: score += 10
            if name == 'ms-human-700.xml': score += 14
            if 'manipulation' in name: score += 8
            if 'full' in name or 'body' in name: score += 4
            if 'model' in name or 'human' in name: score += 2
            if 'scene' in name: score -= 2
            try:
                rel_text = str(rel)
                candidates.append({'path': rel_text, 'name': p.name, 'score': score, 'bytes': p.stat().st_size})
            except OSError:
                pass
        candidates.sort(key=lambda x: (-x['score'], x['path'].lower()))
        return candidates[:200]

    def load(self, xml_path):
        with self._lock:
            if self._renderer is not None:
                try: self._renderer.close()
                except Exception: pass
                self._renderer = None
            self.model = self.data = None
            self.error = None
            self._last_action = "none"
            self._last_control_count = 0
            try:
                path = Path(xml_path).expanduser()
                if not path.is_absolute():
                    path = (self.project_root / path).resolve()
                else:
                    path = path.resolve()
                # Prevent a local API request from loading arbitrary files outside the project.
                if self.project_root != path and self.project_root not in path.parents:
                    raise ValueError('Choose an XML file inside the Ripwave Become Human project folder.')
                if not path.is_file() or path.suffix.lower() != '.xml':
                    raise ValueError(f'Model XML does not exist: {path}')
                import mujoco
                self._mujoco = mujoco
                # Keep the upstream XML untouched. Compile a runtime scene from it
                # and add only local, named world geoms for the field and target.
                spec = mujoco.MjSpec.from_file(str(path))
                target_body = spec.worldbody.add_body(name="ripwave_target_mount", pos=[0.65, 0.0, 0.05], mocap=1)
                target_body.add_geom(
                    name="ripwave_target", type=mujoco.mjtGeom.mjGEOM_SPHERE,
                    pos=[0.0, 0.0, 0.0], size=[0.05], rgba=[0.72, 0.91, 0.24, 1.0],
                    contype=1, conaffinity=1, condim=3, friction=[1.0, 0.005, 0.0001], group=0)
                for index in range(9):
                    spec.worldbody.add_geom(
                        name=f"ripwave_field_{index:02d}", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                        pos=[0.0, 0.0, 0.003], size=[0.02, 0.0015],
                        rgba=[0.20, 0.82, 0.67, 0.12], contype=0, conaffinity=0, group=0)
                # A restrained 5 m x 5 m world grid, built in MuJoCo meters.
                # These visual-only strips sit just above the floor and cannot
                # affect contacts, dynamics, or measured character bounds.
                grid_extent, grid_step = 2.5, 0.25
                count = int(round(grid_extent * 2 / grid_step)) + 1
                for index in range(count):
                    coord = -grid_extent + index * grid_step
                    major = abs(coord - round(coord)) < 1e-8
                    rgba = ([0.28, 0.48, 0.68, 0.38] if major
                            else [0.22, 0.36, 0.51, 0.24])
                    half_width = 0.008 if major else 0.0035
                    for axis, pos, size in (
                        ("x", [coord, 0.0, 0.004], [half_width, grid_extent, 0.002]),
                        ("y", [0.0, coord, 0.004], [grid_extent, half_width, 0.002]),
                    ):
                        geom = spec.worldbody.add_geom(
                            name=f"ripwave_grid_{axis}_{index:02d}",
                            type=mujoco.mjtGeom.mjGEOM_BOX, pos=pos, size=size,
                            rgba=rgba, contype=0, conaffinity=0, group=0)
                        if abs(coord) < 1e-8:
                            geom.rgba = [0.72, 0.91, 0.24, 0.48]
                self.model = spec.compile()
                # The upstream XML requests a 3840x2160 offscreen buffer. The local
                # studio uses a measured, bounded preview size instead.
                self.model.vis.global_.offwidth = 800
                self.model.vis.global_.offheight = 600
                self.model.vis.quality.shadowsize = 1024
                self.model.vis.quality.offsamples = 2
                self.data = mujoco.MjData(self.model)
                self._reset_state()
                self._field_geom_ids = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM,
                                                           f"ripwave_field_{i:02d}") for i in range(9)]
                self._grid_geom_ids = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM,
                                                          f"ripwave_grid_{axis}_{i:02d}")
                                       for axis in ("x", "y") for i in range(21)]
                self._target_geom_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "ripwave_target")
                target_body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "ripwave_target_mount")
                self._target_mocap_id = int(self.model.body_mocapid[target_body_id])
                self._reference_body_height_m = self._body_bounds()["size_m"][2]
                self._apply_field_geometry()
                self.xml_path = str(path)
            except Exception as exc:
                self.error = self._explain_error(exc)
                self.xml_path = None
        return self.status()

    @staticmethod
    def _explain_error(exc):
        message = str(exc)
        if isinstance(exc, ModuleNotFoundError) and exc.name == "mujoco":
            return "MuJoCo is not installed. Run: python -m pip install mujoco"
        if "XML Error" in message or "Error opening file" in message:
            return f"MuJoCo could not resolve this XML or one of its referenced assets: {message}"
        return message

    def _name(self, obj_type, index):
        name = self._mujoco.mj_id2name(self.model, obj_type, index)
        return name or f"{obj_type.name.lower()}_{index}"

    def _reset_state(self):
        """Start from the model's authored initial keyframe when available."""
        if self.model.nkey:
            self._mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        else:
            self._mujoco.mj_resetData(self.model, self.data)
        self._mujoco.mj_forward(self.model, self.data)

    def set_field(self, field):
        """Render the ripple as visual-only MuJoCo cylinders in world coordinates."""
        if self.model is None:
            return
        with self._lock:
            self._field_radius_m = max(0.08, min(1.0, float(field.get("radius", 0.28))))
            self._field_amplitude = max(0.0, min(1.0, float(field.get("amplitude", 0.35))))
            self._field_active = bool(field.get("active", False))
            self._apply_field_geometry()

    def _apply_field_geometry(self):
        if self.model is None or self.data is None or not self._field_geom_ids:
            return
        import numpy as np
        # Center the field on the measured footprint of the collidable feet.
        footprint = self._footprint_bounds()
        center_x = float((footprint[0][0] + footprint[1][0]) * 0.5) if footprint else 0.0
        center_y = float((footprint[0][1] + footprint[1][1]) * 0.5) if footprint else 0.0
        strength = self._field_amplitude / 0.35
        for index, geom_id in enumerate(self._field_geom_ids):
            fraction = 0.22 + index * 0.18
            self.model.geom_pos[geom_id] = (center_x, center_y, 0.003)
            self.model.geom_size[geom_id, 0] = max(0.005, self._field_radius_m * fraction)
            # Multiple translucent discs overlap at the center. Keep the
            # inactive envelope quiet so it reads as a field marker, not a
            # green platform; activity remains a clear but restrained cue.
            pulse = 0.018 if not self._field_active else 0.06
            self.model.geom_rgba[geom_id, 3] = min(0.38, pulse * strength * (1.0 - index * 0.055))
        self._mujoco.mj_forward(self.model, self.data)

    def _geom_world_bounds(self, geom_id):
        """Return exact transformed bounds from MuJoCo's local geom AABB."""
        import numpy as np
        local = self.model.geom_aabb[geom_id]
        rotation = self.data.geom_xmat[geom_id].reshape(3, 3)
        center = self.data.geom_xpos[geom_id] + rotation @ local[:3]
        half = np.abs(rotation) @ local[3:]
        return center - half, center + half

    def _footprint_bounds(self):
        import numpy as np
        sides = ("r", "l")
        foot_body_ids = {self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, f"{name}_{side}")
                         for side in sides for name in ("calcn", "toes")}
        if -1 in foot_body_ids:
            foot_body_ids.discard(-1)
        boxes = []
        for geom_id in range(self.model.ngeom):
            if int(self.model.geom_bodyid[geom_id]) not in foot_body_ids:
                continue
            if not self.model.geom_contype[geom_id] or not self.model.geom_conaffinity[geom_id]:
                continue
            if self.model.geom_type[geom_id] == self._mujoco.mjtGeom.mjGEOM_PLANE:
                continue
            boxes.append(self._geom_world_bounds(geom_id))
        return (np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)) if boxes else None

    def _body_bounds(self):
        import numpy as np
        geom_type = self._mujoco.mjtObj.mjOBJ_GEOM
        floor_id = self._mujoco.mj_name2id(self.model, geom_type, "floor")
        excluded = set(self._field_geom_ids + self._grid_geom_ids + [self._target_geom_id, floor_id])
        boxes = [self._geom_world_bounds(i) for i in range(self.model.ngeom)
                 if i not in excluded and self.model.geom_type[i] != self._mujoco.mjtGeom.mjGEOM_PLANE]
        low = np.min([box[0] for box in boxes], axis=0)
        high = np.max([box[1] for box in boxes], axis=0)
        return {"min_m": low.tolist(), "max_m": high.tolist(), "size_m": (high - low).tolist()}

    def _bounds_for_bodies(self, body_ids):
        import numpy as np
        boxes = [self._geom_world_bounds(index) for index in range(self.model.ngeom)
                 if int(self.model.geom_bodyid[index]) in body_ids
                 and index not in self._field_geom_ids
                 and index not in self._grid_geom_ids
                 and index != self._target_geom_id
                 and self.model.geom_type[index] != self._mujoco.mjtGeom.mjGEOM_PLANE]
        if not boxes:
            return None
        low = np.min([box[0] for box in boxes], axis=0)
        high = np.max([box[1] for box in boxes], axis=0)
        return {"min_m": low.tolist(), "max_m": high.tolist(), "size_m": (high - low).tolist()}

    def _world_diagnostics(self):
        import numpy as np
        geom_type = self._mujoco.mjtObj.mjOBJ_GEOM
        floor_id = self._mujoco.mj_name2id(self.model, geom_type, "floor")
        bounds = self._body_bounds()
        low = np.asarray(bounds["min_m"])
        high = np.asarray(bounds["max_m"])
        footprint = self._footprint_bounds()
        foot_names = ("calcn_r", "calcn_l", "toes_r", "toes_l")
        feet = {}
        for name in foot_names:
            body_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, name)
            if body_id >= 0:
                feet[name] = self.data.xpos[body_id].tolist()
        pelvis_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        sternum_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, "sternum")
        spine = self.data.xpos[sternum_id] - self.data.xpos[pelvis_id] if pelvis_id >= 0 and sternum_id >= 0 else np.array([0, 0, 1])
        norm = float(np.linalg.norm(spine)) or 1.0
        upright_angle = math.degrees(math.acos(float(np.clip(spine[2] / norm, -1.0, 1.0))))
        pelvis_bounds = self._bounds_for_bodies({pelvis_id}) if pelvis_id >= 0 else None
        torso_body_ids = set()
        for body_id in range(1, self.model.nbody):
            body_name = self._name(self._mujoco.mjtObj.mjOBJ_BODY, body_id).lower()
            if (body_name in {"pelvis", "sacrum", "sternum"} or body_name.startswith("lumbar")
                    or body_name.startswith("rib")):
                torso_body_ids.add(body_id)
        torso_bounds = self._bounds_for_bodies(torso_body_ids)
        floor_contacts, foot_contacts, target_contacts = [], [], []
        for index in range(int(self.data.ncon)):
            contact = self.data.contact[index]
            geom1, geom2 = int(contact.geom1), int(contact.geom2)
            names = [self._name(geom_type, geom1), self._name(geom_type, geom2)]
            pair = {"geoms": names, "distance_m": float(contact.dist)}
            if floor_id in (geom1, geom2):
                floor_contacts.append(pair)
                body_ids = {int(self.model.geom_bodyid[geom1]), int(self.model.geom_bodyid[geom2])}
                feet_ids = {self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, n)
                            for n in ("calcn_r", "calcn_l", "toes_r", "toes_l")}
                if body_ids & feet_ids:
                    foot_contacts.append(pair)
            if self._target_geom_id in (geom1, geom2):
                target_contacts.append(pair)
        target_pos = self.data.geom_xpos[self._target_geom_id].tolist() if self._target_geom_id >= 0 else None
        floor_pos = self.data.geom_xpos[floor_id].tolist() if floor_id >= 0 else [0.0, 0.0, 0.0]
        initial_joint_pose = {}
        if self.model.nkey:
            for joint_id in range(self.model.njnt):
                address = int(self.model.jnt_qposadr[joint_id])
                value = float(self.model.key_qpos[0, address])
                if abs(value) > 1e-9:
                    initial_joint_pose[self._name(self._mujoco.mjtObj.mjOBJ_JOINT, joint_id)] = value
        return {
            "units": "meters", "up_axis": "+Z", "gravity_m_s2": self.model.opt.gravity.tolist(),
            "ground": {"geom": "floor", "height_m": float(floor_pos[2]),
                       "contype": int(self.model.geom_contype[floor_id]) if floor_id >= 0 else None,
                       "conaffinity": int(self.model.geom_conaffinity[floor_id]) if floor_id >= 0 else None,
                       "visual_grid": {"extent_m": 2.5, "spacing_m": 0.25,
                                       "collision_enabled": False}},
            "body_bounds": bounds, "body_height_m": float(high[2] - low[2]),
            "torso_bounds": torso_bounds, "pelvis_bounds": pelvis_bounds,
            "reference_body_height_m": self._reference_body_height_m,
            "foot_positions": feet,
            "footprint_bounds": ({"min_m": footprint[0].tolist(), "max_m": footprint[1].tolist(),
                                  "size_m": (footprint[1] - footprint[0]).tolist()} if footprint else None),
            "foot_ground_separation_m": float(footprint[0][2]) if footprint else None,
            "foot_ground_clearance_m": max(0.0, float(footprint[0][2])) if footprint else None,
            "foot_ground_aabb_penetration_m": max(0.0, -float(footprint[0][2])) if footprint else None,
            "foot_ground_penetration_m": max(0.0, -min((pair["distance_m"] for pair in foot_contacts), default=0.0)),
            "pelvis_position_m": self.data.xpos[pelvis_id].tolist() if pelvis_id >= 0 else None,
            "sternum_position_m": self.data.xpos[sternum_id].tolist() if sternum_id >= 0 else None,
            "pelvis_to_sternum_upright_error_deg": upright_angle,
            "target": {"position_m": target_pos, "radius_m": 0.05,
                       "contype": int(self.model.geom_contype[self._target_geom_id]) if self._target_geom_id >= 0 else None,
                       "conaffinity": int(self.model.geom_conaffinity[self._target_geom_id]) if self._target_geom_id >= 0 else None},
            "field": {"center_m": [float(self.model.geom_pos[self._field_geom_ids[0], 0]),
                                      float(self.model.geom_pos[self._field_geom_ids[0], 1]), 0.003],
                      "radius_m": self._field_radius_m,
                      "outer_visual_radius_m": self._field_radius_m * 1.66,
                      "nominal_body_height_m": self._reference_body_height_m,
                      "radius_to_body_height": self._field_radius_m / max(1e-9, self._reference_body_height_m or 1.0),
                      "radius_to_footprint_length": (self._field_radius_m / float(footprint[1][0] - footprint[0][0])
                                                     if footprint and footprint[1][0] > footprint[0][0] else None),
                      "radius_to_footprint_width": (self._field_radius_m / float(footprint[1][1] - footprint[0][1])
                                                    if footprint and footprint[1][1] > footprint[0][1] else None),
                      "collision_enabled": False},
            "contacts": {"total": int(self.data.ncon), "floor": floor_contacts,
                         "foot_floor_count": len(foot_contacts), "foot_floor": foot_contacts,
                         "target": target_contacts},
            "initial_nonzero_joint_qpos": initial_joint_pose,
        }

    def status(self):
        with self._lock:
            if self.model is None:
                return {'enabled': False, 'reason': self.error or 'No model loaded.', 'model_path': None}
            actuator_type = self._mujoco.mjtObj.mjOBJ_ACTUATOR
            joint_type = self._mujoco.mjtObj.mjOBJ_JOINT
            actuators = [self._name(actuator_type, i) for i in range(int(self.model.nu))]
            joints = [self._name(joint_type, i) for i in range(int(self.model.njnt))]
            ranges = []
            for i, name in enumerate(actuators):
                low, high = (float(x) for x in self.model.actuator_ctrlrange[i])
                ranges.append({'name': name, 'ctrl_min': low, 'ctrl_max': high})
            finite = bool(math.isfinite(float(self.data.time)) and all(math.isfinite(float(x)) for x in self.data.qpos))
            arm_candidates = [i for i, name in enumerate(actuators)
                              if name.endswith("_r") and any(token in name.upper() for token in ("DELT", "SUPSP"))]
            return {
                'enabled': True, 'model_path': self.xml_path,
                'model_name': Path(self.xml_path).name,
                'nq': int(self.model.nq), 'nv': int(self.model.nv), 'nu': int(self.model.nu),
                'nbody': int(self.model.nbody), 'njnt': int(self.model.njnt), 'ngeom': int(self.model.ngeom),
                'timestep': float(self.model.opt.timestep), 'time': float(self.data.time),
                'state_finite': finite, 'mode': 'loaded_controlled_baseline',
                'actuator_names': actuators[:120], 'actuator_names_total': len(actuators),
                'actuator_ranges': ranges[:120], 'joint_names': joints[:120], 'joint_names_total': len(joints),
                'controls_enabled': bool(arm_candidates),
                'walking_controller_available': False,
                'control_mapping': 'right shoulder elevation baseline' if arm_candidates else None,
                'control_candidate_count': len(arm_candidates),
                'last_action': self._last_action, 'last_control_count': self._last_control_count,
                'controls_note': ('Verified bounded DELT/SUPSP right-arm muscle baseline; this is not a trained pose policy.'
                                  if arm_candidates else
                                  'No verified right-arm muscle candidates were found; movement commands stay disabled.'),
                'renderer': {'kind': 'MuJoCo offscreen OpenGL', 'ready': self._renderer is not None,
                    'width': 800, 'height': 600, 'last_render_ms': self._render_ms},
                'world': self._world_diagnostics(), 'scene_augmented': True
            }

    @staticmethod
    def _png(rgb):
        """Encode MuJoCo's RGB array as PNG without adding an imaging dependency."""
        height, width, _ = rgb.shape
        raw = b''.join(b'\x00' + memoryview(rgb[y]).cast('B').tobytes() for y in range(height))
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', width, height, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b''))

    def render_png(self, azimuth=180, elevation=-8, distance=3.2):
        """Render the current MuJoCo state with a bounded interactive free camera."""
        with self._render_lock, self._lock:
            if self.model is None:
                raise RuntimeError(self.error or 'Load a MuJoCo model to render the simulation.')
            started = __import__('time').perf_counter()
            try:
                if self._renderer is None:
                    self._renderer = self._mujoco.Renderer(self.model, height=600, width=800)
                    self._render_options = self._mujoco.MjvOption()
                    self._mujoco.mjv_defaultOption(self._render_options)
                    # 700 tendon paths overwhelm the body silhouette and dominate
                    # offscreen cost; retain actual bone meshes and skin capsules.
                    self._render_options.flags[self._mujoco.mjtVisFlag.mjVIS_TENDON] = 0
                    self._render_options.geomgroup[3] = 0
                camera = self._mujoco.MjvCamera()
                self._mujoco.mjv_defaultCamera(camera)
                camera.type = self._mujoco.mjtCamera.mjCAMERA_FREE
                world = self._world_diagnostics()
                bounds = world['body_bounds']
                camera.lookat[:] = ((bounds['min_m'][0] + bounds['max_m'][0]) * 0.5,
                                    (bounds['min_m'][1] + bounds['max_m'][1]) * 0.5,
                                    (bounds['min_m'][2] + bounds['max_m'][2]) * 0.5)
                camera.azimuth = max(-180.0, min(180.0, float(azimuth)))
                camera.elevation = max(-35.0, min(35.0, float(elevation)))
                camera.distance = max(1.6, min(4.8, float(distance)))
                self._renderer.update_scene(self.data, camera=camera, scene_option=self._render_options)
                image = self._renderer.render()
                # Recompose the near-neutral viewer backdrop and plane for the
                # studio; chromatic anatomical meshes and dark contact shadows stay.
                import numpy as np
                high, low = image.max(axis=2), image.min(axis=2)
                backdrop = (high - low < 18) & (image.mean(axis=2) > 85)
                rows = np.linspace(12, 23, image.shape[0], dtype=np.uint8)[:, None]
                background = np.empty_like(image)
                background[:, :, 0] = rows
                background[:, :, 1] = rows + 6
                background[:, :, 2] = rows + 13
                image[backdrop] = background[backdrop]
                payload = self._png(image)
                self._render_ms = round((__import__('time').perf_counter() - started) * 1000, 1)
                return payload
            except Exception as exc:
                self._render_ms = round((__import__('time').perf_counter() - started) * 1000, 1)
                raise RuntimeError(f'MuJoCo renderer unavailable: {exc}') from exc

    def step(self, steps=1):
        with self._lock:
            if self.model is None:
                return self.status()
            steps = max(1, min(int(steps), 1000))
            for _ in range(steps):
                self._mujoco.mj_step(self.model, self.data)
            self._apply_field_geometry()
            if not all(math.isfinite(float(x)) for x in self.data.qpos):
                raise RuntimeError("MuJoCo state became non-finite while stepping.")
            return self.status()

    def execute_intent(self, intent):
        """Apply only mappings verified against the loaded model's actuator names."""
        with self._lock:
            if intent in {"neutral", "reset"}:
                if self.model is None:
                    return {'executed': False, 'movement_observed': False, 'execution_status': 'unavailable',
                            'message': 'Field reset; load a MuJoCo model to reset its simulation state.'}
                self._reset_state()
                self._apply_field_geometry()
                self._last_action = "neutral"
                self._last_control_count = 0
                return {'executed': True, 'movement_observed': False, 'execution_status': 'completed',
                        'message': 'Simulation reset to neutral state.'}
            if intent == "stop":
                if self.model is None:
                    return {'executed': False, 'movement_observed': False, 'execution_status': 'unavailable',
                            'message': 'Field stopped; no MuJoCo model is loaded.'}
                self.data.ctrl[:] = 0
                self._last_action = "stop"
                self._last_control_count = int(self.model.nu)
                self.step(1)
                return {'executed': True, 'movement_observed': False, 'execution_status': 'stopped',
                        'message': 'All actuator controls set to zero.'}
            if intent == 'walking':
                return {'executed': False, 'movement_observed': False, 'execution_status': 'unsupported',
                        'message': 'Intent recorded only; no validated walking controller or trained policy is installed.'}
            if intent in {'start_ripple', 'expand', 'contract', 'audio_modulate'}:
                return {'executed': False, 'movement_observed': False, 'execution_status': 'field_only',
                        'message': 'Ripple field updated; no human actuator mapping is defined.'}
            if intent in {'lower_right_arm', 'reach_forward'}:
                return {'executed': False, 'movement_observed': False, 'execution_status': 'unsupported',
                        'message': 'This movement is recognized, but no validated actuator mapping is available.'}
            if intent != "raise_right_arm":
                return {'executed': False, 'movement_observed': False, 'execution_status': 'unsupported',
                        'message': 'No MuJoCo mapping is defined for this intent.'}
            if self.model is None:
                return {'executed': False, 'movement_observed': False, 'execution_status': 'unavailable',
                        'message': 'Load a MuJoCo model before using right-arm control.'}
            names = [self._name(self._mujoco.mjtObj.mjOBJ_ACTUATOR, i) for i in range(int(self.model.nu))]
            indices = [i for i, name in enumerate(names)
                       if name.endswith("_r") and any(token in name.upper() for token in ("DELT", "SUPSP"))]
            if not indices:
                return {'executed': False, 'movement_observed': False, 'execution_status': 'unsupported',
                        'message': 'Right-arm control is unavailable: this model has no verified DELT/SUPSP actuators.'}
            # Make each baseline deterministic and prevent accumulated controls
            # or a prior pose from carrying into the next bounded action.
            self._reset_state()
            self.data.ctrl[:] = 0
            hand_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, 'hand_r')
            shoulder_body_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, 'clavicle_r')
            hand_offset_before = (self.data.xpos[hand_id] - self.data.xpos[shoulder_body_id]).copy() if hand_id >= 0 and shoulder_body_id >= 0 else None
            for i in indices:
                low, high = (float(x) for x in self.model.actuator_ctrlrange[i])
                self.data.ctrl[i] = max(low, min(high, 1.0))
            before = self.data.qpos.copy()
            for _ in range(100):
                self._mujoco.mj_step(self.model, self.data)
            if not all(math.isfinite(float(x)) for x in self.data.qpos):
                raise RuntimeError("Right-arm baseline produced a non-finite MuJoCo state.")
            target_id = self._mujoco.mj_name2id(
                self.model, self._mujoco.mjtObj.mjOBJ_JOINT, 'shoulder_elv_r')
            target_addr = int(self.model.jnt_qposadr[target_id]) if target_id >= 0 else None
            delta = float(abs(float(self.data.qpos[target_addr] - before[target_addr]))) if target_addr is not None else 0.0
            hand_delta = (float(math.dist(hand_offset_before, self.data.xpos[hand_id] - self.data.xpos[shoulder_body_id]))
                          if hand_offset_before is not None else None)
            self._last_action = "raise_right_arm_baseline"
            self._last_control_count = len(indices)
            self._apply_field_geometry()
            return {'executed': True, 'movement_observed': delta > 1e-9,
                    'execution_status': 'completed' if delta > 1e-9 else 'no_change',
                    'movement_delta': delta, 'target_joint': 'shoulder_elv_r', 'target_joint_delta': delta,
                    'hand_relative_displacement_m': hand_delta, 'control_count': len(indices), 'steps': 100,
                    'hand_world_position_m': self.data.xpos[hand_id].tolist() if hand_id >= 0 else None,
                    'message': 'Applied bounded right-arm DELT/SUPSP activation for 100 steps from the model init keyframe.'}
