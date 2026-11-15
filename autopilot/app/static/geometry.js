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
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width * height > 262144 || data.length !== width * height * 4) throw new Error('Dimensiune imagine invalidă.');
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
    if (edges.length > 80000) throw new Error('Imagine prea complexă. Folosește o imagine mai simplă.');
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
    if (!contours.length) throw new Error('Nu am găsit un contur. Ajustează pragul sau alege o imagine cu contrast.');
    if (contours.reduce((n, c) => n + c.length, 0) > 3000) throw new Error('Prea multe puncte. Mărește simplificarea sau folosește o imagine mai simplă.');
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
  return { trace, pathData, svg };
});
