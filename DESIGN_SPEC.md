# Ripwave Become Human spatial studio design specification

## Product thesis

The studio turns a text or browser-recognized speech intent into an inspectable field update and, only where a verified mapping exists, a measurable MuJoCo action. The model render is evidence of the simulation state, not an animation that stands in for it.

## How the two frameworks shape this implementation

**BEAST** supplies the whole-system decision method: clarify the user job, map the user-visible lifecycle to the backend contract, make state ownership explicit, design failure states, then verify the end-to-end slice. This is why the API now identifies the server interpreter, preserves validation errors, returns the controller outcome with each event, and displays `received → validated → field updated → simulation outcome` rather than treating every accepted HTTP response as movement. The visual hierarchy puts the command and spatial state first, with model diagnostics and the trace available as supporting evidence. Reduced-motion rules and DOM labels keep meaning available outside animated pixels.

**FORMA × STORYTELLER** governs the scene translation. The narrative turn is command → consequence → observed state. A ripple is shown only when the coordination field changes; model motion comes only from MuJoCo state; unsupported gait has no mannequin animation. FORMA's medium-selection rule favors the smallest defensible rendering path: MuJoCo already loads this model's MJCF, STL mesh assets, transforms, materials, and simulation state, so its native offscreen renderer avoids a second MJCF importer and a geometry conversion that could drift from the simulation. The scene uses the model's real geometry and its authored camera framing, with a bounded orbit camera and a quiet screen-space composition. Motion is sampled from the simulator and is never invented for acknowledgement.

These are related but distinct roles: BEAST governs product coherence, state, trust, and verification; FORMA governs what the scene means and how the chosen medium renders it.

## Scene and rendering contract

- **Renderer:** Python MuJoCo 3.17 offscreen OpenGL; 800 × 600 RGB frames encoded as PNG with the standard library and polled by the browser twice per second. No Three.js, MJCF-to-glTF conversion, or new rendering dependency is introduced.
- **Source geometry:** `MS-Human-700.xml` plus its relative includes and `Geometry/*.stl` assets, loaded by MuJoCo. The scene contains anatomical bone meshes and the model's translucent capsule geoms. The 700 tendon paths and visual wrap geoms are hidden to preserve the silhouette and avoid their measured rendering cost. This is actual simulation model geometry, not photoreal skin or a Blender character.
- **State authority:** Python/MuJoCo owns model load state, controls, and `qpos`; the browser owns only the camera view, microphone features, and transient presentation. A right-arm event reports the measured maximum absolute `qpos` delta after 20 steps. A render depicts the latest simulator state.
- **Camera:** MuJoCo free camera centered near `(0, 0, 1 m)`, with azimuth, elevation, and distance clamped by the backend. The initial view is a front view. Drag and wheel orbit/zoom; arrow keys and +/- work when the stage is focused; recenter restores the starting framing. Model coordinates follow the MJCF's metre scale and Z-up world.
- **Field visualization:** the existing projected rings and particles encode field radius, amplitude, and active state. They are a coordination visualization, not model geometry or a physical force field. The human render is composited over this view; no body motion is driven by field animation.
- **Motion:** the view refreshes only to show current backend state and to acknowledge user camera input. It does not run a gait loop. `walking` is an intent-only trace with no human movement.
- **License:** the repository's MS-Human-700 `LICENSE` is Apache License 2.0. Retain the source license and attribution when redistributing the model or derivatives.

## Command and failure states

`received → recognized → validated → field_updated → [completed | completed_no_motion_observed | stopped | field_only | unsupported | unavailable]`

- Expand, contract, start ripple, and audio modulation update the coordination field only.
- Raise right arm applies the existing bounded `DELT*_r` / `SUPSP*_r` actuator baseline. “Completed” means a finite state and a measured `qpos` delta; the status text includes the delta.
- Neutral/reset resets MuJoCo when loaded and always resets the field. Without a loaded model, the UI reports that only the field reset.
- Stop clears actuator controls when a model is loaded and stops the ripple field. It does not claim that the model returned to neutral.
- Walking is accepted as a recorded intent but explicitly marked unsupported; it leaves the field and human pose unchanged.
- Invalid payloads and low-confidence recognition are rejected; model/renderer unavailability is shown as degraded status with the backend reason.

## Runtime and performance evidence

The server reports `sys.executable` and Python version so an environment mismatch is visible. The default model is loaded at startup when MuJoCo is importable. The renderer is lazy: the first OpenGL context initialization took about 6.3 seconds in a local worker-thread check; after warm-up, HTTP frames at 800 x 600 measured 67 to 224 ms. The browser polls at 2 Hz, so this is a responsive snapshot view rather than a continuous real-time viewport. The source XML requests a 3840 x 2160 buffer; the studio bounds it to 800 x 600 with shadows and tendon overlays disabled. These measurements are specific to this Windows machine. If OpenGL initialization fails, model simulation stays available and the view reports the renderer error.

## Known limits

The snapshot renderer is not a continuous 60 FPS WebGL viewport. Full photoreal human surface appearance, camera-perfect skin shading, and validated walking require separate source assets or a controller/policy. No physical actuator is connected. This is a simulation and live visualization, not a validated digital twin or safety system.
