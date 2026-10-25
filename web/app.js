(() => {
  const $ = id => document.getElementById(id);
  const speechUtils = window.RipwaveSpeech;
  const canvas = $('field');
  const ctx = canvas.getContext('2d');
  const state = {
    field: { amplitude: .35, frequency_hz: 1, radius: .28, active: false, revision: 0, last_intent: 'idle' },
    medium: null, mediumSelectedOrigin: null, mediumMapLayout: null,
    rms: 0, centroid: 0, micOn: false, speech: null, speechBusy: false,
    audioContext: null, analyser: null, source: null, stream: null, raf: null,
    started: performance.now(), model: null, renderUrl: null, renderBusy: false,
    renderedAt: 0, camera: { azimuth: 180, elevation: -8, distance: 3.2 },
    drag: null, pendingCommands: 0
  };

  function setFeedback(message, kind = 'ok') {
    $('feedback').textContent = message;
    $('feedbackIcon').textContent = kind === 'error' ? '!' : kind === 'warn' ? '–' : '✳';
    $('feedbackIcon').style.color = kind === 'error' ? 'var(--red)' : kind === 'warn' ? 'var(--amber)' : 'var(--cyan)';
    if ($('visualCue').checked) $('feedback').animate?.([{ opacity: .55 }, { opacity: 1 }], { duration: 220 });
  }

  function tone(success = true) {
    if (!$('audioCue').checked || !state.audioContext) return;
    const now = state.audioContext.currentTime;
    const osc = state.audioContext.createOscillator();
    const gain = state.audioContext.createGain();
    osc.type = 'sine'; osc.frequency.value = success ? 660 : 240;
    gain.gain.setValueAtTime(.0001, now);
    gain.gain.exponentialRampToValueAtTime(Math.max(.0002, Number($('volume').value)), now + .025);
    gain.gain.exponentialRampToValueAtTime(.0001, now + .18);
    osc.connect(gain); gain.connect(state.audioContext.destination); osc.start(now); osc.stop(now + .2);
  }

  async function sendTranscript(transcript, source = 'text', confidence = 1) {
    const text = String(transcript || '').trim();
    const stopRequest = /^(stop|stop walking|stop the walk|halt|pause)[.!?]*$/i.test(text);
    if (!text || (state.pendingCommands > 0 && !stopRequest)) return;
    $('transcriptText').textContent = text;
    $('interpretedIntent').textContent = 'Interpreting…';
    $('commandState').textContent = 'Submitting';
    state.pendingCommands++; $('send').disabled = true;
    setFeedback(`Submitting ${source} command for interpretation…`);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/event', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ schema_version: '1.0', source, transcript: text, confidence,
          acoustics: { rms_energy: state.rms, spectral_centroid_hz: state.centroid } }), signal: controller.signal
      });
      const data = await response.json().catch(() => ({}));
      const result = data.result;
      if (result?.intent) $('interpretedIntent').textContent = result.intent.replaceAll('_', ' ');
      else $('interpretedIntent').textContent = 'Unrecognized';
      if (!response.ok || !data.ok) {
        const message = result?.message || data.error || `Request failed (${response.status})`;
        $('commandState').textContent = result?.status || 'Failed';
        setFeedback(message, 'error'); tone(false);
      } else {
        state.field = data.field;
        if (data.medium) { state.medium = data.medium; drawMedium(data.medium); }
        const simulation = result.simulation || {};
        const status = simulation.execution_status || result.execution_status || 'failed';
        $('commandState').textContent = status.replaceAll('_', ' ');
        const measurement = simulation.movement_observed
          ? ` Measured shoulder change ${Number(simulation.movement_delta).toFixed(3)} rad; hand displacement ${Number(simulation.hand_relative_displacement_m).toFixed(3)} m.` : '';
        const kind = status === 'unsupported' || status === 'field_only' ? 'warn' : status === 'failed' || status === 'rejected' ? 'error' : 'ok';
        setFeedback(`${status.replaceAll('_', ' ')}: ${simulation.message || result.outcome || 'No outcome details returned.'}${measurement}`, kind);
        tone(kind === 'ok'); updateUI(); draw();
        $('renderBadge').textContent = `SIMULATION UPDATED · REQUESTING SNAPSHOT${state.renderedAt ? ` · LAST FRAME ${new Date(state.renderedAt).toLocaleTimeString()}` : ''}`;
        $('renderBadge').className = 'render-badge'; refreshRender();
      }
    } catch (error) {
      $('commandState').textContent = error.name === 'AbortError' ? 'Outcome unknown' : 'Failed';
      const message = error.name === 'AbortError'
        ? 'Command request timed out; the backend outcome is unconfirmed. Check the event log before retrying.'
        : `Command request failed: ${error.message}. Check the local runtime and try again.`;
      setFeedback(message, 'error');
      tone(false);
    } finally {
      clearTimeout(timeout);
      state.pendingCommands = Math.max(0, state.pendingCommands - 1); $('send').disabled = state.pendingCommands > 0;
      refreshLog();
    }
  }

  $('send').addEventListener('click', () => sendTranscript($('command').value));
  $('command').addEventListener('keydown', event => { if (event.key === 'Enter') sendTranscript($('command').value); });
  document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => {
    $('command').value = button.dataset.command;
    sendTranscript(button.dataset.command);
  }));
  $('volume').addEventListener('input', () => { $('volumeValue').textContent = `${Math.round(Number($('volume').value) / .25 * 100)}%`; });
  $('refresh').addEventListener('click', () => { connect(); loadModels(); refreshLog(); });
  $('clearHint').addEventListener('click', refreshLog);
  $('traceNav').addEventListener('click', () => $('trace').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }));
  $('recenter').addEventListener('click', () => { state.camera = { azimuth: 180, elevation: -8, distance: 3.2 }; refreshRender(); setFeedback('Simulation camera reset to its starting view.'); });
  $('worldDiagToggle').addEventListener('click', () => {
    const panel = $('worldDiagnostics');
    panel.hidden = !panel.hidden;
    $('worldDiagToggle').setAttribute('aria-pressed', String(!panel.hidden));
  });
  $('neighborMessages').addEventListener('change', async event => {
    const checkbox = event.currentTarget;
    checkbox.disabled = true;
    try {
      const response = await fetch('/api/field/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messaging_enabled: checkbox.checked }) });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Could not change neighbor signaling.');
      setFeedback(`Direct neighbor messages ${checkbox.checked ? 'enabled as a bounded supplement' : 'disabled; shared-field mode active'}.`);
      refreshMedium();
    } catch (error) {
      checkbox.checked = !checkbox.checked; setFeedback(error.message, 'error');
    } finally { checkbox.disabled = false; }
  });
  $('injectStressor').addEventListener('click', async () => {
    const blueprint = state.medium?.blueprint;
    if (!blueprint) { setFeedback('Field state is not available yet.', 'error'); return; }
    const origin = state.mediumSelectedOrigin || [Math.floor(blueprint.width / 2), Math.floor(blueprint.height / 2)];
    const button = $('injectStressor'); button.disabled = true;
    try {
      const response = await fetch('/api/stressor', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: (crypto.randomUUID?.() || `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`),
          type: $('stressorType').value, origin_cell: origin, intensity: Number($('stressorIntensity').value), duration: 200 }) });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || data.message || 'Stressor injection failed.');
      state.mediumSelectedOrigin = null;
      setFeedback(`Event ${data.result.event_id} entered the distributed field at cell ${data.result.origin_cell.join(', ')}.`);
      drawMedium(data.medium);
    } catch (error) { setFeedback(error.message, 'error'); }
    finally { button.disabled = false; }
  });
  $('mediumMap').addEventListener('click', event => {
    if (!state.medium) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const layout = state.mediumMapLayout || { x: 0, y: 0, width: rect.width, height: rect.height };
    const px = event.clientX - rect.left - layout.x, py = event.clientY - rect.top - layout.y;
    const x = Math.floor(px / layout.width * state.medium.blueprint.width);
    const y = Math.floor(py / layout.height * state.medium.blueprint.height);
    state.mediumSelectedOrigin = [Math.max(0, Math.min(state.medium.blueprint.width - 1, x)),
      Math.max(0, Math.min(state.medium.blueprint.height - 1, y))];
    $('injectStressor').textContent = `Inject at ${state.mediumSelectedOrigin.join(', ')}`;
    drawMedium(state.medium);
  });

  function updateUI() {
    const field = state.field;
    $('amp').textContent = Number(field.amplitude).toFixed(2);
    $('freq').innerHTML = `${Number(field.frequency_hz).toFixed(2)} <small>Hz</small>`;
    $('radius').innerHTML = `${Number(field.radius).toFixed(2)} <small>m</small>`;
    $('revision').textContent = field.revision;
    $('activeTag').textContent = field.active ? 'FIELD ACTIVE' : 'STANDBY';
    $('activeTag').classList.toggle('active', !!field.active);
    $('stageMode').textContent = field.active ? 'FIELD PROPAGATING' : 'FIELD DORMANT';
    document.querySelector('.live-dot').classList.toggle('on', !!field.active);
    $('lastIntent').textContent = (field.last_intent || 'idle').replaceAll('_', ' ');
    $('rms').textContent = state.rms.toFixed(2); $('rmsDetail').textContent = state.rms.toFixed(3);
    $('centroid').innerHTML = `${Math.round(state.centroid)} <small>Hz</small>`;
    $('acousticState').textContent = state.micOn ? 'LISTENING' : 'OFFLINE';
    $('ampBar').style.width = `${Math.min(100, Number(field.amplitude) * 100)}%`;
    $('freqBar').style.width = `${Math.min(100, Number(field.frequency_hz) / 3 * 100)}%`;
    $('radiusBar').style.width = `${Math.min(100, Number(field.radius) * 100)}%`;
    $('meterFill').style.width = `${Math.min(100, state.rms * 300)}%`;
    $('audioMeterFill').style.width = `${Math.min(100, state.rms * 300)}%`;
  }

  async function refreshLog() {
    try {
      const response = await fetch('/api/log', { cache: 'no-store' });
      const data = await response.json();
      const target = $('eventLog'); target.replaceChildren();
      $('eventCount').textContent = `${data.events.length} EVENTS`;
      if (!data.events.length) {
        const empty = document.createElement('div'); empty.className = 'empty-state';
        const title = document.createElement('strong'); title.textContent = 'No events yet';
        const detail = document.createElement('small'); detail.textContent = 'Commands, rejections, and outcomes appear here.';
        empty.append(title, detail); target.append(empty); return;
      }
      data.events.slice().reverse().slice(0, 12).forEach(row => {
        const item = document.createElement('div'); item.className = 'event';
        const time = document.createElement('time'); time.textContent = new Date(row.received_at * 1000).toLocaleTimeString();
        const label = document.createElement('strong');
        label.textContent = row.event?.intent ? row.event.intent.replaceAll('_', ' ') : (row.event?.transcript || 'Rejected command');
        const source = document.createElement('span'); source.className = 'source'; source.textContent = row.event?.source || 'unknown';
        const status = document.createElement('span'); status.className = 'result';
        status.textContent = (row.result?.status || row.result?.execution_status || 'recorded').replaceAll('_', ' ').toUpperCase();
        status.title = row.result?.message || row.result?.outcome || '';
        const details = document.createElement('small'); details.className = 'outcome';
        const simulation = row.result?.simulation || {};
        details.textContent = row.event?.transcript || row.result?.message || row.result?.outcome || simulation.message || '';
        if (simulation.movement_observed) details.textContent += ` · shoulder ${Number(simulation.movement_delta).toFixed(3)} rad · hand ${Number(simulation.hand_relative_displacement_m).toFixed(3)} m`;
        item.append(time, label, source, status, details); target.append(item);
      });
    } catch (error) { $('eventCount').textContent = 'LOG UNAVAILABLE'; }
  }

  async function connect() {
    try {
      const response = await fetch('/api/health', { cache: 'no-store' });
      const data = await response.json();
      $('statusDot').className = 'status-dot'; $('statusText').textContent = 'Local runtime connected';
      $('runtimeMeta').textContent = `Python ${data.python} · ${data.mujoco?.enabled ? `MuJoCo model loaded · ${data.mujoco.model_name}` : 'MuJoCo model unavailable'}`;
      $('runtimeMeta').title = data.python_executable || '';
      showModel(data.mujoco);
    } catch (error) {
      $('statusDot').className = 'status-dot offline'; $('statusText').textContent = 'Local runtime unavailable';
      $('runtimeMeta').textContent = 'Check that the local server is running, then refresh.';
      $('renderBadge').textContent = 'RUNTIME OFFLINE'; $('renderBadge').className = 'render-badge error';
    }
  }

  function showModel(model) {
    state.model = model;
    $('nbody').textContent = model?.enabled ? model.nbody : '—';
    $('njnt').textContent = model?.enabled ? model.njnt : '—';
    $('nu').textContent = model?.enabled ? model.nu : '—';
    $('modelBadge').textContent = model?.enabled ? 'MODEL LOADED' : 'NOT LOADED';
    $('modelBadge').classList.toggle('active', !!model?.enabled);
    $('mujocoStatus').className = `model-status${model?.enabled ? '' : ' bad'}`;
    $('mujocoStatus').textContent = model?.enabled
      ? `${model.model_name} · ${model.state_finite ? 'finite state' : 'state invalid'} · walking controller unavailable`
      : (model?.reason || 'MuJoCo model unavailable.');
    $('controlNotice').querySelector('p').textContent = model?.walking_controller_available
      ? 'A walking controller is available.'
      : `Right-arm baseline: ${model?.controls_enabled ? 'available' : 'unavailable'}. Walking is not supported; no validated gait controller or motion policy is installed.`;
    renderWorldDiagnostics(model?.world);
    if (model?.enabled) refreshRender();
    else { $('renderBadge').textContent = 'MODEL VIEW UNAVAILABLE'; $('renderBadge').className = 'render-badge error'; }
  }

  function renderWorldDiagnostics(world) {
    const panel = $('worldDiagnostics');
    if (!world) { panel.textContent = 'World diagnostics unavailable.'; return; }
    const point = v => v ? v.map(n => Number(n).toFixed(3)).join(', ') : 'unavailable';
    const min = world.body_bounds?.min_m, max = world.body_bounds?.max_m, size = world.body_bounds?.size_m;
    const feet = world.foot_positions || {};
    panel.textContent = [
      'WORLD DIAGNOSTICS · MUJOCO · METERS · +Z UP',
      `GROUND z ${Number(world.ground?.height_m ?? 0).toFixed(3)} m · contact ${world.ground?.contype}/${world.ground?.conaffinity}`,
      `BODY bounds ${point(min)} → ${point(max)} · size ${point(size)} · nominal height ${Number(world.reference_body_height_m || world.body_height_m).toFixed(3)} m`,
      `TORSO bounds ${point(world.torso_bounds?.min_m)} → ${point(world.torso_bounds?.max_m)} · pelvis bounds ${point(world.pelvis_bounds?.min_m)} → ${point(world.pelvis_bounds?.max_m)}`,
      `PELVIS ${point(world.pelvis_position_m)} · STERNUM ${point(world.sternum_position_m)} · upright error ${Number(world.pelvis_to_sternum_upright_error_deg || 0).toFixed(1)}°`,
      `FEET calcn R ${point(feet.calcn_r)} · L ${point(feet.calcn_l)}`,
      `TOES R ${point(feet.toes_r)} · L ${point(feet.toes_l)} · footprint clearance ${Number(world.foot_ground_clearance_m ?? 0).toFixed(3)} m`,
      `TARGET ${point(world.target?.position_m)} · r ${Number(world.target?.radius_m || 0).toFixed(3)} m · collision ${world.target?.contype}/${world.target?.conaffinity}`,
      `FIELD center ${point(world.field?.center_m)} · radius ${Number(world.field?.radius_m || 0).toFixed(3)} m (${(100 * Number(world.field?.radius_to_body_height || 0)).toFixed(1)}% height; ${Number(world.field?.radius_to_footprint_length || 0).toFixed(2)}× footprint length / ${Number(world.field?.radius_to_footprint_width || 0).toFixed(2)}× width) · outer ring ${Number(world.field?.outer_visual_radius_m || 0).toFixed(3)} m`,
      `CONTACTS ${world.contacts?.total ?? 0} total · ${world.contacts?.foot_floor_count ?? 0} foot/floor · ${world.contacts?.target?.length ?? 0} target`
    ].join('\n');
  }

  function drawMedium(snapshot) {
    if (!snapshot?.view || !snapshot?.blueprint) return;
    state.medium = snapshot;
    const canvas = $('mediumMap'), context = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (!rect.width || !rect.height) return;
    const pixelWidth = Math.max(1, Math.floor(rect.width * dpr)), pixelHeight = Math.max(1, Math.floor(rect.height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) { canvas.width = pixelWidth; canvas.height = pixelHeight; }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.fillStyle = '#111a20'; context.fillRect(0, 0, rect.width, rect.height);
    const view = snapshot.view, scale = Math.min(rect.width / view.width, rect.height / view.height);
    const drawWidth = view.width * scale, drawHeight = view.height * scale;
    const offsetX = (rect.width - drawWidth) * .5, offsetY = (rect.height - drawHeight) * .5;
    state.mediumMapLayout = { x: offsetX, y: offsetY, width: drawWidth, height: drawHeight };
    const taskHue = id => ((Number(id) * 137.508) % 360 + 360) % 360;
    const taskSizeX = drawWidth / view.width, taskSizeY = drawHeight / view.height;
    let index = 0;
    for (let y = 0; y < view.height; y++) for (let x = 0; x < view.width; x++, index++) {
      const cell = view.cells[index]; if (!cell) continue;
      const [role, signal, memory, rippleTask, memoryTask] = cell;
      const hue = rippleTask >= 0 ? taskHue(rippleTask) : memoryTask >= 0 ? taskHue(memoryTask) : 268;
      if (signal > .015 && rippleTask >= 0) {
        context.fillStyle = `hsla(${hue},78%,${Math.round(40 + signal * 30)}%,${Math.min(.96,.22 + signal * .78)})`;
      } else if (role === 0) {
        context.fillStyle = signal > .01 ? `hsla(${hue},55%,${Math.round(72 + signal * 18)}%,.96)` : '#dce8e6';
      } else if (role === 1) {
        context.fillStyle = `rgba(72,190,204,${Math.min(.88,.2 + signal * .7)})`;
      } else if (memory > .015) {
        context.fillStyle = `hsla(${hue},54%,${Math.round(28 + memory * 35)}%,${Math.min(.9,.18 + memory * .75)})`;
      } else {
        context.fillStyle = 'rgba(52,68,76,.42)';
      }
      context.fillRect(offsetX + x * taskSizeX, offsetY + y * taskSizeY,
        Math.ceil(taskSizeX * .9), Math.ceil(taskSizeY * .9));
    }
    snapshot.tasks.forEach(task => {
      const [cellX, cellY] = task.cell, x = offsetX + (cellX / view.stride + .5) * taskSizeX;
      const y = offsetY + (cellY / view.stride + .5) * taskSizeY, hue = taskHue(task.id);
      context.beginPath(); context.arc(x, y, Math.max(3, Math.min(6, scale * 2)), 0, Math.PI * 2);
      context.strokeStyle = `hsl(${hue},88%,72%)`; context.lineWidth = 1.5; context.stroke();
      context.beginPath(); context.arc(x, y, 1.5, 0, Math.PI * 2); context.fillStyle = `hsl(${hue},94%,82%)`; context.fill();
    });
    if (state.mediumSelectedOrigin) {
      const [x, y] = state.mediumSelectedOrigin;
      const px = offsetX + (x / view.stride + .5) * taskSizeX, py = offsetY + (y / view.stride + .5) * taskSizeY;
      context.beginPath(); context.arc(px, py, 5, 0, Math.PI * 2); context.strokeStyle = '#c3fa3b'; context.lineWidth = 2; context.stroke();
    }
    const d = snapshot.diagnostics, roles = d.role_counts, messages = d.messages, update = d.update;
    const active = snapshot.tasks.slice(0, 3).map(task => `#${task.id} ${task.type} · ${task.event_id.slice(0, 12)}`).join('\n');
    $('mediumStatus').textContent = messages.enabled ? 'FIELD + LOCAL' : 'FIELD ONLY';
    $('mediumStatus').classList.toggle('active', !!messages.enabled);
    $('neighborMessages').checked = !!messages.enabled;
    $('mediumStats').textContent = [
      `${d.cell_count.toLocaleString()} cells · ${d.active_task_count} tasks · ${d.active_stressor_count} stressors · ${d.active_ripple_count} ripples`,
      `CORE ${roles.CORE.toLocaleString()}  /  MID ${roles.MID.toLocaleString()}  /  SHELL ${roles.SHELL.toLocaleString()} · mean memory ${d.memory.mean_cell_memory.toFixed(3)}`,
      `NEIGHBOR sent ${messages.sent} · delivered ${messages.delivered} · expired ${messages.expired} · dropped ${messages.dropped} · pending ${messages.pending}`,
      `FIELD ${update.last_ms_per_tick.toFixed(2)} ms/tick · measured ${update.measured_hz.toFixed(1)} Hz · mean signal ${d.signal.mean_intensity.toFixed(3)}`,
      active || 'No active event identities'
    ].join('\n');
    $('injectStressor').textContent = state.mediumSelectedOrigin
      ? `Inject at ${state.mediumSelectedOrigin.join(', ')}` : 'Inject at center';
  }

  async function refreshMedium() {
    try {
      const response = await fetch('/api/field', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Field request failed (${response.status})`);
      drawMedium(await response.json());
    } catch (error) {
      $('mediumStatus').textContent = 'FIELD UNAVAILABLE';
      $('mediumStats').textContent = error.message;
    }
  }

  async function refreshRender() {
    if (!state.model?.enabled || state.renderBusy) return;
    state.renderBusy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const camera = state.camera;
      const query = new URLSearchParams({ azimuth: camera.azimuth, elevation: camera.elevation, distance: camera.distance, frame: Date.now() });
      const response = await fetch(`/api/render?${query}`, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) { const problem = await response.json().catch(() => ({})); throw new Error(problem.error || `Render failed (${response.status})`); }
      const url = URL.createObjectURL(await response.blob());
      const image = $('humanRender');
      image.onload = () => {
        if (state.renderUrl) URL.revokeObjectURL(state.renderUrl);
        state.renderUrl = url; state.renderedAt = Date.now();
        $('renderBadge').textContent = `MUJOCO SNAPSHOT · ${new Date(state.renderedAt).toLocaleTimeString()} · ${state.model.renderer?.last_render_ms ?? '…'} ms`;
        $('renderBadge').className = 'render-badge ready';
      };
      image.onerror = () => { URL.revokeObjectURL(url); $('renderBadge').textContent = 'MODEL FRAME COULD NOT BE DISPLAYED'; $('renderBadge').className = 'render-badge error'; };
      image.src = url;
    } catch (error) {
      const detail = error.name === 'AbortError' ? 'Render timed out' : error.message;
      const age = state.renderedAt ? ` · previous frame ${Math.round((Date.now() - state.renderedAt) / 1000)}s old` : '';
      $('renderBadge').textContent = `RENDER ERROR · ${detail}${age}`;
      $('renderBadge').className = 'render-badge error';
    } finally { clearTimeout(timeout); state.renderBusy = false; }
  }

  const stage = $('stage');
  stage.addEventListener('pointerdown', event => {
    if (!state.model?.enabled || event.target.closest('button')) return;
    state.drag = { x: event.clientX, y: event.clientY, id: event.pointerId };
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener('pointermove', event => {
    if (!state.drag || state.drag.id !== event.pointerId) return;
    const dx = event.clientX - state.drag.x, dy = event.clientY - state.drag.y;
    state.drag = { x: event.clientX, y: event.clientY, id: event.pointerId };
    state.camera.azimuth = (state.camera.azimuth + dx * .45 + 180) % 360 - 180;
    state.camera.elevation = Math.max(-35, Math.min(35, state.camera.elevation - dy * .35));
    refreshRender();
  });
  stage.addEventListener('pointerup', () => { state.drag = null; });
  stage.addEventListener('pointercancel', () => { state.drag = null; });
  stage.addEventListener('wheel', event => {
    if (!state.model?.enabled) return;
    event.preventDefault(); state.camera.distance = Math.max(1.6, Math.min(4.8, state.camera.distance + event.deltaY * .003)); refreshRender();
  }, { passive: false });
  stage.addEventListener('keydown', event => {
    const step = event.shiftKey ? 12 : 4;
    if (event.key === 'ArrowLeft') state.camera.azimuth -= step;
    else if (event.key === 'ArrowRight') state.camera.azimuth += step;
    else if (event.key === 'ArrowUp') state.camera.elevation = Math.min(35, state.camera.elevation + step);
    else if (event.key === 'ArrowDown') state.camera.elevation = Math.max(-35, state.camera.elevation - step);
    else if (event.key === '+' || event.key === '=') state.camera.distance = Math.max(1.6, state.camera.distance - .15);
    else if (event.key === '-') state.camera.distance = Math.min(4.8, state.camera.distance + .15);
    else return;
    event.preventDefault(); refreshRender();
  });

  async function loadModels() {
    const select = $('modelSelect');
    try {
      const response = await fetch('/api/models'); const data = await response.json(); select.replaceChildren();
      if (!data.models.length) { select.add(new Option('No XML files found in project', '')); return; }
      data.models.forEach(model => { const option = new Option(`${model.name} · ${(model.bytes / 1024).toFixed(0)} KB`, model.path); option.title = model.path; select.add(option); });
      if (state.model?.model_path) {
        const current = [...select.options].find(option => option.value === state.model.model_path);
        if (current) select.value = current.value;
      } else {
        const preferred = data.models.find(model => model.name.toLowerCase() === 'ms-human-700.xml');
        if (preferred) select.value = preferred.path;
      }
    } catch { select.replaceChildren(new Option('Could not scan model files', '')); }
  }

  $('loadModel').addEventListener('click', async () => {
    const path = $('modelSelect').value;
    if (!path) { setFeedback('No XML model found. Keep the extracted model assets inside this project folder.', 'error'); return; }
    const button = $('loadModel'); button.disabled = true; button.textContent = 'Loading…';
    $('mujocoStatus').textContent = 'Loading XML and resolving referenced assets…';
    try {
      const response = await fetch('/api/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
      const data = await response.json(); showModel(data.mujoco || { enabled: false, reason: data.error || 'Model load failed.' });
      if (!response.ok || !data.ok) throw new Error(data.mujoco?.reason || data.error || 'Model failed to load');
      setFeedback(`Loaded ${data.mujoco.model_name}. Walking controller unavailable.`);
    } catch (error) { $('mujocoStatus').className = 'model-status bad'; setFeedback(`Model load failed: ${error.message}`, 'error'); }
    finally { button.disabled = false; button.textContent = 'Load'; }
  });

  $('mic').addEventListener('click', async () => {
    if (state.micOn) { stopMic(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { $('micStatus').textContent = 'Microphone access is unavailable in this browser or origin; text commands still work.'; return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Web Audio API is unavailable');
      state.audioContext = new AudioContextClass(); await state.audioContext.resume();
      state.analyser = state.audioContext.createAnalyser(); state.analyser.fftSize = 2048; state.analyser.smoothingTimeConstant = .65;
      state.source = state.audioContext.createMediaStreamSource(stream); state.source.connect(state.analyser);
      state.stream = stream; state.micOn = true; $('mic').textContent = 'Disable';
      $('micStatus').textContent = 'Microphone active; audio analysis stays in this browser.';
      const timeData = new Float32Array(state.analyser.fftSize), frequencyData = new Uint8Array(state.analyser.frequencyBinCount);
      const tick = () => {
        if (!state.micOn) return;
        state.analyser.getFloatTimeDomainData(timeData);
        let sum = 0; for (const value of timeData) sum += value * value;
        state.rms = Math.sqrt(sum / timeData.length);
        state.analyser.getByteFrequencyData(frequencyData);
        let weighted = 0, total = 0;
        for (let i = 0; i < frequencyData.length; i++) { weighted += i * state.audioContext.sampleRate / state.analyser.fftSize * frequencyData[i]; total += frequencyData[i]; }
        state.centroid = total ? weighted / total : 0; updateUI(); draw(); state.raf = requestAnimationFrame(tick);
      };
      tick();
    } catch (error) {
      $('micStatus').textContent = `Microphone unavailable or permission denied: ${error.message}. Text commands still work.`;
    }
  });

  async function stopMic() {
    state.micOn = false;
    if (state.raf) cancelAnimationFrame(state.raf);
    state.stream?.getTracks().forEach(track => track.stop()); state.source?.disconnect();
    if (state.audioContext && state.audioContext.state !== 'closed') await state.audioContext.close().catch(() => {});
    state.raf = state.stream = state.source = state.analyser = state.audioContext = null;
    $('mic').textContent = 'Enable'; $('micStatus').textContent = 'Microphone is off · audio remains local'; updateUI();
  }

  $('speech').addEventListener('click', () => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      $('speechStatus').textContent = 'Speech recognition unsupported in this browser. Use the command box.';
      $('micStatus').textContent = 'Speech recognition unsupported; text commands remain available.';
      $('commandState').textContent = 'Voice unsupported'; return;
    }
    if (state.speech) { state.speech.stop(); $('speechLabel').textContent = 'Speak'; return; }
    if (state.pendingCommands > 0 || state.speechBusy) return;
    const recognition = new Recognition(); recognition.lang = navigator.language || 'en-US';
    recognition.interimResults = true; recognition.maxAlternatives = 1;
    state.speech = recognition; state.speechBusy = false;
    const handled = new Set();
    recognition.onstart = () => {
      $('speechLabel').textContent = 'Listening…'; $('speechStatus').textContent = 'Listening for one command…';
      $('commandState').textContent = 'Listening'; setFeedback('Listening. Speak one command, then wait for its result.');
    };
    recognition.onspeechstart = () => { $('speechStatus').textContent = 'Speech detected; waiting for a final transcript…'; $('commandState').textContent = 'Speech detected'; };
    recognition.onresult = event => {
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (!result.isFinal) $('transcriptText').textContent = `${result[0].transcript.trim()} …`;
      }
      const finalResult = speechUtils.consumeFinalResults(event.results, event.resultIndex, handled);
      const finalTranscript = finalResult.transcript, confidence = finalResult.confidence;
      if (!finalTranscript || state.speechBusy) return;
      state.speechBusy = true; $('command').value = finalTranscript;
      $('transcriptText').textContent = finalTranscript; $('speechStatus').textContent = 'Transcript received; interpreting command…';
      if (confidence < .55 && finalTranscript.toLowerCase().replace(/[.!?]+$/,'').trim() !== 'stop') {
        $('interpretedIntent').textContent = 'Needs confirmation'; $('commandState').textContent = 'Low confidence';
        setFeedback('Speech confidence was low. Review the transcript and submit it with the text command button.', 'warn');
        return;
      }
      $('speechStatus').textContent = 'Transcript received; command submitted once.';
      sendTranscript(finalTranscript, 'voice', confidence);
    };
    recognition.onnomatch = () => { $('speechStatus').textContent = 'Speech detected, but no transcript was recognized. Try again or type the command.'; $('commandState').textContent = 'No match'; };
    recognition.onerror = event => {
      const detail = event.error === 'not-allowed' || event.error === 'service-not-allowed' ? 'Microphone permission denied.' : `Recognition error: ${event.error}.`;
      $('speechStatus').textContent = `${detail} Text commands remain available.`; $('commandState').textContent = 'Voice error';
      setFeedback(detail, 'error');
    };
    recognition.onend = () => {
      $('speechLabel').textContent = 'Speak';
      $('speechStatus').textContent = state.speechBusy ? 'Recognition ended; command processing continues.' : 'Recognition ended. Ready to listen again.';
      state.speech = null;
    };
    try { recognition.start(); }
    catch (error) { state.speech = null; $('speechLabel').textContent = 'Speak'; $('speechStatus').textContent = `Could not start speech recognition: ${error.message}`; $('commandState').textContent = 'Voice unavailable'; }
  });

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.floor(rect.width * dpr)); canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); draw();
  }

  function draw() {
    // Ripple geometry is drawn by MuJoCo in world coordinates; the canvas is
    // intentionally transparent and is retained only for layout compatibility.
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  }

  window.addEventListener('resize', resizeCanvas); resizeCanvas(); connect(); loadModels(); refreshLog(); refreshMedium(); updateUI(); draw();
  setInterval(async () => {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' }); const data = await response.json();
      state.field = data.field; if (data.medium) drawMedium(data.medium); showModel(data.mujoco); updateUI(); if (!state.micOn) draw();
    } catch (error) {
      $('statusDot').className = 'status-dot offline'; $('statusText').textContent = 'Runtime status unavailable';
    }
  }, 2500);
  setInterval(refreshMedium, 1000);
  setInterval(refreshRender, 500);
})();
