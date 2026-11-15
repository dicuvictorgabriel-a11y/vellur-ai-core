'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const G = require('../app/static/geometry.js');

const square = () => [[{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }]];

test('inserts a point into the nearest edge', () => {
  const c = square();
  const at = G.insertPoint(c, { x: 5, y: 1 });
  assert.deepEqual(at, { contour: 0, point: 1 });
  assert.deepEqual(c[0][1], { x: 5, y: 1 });
  assert.equal(c[0].length, 5);
});

test('deleting keeps a closed contour valid', () => {
  const c = square();
  assert.equal(G.deletePoint(c, 0, 0), true);
  assert.equal(c[0].length, 3);
  assert.equal(G.deletePoint(c, 0, 0), true);   // below 3 points the contour goes away
  assert.equal(c.length, 0);
  assert.equal(G.deletePoint(c, 0, 0), false);
});

test('a saved project opens again unchanged', () => {
  const project = { width: 40, height: 30, contours: square(), smoothness: 0.3, thickness: 2 };
  const src = 'data:image/png;base64,iVBORw0KGgo=';
  const opened = G.parseProject(G.serialize(project, src));
  assert.deepEqual(opened.project, project);
  assert.equal(opened.source, src);
});

test('opening rejects foreign or damaged files and unsafe sources', () => {
  assert.throws(() => G.parseProject('not json'), /not a VELLUR Precision project/);
  assert.throws(() => G.parseProject(JSON.stringify({ format: 'other', version: 1 })), /not a VELLUR/);
  const base = { format: 'vellur-precision', version: 1, width: 10, height: 10, contours: square() };
  assert.throws(() => G.parseProject(JSON.stringify({ ...base, width: -1 })), /damaged/);
  assert.throws(() => G.parseProject(JSON.stringify({ ...base, contours: [[{ x: 1, y: 1 }]] })), /damaged/);
  assert.throws(() => G.parseProject(JSON.stringify({ ...base, contours: [[{ x: 'a' }, {}, {}]] })), /damaged/);
  const evil = G.parseProject(JSON.stringify({ ...base, source: 'javascript:alert(1)' }));
  assert.equal(evil.source, '');
});
