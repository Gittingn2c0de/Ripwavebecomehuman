"""End-to-end setup diagnostic for Ripwave Become Human."""
from pathlib import Path
import sys
import math
ROOT = Path(__file__).resolve().parent
print(f'Python: {sys.version.split()[0]} ({sys.executable})')
mu = None
try:
    import mujoco as mu
    print(f'MuJoCo: installed ({mu.__version__})')
except Exception as exc:
    print(f'MuJoCo: unavailable ({exc})')
models = sorted(p for p in ROOT.rglob('*.xml') if not any(x in {'.git', '__pycache__', '.venv', 'venv', 'node_modules'} for x in p.parts))
print(f'XML files discovered under project: {len(models)}')
for p in models[:30]:
    print(' -', p.relative_to(ROOT))
if len(models) > 30:
    print(f' ... and {len(models)-30} more')
main = ROOT / 'MS-Human-700-main' / 'MS-Human-700.xml'
if not main.is_file():
    print(f'ERROR: expected full-body model is missing: {main}')
    raise SystemExit(1)
if mu is None:
    raise SystemExit('Install MuJoCo in this interpreter: python -m pip install mujoco')
try:
    model = mu.MjModel.from_xml_path(str(main))
    data = mu.MjData(model)
    mu.mj_step(model, data)
    finite = all(math.isfinite(float(x)) for x in data.qpos)
    print(f'Full-body model: loaded ({main.relative_to(ROOT)})')
    print(f'  bodies={model.nbody} joints={model.njnt} actuators={model.nu} nq={model.nq} nv={model.nv} timestep={model.opt.timestep}')
    print(f'  one simulation step: {"finite" if finite else "NON-FINITE"}')
    if not finite:
        raise SystemExit(1)
except Exception as exc:
    print(f'ERROR: full-body model failed to load or step: {exc}')
    raise SystemExit(1)
