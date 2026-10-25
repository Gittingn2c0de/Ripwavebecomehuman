# Ripwave Become Human â€” Spatial Control Studio

Ripwave Become Human is a local-first command and simulation interface. Text and optional browser speech recognition produce validated commands; the deterministic field engine updates ripple state; the MuJoCo adapter executes only explicitly bounded/verified actions. The stage shows snapshots of the loaded model's actual MuJoCo geometry, not a browser-side character animation.

## Run this workspace

On this Windows workstation, use the Python installation that has MuJoCo 3.17.0. The project `.venv` may not contain MuJoCo. In PowerShell from this directory:

```powershell
$env:RWH_PORT = '8766'
& 'C:\Users\maela\AppData\Local\Programs\Python\Python313\python.exe' app.py
```

Open <http://127.0.0.1:8766>. Port 8765 is used by an older local instance on this workstation. To install dependencies in a new environment, run `python -m pip install -r requirements.txt`; then use that same interpreter for `python verify_setup.py` and `python app.py`.

`/api/health` reports the actual Python executable, MuJoCo/model load state, actuator and joint counts, and renderer readiness. The app loads `MS-Human-700-main/MS-Human-700.xml` at startup when present. Keep the model's relative assets and XML files together inside this project.

## Commands

Enter a phrase and select **Run command**, or select **Speak** where the browser supports the Web Speech API. The transcript and interpreted intent are shown separately. Exact phrase matching ignores case and trailing punctuation; it does not search arbitrary substrings.

| Phrase | Result |
| --- | --- |
| `raise your right arm` | Runs the bounded right-arm baseline if verified actuators are available. |
| `lower your right arm` | Recognized; reported unsupported because no validated lowering controller is mapped. |
| `reach forward` | Recognized; reported unsupported because no validated reach controller is mapped. |
| `start walking` | Recognized; explicitly unsupported until a validated gait controller is installed. |
| `stop walking` / `stop` | Zeros all MuJoCo controls and stops the field. |
| `return to neutral` / `reset` | Restores the model's authored initial keyframe and resets field state. |
| `expand`, `contract`, `start ripple` | Update the Ripwave-inspired field only; they do not move the human model. |

Recognition status distinguishes listening, transcript received, low confidence, unsupported browser, permission failure, request failure, and execution result. A final speech result is submitted once; if browser confidence is low, review and submit the visible transcript using the text control. **Speak** and the separate microphone-analysis control use browser microphone access. Microphone analysis (RMS and spectral centroid) is computed in the page and is not uploaded to this server. Browser speech recognition is provided by the browser and its configured recognition service; this application cannot guarantee that the browser keeps recognition audio on-device. `localhost` and `127.0.0.1` are usually trusted origins for microphone permissions, but browser/device permission is still required. Text commands remain available if voice is blocked or unsupported.

## Simulation and rendering

The default full-body model has 81 bodies, 85 joints, and 700 muscle actuators in the current MuJoCo runtime. The right-arm baseline selects candidate DELT/SUPSP right-side actuators from the loaded model, clamps controls to the model's XML ranges, advances 100 simulation steps, and reports measured shoulder and hand-relative changes. This is a bounded baseline, not a trained pose controller. `stop` zeros controls; `neutral` and `reset` use the authored `init` keyframe.

The MuJoCo offscreen renderer serves PNG snapshots polled by the browser at about 2 Hz. The time on the view badge identifies when the displayed frame arrived. Orbit and zoom issue new snapshot requests. This is not a continuous 60 FPS viewport and the meshes are not photoreal skin.

### World coordinates and scene additions

The runtime convention is meters, +Z up, +X forward, and +Y toward the model's left; the authored ground plane is z=0 and gravity is (0, 0, -9.81) m/sÂ². The original `MS-Human-700.xml` and its assets remain unchanged. At load, `MjSpec` compiles a runtime scene derived from that XML and adds nine visual-only ripple cylinders, 42 non-colliding world-grid strips (0.25 m spacing across a 5 m square), plus a static/mocap target sphere. Runtime geom count changes from 349 to 401 while nq/nv/nu stay 85/85/700. The grid is part of the MuJoCo scene, aligned to the z=0 floor, and has no collision effect. The viewport uses a full-frame MuJoCo render with restrained shadows. The target center is (0.65, 0, 0.05) m with radius 0.05 m and collision masks 1/1, so its surface is tangent to the floor plane. MuJoCo omits static-static floor contacts, but the target has been verified to produce contacts with the dynamic pelvis when overlapped; its contact is not fabricated. The field rings have collision masks 0/0 and are centered on the measured live collidable-foot footprint. At the authored initial pose, rendered-body bounds are 0.377 Ã— 0.462 Ã— 1.767 m; the active foot collision footprint is 0.281 Ã— 0.294 m, with its lowest geometry 0.003399 m above the floor. The default 0.28 m field radius is 15.8% of nominal body height and 1.00Ã— footprint length / 0.95Ã— footprint width; the outer displayed ring is 0.465 m from center. **World data** shows ground, body bounds, foot points, target, field radius/scales, and contacts. The character, floor, target, and ripples are rendered from the same MuJoCo state; no browser-side character is used.

At the authored initial keyframe, pelvis position is (0, 0, 0.950) m and sternum position is (-0.007, 0, 1.418) m, with a 0.9Â° sternum-axis error from vertical. The pelvis geoms span about 0.221 Ã— 0.353 Ã— 0.281 m; the pelvis/sacrum/lumbar/sternum/rib trunk envelope spans 0.300 Ã— 0.417 Ã— 0.722 m. Initial active foot geoms have collision enabled (contype/conaffinity 1/1), but their minimum is 3.399 mm above the floor, so MuJoCo reports zero contacts at t=0. No stance correction or teleport is applied. With all controls left at zero, actual foot-floor contacts begin by 0.1 s (10 contacts; maximum contact penetration 2.62 mm), then the body loses upright stability: at 0.5 s the pelvis is at z=0.684 m and sternum-axis error is 14Â°; at 1 s it is 123Â° and the model is falling/collapsed; at 2 s the body bounds height is 0.599 m and the sternum-axis error is 116Â°. This is a passive zero-control stability result, not a walking test. The visible initial foot clearance, without an active controller to balance and maintain the authored pose, explains why a neutral frozen keyframe cannot be described as stable stance.

The existing authored pose is not contact-supported at exactly t=0: the lowest collidable foot geometry is 3.4 mm above the floor (the toe-specific collision geoms are 9.1 mm above it), so the initial contact count is zero. The foot-bone geoms have collision masks 0/0; the active foot contacts are the skin geoms with masks 1/1. With all muscle controls at zero, foot/floor contacts appear by 0.1 s, but this is not a stable stance: the pelvis drops from z=0.95 m to 0.58 m by 1.0 s and the pelvis-to-sternum axis is inverted by about 123 degrees. At 2.0 s the pelvis is near z=0.50 m and the skull also contacts the floor. No initial pose correction or teleport is applied.

### Walking support

Walking is **not available**. Upstream `msgym` publishes a full-body imitation environment, a `walking_gait.npz` reference trajectory, DynSyn-SAC scripts, and a 214 MB full-body checkpoint archive. None of the environment package, reference trajectory, or policy artifacts is installed locally. The release asset's internal file list could not be verified without retrieving the archive, so this project has not downloaded it. A compatible full-body checkpoint still needs validation against the exact model revision, expected observation/action ordering, custom policy code, `MuscleNormWrapper`, and saved `VecNormalize` statistics. The app continues to report walking unsupported; it does not animate a fake gait.

## Verification

Use the MuJoCo-enabled interpreter:

```powershell
& 'C:\Users\maela\AppData\Local\Programs\Python\Python313\python.exe' -m unittest discover -s tests -v
& 'C:\Users\maela\AppData\Local\Programs\Python\Python313\python.exe' verify_setup.py
```

The setup check loads the full-body XML and referenced assets, reports model dimensions, and performs a finite simulation step. The command log includes rejected and unsupported requests as well as successful outcomes.


## Interface system

The studio uses a clinical paper surface around a deep blue-black simulation instrument. Bahnschrift/Segoe display type establishes headings, system sans remains readable for controls, and Cascadia Code labels state and measurements. Spacing follows a 4 px base rhythm; controls share focus, hover, pressed, and reduced-motion behavior. Lime marks the active field/action, teal indicates interaction, blue is reserved for measured data, and red is reserved for stop/error states. MuJoCo renders the only character and world; the browser stage scales that snapshot as one full-frame image.

## Distributed field prototype

The local field is a deterministic, bounded 2D structure-of-arrays medium in `ripwave_human/medium.py`, separate from the MuJoCo model and browser renderer. Its default blueprint is 96 × 96 cells at 0.05 m per cell (9,216 cells); the validated hard ceiling is 65,536 cells. The field uses a centered XY origin in meters, CORE/MID/SHELL task-relative roles, bounded ripple identities, selective shell memory, and per-tick diagnostics. The browser map visualizes a downsampled snapshot of the live field arrays, not a second simulation.

`POST /api/field/blueprint` initializes/reinitializes a field and can add up to 32 stressors. `POST /api/stressor` injects an identified, typed, bounded-duration task stressor. `POST /api/field/step` advances deterministic test ticks. `GET /api/field` exposes a rendered snapshot and live diagnostics; `GET /api/field/egress` reports the strongest eligible field signal. Egress is observational: `consumed` is false and there is no controller attached to it.

The field is primary. Optional direct neighbor messages are disabled by default and can be enabled with `POST /api/field/config`; when enabled they are one-hop/cardinal, carry task identity and bounded TTL, and enforce queue, per-tick send/delivery, deduplication, and expiry limits. This adds an explicit bounded signaling path; the upstream demo itself is field-first but its MID role reads adjacent CORE task load directly, rather than implementing queued message envelopes. See the [upstream demo](https://github.com/Gittingn2c0de/Ripwave/blob/main/demos/ripple_v0.1.html), [architecture](https://github.com/Gittingn2c0de/Ripwave/blob/main/ARCHITECTURE.md), and [extension notes](https://github.com/Gittingn2c0de/Ripwave/blob/main/docs/extending.md). The local shell-memory threshold is 0.03 because the upstream demo's 0.12 threshold is unreachable at its documented shell radius/decay parameters (maximum spatial decay is about 0.106 before time decay); the implemented ripple equation remains unchanged.

Humanoid ingress is evidence-gated: the right-arm path reports measured hand world position/displacement, and simulation stepping compares actual MuJoCo body positions. Only measured displacement above 0.1 mm injects a `humanoid_motion` event. Command text does not fabricate movement. Walking remains unsupported, and field egress currently does not actuate the human. No LLM runs per cell or tick.

Resource bounds include 256 ripples, 32 active tasks/stressors, 65,536 memory traces globally (four per cell), 2,048 recent event IDs, 8,192 queued messages, and at most 2,304 cells per UI snapshot. The background clock targets 20 Hz and drops missed slots rather than accumulating catch-up work. These limits bound allocations; actual update rate depends on workload and host hardware and is reported in `/api/health` diagnostics.
