(() => {
  'use strict';
  const $ = id => document.getElementById(id), G = window.PrecisionGeometry;
  const canvas = $('canvas'), reference = $('reference'), shape = $('shape'), handles = $('handles');
  const buffer = document.createElement('canvas'), ctx = buffer.getContext('2d', { willReadFrequently: true });
  let project = null, source = '', undoStack = [], redoStack = [], view = null, drag = null, sel = null, dirty = false, docName = 'Untitled';
  const copy = x => JSON.parse(JSON.stringify(x));
  const message = text => { $('message').textContent = text; };
  const points = () => project ? project.contours.reduce((n, c) => n + c.length, 0) : 0;
  function setDirty(on) { dirty = on; $('dirty').hidden = !on; }
  // ── history: every change goes through snapshot(); redo is cleared by a new change ─────────────
  function snapshot() {
    if (!project) return;
    undoStack.push(copy(project)); if (undoStack.length > 60) undoStack.shift();
    redoStack = []; syncHistory(); setDirty(true);
  }
  function syncHistory() { $('undo').disabled = !undoStack.length; $('redo').disabled = !redoStack.length; }
  function undo() { if (!undoStack.length) return; redoStack.push(copy(project)); project = undoStack.pop(); sel = null; syncHistory(); setDirty(true); render(); message('Change undone.'); }
  function redo() { if (!redoStack.length) return; undoStack.push(copy(project)); project = redoStack.pop(); sel = null; syncHistory(); setDirty(true); render(); message('Change redone.'); }
  // ── view ────────────────────────────────────────────────────────────────────────────────────────
  function fit() {
    if (!project) return;
    const pad = Math.max(project.width, project.height) * .08;
    view = { x: -pad, y: -pad, w: project.width + pad * 2, h: project.height + pad * 2 };
    updateView();
  }
  function updateView() {
    canvas.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
    if (project) $('zoomValue').textContent = Math.round((project.width * 1.16) / view.w * 100) + '%';
  }
  function zoomAt(factor, cx, cy) {
    if (!view || !project) return;
    const w = Math.max(project.width * .05, Math.min(project.width * 20, view.w * factor)), ratio = w / view.w;
    const ax = cx ?? view.x + view.w / 2, ay = cy ?? view.y + view.h / 2;
    view.x = ax - (ax - view.x) * ratio; view.y = ay - (ay - view.y) * ratio; view.w = w; view.h *= ratio;
    updateView(); render();
  }
  // ── rendering and inspector ─────────────────────────────────────────────────────────────────────
  function render() {
    if (!project) return;
    reference.setAttribute('href', source || '');
    reference.setAttribute('width', project.width); reference.setAttribute('height', project.height);
    reference.style.display = $('overlay').checked && source ? '' : 'none';
    shape.setAttribute('d', project.contours.length ? G.pathData(project.contours, project.smoothness) : '');
    shape.setAttribute('stroke-width', project.thickness);
    if (sel && !(project.contours[sel.ci] && project.contours[sel.ci][sel.pi])) sel = null;
    handles.replaceChildren();
    const r = Math.max(view.w, view.h) / 170;
    if ($('nodes').checked) project.contours.forEach((contour, ci) => contour.forEach((point, pi) => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      const on = sel && sel.ci === ci && sel.pi === pi;
      c.setAttribute('cx', point.x); c.setAttribute('cy', point.y); c.setAttribute('r', on ? r * 1.5 : r);
      if (on) c.setAttribute('class', 'on');
      c.dataset.contour = ci; c.dataset.point = pi;
      handles.appendChild(c);
    }));
    $('smooth').value = project.smoothness; $('thickness').value = project.thickness;
    $('smoothValue').textContent = Number(project.smoothness).toFixed(2);
    $('thicknessValue').textContent = `${project.thickness} px`;
    $('stats').textContent = `${project.contours.length} outlines · ${points()} points · ${project.width}×${project.height} px`;
    inspector();
  }
  function inspector() {
    const p = sel && project.contours[sel.ci][sel.pi];
    $('selNone').hidden = !!p; $('selFields').hidden = !p;
    if (!p) return;
    if (document.activeElement !== $('px')) $('px').value = Math.round(p.x * 100) / 100;
    if (document.activeElement !== $('py')) $('py').value = Math.round(p.y * 100) / 100;
    $('selInfo').textContent = `Outline ${sel.ci + 1} · point ${sel.pi + 1} of ${project.contours[sel.ci].length}`;
  }
  function select(ci, pi) { sel = ci == null ? null : { ci, pi }; render(); }
  function setPoint(x, y) {
    if (!sel) return; const p = project.contours[sel.ci][sel.pi];
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    p.x = x; p.y = y; render();
  }
  for (const id of ['px', 'py']) {
    $(id).addEventListener('focus', snapshot);
    $(id).addEventListener('input', () => setPoint(Number($('px').value), Number($('py').value)));
    $(id).addEventListener('keydown', e => { if (e.key === 'Enter') e.target.blur(); e.stopPropagation(); });
  }
  function deleteSelected() {
    if (!sel) return; snapshot();
    G.deletePoint(project.contours, sel.ci, sel.pi); sel = null; render(); message('Point deleted.');
  }
  $('deletePoint').onclick = deleteSelected;
  // ── loading images and projects ─────────────────────────────────────────────────────────────────
  function retrace() {
    try {
      if (!buffer.width) { message('Import an image first.'); return; }
      const pixels = ctx.getImageData(0, 0, buffer.width, buffer.height);
      const contours = G.trace(pixels.data, buffer.width, buffer.height, Number($('threshold').value), Number($('tolerance').value));
      snapshot();
      project = { width: buffer.width, height: buffer.height, contours, smoothness: project?.smoothness ?? .55, thickness: project?.thickness ?? 0 };
      sel = null; if (!view) fit();
      render(); message(`${contours.length} outlines · ${points()} points, estimated from pixels.`);
    } catch (error) { message(error.message); }
  }
  function drawSource(img) { buffer.width = img.width; buffer.height = img.height; ctx.drawImage(img, 0, 0); }
  function useImage(img, url, name) {
    const scale = Math.min(1, 512 / Math.max(img.width, img.height));
    const candidate = document.createElement('canvas');
    candidate.width = Math.max(1, Math.round(img.width * scale)); candidate.height = Math.max(1, Math.round(img.height * scale));
    const c = candidate.getContext('2d', { willReadFrequently: true });
    c.drawImage(img, 0, 0, candidate.width, candidate.height);
    let contours;
    try {
      contours = G.trace(c.getImageData(0, 0, candidate.width, candidate.height).data, candidate.width, candidate.height,
        Number($('threshold').value), Number($('tolerance').value));
    } catch (error) { message(error.message); return; }
    drawSource(candidate); source = candidate.toDataURL('image/png'); undoStack = []; redoStack = []; syncHistory(); sel = null;
    project = { width: buffer.width, height: buffer.height, contours, smoothness: .55, thickness: 0 };
    docName = name || 'Untitled'; $('docName').textContent = docName; setDirty(!!name);
    fit(); render(); message(`${contours.length} outlines · ${points()} points, estimated from pixels.`);
  }
  function load(url, name) {
    const img = new Image();
    img.onload = () => useImage(img, url, name);
    img.onerror = () => message('The image could not be read.');
    img.src = url;
  }
  function demo() {
    if (dirty && !confirm('Replace the current document? Unsaved changes will be lost.')) return;
    const c = document.createElement('canvas'); c.width = 480; c.height = 320;
    const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 480, 320);
    x.fillStyle = '#15191e'; x.beginPath(); x.moveTo(64, 234); x.bezierCurveTo(95, 50, 215, 32, 240, 133); x.bezierCurveTo(272, 43, 393, 59, 416, 234); x.bezierCurveTo(372, 197, 324, 181, 240, 252); x.bezierCurveTo(155, 181, 105, 197, 64, 234); x.closePath(); x.fill();
    x.fillStyle = '#fff'; x.beginPath(); x.ellipse(240, 159, 44, 26, 0, 0, Math.PI * 2); x.fill();
    load(c.toDataURL('image/png'));
  }
  $('image').addEventListener('change', e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 10 * 1024 * 1024) { message('Choose a PNG or JPEG of at most 10 MB.'); return; }
    if (dirty && !confirm('Replace the current document? Unsaved changes will be lost.')) return;
    const reader = new FileReader(); reader.onload = () => load(reader.result, file.name.replace(/\.[^.]+$/, '')); reader.onerror = () => message('The file could not be read.'); reader.readAsDataURL(file);
  });
  function download(blob, name) {
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const fileBase = () => (docName || 'vellur-precision').replace(/[^\w.-]+/g, '-').slice(0, 60) || 'vellur-precision';
  function save() {
    if (!project) { message('There is nothing to save yet.'); return; }
    download(new Blob([G.serialize(project, source)], { type: 'application/json' }), fileBase() + '.vellur.json');
    setDirty(false); message('Project saved on your device. Reopen it with Open.');
  }
  $('save').onclick = save;
  $('open').onclick = () => { if (dirty && !confirm('Open another project? Unsaved changes will be lost.')) return; $('openFile').click(); };
  $('openFile').addEventListener('change', e => {
    const file = e.target.files[0]; e.target.value = '';
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) { message('The file is too large (20 MB at most).'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const opened = G.parseProject(String(reader.result));
        project = opened.project; source = opened.source; sel = null; undoStack = []; redoStack = []; syncHistory();
        buffer.width = 0;
        if (source) { const img = new Image(); img.onload = () => drawSource(img); img.src = source; }
        docName = file.name.replace(/(\.vellur)?\.json$/i, ''); $('docName').textContent = docName; setDirty(false);
        fit(); render(); message(`Project opened: ${project.contours.length} outlines · ${points()} points.`);
      } catch (err) { message(err.message); }
    };
    reader.onerror = () => message('The file could not be read.');
    reader.readAsText(file);
  });
  $('export').onclick = () => {
    if (!project) { message('Import an image first.'); return; }
    download(new Blob([G.svg(project)], { type: 'image/svg+xml' }), fileBase() + '.svg'); message('SVG downloaded.');
  };
  $('demo').onclick = demo; $('retrace').onclick = retrace;
  $('threshold').oninput = () => { $('thresholdValue').textContent = $('threshold').value; };
  $('tolerance').oninput = () => { $('toleranceValue').textContent = $('tolerance').value + ' px'; };
  for (const [id, key] of [['smooth', 'smoothness'], ['thickness', 'thickness']]) {
    $(id).addEventListener('pointerdown', snapshot);
    $(id).addEventListener('keydown', e => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) snapshot(); e.stopPropagation(); });
    $(id).oninput = () => { if (project) { project[key] = Number($(id).value); render(); } };
  }
  $('overlay').onchange = render; $('nodes').onchange = render;
  $('undo').onclick = undo; $('redo').onclick = redo;
  // ── pointer: select / drag a point, pan the background, double click inserts a point ───────────
  function position(e) {
    const p = canvas.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    return p.matrixTransform(canvas.getScreenCTM().inverse());
  }
  canvas.addEventListener('pointerdown', e => {
    if (!project) return;
    const t = e.target;
    if (t.dataset.point !== undefined) {
      const ci = Number(t.dataset.contour), pi = Number(t.dataset.point);
      sel = { ci, pi }; drag = { type: 'point', moved: false, start: position(e) };
    } else drag = { type: 'pan', start: position(e), view: { ...view }, moved: false };
    canvas.setPointerCapture(e.pointerId); e.preventDefault(); render();
  });
  canvas.addEventListener('pointermove', e => {
    if (!drag) return;
    const p = position(e);
    if (drag.type === 'point') {
      if (!drag.moved) { if (Math.hypot(p.x - drag.start.x, p.y - drag.start.y) < view.w / 400) return; snapshot(); drag.moved = true; }
      project.contours[sel.ci][sel.pi] = { x: p.x, y: p.y }; render();
    } else { drag.moved = true; view.x += drag.start.x - p.x; view.y += drag.start.y - p.y; updateView(); }
  });
  canvas.addEventListener('pointerup', () => { if (drag && drag.type === 'pan' && !drag.moved && sel) select(null); drag = null; });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  canvas.addEventListener('dblclick', e => {
    if (!project || e.target.dataset.point !== undefined) return;
    snapshot();
    const at = G.insertPoint(project.contours, position(e));
    if (at) { sel = { ci: at.contour, pi: at.point }; render(); message('New point added to the outline.'); }
  });
  $('zoomIn').onclick = () => zoomAt(.8); $('zoomOut').onclick = () => zoomAt(1.25); $('resetZoom').onclick = () => { fit(); render(); };
  canvas.addEventListener('wheel', e => { e.preventDefault(); const p = position(e); zoomAt(e.deltaY < 0 ? .9 : 1.1, p.x, p.y); }, { passive: false });
  // ── keyboard ────────────────────────────────────────────────────────────────────────────────────
  document.addEventListener('keydown', e => {
    if (e.target.matches && e.target.matches('input[type=number], input[type=text]')) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (mod && k === 'y') { e.preventDefault(); redo(); return; }
    if (mod && k === 's') { e.preventDefault(); save(); return; }
    if (!sel || !project) return;
    if (k === 'delete' || k === 'backspace') { e.preventDefault(); deleteSelected(); return; }
    if (k === 'escape') { select(null); return; }
    const step = (e.shiftKey ? 10 : 1) * (view.w / 480);
    const d = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }[k];
    if (d) { e.preventDefault(); if (!e.repeat) snapshot(); const p = project.contours[sel.ci][sel.pi]; setPoint(p.x + d[0], p.y + d[1]); }
  });
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  // ── mobile: the inspector is a bottom sheet opened from the toolbar ─────────────────────────────
  $('inspectorToggle').onclick = () => { const open = document.body.classList.toggle('sheet'); $('inspectorToggle').setAttribute('aria-expanded', String(open)); };
  demo();
})();
