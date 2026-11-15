'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const G = require('../app/static/geometry.js');

function image(w, h, fn) {
  const pixels = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4, color = fn(x, y);
    pixels[i] = pixels[i + 1] = pixels[i + 2] = color ? 0 : 255;
    pixels[i + 3] = 255;
  }
  return pixels;
}

test('extracts closed outer contour and hole', () => {
  const pixels = image(24, 24, (x, y) => x >= 2 && x < 22 && y >= 2 && y < 22 && !(x >= 8 && x < 16 && y >= 8 && y < 16));
  const contours = G.trace(pixels, 24, 24, 128, 0.5);
  assert.equal(contours.length, 2);
  assert.ok(contours.every(c => c.length >= 3));
  const d = G.pathData(contours, .5);
  assert.equal((d.match(/ Z/g) || []).length, 2);
  assert.match(d, / C /);
});

test('editing points and smoothness changes exported curves and thickness changes stroke', () => {
  const p = { width: 24, height: 24, contours: G.trace(image(24, 24, (x, y) => x > 2 && x < 20 && y > 2 && y < 20), 24, 24, 128, 1), smoothness: 0, thickness: 0 };
  const original = G.svg(p);
  p.contours[0][0].x += 2;
  const moved = G.svg(p);
  assert.notEqual(moved, original);
  p.smoothness = .8;
  const smooth = G.svg(p);
  assert.notEqual(smooth, moved);
  p.thickness = 4;
  const thick = G.svg(p);
  assert.match(thick, /stroke-width="4"/);
  assert.notEqual(thick, smooth);
  assert.match(thick, /fill-rule="evenodd"/);
  process.stdout.write('SVG_FOR_PARSE=' + thick + '\n');
});

test('transparent image is background and blank images fail safely', () => {
  const pixels = new Uint8ClampedArray(10 * 10 * 4);
  assert.throws(() => G.trace(pixels, 10, 10, 128, 1), /contur/);
  assert.throws(() => G.trace(pixels, 10000, 10000, 128, 1), /Dimensiune/);
});

test('diagonally touching shapes remain separate', () => {
  const contours = G.trace(image(8, 8, (x, y) => (x < 3 && y < 3) || (x >= 3 && x < 6 && y >= 3 && y < 6)), 8, 8, 128, .5);
  assert.equal(contours.length, 2);
});
