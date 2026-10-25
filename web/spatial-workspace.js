(() => {
  'use strict';

  const workspace = document.getElementById('spatialWorkspace');
  const threadLayer = document.getElementById('spatialThreadLayer');
  if (!workspace || !threadLayer) return;

  const nodeElements = [...workspace.querySelectorAll('[data-spatial-node]')];
  const initial = {
    core: { x: 0, y: 0 },
    medium: { x: 0, y: 0 },
    input: { x: 0, y: 0 },
    simulation: { x: 0, y: 0 },
    trace: { x: 0, y: 0 }
  };
  const layoutKey = 'ripwave-is-human-spatial-layout';
  let savedLayout = {};
  try {
    savedLayout = JSON.parse(localStorage.getItem(layoutKey) || '{}');
  } catch (error) {
    savedLayout = {};
  }
  const nodes = new Map(nodeElements.map(element => [
    element.dataset.spatialNode,
    { element, x: 0, y: 0, vx: 0, vy: 0,
      offsetX: Number(savedLayout[element.dataset.spatialNode]?.x) || 0,
      offsetY: Number(savedLayout[element.dataset.spatialNode]?.y) || 0,
      pinned: false, hydrated: false, lastUsed: performance.now() }
  ]));
  const links = [
    ['core', 'medium', 'data'],
    ['core', 'input', 'control'],
    ['core', 'simulation', 'state']
  ];
  const groups = new Set();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const mobile = () => window.matchMedia('(max-width: 900px)').matches;
  let drag = null;
  let showThreads = true;
  let animationFrame = 0;
  let previousTime = performance.now();

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const point = node => {
    const workspaceRect = workspace.getBoundingClientRect();
    const rect = node.element.getBoundingClientRect();
    return {
      x: rect.left - workspaceRect.left + rect.width / 2,
      y: rect.top - workspaceRect.top + rect.height / 2
    };
  };

  function renderThreads() {
    if (!showThreads || mobile()) {
      threadLayer.replaceChildren();
      return;
    }
    const rect = workspace.getBoundingClientRect();
    const svg = document.getElementById('spatialThreads');
    svg.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
    const fragment = document.createDocumentFragment();
    links.forEach(([from, to, kind]) => {
      const source = nodes.get(from);
      const target = nodes.get(to);
      if (!source || !target) return;
      const a = point(source);
      const b = point(target);
      const bend = Math.max(18, Math.min(54, Math.abs(b.x - a.x) * 0.14));
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M ${a.x} ${a.y} C ${a.x} ${a.y + bend}, ${b.x} ${b.y - bend}, ${b.x} ${b.y}`);
      path.setAttribute('class', `spatial-thread ${kind}`);
      fragment.appendChild(path);
    });
    threadLayer.replaceChildren(fragment);
  }

  function saveLayout() {
    const layout = Object.fromEntries([...nodes].map(([id, node]) => [
      id, { x: Math.round(node.offsetX * 10) / 10, y: Math.round(node.offsetY * 10) / 10 }
    ]));
    localStorage.setItem(layoutKey, JSON.stringify(layout));
  }

  function render() {
    renderThreads();
    nodeElements.forEach(element => {
      const node = nodes.get(element.dataset.spatialNode);
      if (!node.hydrated) {
        node.offsetX = Number(savedLayout[element.dataset.spatialNode]?.x) || node.offsetX;
        node.offsetY = Number(savedLayout[element.dataset.spatialNode]?.y) || node.offsetY;
        node.hydrated = true;
      }
      if (!drag || drag.node !== node) {
        element.style.transform = `translate(${node.offsetX}px, ${node.offsetY}px)`;
      }
      element.classList.toggle('spatial-snapped', node.pinned);
    });
  }

  function detach(node) {
    node.pinned = false;
    node.element.classList.remove('spatial-snapped');
    [...groups].forEach(group => {
      if (!group.includes(node.element.dataset.spatialNode)) return;
      groups.delete(group);
      group.split('|').forEach(id => {
        const member = nodes.get(id);
        if (member) {
          member.pinned = false;
          member.element.classList.remove('spatial-snapped');
        }
      });
    });
    delete workspace.dataset.spatialNotice;
  }

  function snap(node) {
    if (mobile()) return;
    const nodePoint = point(node);
    let nearest = null;
    let nearestDistance = Infinity;
    nodes.forEach(candidate => {
      if (candidate === node) return;
      const candidateDistance = distance(nodePoint, point(candidate));
      if (candidateDistance < nearestDistance) {
        nearest = candidate;
        nearestDistance = candidateDistance;
      }
    });
    if (nearest && nearestDistance < 170) {
      node.pinned = true;
      nearest.pinned = true;
      node.vx = node.vy = nearest.vx = nearest.vy = 0;
      node.element.classList.add('spatial-snapped');
      nearest.element.classList.add('spatial-snapped');
      groups.add([node.element.dataset.spatialNode, nearest.element.dataset.spatialNode].sort().join('|'));
      workspace.dataset.spatialNotice = 'Panels grouped — drag apart to detach';
    }
  }

  function onPointerDown(event) {
    if (mobile() || event.target.closest('button, input, select, textarea, a, label')) return;
    const element = event.currentTarget;
    const node = nodes.get(element.dataset.spatialNode);
    const rect = element.getBoundingClientRect();
    node.lastUsed = performance.now();
    drag = { element, node, pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top, lastX: event.clientX, lastY: event.clientY, startX: event.clientX, startY: event.clientY, moved: false };
    element.classList.add('spatial-dragging');
    element.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = workspace.getBoundingClientRect();
    if (Math.hypot(event.movementX, event.movementY) > 0) drag.moved = true;
    if (drag.node.pinned && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 210) detach(drag.node);
    drag.node.offsetX += event.clientX - drag.lastX;
    drag.node.offsetY += event.clientY - drag.lastY;
    drag.lastX = event.clientX;
    drag.lastY = event.clientY;
    const nodeRect = drag.element.getBoundingClientRect();
    const left = nodeRect.left - rect.left;
    const top = nodeRect.top - rect.top;
    drag.node.offsetX += clamp(8 - left, 0, Infinity) - clamp(left + nodeRect.width - rect.width + 8, 0, Infinity);
    drag.node.offsetY += clamp(8 - top, 0, Infinity) - clamp(top + nodeRect.height - rect.height + 8, 0, Infinity);
    drag.element.style.transform = `translate(${drag.node.offsetX}px, ${drag.node.offsetY}px)`;
    drag.node.vx = drag.node.vy = 0;
    renderThreads();
  }

  function onPointerUp(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const current = drag;
    drag = null;
    current.element.classList.remove('spatial-dragging');
    if (current.moved) snap(current.node);
    saveLayout();
    render();
  }

  nodeElements.forEach(element => {
    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', onPointerUp);
    element.addEventListener('pointercancel', onPointerUp);
    element.addEventListener('focusin', () => {
      const node = nodes.get(element.dataset.spatialNode);
      node.lastUsed = performance.now();
      node.pinned = true;
    });
    element.addEventListener('focusout', () => {
      const node = nodes.get(element.dataset.spatialNode);
      if (!element.contains(document.activeElement)) node.pinned = false;
    });
  });

  function resetLayout() {
    groups.clear();
    savedLayout = {};
    localStorage.removeItem(layoutKey);
    nodeElements.forEach(element => {
      element.style.transform = '';
      const node = nodes.get(element.dataset.spatialNode);
      node.vx = node.vy = node.offsetX = node.offsetY = 0;
      node.pinned = false;
      node.lastUsed = performance.now();
      element.classList.remove('spatial-snapped', 'spatial-dragging');
    });
    delete workspace.dataset.spatialNotice;
    render();
  }

  window.RipwaveSpatialWorkspace = Object.freeze({
    reset: resetLayout,
    setThreadsVisible(visible) {
      showThreads = Boolean(visible);
      render();
    },
    getState() {
      return Object.fromEntries([...nodes].map(([id, node]) => [
        id, { pinned: node.pinned, x: node.offsetX, y: node.offsetY }
      ]));
    }
  });

  const controls = document.createElement('div');
  controls.className = 'spatial-tools';
  controls.innerHTML = '<button type="button" id="spatialThreadsToggle" aria-pressed="true">Threads</button><button type="button" id="spatialReset">Recenter workspace</button>';
  workspace.prepend(controls);
  controls.querySelector('#spatialThreadsToggle').addEventListener('click', event => {
    showThreads = !showThreads;
    event.currentTarget.setAttribute('aria-pressed', String(showThreads));
    render();
  });
  controls.querySelector('#spatialReset').addEventListener('click', resetLayout);

  function animate(now) {
    const elapsed = Math.min((now - previousTime) / 1000, 0.05);
    previousTime = now;
    if (!reducedMotion.matches && !drag && !mobile()) {
      nodes.forEach(node => {
        if (node.pinned || now - node.lastUsed < 4000) return;
        node.vx *= Math.pow(0.88, elapsed * 60);
        node.vy *= Math.pow(0.88, elapsed * 60);
        node.vx += Math.sin(now / 90000 + node.element.offsetTop) * elapsed * 0.001;
        node.vy += Math.cos(now / 110000 + node.element.offsetLeft) * elapsed * 0.001;
        node.offsetX = clamp(node.offsetX + node.vx, -42, 42);
        node.offsetY = clamp(node.offsetY + node.vy, -28, 28);
        node.element.style.transform = `translate(${node.offsetX}px, ${node.offsetY}px)`;
      });
    }
    if (Math.floor(now / 250) !== Math.floor((now - elapsed * 1000) / 250)) renderThreads();
    animationFrame = requestAnimationFrame(animate);
  }

  window.addEventListener('resize', render);
  reducedMotion.addEventListener?.('change', render);
  render();
  animationFrame = requestAnimationFrame(animate);
})();
