(() => {
  'use strict';
  const $ = id => document.getElementById(id), G = window.PrecisionGeometry;
  const canvas = $('canvas'), reference = $('reference'), shape = $('shape'), handles = $('handles');
  const buffer = document.createElement('canvas'), ctx = buffer.getContext('2d', { willReadFrequently: true });
  let project = null, source = '', history = [], view = null, drag = null;
  const copy = x => JSON.parse(JSON.stringify(x));
  function message(text) { $('message').textContent = text; }
  function snapshot() {
    if (!project) return;
    history.push(copy(project));
    if (history.length > 30) history.shift();
    $('undo').disabled = false;
  }
  function fit() {
    if (!project) return;
    const pad = Math.max(project.width, project.height) * .1;
    view = { x: -pad, y: -pad, w: project.width + pad * 2, h: project.height + pad * 2 };
    updateView();
  }
  function updateView() { canvas.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`); }
  function render() {
    if (!project) return;
    reference.setAttribute('href', source);
    reference.setAttribute('width', project.width); reference.setAttribute('height', project.height);
    reference.style.display = $('overlay').checked ? '' : 'none';
    shape.setAttribute('d', G.pathData(project.contours, project.smoothness));
    shape.setAttribute('stroke-width', project.thickness);
    handles.replaceChildren();
    if ($('nodes').checked) project.contours.forEach((contour, ci) => contour.forEach((point, pi) => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', point.x); circle.setAttribute('cy', point.y);
      circle.setAttribute('r', Math.max(view.w, view.h) / 180);
      circle.dataset.contour = ci; circle.dataset.point = pi;
      handles.appendChild(circle);
    }));
    $('smooth').value = project.smoothness; $('thickness').value = project.thickness;
    $('smoothValue').textContent = Number(project.smoothness).toFixed(2);
    $('thicknessValue').textContent = `${project.thickness} px`;
  }
  function retrace() {
    try {
      const pixels = ctx.getImageData(0, 0, buffer.width, buffer.height);
      const contours = G.trace(pixels.data, buffer.width, buffer.height, Number($('threshold').value), Number($('tolerance').value));
      snapshot();
      project = { width: buffer.width, height: buffer.height, contours, smoothness: project?.smoothness ?? .55, thickness: project?.thickness ?? 0 };
      if (!view) fit();
      render(); message(`${contours.length} contururi · ${contours.reduce((n, c) => n + c.length, 0)} puncte. Estimare din pixeli.`);
    } catch (error) { message(error.message); }
  }
  function useImage(img, url) {
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
    buffer.width = candidate.width; buffer.height = candidate.height; ctx.drawImage(candidate, 0, 0);
    source = url; history = []; $('undo').disabled = true;
    project = { width: buffer.width, height: buffer.height, contours, smoothness: .55, thickness: 0 };
    fit(); render(); message(`${contours.length} contururi · ${contours.reduce((n, item) => n + item.length, 0)} puncte. Estimare din pixeli.`);
  }
  function load(url) {
    const img = new Image();
    img.onload = () => useImage(img, url);
    img.onerror = () => message('Imaginea nu a putut fi citită.');
    img.src = url;
  }
  function demo() {
    const c = document.createElement('canvas'); c.width = 480; c.height = 320;
    const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 480, 320);
    x.fillStyle = '#15191e'; x.beginPath(); x.moveTo(64, 234); x.bezierCurveTo(95, 50, 215, 32, 240, 133); x.bezierCurveTo(272, 43, 393, 59, 416, 234); x.bezierCurveTo(372, 197, 324, 181, 240, 252); x.bezierCurveTo(155, 181, 105, 197, 64, 234); x.closePath(); x.fill();
    x.fillStyle = '#fff'; x.beginPath(); x.ellipse(240, 159, 44, 26, 0, 0, Math.PI * 2); x.fill();
    load(c.toDataURL('image/png'));
  }
  $('image').addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 10 * 1024 * 1024) { message('Alege un PNG sau JPEG de cel mult 10 MB.'); return; }
    const reader = new FileReader(); reader.onload = () => load(reader.result); reader.onerror = () => message('Fișierul nu a putut fi citit.'); reader.readAsDataURL(file);
    e.target.value = '';
  });
  $('demo').onclick = demo; $('retrace').onclick = retrace;
  $('threshold').oninput = () => $('thresholdValue').textContent = $('threshold').value;
  $('tolerance').oninput = () => $('toleranceValue').textContent = $('tolerance').value + ' px';
  for (const [id, key] of [['smooth', 'smoothness'], ['thickness', 'thickness']]) {
    $(id).addEventListener('pointerdown', snapshot);
    $(id).addEventListener('keydown', e => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) snapshot(); });
    $(id).oninput = () => { if (project) { project[key] = Number($(id).value); render(); } };
  }
  $('overlay').onchange = render; $('nodes').onchange = render;
  $('undo').onclick = () => { if (history.length) { project = history.pop(); $('undo').disabled = !history.length; render(); message('Ultima modificare a fost anulată.'); } };
  $('export').onclick = () => {
    if (!project) { message('Importă mai întâi o imagine.'); return; }
    const url = URL.createObjectURL(new Blob([G.svg(project)], { type: 'image/svg+xml' }));
    const a = document.createElement('a'); a.href = url; a.download = 'vellur-precision.svg'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  function position(e) {
    const p = canvas.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    return p.matrixTransform(canvas.getScreenCTM().inverse());
  }
  canvas.addEventListener('pointerdown', e => {
    if (!project) return;
    const target = e.target;
    if (target.dataset.point !== undefined) {
      snapshot(); drag = { type: 'point', ci: Number(target.dataset.contour), pi: Number(target.dataset.point) };
    } else drag = { type: 'pan', start: position(e), view: { ...view } };
    canvas.setPointerCapture(e.pointerId); e.preventDefault();
  });
  canvas.addEventListener('pointermove', e => {
    if (!drag) return;
    const p = position(e);
    if (drag.type === 'point') { project.contours[drag.ci][drag.pi] = { x: p.x, y: p.y }; render(); }
    else { view.x += drag.start.x - p.x; view.y += drag.start.y - p.y; updateView(); }
  });
  canvas.addEventListener('pointerup', () => { drag = null; });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  function zoom(factor) {
    if (!view) return;
    const w = Math.max(project.width * .1, Math.min(project.width * 10, view.w * factor));
    const ratio = w / view.w; view.x += (view.w - w) / 2; view.y += (view.h - view.h * ratio) / 2; view.w = w; view.h *= ratio; updateView(); render();
  }
  $('zoomIn').onclick = () => zoom(.8); $('zoomOut').onclick = () => zoom(1.25); $('resetZoom').onclick = () => { fit(); render(); };
  canvas.addEventListener('wheel', e => { e.preventDefault(); zoom(e.deltaY < 0 ? .9 : 1.1); }, { passive: false });
  demo();
})();
