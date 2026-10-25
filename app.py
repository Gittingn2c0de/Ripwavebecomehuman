#!/usr/bin/env python3
"""Local-first Ripwave Become Human app server."""
from __future__ import annotations
import json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from ripwave_human.engine import RippleEngine, validate_event
from ripwave_human.medium import DistributedMedium
from ripwave_human.mujoco_adapter import MuJoCoAdapter

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('RWH_HOST', '127.0.0.1')
PORT = int(os.environ.get('RWH_PORT', '8765'))
engine = RippleEngine()
medium = DistributedMedium()
configured_xml = os.environ.get('RWH_MUJOCO_XML')
mujoco = MuJoCoAdapter(configured_xml, ROOT)
if not configured_xml:
    default_model = ROOT / 'MS-Human-700-main' / 'MS-Human-700.xml'
    if default_model.is_file():
        mujoco.load(str(default_model))
mujoco.set_field(engine.snapshot())
events, events_lock = [], threading.Lock()
render_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mujoco-render')

class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else (body.encode('utf-8') if isinstance(body, str) else json.dumps(body).encode('utf-8'))
        self.send_response(status); self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 2 or length > 16384: raise ValueError('Request body must be between 2 and 16384 bytes.')
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict): raise ValueError('Request body must be a JSON object.')
        return payload

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            sys = __import__('sys')
            self._send(200, {'ok': True, 'service': 'ripwave-human', 'mujoco': mujoco.status(),
                             'medium': medium.diagnostics(),
                             'python': sys.version.split()[0], 'python_executable': sys.executable})
        elif path == '/api/render':
            try:
                query = parse_qs(urlparse(self.path).query)
                number = lambda key, default: float(query.get(key, [default])[0])
                frame = render_worker.submit(mujoco.render_png, number('azimuth', 180),
                                             number('elevation', -8), number('distance', 3.2)).result()
                self._send(200, frame, 'image/png')
            except ValueError as exc:
                self._send(400, {'ok': False, 'error': str(exc)})
            except RuntimeError as exc:
                self._send(503, {'ok': False, 'error': str(exc), 'mujoco': mujoco.status()})
        elif path == '/api/state':
            self._send(200, {'field': engine.snapshot(), 'medium': medium.snapshot(),
                             'mujoco': mujoco.status()})
        elif path == '/api/field': self._send(200, medium.snapshot())
        elif path == '/api/field/egress': self._send(200, medium.outbound_to_humanoid())
        elif path == '/api/models': self._send(200, {'models': mujoco.discover()})
        elif path == '/api/log':
            with events_lock: self._send(200, {'events': list(events[-100:])})
        elif path == '/' or path == '/index.html': self._send(200, (ROOT/'web'/'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/app.js': self._send(200, (ROOT/'web'/'app.js').read_bytes(), 'text/javascript; charset=utf-8')
        elif path == '/spatial-workspace.js': self._send(200, (ROOT/'web'/'spatial-workspace.js').read_bytes(), 'text/javascript; charset=utf-8')
        elif path == '/speech.js': self._send(200, (ROOT/'web'/'speech.js').read_bytes(), 'text/javascript; charset=utf-8')
        elif path == '/style.css': self._send(200, (ROOT/'web'/'style.css').read_bytes(), 'text/css; charset=utf-8')
        elif path == '/favicon.ico': self.send_response(204); self.end_headers()
        else: self._send(404, {'error':'not_found'})

    def do_POST(self):
        global mujoco
        path = urlparse(self.path).path
        event = None
        payload = {}
        try:
            payload = self._body()
            if path == '/api/event':
                event = validate_event(payload)
                simulation = mujoco.execute_intent(event['intent'])
                result = engine.apply(event)
                mujoco.set_field(engine.snapshot())
                if event['intent'] in {'stop', 'neutral', 'reset'}:
                    medium.stop()
                else:
                    medium.inject_stressor(event_id=event['event_id'],
                                           stressor_type=event['intent'], kind='command',
                                           intensity=max(0.08, min(1.0, event['confidence'])),
                                           duration=200)
                humanoid_ingress = None
                if simulation.get('movement_observed') and simulation.get('hand_world_position_m'):
                    humanoid_ingress = medium.inject_humanoid_motion(
                        event['event_id'] + ':mujoco-motion',
                        simulation['hand_world_position_m'][:2],
                        simulation.get('hand_relative_displacement_m') or 0.0,
                        source='measured_mujoco_raise_right_arm')
                result['simulation'] = simulation
                result['transcript'] = event.get('transcript', '')
                result['confidence'] = event['confidence']
                result['execution_status'] = simulation.get('execution_status', 'failed')
                result['status'] = result['execution_status']
                result['outcome'] = simulation.get('message', '')
                result['lifecycle'] = ['received', 'recognized', 'validated', 'field_updated',
                                       simulation.get('execution_status', 'unavailable')]
                result['humanoid_field_ingress'] = humanoid_ingress
                record = {'received_at': time.time(), 'event': event, 'result': result}
                with events_lock: events.append(record); del events[:-500]
                self._send(200, {'ok': True, 'result': result, 'field': engine.snapshot(),
                                 'medium': medium.snapshot(), 'mujoco': mujoco.status()})
            elif path == '/api/field/blueprint':
                snapshot = medium.configure(payload)
                self._send(200, {'ok': True, 'medium': snapshot})
            elif path == '/api/field/config':
                if 'messaging_enabled' not in payload:
                    raise ValueError('messaging_enabled boolean is required.')
                diagnostics = medium.set_messaging(payload['messaging_enabled'])
                self._send(200, {'ok': True, 'diagnostics': diagnostics})
            elif path == '/api/field/step':
                diagnostics = medium.advance(payload.get('steps', 1))
                self._send(200, {'ok': True, 'diagnostics': diagnostics, 'medium': medium.snapshot()})
            elif path == '/api/stressor':
                stressor_type = payload.get('type', payload.get('stressor_type', 'disturbance'))
                result = medium.inject_stressor(
                    event_id=payload.get('event_id'), stressor_type=stressor_type,
                    origin_cell=payload.get('origin_cell'), origin_m=payload.get('origin_m'),
                    intensity=payload.get('intensity', 0.6), duration=payload.get('duration', 200))
                self._send(200, {'ok': True, 'result': result, 'medium': medium.snapshot()})
            elif path == '/api/model':
                rel = payload.get('path')
                if not isinstance(rel, str) or not rel.strip(): raise ValueError('Select a model XML first.')
                # The OpenGL renderer is thread-affine; reload on its owner worker
                # so closing/recreating the context stays on the same thread.
                result = render_worker.submit(mujoco.load, rel).result()
                mujoco.set_field(engine.snapshot())
                self._send(200 if result.get('enabled') else 422, {'ok': result.get('enabled', False), 'mujoco': result})
            elif path == '/api/sim/step':
                before = None
                if mujoco.model is not None:
                    before = mujoco.data.xpos.copy()
                status = mujoco.step(payload.get('steps', 1))
                measured_motion = None
                if before is not None:
                    import numpy as np
                    displacement = np.linalg.norm(mujoco.data.xpos - before, axis=1)
                    max_displacement = float(displacement[1:].max()) if len(displacement) > 1 else 0.0
                    if max_displacement > 0.0001:
                        world = status.get('world') or {}
                        origin = world.get('sternum_position_m') or [0.0, 0.0, 0.0]
                        measured_motion = medium.inject_humanoid_motion(
                            f"simstep-{status.get('time', 0):.6f}", origin[:2], max_displacement,
                            source='measured_mujoco_sim_step')
                self._send(200, {'ok': True, 'mujoco': status, 'measured_motion': measured_motion,
                                 'medium': medium.snapshot()})
            else: self._send(404, {'error':'not_found'})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            message = str(exc)
            if path == '/api/event':
                rejected = {'accepted': False, 'status': 'rejected', 'execution_status': 'rejected',
                            'message': message, 'transcript': payload.get('transcript', payload.get('text', ''))}
                record = {'received_at': time.time(), 'event': {'source': payload.get('source', 'text'),
                          'transcript': rejected['transcript'], 'intent': payload.get('intent')}, 'result': rejected}
                with events_lock: events.append(record); del events[:-500]
                self._send(400, {'ok': False, 'error': message, 'result': rejected})
            else: self._send(400, {'ok':False, 'error':message})
        except RuntimeError as exc:
            message = str(exc)
            if path == '/api/event':
                failed = {'command_id': event.get('event_id') if event else None, 'accepted': bool(event),
                          'status': 'failed', 'execution_status': 'failed', 'intent': event.get('intent') if event else None,
                          'transcript': event.get('transcript', '') if event else payload.get('transcript', ''), 'message': message}
                record = {'received_at': time.time(), 'event': event or {'source': payload.get('source', 'text'),
                          'transcript': failed['transcript'], 'intent': failed['intent']}, 'result': failed}
                with events_lock: events.append(record); del events[:-500]
                self._send(500, {'ok':False, 'error':message, 'result':failed, 'mujoco':mujoco.status()})
            else: self._send(422, {'ok':False, 'error':message, 'mujoco':mujoco.status()})
        except Exception as exc:
            message = f'internal_error: {exc}'
            if path == '/api/event':
                failed = {'command_id': event.get('event_id') if event else None, 'accepted': bool(event),
                          'status': 'failed', 'execution_status': 'failed', 'intent': event.get('intent') if event else None,
                          'transcript': event.get('transcript', '') if event else payload.get('transcript', ''), 'message': message}
                record = {'received_at': time.time(), 'event': event or {'source': payload.get('source', 'text'),
                          'transcript': failed['transcript'], 'intent': failed['intent']}, 'result': failed}
                with events_lock: events.append(record); del events[:-500]
                self._send(500, {'ok':False, 'error':message, 'result':failed})
            else: self._send(500, {'ok':False, 'error':message})

    def log_message(self, fmt, *args): print('[http] ' + fmt % args)


def _run_medium_clock():
    """Advance the local medium independently of image rendering and HTTP polling."""
    deadline = time.monotonic()
    while True:
        medium.advance(1)
        deadline += 0.05
        delay = deadline - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            # Drop missed clock slots instead of running an unbounded catch-up loop.
            deadline = time.monotonic()

if __name__ == '__main__':
    print(f'Ripwave Become Human listening on http://{HOST}:{PORT}')
    status = mujoco.status()
    summary = {key: status.get(key) for key in ('enabled', 'reason', 'model_name', 'nbody', 'njnt', 'nu', 'timestep')}
    print('MuJoCo:', summary)
    threading.Thread(target=_run_medium_clock, name='ripwave-medium', daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
