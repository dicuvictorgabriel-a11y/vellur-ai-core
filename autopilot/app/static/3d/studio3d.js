import * as THREE from './vendor/three.module.min.js';
import { OrbitControls } from './vendor/addons/controls/OrbitControls.js';
import { GLTFLoader } from './vendor/addons/loaders/GLTFLoader.js';
import { OBJLoader } from './vendor/addons/loaders/OBJLoader.js';
import { STLLoader } from './vendor/addons/loaders/STLLoader.js';
import { RoomEnvironment } from './vendor/addons/environments/RoomEnvironment.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const taskId = (params.get('task') || '').trim();
const isEmbed = params.get('embed') === '1';
const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

const state = {
  models: [], currentEntry: null, root: null, mixer: null, actions: [], playing: false,
  selected: null, helper: null, wire: false, spin: false, loadToken: 0, pendingEntry: null,
  updateEntry: null, poll: null, signature: '', ready: false,
};
let renderer, scene, camera, controls, raycaster, grid, pmrem, keyLight;

function bytes(n) {
  if (!Number.isFinite(n)) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function baseName(path) {
  return String(path || '').split('/').pop() || path || 'model';
}

function assetUrl(path) {
  const clean = String(path || '').split('/').filter(Boolean).map(encodeURIComponent).join('/');
  return `/autopilot/api/agent/tasks/${encodeURIComponent(taskId)}/asset/${clean}`;
}

async function api(path, opts = {}) {
  const headers = { 'X-Vellur-Request': '1', ...(opts.body ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) };
  const r = await fetch('/autopilot/api/' + path, {
    credentials: 'same-origin', cache: 'no-store', ...opts, headers,
  });
  const type = r.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await r.json().catch(() => ({})) : await r.text().catch(() => '');
  if (!r.ok) {
    const detail = data && typeof data === 'object' ? (data.message || data.detail || data.error) : data;
    throw new Error(detail ? String(detail).slice(0, 300) : 'HTTP ' + r.status);
  }
  return data;
}

function setSub(text) { $('sub').textContent = text || ''; }

function setEmpty(title, text) {
  const card = $('empty');
  card.querySelector('b').textContent = title;
  card.querySelector('p').textContent = text;
  card.hidden = false;
  $('loading').hidden = true;
  $('error').hidden = true;
}

function setLoading(text, hint) {
  $('empty').hidden = true;
  $('error').hidden = true;
  $('loading').hidden = false;
  $('loadText').textContent = text || 'Loading model…';
  $('loadHint').textContent = hint || 'Large models can take a moment. You can keep orbiting the previous model until the new one is ready.';
  $('progress').querySelector('i').style.width = '0%';
}

function setProgress(fraction) {
  if (!Number.isFinite(fraction) || fraction <= 0) return;
  $('progress').querySelector('i').style.width = Math.min(100, Math.max(3, fraction * 100)).toFixed(1) + '%';
}

function friendlyError(e, entry) {
  const msg = String((e && e.message) || e || 'unknown error');
  if (/401|403|login required|password/i.test(msg)) return 'Your session expired. Reload the dashboard and open 3D Studio again.';
  if (/404|not found/i.test(msg)) return 'The file is no longer in this task workspace. Rescan the models.';
  if (/Failed to fetch|NetworkError|load failed/i.test(msg)) return 'The server did not respond while loading the file. Check your connection and try again.';
  if (/unsupported|format/i.test(msg)) return 'This file format cannot be previewed in the browser.';
  return 'The model could not be loaded' + (entry ? ' (' + baseName(entry.path) + ')' : '') + ': ' + msg;
}

function showError(message, entry) {
  state.pendingEntry = entry || state.pendingEntry;
  $('loading').hidden = true;
  $('empty').hidden = true;
  $('errorText').textContent = message;
  $('error').hidden = false;
  $('errorRetry').hidden = !state.pendingEntry;
}

function initScene() {
  const canvas = $('canvas');
  const stage = $('stage');
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  } catch (e) {
    setEmpty('WebGL is not available', 'This browser or device cannot show 3D models. Try a current Chrome, Edge, Safari or Firefox with hardware acceleration enabled.');
    return false;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(stage.clientWidth || 1, stage.clientHeight || 1, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(38, 1, 0.01, 3000);
  camera.position.set(3.2, 2.2, 3.8);

  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.screenSpacePanning = true;
  controls.target.set(0, 1, 0);
  controls.minDistance = 0.4;
  controls.maxDistance = 120;
  controls.update();

  raycaster = new THREE.Raycaster();
  pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  scene.add(new THREE.HemisphereLight(0xcfe2ff, 0x1a1410, 0.55));
  keyLight = new THREE.DirectionalLight(0xffffff, 2.3);
  keyLight.position.set(4.5, 7, 5);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  keyLight.shadow.camera.near = 0.1;
  keyLight.shadow.camera.far = 40;
  keyLight.shadow.camera.left = -6;
  keyLight.shadow.camera.right = 6;
  keyLight.shadow.camera.top = 6;
  keyLight.shadow.camera.bottom = -6;
  keyLight.shadow.bias = -0.0004;
  keyLight.shadow.normalBias = 0.02;
  scene.add(keyLight);
  const fill = new THREE.DirectionalLight(0x8ec5ff, 0.75);
  fill.position.set(-5, 3.5, -4);
  scene.add(fill);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.ShadowMaterial({ opacity: 0.3 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.renderOrder = -2;
  scene.add(ground);
  grid = new THREE.GridHelper(20, 40, 0x2f4355, 0x1a2732);
  grid.material.transparent = true;
  grid.material.opacity = 0.55;
  grid.position.y = 0.002;
  scene.add(grid);

  const resize = () => {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(stage);
  resize();

  let last = performance.now();
  const tick = (now) => {
    requestAnimationFrame(tick);
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    if (state.spin && state.root) state.root.rotation.y += dt * 0.38;
    if (state.playing && state.mixer) state.mixer.update(dt);
    if (state.helper && state.selected) state.helper.box.setFromObject(state.selected);
    controls.update();
    renderer.render(scene, camera);
  };
  requestAnimationFrame(tick);
  wirePointer();
  state.ready = true;
  return true;
}

function disposeObject(object) {
  if (!object) return;
  const materials = new Set();
  object.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const m = o.material;
    (Array.isArray(m) ? m : [m]).forEach((x) => { if (x) materials.add(x); });
  });
  materials.forEach((m) => {
    try {
      for (const k of Object.keys(m)) {
        const v = m[k];
        if (v && v.isTexture) v.dispose();
      }
      m.dispose();
    } catch (e) { /* a shared or custom material may already be disposed */ }
  });
}

function prepareObject(object) {
  object.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = true;
    o.receiveShadow = true;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((m) => { if (m) { m.envMapIntensity = 0.95; m.wireframe = state.wire; m.needsUpdate = true; } });
  });
}

function fitObject(object) {
  object.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const max = Math.max(size.x, size.y, size.z);
  if (!Number.isFinite(max) || max <= 0) throw new Error('the model has no visible geometry');
  object.scale.multiplyScalar(2.6 / max);
  object.updateMatrixWorld(true);
  box.setFromObject(object);
  const center = box.getCenter(new THREE.Vector3());
  object.position.x -= center.x;
  object.position.z -= center.z;
  object.position.y -= box.min.y;
  object.updateMatrixWorld(true);
}

function frameCamera() {
  if (!state.root) return;
  const box = new THREE.Box3().setFromObject(state.root);
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = Math.max(sphere.radius, 0.35);
  const dist = (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.18;
  const dir = new THREE.Vector3(1, 0.72, 1).normalize();
  camera.position.copy(sphere.center).addScaledVector(dir, dist);
  camera.near = Math.max(dist / 2000, 0.01);
  camera.far = Math.max(dist * 40, 100);
  camera.updateProjectionMatrix();
  controls.target.copy(sphere.center);
  controls.minDistance = radius * 0.35;
  controls.maxDistance = radius * 24;
  controls.update();
}

function statsFor(object) {
  let vertices = 0, triangles = 0, meshes = 0;
  object.traverse((o) => {
    if (!o.isMesh || !o.geometry) return;
    meshes += 1;
    const pos = o.geometry.attributes && o.geometry.attributes.position;
    if (!pos) return;
    vertices += pos.count;
    triangles += o.geometry.index ? o.geometry.index.count / 3 : pos.count / 3;
  });
  return { vertices, triangles: Math.round(triangles), meshes };
}

function install(loaded, entry) {
  const object = loaded.object;
  prepareObject(object);
  fitObject(object);

  if (state.root) {
    scene.remove(state.root);
    disposeObject(state.root);
  }
  clearSelection(false);
  state.root = new THREE.Group();
  state.root.add(object);
  state.root.rotation.set(0, 0, 0);
  scene.add(state.root);

  state.currentEntry = entry;
  state.pendingEntry = entry;
  state.mixer = null;
  state.actions = [];
  state.playing = false;
  if (loaded.animations && loaded.animations.length) {
    state.mixer = new THREE.AnimationMixer(object);
    state.actions = loaded.animations.map((clip) => {
      const action = state.mixer.clipAction(clip);
      action.clampWhenFinished = true;
      action.loop = THREE.LoopRepeat;
      return action;
    });
  }
  $('btnAnim').hidden = !state.actions.length;
  $('btnAnim').setAttribute('aria-pressed', 'false');
  $('btnAnim').title = state.actions.length ? 'Play animation' : 'No animation';
  $('btnAnim').setAttribute('aria-label', state.actions.length ? 'Play animation' : 'No animation');

  const s = statsFor(object);
  $('stats').textContent = `${nf.format(s.vertices)} vertices · ${nf.format(s.triangles)} triangles · ${s.meshes} mesh${s.meshes === 1 ? '' : 'es'}`;
  $('modelName').textContent = entry.name || baseName(entry.path);
  const extras = [String(entry.format || '').toUpperCase(), bytes(entry.size)];
  if (entry.source === 'derived' || entry.derived) extras.push('from ' + baseName(entry.sourcePath || 'Blender'));
  if (state.actions.length) extras.push(state.actions.length + ' animation' + (state.actions.length === 1 ? '' : 's'));
  setSub(extras.filter(Boolean).join(' · '));
  document.title = (entry.name || baseName(entry.path)) + ' · VELLUR 3D Studio';

  frameCamera();
  $('empty').hidden = true;
  $('loading').hidden = true;
  $('error').hidden = true;
  $('updatePill').hidden = true;
  state.updateEntry = null;
  if (!entry.local) setUrlModel(entry.path);
  renderList();
  if (window.parent !== window && !entry.local) {
    window.parent.postMessage({ type: 'vellur-3d-ready', taskId, model: entry.path }, location.origin);
  }
}

function loadGLTF(url, token) {
  return new Promise((resolve, reject) => {
    const loader = new GLTFLoader();
    loader.load(url,
      (gltf) => resolve({ object: gltf.scene, animations: gltf.animations || [] }),
      (ev) => { if (token === state.loadToken && ev.total) setProgress(ev.loaded / ev.total); },
      reject);
  });
}

function loadOBJ(url, token) {
  return new Promise((resolve, reject) => {
    const loader = new OBJLoader();
    loader.load(url,
      (group) => resolve({ object: group, animations: [] }),
      (ev) => { if (token === state.loadToken && ev.total) setProgress(ev.loaded / ev.total); },
      reject);
  });
}

function loadSTL(url, token) {
  return new Promise((resolve, reject) => {
    const loader = new STLLoader();
    loader.load(url,
      (geometry) => {
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
          color: 0xb9c8d8, metalness: 0.18, roughness: 0.5, envMapIntensity: 0.9,
        }));
        mesh.name = baseName(state.pendingEntry && state.pendingEntry.path);
        resolve({ object: mesh, animations: [] });
      },
      (ev) => { if (token === state.loadToken && ev.total) setProgress(ev.loaded / ev.total); },
      reject);
  });
}

async function loadModel(entry) {
  if (!entry) return;
  if (!entry.previewable) {
    if (entry.format === 'blend') return prepareBlend(entry);
    showError(entry.reason || entry.problem || 'This file cannot be previewed in the browser.', entry);
    return;
  }
  const token = ++state.loadToken;
  state.pendingEntry = entry;
  setLoading(`Loading ${entry.name || baseName(entry.path)}…`);
  const url = entry.local ? entry.url : assetUrl(entry.path) + '?v=' + encodeURIComponent(entry.mtime || Date.now());
  try {
    let loaded;
    if (entry.format === 'glb' || entry.format === 'gltf') loaded = await loadGLTF(url, token);
    else if (entry.format === 'obj') loaded = await loadOBJ(url, token);
    else if (entry.format === 'stl') loaded = await loadSTL(url, token);
    else throw new Error('unsupported format');
    if (token !== state.loadToken) { disposeObject(loaded.object); return; }
    install(loaded, entry);
  } catch (e) {
    if (token !== state.loadToken) return;
    showError(friendlyError(e, entry), entry);
  }
}

async function prepareBlend(entry) {
  const token = ++state.loadToken;
  state.pendingEntry = entry;
  setLoading(`Preparing ${entry.name || baseName(entry.path)} with Blender…`, 'Blender is optional. If it is not installed, ask the owner to enable it or export a GLB instead.');
  try {
    const result = await api(`agent/tasks/${encodeURIComponent(taskId)}/blend-export`, {
      method: 'POST', body: JSON.stringify({ path: entry.path }),
    });
    if (token !== state.loadToken) return;
    await refreshModels(false);
    const fresh = state.models.find((m) => m.path === (result.model && result.model.path)) || result.model;
    if (!fresh) throw new Error('Blender did not return a preview file');
    await loadModel(fresh);
  } catch (e) {
    if (token !== state.loadToken) return;
    showError(friendlyError(e, entry), entry);
  }
}

function selectionText(info) {
  const parts = ['[3D selection]', info.name, '—', info.type];
  if (info.triangles) parts.push(`, ${nf.format(info.triangles)} triangles`);
  if (info.vertices) parts.push(`, ${nf.format(info.vertices)} vertices`);
  if (info.material) parts.push(`, material ${info.material}`);
  if (info.path) parts.push(`, file ${info.path}`);
  return parts.join(' ').replace(/\s+,/g, ',').replace(/\s+/g, ' ').trim();
}

function postSelection(info) {
  if (window.parent === window) {
    $('selNote').textContent = 'Open this from an Agent task to add the selection to the chat.';
    return;
  }
  window.parent.postMessage({ type: 'vellur-3d-selection', taskId, selection: info }, location.origin);
  $('selNote').textContent = 'Added to the chat message.';
  setTimeout(() => { $('selNote').textContent = ''; }, 2600);
}

function clearSelection(keepNote) {
  if (state.helper) {
    scene.remove(state.helper);
    state.helper.geometry.dispose();
    state.helper.material.dispose();
    state.helper = null;
  }
  state.selected = null;
  $('selection').hidden = true;
  if (!keepNote) $('selNote').textContent = '';
}

function selectMesh(mesh) {
  clearSelection(true);
  $('selNote').textContent = '';
  state.selected = mesh;
  state.helper = new THREE.Box3Helper(new THREE.Box3().setFromObject(mesh), 0xc8ff3d);
  state.helper.material.transparent = true;
  state.helper.material.opacity = 0.9;
  state.helper.material.depthTest = false;
  state.helper.renderOrder = 10;
  scene.add(state.helper);

  const geo = mesh.geometry;
  const pos = geo && geo.attributes && geo.attributes.position;
  const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
  const info = {
    name: mesh.name || 'Unnamed mesh',
    type: mesh.isSkinnedMesh ? 'Skinned mesh' : mesh.isInstancedMesh ? 'Instanced mesh' : 'Mesh',
    vertices: pos ? pos.count : 0,
    triangles: pos ? Math.round(geo.index ? geo.index.count / 3 : pos.count / 3) : 0,
    material: (material && (material.name || material.type)) || '',
    path: state.currentEntry ? state.currentEntry.path : '',
  };
  $('selName').textContent = info.name;
  $('selType').textContent = info.type;
  $('selTris').textContent = info.triangles ? nf.format(info.triangles) : '—';
  $('selVerts').textContent = info.vertices ? nf.format(info.vertices) : '—';
  $('selMat').textContent = info.material || '—';
  $('selPath').textContent = info.path || '—';
  $('selection').hidden = false;
}

function pick(event) {
  if (!state.root || !state.ready) return;
  const rect = renderer.domElement.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const p = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  raycaster.setFromCamera(p, camera);
  const hits = raycaster.intersectObject(state.root, true).filter((h) => h.object && h.object.isMesh);
  if (hits.length) selectMesh(hits[0].object);
  else clearSelection();
}

function renderList() {
  const list = $('list');
  const signature = [state.currentEntry ? state.currentEntry.path : '', ...state.models.map((m) =>
    [m.path, m.name, m.mtime, m.size, m.previewable, m.derived, m.sourcePath].join(':'))].join('|');
  $('count').textContent = state.models.length ? state.models.length + ' file' + (state.models.length === 1 ? '' : 's') : 'none yet';
  if (signature === state.signature) return;
  state.signature = signature;
  list.innerHTML = '';
  if (!state.models.length) {
    const empty = document.createElement('div');
    empty.className = 'item';
    empty.style.cursor = 'default';
    empty.innerHTML = '<span class="badge">3D</span><span class="meta"><b>No models found</b><small>The agent has not created a 3D file yet.</small></span><span></span>';
    list.appendChild(empty);
    return;
  }
  state.models.forEach((m) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'item' + (state.currentEntry && state.currentEntry.path === m.path ? ' on' : '');
    b.setAttribute('role', 'listitem');
    const fmt = String(m.format || '').toUpperCase();
    const name = m.name || baseName(m.path);
    const detail = [fmt, bytes(m.size)];
    if (m.source === 'derived' || m.derived) detail.push('derived');
    if (m.format === 'blend') detail.push(m.previewable ? 'ready' : 'needs Blender');
    b.innerHTML = '<span class="badge"></span><span class="meta"><b></b><small></small></span><span class="state"></span>';
    b.querySelector('.badge').textContent = fmt.slice(0, 5);
    b.querySelector('.meta b').textContent = name;
    b.querySelector('.meta small').textContent = detail.filter(Boolean).join(' · ');
    const stateEl = b.querySelector('.state');
    stateEl.textContent = m.previewable ? '' : m.format === 'blend' ? 'Prepare' : 'Unavailable';
    stateEl.classList.toggle('prep', !m.previewable && m.format === 'blend');
    b.onclick = () => {
      if (window.matchMedia('(max-width: 899px)').matches) document.body.classList.remove('panel-open');
      loadModel(m);
    };
    list.appendChild(b);
  });
}

function applyModels(data) {
  const models = Array.isArray(data.models) ? data.models : [];
  state.models = models;
  const available = !!data.blender;
  $('blenderState').textContent = available
    ? 'Blender is available for .blend files.'
    : 'Blender is not installed; only GLB, glTF, OBJ and STL can be previewed.';
  renderList();

  if (state.currentEntry) {
    const fresh = models.find((m) => m.path === state.currentEntry.path);
    if (!fresh && state.root) {
      showUpdate('This model is no longer in the workspace', null);
    } else if (fresh && (fresh.mtime !== state.currentEntry.mtime || fresh.size !== state.currentEntry.size)) {
      showUpdate('Model updated', fresh);
    }
  }

  if (!state.root) setSub(`Task ${taskId} · ` + (models.length ? models.length + ' model file' + (models.length === 1 ? '' : 's') : 'no models yet'));
  if (!state.root && !state.pendingEntry) {
    const wanted = params.get('model');
    const first = (wanted && models.find((m) => m.path === wanted && m.previewable)) || models.find((m) => m.previewable);
    if (first) loadModel(first);
    else if (!models.length) {
      setEmpty('No 3D model yet', 'Ask the agent to create or edit a GLB, glTF, OBJ or STL file in this task. The result appears here automatically.');
    } else {
      setEmpty('No browser-ready model', 'This task has .blend files only. Click one in the list to prepare a GLB with Blender, if it is installed.');
    }
  }
}

async function refreshModels(showSpinner) {
  if (showSpinner) {
    $('btnRefresh').disabled = true;
    $('blenderState').textContent = 'Rescanning workspace…';
  }
  try {
    const data = await api(`agent/tasks/${encodeURIComponent(taskId)}/models`);
    applyModels(data);
    return data;
  } catch (e) {
    $('blenderState').textContent = 'Could not read the workspace: ' + e.message;
    if (!state.root) showError(friendlyError(e), null);
    return null;
  } finally {
    $('btnRefresh').disabled = false;
  }
}

function showUpdate(text, entry) {
  state.updateEntry = entry || null;
  $('updateText').textContent = text;
  $('updateBtn').textContent = entry ? 'Reload' : 'Rescan';
  $('updatePill').hidden = false;
}

function setUrlModel(path) {
  try {
    const url = new URL(location.href);
    if (path) url.searchParams.set('model', path);
    else url.searchParams.delete('model');
    history.replaceState(null, '', url);
  } catch (e) { /* history can be unavailable in some embedded contexts */ }
}

function schedulePoll() {
  clearTimeout(state.poll);
  state.poll = setTimeout(poll, 10000);
}

async function poll() {
  if (document.hidden) return schedulePoll();
  try {
    const data = await api(`agent/tasks/${encodeURIComponent(taskId)}/models`);
    applyModels(data);
  } catch (e) { /* transient network errors are retried on the next tick */ }
  schedulePoll();
}

function wireToolbar() {
  $('btnFit').onclick = () => frameCamera();
  $('btnGrid').onclick = () => {
    grid.visible = !grid.visible;
    $('btnGrid').setAttribute('aria-pressed', String(grid.visible));
  };
  $('btnWire').onclick = () => {
    state.wire = !state.wire;
    if (state.root) {
      state.root.traverse((o) => {
        if (!o.isMesh) return;
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
          if (!m) return;
          m.wireframe = state.wire;
          m.needsUpdate = true;
        });
      });
    }
    $('btnWire').setAttribute('aria-pressed', String(state.wire));
  };
  $('btnSpin').onclick = () => {
    state.spin = !state.spin;
    $('btnSpin').setAttribute('aria-pressed', String(state.spin));
  };
  $('btnAnim').onclick = () => {
    if (!state.mixer || !state.actions.length) return;
    state.playing = !state.playing;
    if (state.playing) state.actions.forEach((a) => a.play());
    else state.actions.forEach((a) => a.paused = true);
    if (state.playing) state.actions.forEach((a) => { a.paused = false; });
    $('btnAnim').setAttribute('aria-pressed', String(state.playing));
    $('btnAnim').title = state.playing ? 'Pause animation' : 'Play animation';
  };
  $('btnShot').onclick = () => {
    if (!state.ready) return;
    renderer.render(scene, camera);
    const a = document.createElement('a');
    const name = (state.currentEntry && (state.currentEntry.name || baseName(state.currentEntry.path))) || 'vellur-model';
    a.download = name.replace(/\.[^.]+$/, '') + '.png';
    a.href = renderer.domElement.toDataURL('image/png');
    a.click();
  };
  $('btnFull').onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $('app').requestFullscreen();
    } catch (e) { /* fullscreen can be blocked by the embedding page */ }
  };
  $('btnPanel').onclick = () => document.body.classList.toggle('panel-open');
  $('btnRefresh').onclick = () => refreshModels(true);
  $('emptyRefresh').onclick = () => refreshModels(true);
  $('updateBtn').onclick = () => {
    $('updatePill').hidden = true;
    if (state.updateEntry) loadModel(state.updateEntry);
    else refreshModels(true);
  };
  $('updateDismiss').onclick = () => { $('updatePill').hidden = true; };
  $('errorRetry').onclick = () => {
    $('error').hidden = true;
    if (state.pendingEntry) loadModel(state.pendingEntry);
  };
  $('errorClose').onclick = () => { $('error').hidden = true; };
  $('selClose').onclick = () => clearSelection();
  $('selInsert').onclick = () => {
    if (!state.selected) return;
    const material = Array.isArray(state.selected.material) ? state.selected.material[0] : state.selected.material;
    const pos = state.selected.geometry && state.selected.geometry.attributes && state.selected.geometry.attributes.position;
    postSelection({
      name: state.selected.name || 'Unnamed mesh',
      type: state.selected.isSkinnedMesh ? 'Skinned mesh' : state.selected.isInstancedMesh ? 'Instanced mesh' : 'Mesh',
      vertices: pos ? pos.count : 0,
      triangles: pos ? Math.round(state.selected.geometry.index ? state.selected.geometry.index.count / 3 : pos.count / 3) : 0,
      material: (material && (material.name || material.type)) || '',
      path: state.currentEntry ? state.currentEntry.path : '',
    });
  };
  $('selCopy').onclick = async () => {
    if (!state.selected) return;
    const text = selectionText({
      name: state.selected.name || 'Unnamed mesh', type: 'Mesh',
      path: state.currentEntry ? state.currentEntry.path : '',
    });
    try {
      await navigator.clipboard.writeText(text);
      $('selNote').textContent = 'Copied.';
      setTimeout(() => { $('selNote').textContent = ''; }, 2000);
    } catch (e) {
      $('selNote').textContent = 'Could not copy automatically.';
    }
  };

  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshModels(false); });
}

function wirePointer() {
  let down = null;
  const canvas = renderer.domElement;
  canvas.addEventListener('pointerdown', (e) => {
    down = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
  });
  canvas.addEventListener('pointerup', (e) => {
    if (!down || down.id !== e.pointerId) return;
    const dist = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    const dt = performance.now() - down.t;
    down = null;
    if (dist < 7 && dt < 750) pick(e);
  });
  canvas.addEventListener('pointercancel', () => { down = null; });
}

// ── models from this device: viewed locally, never uploaded ───────────────────────────────────────
const LOCAL_FORMATS = { glb: 'glb', gltf: 'gltf', obj: 'obj', stl: 'stl' };
let localUrl = '';
function openLocal(file) {
  if (!file) return;
  const ext = String(file.name.split('.').pop() || '').toLowerCase();
  if (!LOCAL_FORMATS[ext]) { showError('Choose a GLB, glTF, OBJ or STL file.', null); return; }
  if (file.size > 200 * 1024 * 1024) { showError('This file is larger than 200 MB.', null); return; }
  if (localUrl) URL.revokeObjectURL(localUrl);
  localUrl = URL.createObjectURL(file);
  const entry = { path: file.name, name: file.name, format: LOCAL_FORMATS[ext], size: file.size, previewable: true, local: true, url: localUrl };
  if (!state.models.some((m) => m.local && m.name === file.name)) state.models = [entry, ...state.models.filter((m) => !m.local)];
  else state.models = state.models.map((m) => (m.local && m.name === file.name ? entry : m));
  if (ext === 'gltf') $('loadHint').textContent = 'A .gltf file that references separate .bin or texture files cannot load them from here; use a single .glb.';
  loadModel(entry);
}
function wireLocal() {
  const input = $('localFile');
  $('btnOpen').onclick = () => input.click();
  $('emptyOpen').onclick = () => input.click();
  input.onchange = () => { openLocal(input.files[0]); input.value = ''; };
  const stage = $('stage');
  stage.addEventListener('dragover', (e) => { if ([...(e.dataTransfer || {}).types || []].includes('Files')) { e.preventDefault(); stage.classList.add('drop'); } });
  stage.addEventListener('dragleave', (e) => { if (!stage.contains(e.relatedTarget)) stage.classList.remove('drop'); });
  stage.addEventListener('drop', (e) => { e.preventDefault(); stage.classList.remove('drop'); openLocal(e.dataTransfer && e.dataTransfer.files[0]); });
}

async function init() {
  if (!/^\d+$/.test(taskId)) {
    if (!initScene()) return;
    wireToolbar(); wireLocal();
    // Without a task there is no workspace to rescan and no agent conversation to add a selection to.
    for (const id of ['btnRefresh', 'emptyRefresh', 'selInsert']) $(id).hidden = true;
    setEmpty('Open a 3D model', 'Open a GLB, glTF, OBJ or STL file from this device (it stays in your browser), or open 3D Studio from an Agent task to see the models the agent creates.');
    setSub('Local files · nothing is uploaded');
    $('blenderState').textContent = 'Files you open here are viewed in the browser only.';
    $('count').textContent = '';
    return;
  }
  if (!initScene()) return;
  wireToolbar(); wireLocal();
  if (isEmbed) {
    document.body.classList.add('embed');
    $('home').title = 'Close preview';
    $('home').onclick = (e) => {
      e.preventDefault();
      window.parent.postMessage({ type: 'vellur-3d-close' }, location.origin);
    };
  }
  setSub('Reading workspace…');
  await refreshModels(false);
  schedulePoll();
}

init();
