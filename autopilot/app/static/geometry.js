(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PrecisionGeometry = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  function simplifyOpen(points, tolerance) {
    if (points.length <= 2) return points;
    const a = points[0], b = points[points.length - 1], dx = b.x - a.x, dy = b.y - a.y;
    const length = Math.hypot(dx, dy);
    let max = 0, index = 0;
    for (let i = 1; i < points.length - 1; i++) {
      const p = points[i];
      const d = length ? Math.abs(dy * p.x - dx * p.y + b.x * a.y - b.y * a.x) / length : distance(a, p);
      if (d > max) { max = d; index = i; }
    }
    if (max <= tolerance) return [a, b];
    return simplifyOpen(points.slice(0, index + 1), tolerance).slice(0, -1)
      .concat(simplifyOpen(points.slice(index), tolerance));
  }
  function simplifyClosed(points, tolerance) {
    if (points.length < 4) return points;
    let far = 1;
    for (let i = 2; i < points.length; i++) if (distance(points[0], points[i]) > distance(points[0], points[far])) far = i;
    const a = simplifyOpen(points.slice(0, far + 1), tolerance);
    const b = simplifyOpen(points.slice(far).concat([points[0]]), tolerance);
    const result = a.slice(0, -1).concat(b.slice(0, -1));
    return result.length >= 3 ? result : points;
  }
  function trace(data, width, height, threshold, tolerance) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width * height > 262144 || data.length !== width * height * 4) throw new Error('Invalid image size.');
    const mask = new Uint8Array(width * height);
    for (let i = 0; i < mask.length; i++) {
      const p = i * 4, alpha = data[p + 3] / 255;
      const luminance = (0.2126 * data[p] + 0.7152 * data[p + 1] + 0.0722 * data[p + 2]) * alpha + 255 * (1 - alpha);
      mask[i] = luminance < threshold ? 1 : 0;
    }
    const edges = [], starts = new Map();
    function add(x1, y1, x2, y2) {
      const edge = { x1, y1, x2, y2, used: false }, key = x1 + ',' + y1;
      if (!starts.has(key)) starts.set(key, []);
      starts.get(key).push(edge); edges.push(edge);
    }
    const isOn = (x, y) => x >= 0 && y >= 0 && x < width && y < height && mask[y * width + x];
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (isOn(x, y)) {
      if (!isOn(x, y - 1)) add(x, y, x + 1, y);
      if (!isOn(x + 1, y)) add(x + 1, y, x + 1, y + 1);
      if (!isOn(x, y + 1)) add(x + 1, y + 1, x, y + 1);
      if (!isOn(x - 1, y)) add(x, y + 1, x, y);
    }
    if (edges.length > 80000) throw new Error('The image is too complex. Use a simpler image.');
    const contours = [];
    for (const first of edges) {
      if (first.used) continue;
      let edge = first, points = [];
      while (edge && !edge.used) {
        edge.used = true; points.push({ x: edge.x1, y: edge.y1 });
        if (edge.x2 === first.x1 && edge.y2 === first.y1) break;
        const candidates = (starts.get(edge.x2 + ',' + edge.y2) || []).filter(e => !e.used);
        const dx = edge.x2 - edge.x1, dy = edge.y2 - edge.y1;
        candidates.sort((a, b) => turn(b) - turn(a));
        function turn(e) {
          const ex = e.x2 - e.x1, ey = e.y2 - e.y1;
          const cross = dx * ey - dy * ex, dot = dx * ex + dy * ey;
          return cross > 0 ? 3 : dot > 0 ? 2 : cross < 0 ? 1 : 0;
        }
        edge = candidates[0];
      }
      if (edge && points.length >= 4) {
        const simple = simplifyClosed(points, tolerance);
        if (simple.length >= 3) contours.push(simple);
      }
    }
    if (!contours.length) throw new Error('No outline found. Adjust the threshold or choose an image with more contrast.');
    if (contours.reduce((n, c) => n + c.length, 0) > 3000) throw new Error('Too many points. Increase simplification or use a simpler image.');
    return contours;
  }
  function num(n) { return String(Math.round(n * 1000) / 1000); }
  function pathData(contours, smoothness) {
    const s = Math.max(0, Math.min(1, Number(smoothness))) / 6;
    return contours.map(points => {
      let d = `M ${num(points[0].x)} ${num(points[0].y)}`;
      for (let i = 0; i < points.length; i++) {
        const prev = points[(i - 1 + points.length) % points.length], a = points[i];
        const b = points[(i + 1) % points.length], next = points[(i + 2) % points.length];
        d += ` C ${num(a.x + (b.x - prev.x) * s)} ${num(a.y + (b.y - prev.y) * s)} ${num(b.x - (next.x - a.x) * s)} ${num(b.y - (next.y - a.y) * s)} ${num(b.x)} ${num(b.y)}`;
      }
      return d + ' Z';
    }).join(' ');
  }
  function svg(project) {
    const d = pathData(project.contours, project.smoothness);
    const width = Math.max(0, Number(project.thickness) || 0);
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${project.width} ${project.height}" width="${project.width}" height="${project.height}"><path d="${d}" fill="#20252b" fill-rule="evenodd" stroke="#20252b" stroke-width="${num(width)}" stroke-linejoin="round"/></svg>`;
  }
  // ── editing a document: insert / delete points, save and open project files ─────────────────
  function segmentDistance(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len)) : 0;
    const x = a.x + t * dx, y = a.y + t * dy;
    return Math.hypot(p.x - x, p.y - y);
  }
  // Insert `p` into the closest edge of the closest contour; returns {contour, point} indices of the new point.
  function insertPoint(contours, p) {
    let best = null;
    contours.forEach((c, ci) => c.forEach((a, i) => {
      const d = segmentDistance(p, a, c[(i + 1) % c.length]);
      if (!best || d < best.d) best = { d, ci, at: i + 1 };
    }));
    if (!best) return null;
    contours[best.ci].splice(best.at, 0, { x: p.x, y: p.y });
    return { contour: best.ci, point: best.at };
  }
  // A closed contour keeps at least 3 points; deleting below that removes the whole contour.
  function deletePoint(contours, ci, pi) {
    const c = contours[ci]; if (!c || !c[pi]) return false;
    if (c.length > 3) c.splice(pi, 1); else contours.splice(ci, 1);
    return true;
  }
  const FORMAT = 'vellur-precision';
  function serialize(project, source) {
    return JSON.stringify({ format: FORMAT, version: 1, saved: new Date().toISOString(), width: project.width, height: project.height,
      smoothness: project.smoothness, thickness: project.thickness, contours: project.contours, source: source || '' });
  }
  // Validates an opened file strictly: it may come from anywhere, so only known fields and sane numbers are accepted.
  function parseProject(text) {
    let d; try { d = JSON.parse(text); } catch (e) { throw new Error('This file is not a VELLUR Precision project.'); }
    if (!d || d.format !== FORMAT || d.version !== 1) throw new Error('This file is not a VELLUR Precision project.');
    const n = (v, lo, hi) => { const x = Number(v); if (!Number.isFinite(x) || x < lo || x > hi) throw new Error('The project file is damaged.'); return x; };
    const width = n(d.width, 1, 8192), height = n(d.height, 1, 8192);
    if (!Array.isArray(d.contours) || d.contours.length > 5000) throw new Error('The project file is damaged.');
    let total = 0;
    const contours = d.contours.map(c => {
      if (!Array.isArray(c) || c.length < 3) throw new Error('The project file is damaged.');
      total += c.length; if (total > 200000) throw new Error('The project is too large.');
      return c.map(p => ({ x: n(p && p.x, -1e5, 1e5), y: n(p && p.y, -1e5, 1e5) }));
    });
    const source = typeof d.source === 'string' && /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(d.source) ? d.source : '';
    return { project: { width, height, contours, smoothness: n(d.smoothness ?? .55, 0, 1), thickness: n(d.thickness ?? 0, 0, 100) }, source };
  }
  return { trace, pathData, svg, insertPoint, deletePoint, serialize, parseProject };
});
