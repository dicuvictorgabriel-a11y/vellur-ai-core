import * as THREE from './vendor/three.module.min.js';

const DEMO_PROMPT_1 = 'Create a minimalist futuristic room with frosted glass panels, polished concrete flooring, a chrome sphere, and soft studio lighting.';
const DEMO_PROMPT_2 = 'Make the glass more transparent, move the sphere to the left, and adjust the camera.';

export function createSceneStudio(ctx) {
  const { $, api, getThree, onInstallCustomRoot, onSelectMesh, setSub, setStatusNote } = ctx;
  const studio = {
    scenes: [],
    presets: {},
    activeRecord: null,
    dirty: false,
    lightsGroup: null,
    meshById: new Map(),
    activeTab: 'scenes',
  };

  function switchTab(tab) {
    studio.activeTab = tab;
    const map = { scenes: ['tabScenes', 'paneScenes'], objects: ['tabObjects', 'paneObjects'], files: ['tabFiles', 'paneFiles'] };
    for (const [k, [btnId, paneId]] of Object.entries(map)) {
      const on = k === tab;
      $(btnId).classList.toggle('on', on);
      $(btnId).setAttribute('aria-selected', String(on));
      $(paneId).hidden = !on;
    }
  }

  function buildGeometry(obj) {
    const p = obj.params || {};
    const kind = String(obj.primitive || 'box').toLowerCase();
    if (kind === 'sphere') return new THREE.SphereGeometry(Math.max(0.02, Number(p.radius || 0.5)), 40, 28);
    if (kind === 'plane') {
      const g = new THREE.PlaneGeometry(Math.max(0.05, Number(p.width || 4)), Math.max(0.05, Number(p.depth || 4)));
      g.rotateX(-Math.PI / 2);
      return g;
    }
    if (kind === 'cylinder' || kind === 'capsule') {
      return new THREE.CylinderGeometry(
        Math.max(0.02, Number(p.radiusTop ?? p.radius ?? 0.4)),
        Math.max(0.02, Number(p.radiusBottom ?? p.radius ?? 0.4)),
        Math.max(0.04, Number(p.height || 1)),
        36,
      );
    }
    if (kind === 'cone') {
      return new THREE.ConeGeometry(Math.max(0.02, Number(p.radiusBottom ?? p.radius ?? 0.45)), Math.max(0.04, Number(p.height || 1)), 36);
    }
    if (kind === 'torus') {
      return new THREE.TorusGeometry(Math.max(0.05, Number(p.radius || 0.55)), Math.max(0.01, Number(p.tube || 0.16)), 24, 48);
    }
    return new THREE.BoxGeometry(
      Math.max(0.02, Number(p.width || 1)),
      Math.max(0.02, Number(p.height || 1)),
      Math.max(0.02, Number(p.depth || 1)),
    );
  }

  function buildMaterial(matDef, wire) {
    const m = matDef || {};
    const trans = Math.max(0, Math.min(1, Number(m.transmission ?? 0)));
    const opac = Math.max(0.06, Math.min(1, Number(m.opacity ?? 1)));
    const mat = new THREE.MeshPhysicalMaterial({
      name: m.name || m.preset || 'PBR Material',
      color: new THREE.Color(m.color || '#cfd6df'),
      metalness: Math.max(0, Math.min(1, Number(m.metalness ?? 0.08))),
      roughness: Math.max(0.02, Math.min(1, Number(m.roughness ?? 0.4))),
      transmission: trans,
      ior: Math.max(1.0, Math.min(2.4, Number(m.ior ?? 1.5))),
      thickness: trans > 0.05 ? 0.18 : 0,
      opacity: opac,
      transparent: trans > 0.01 || opac < 0.995,
      envMapIntensity: 1.05,
      wireframe: !!wire,
      side: trans > 0.01 || opac < 0.995 ? THREE.DoubleSide : THREE.FrontSide,
    });
    const emInt = Math.max(0, Number(m.emissive_intensity ?? 0));
    if (emInt > 0.01) {
      mat.emissive = new THREE.Color(m.emissive || '#ffffff');
      mat.emissiveIntensity = emInt;
    }
    return mat;
  }

  function renderRecord(rec, preserveCamera = false) {
    studio.activeRecord = rec;
    studio.dirty = false;
    studio.meshById.clear();
    const { scene: threeScene, camera, controls, renderer, grid, getWire } = getThree();
    const sc = rec.scene;

    if (sc.environment) {
      threeScene.background = new THREE.Color(sc.environment.background || '#10151c');
      renderer.toneMappingExposure = Number(sc.environment.exposure || 1.08);
      if (grid) {
        grid.visible = !!sc.environment.grid;
        $('btnGrid').setAttribute('aria-pressed', String(grid.visible));
      }
    }

    if (studio.lightsGroup) threeScene.remove(studio.lightsGroup);
    studio.lightsGroup = new THREE.Group();
    studio.lightsGroup.name = 'StudioLights';
    (sc.lights || []).forEach((l) => {
      let light;
      const col = new THREE.Color(l.color || '#ffffff');
      const inten = Number(l.intensity ?? 1.5);
      if (l.type === 'point') {
        light = new THREE.PointLight(col, inten, 28);
      } else if (l.type === 'hemisphere' || l.type === 'ambient') {
        light = new THREE.HemisphereLight(col, new THREE.Color('#191f28'), inten);
      } else {
        light = new THREE.DirectionalLight(col, inten);
        light.castShadow = !!l.cast_shadow;
        if (light.castShadow) {
          light.shadow.mapSize.set(1024, 1024);
          light.shadow.bias = -0.0004;
        }
      }
      const lp = l.position || [3, 5, 4];
      light.position.set(Number(lp[0] || 0), Number(lp[1] || 4), Number(lp[2] || 0));
      studio.lightsGroup.add(light);
    });
    threeScene.add(studio.lightsGroup);

    const group = new THREE.Group();
    group.name = sc.title || rec.title || 'StudioScene';
    (sc.objects || []).forEach((obj) => {
      if (obj.visible === false) return;
      const mesh = new THREE.Mesh(buildGeometry(obj), buildMaterial(obj.material, getWire()));
      mesh.name = obj.name || obj.id;
      mesh.userData.sceneObjectId = obj.id;
      const pos = obj.position || [0, 0, 0];
      const rot = obj.rotation || [0, 0, 0];
      const scl = obj.scale || [1, 1, 1];
      mesh.position.set(Number(pos[0] || 0), Number(pos[1] || 0), Number(pos[2] || 0));
      mesh.rotation.set(Number(rot[0] || 0), Number(rot[1] || 0), Number(rot[2] || 0));
      mesh.scale.set(Number(scl[0] || 1), Number(scl[1] || 1), Number(scl[2] || 1));
      const trans = Number((obj.material || {}).transmission || 0);
      mesh.castShadow = trans < 0.85;
      mesh.receiveShadow = true;
      studio.meshById.set(obj.id, mesh);
      group.add(mesh);
    });

    onInstallCustomRoot(group, {
      title: rec.title,
      subtitle: `Scene #${rec.id} · v${rec.version} · ${(sc.objects || []).length} objects · GLB ready`,
      exportUrl: rec.export_glb_url,
    });

    if (!preserveCamera && sc.camera) {
      const cp = sc.camera.position || [3.6, 2.15, 4.4];
      const ct = sc.camera.target || [0, 1, 0];
      camera.fov = Number(sc.camera.fov || 38);
      camera.position.set(Number(cp[0]), Number(cp[1]), Number(cp[2]));
      controls.target.set(Number(ct[0]), Number(ct[1]), Number(ct[2]));
      camera.updateProjectionMatrix();
      controls.update();
    }

    $('btnSaveScene').hidden = false;
    $('btnExportGlb').hidden = false;
    $('btnApplyPrompt').textContent = 'Edit Scene';
    updateCameraReadout();
    renderSceneList();
    renderObjectList();
    renderHistory();

    try {
      const url = new URL(location.href);
      url.searchParams.set('scene', String(rec.id));
      history.replaceState(null, '', url);
    } catch (e) { /* embedded sandbox fallback */ }
  }

  function updateCameraReadout() {
    if (!studio.activeRecord) { $('camReadout').textContent = 'No active scene'; return; }
    const cam = studio.activeRecord.scene.camera || {};
    const p = (cam.position || [0, 0, 0]).map((n) => Number(n).toFixed(2)).join(', ');
    const t = (cam.target || [0, 0, 0]).map((n) => Number(n).toFixed(2)).join(', ');
    $('camReadout').textContent = `Pos [${p}] · Target [${t}] · FOV ${cam.fov || 38}°`;
  }

  function renderSceneList() {
    const box = $('sceneList');
    box.innerHTML = '';
    $('sceneCount').textContent = studio.scenes.length ? `${studio.scenes.length}` : '0';
    if (!studio.scenes.length) {
      const empty = document.createElement('div');
      empty.className = 'item';
      empty.style.cursor = 'default';
      empty.innerHTML = '<span class="badge">3D</span><span class="meta"><b>No saved scenes yet</b><small>Use a prompt below or click Demo Room.</small></span><span></span>';
      box.appendChild(empty);
      return;
    }
    studio.scenes.forEach((s) => {
      const b = document.createElement('button');
      b.type = 'button';
      const isOn = studio.activeRecord && studio.activeRecord.id === s.id;
      b.className = 'item' + (isOn ? ' on' : '');
      b.innerHTML = '<span class="badge"></span><span class="meta"><b></b><small></small></span><span class="state"></span>';
      b.querySelector('.badge').textContent = `v${s.version}`;
      b.querySelector('.meta b').textContent = s.title;
      b.querySelector('.meta small').textContent = `${s.object_count} objs · ${s.prompt_count} edits`;
      b.querySelector('.state').textContent = isOn && studio.dirty ? 'unsaved' : '';
      b.onclick = () => openScene(s.id);
      box.appendChild(b);
    });
  }

  function renderHistory() {
    const rec = studio.activeRecord;
    const hist = (rec && Array.isArray(rec.history)) ? rec.history : [];
    $('historyBox').hidden = !hist.length;
    $('histCount').textContent = `${hist.length}`;
    const list = $('historyList');
    list.innerHTML = '';
    hist.slice().reverse().forEach((h) => {
      const d = document.createElement('div');
      d.className = 'hist-item';
      const title = document.createElement('b');
      title.textContent = h.prompt || h.kind || 'Edit';
      const sub = document.createElement('small');
      sub.textContent = h.summary || '';
      d.append(title, sub);
      list.appendChild(d);
    });
  }

  function renderObjectList(selectedId) {
    const box = $('objList');
    box.innerHTML = '';
    const objs = (studio.activeRecord && studio.activeRecord.scene && studio.activeRecord.scene.objects) || [];
    $('objCount').textContent = `${objs.length}`;
    objs.forEach((o) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'item' + (selectedId === o.id ? ' on' : '');
      b.innerHTML = '<span class="badge"></span><span class="meta"><b></b><small></small></span><span class="state"></span>';
      b.querySelector('.badge').textContent = String(o.primitive || 'box').slice(0, 4).toUpperCase();
      b.querySelector('.meta b').textContent = o.name || o.id;
      const preset = (o.material && o.material.preset) || 'pbr';
      b.querySelector('.meta small').textContent = `${preset} · [${(o.position || [0, 0, 0]).map((n) => Number(n).toFixed(1)).join(', ')}]`;
      b.onclick = () => {
        const mesh = studio.meshById.get(o.id);
        if (mesh) onSelectMesh(mesh);
      };
      box.appendChild(b);
    });
  }

  async function refreshScenes() {
    try {
      const data = await api('studio3d/scenes');
      studio.scenes = Array.isArray(data.scenes) ? data.scenes : [];
      studio.presets = data.presets || {};
      renderSceneList();
      return studio.scenes;
    } catch (e) {
      return [];
    }
  }

  async function openScene(sceneId) {
    try {
      const rec = await api(`studio3d/scenes/${encodeURIComponent(sceneId)}`);
      renderRecord(rec, false);
      setStatusNote(`Loaded "${rec.title}" (v${rec.version}).`);
      return rec;
    } catch (e) {
      setStatusNote('Could not load scene: ' + e.message);
      return null;
    }
  }

  async function createFromPrompt(prompt, preset) {
    setStatusNote('Generating 3D scene…');
    try {
      const body = preset
        ? { preset }
        : { prompt, use_ai: !!$('useAiToggle').checked, model: $('aiModel').value || 'vellur-coder' };
      const rec = await api('studio3d/scenes', { method: 'POST', body: JSON.stringify(body) });
      await refreshScenes();
      renderRecord(rec, false);
      const last = (rec.history || [])[rec.history.length - 1];
      setStatusNote((last && last.summary) || `Created "${rec.title}".`);
      return rec;
    } catch (e) {
      setStatusNote('Error: ' + e.message);
      return null;
    }
  }

  async function mutateByPrompt(prompt) {
    if (!studio.activeRecord) return createFromPrompt(prompt);
    setStatusNote('Applying prompt edit to scene…');
    try {
      const rec = await api(`studio3d/scenes/${studio.activeRecord.id}/prompt`, {
        method: 'POST',
        body: JSON.stringify({ prompt, use_ai: !!$('useAiToggle').checked, model: $('aiModel').value || 'vellur-coder' }),
      });
      await refreshScenes();
      renderRecord(rec, false);
      const last = (rec.history || [])[rec.history.length - 1];
      setStatusNote((last && last.summary) || `Updated to v${rec.version}.`);
      return rec;
    } catch (e) {
      setStatusNote('Error: ' + e.message);
      return null;
    }
  }

  async function saveActiveScene(note = 'Saved from 3D Studio inspector') {
    if (!studio.activeRecord) return null;
    setStatusNote('Saving scene…');
    try {
      const rec = await api(`studio3d/scenes/${studio.activeRecord.id}`, {
        method: 'PUT',
        body: JSON.stringify({ title: studio.activeRecord.title, scene: studio.activeRecord.scene, note }),
      });
      await refreshScenes();
      renderRecord(rec, true);
      setStatusNote(`Saved v${rec.version}.`);
      return rec;
    } catch (e) {
      setStatusNote('Save failed: ' + e.message);
      return null;
    }
  }

  function findSceneObjectForMesh(mesh) {
    if (!mesh || !studio.activeRecord) return null;
    const oid = mesh.userData && mesh.userData.sceneObjectId;
    if (!oid) return null;
    return (studio.activeRecord.scene.objects || []).find((o) => o.id === oid) || null;
  }

  function populateInspector(mesh) {
    const obj = findSceneObjectForMesh(mesh);
    const box = $('inspectorFields');
    if (!obj) {
      box.hidden = true;
      renderObjectList(null);
      return;
    }
    box.hidden = false;
    renderObjectList(obj.id);
    const pos = obj.position || [0, 0, 0];
    const scl = obj.scale || [1, 1, 1];
    const rot = obj.rotation || [0, 0, 0];
    const mat = obj.material || {};
    $('insPosX').value = Number(pos[0] || 0).toFixed(2);
    $('insPosY').value = Number(pos[1] || 0).toFixed(2);
    $('insPosZ').value = Number(pos[2] || 0).toFixed(2);
    $('insScale').value = Number(scl[0] || 1).toFixed(2);
    $('insRotY').value = Math.round(THREE.MathUtils.radToDeg(Number(rot[1] || 0)));
    $('insColor').value = /^#[0-9a-fA-F]{6}$/.test(mat.color || '') ? mat.color : '#dceeff';
    $('insPreset').value = ['frosted_glass', 'clear_glass', 'polished_concrete', 'chrome', 'brushed_metal', 'matte_plaster', 'warm_oak', 'emissive_soft'].includes(mat.preset) ? mat.preset : 'custom';
    const setSlider = (id, valId, v) => {
      const n = Number(v);
      $(id).value = String(n);
      $(valId).textContent = n.toFixed(2);
    };
    setSlider('insRough', 'valRough', mat.roughness ?? 0.35);
    setSlider('insMetal', 'valMetal', mat.metalness ?? 0.05);
    setSlider('insTrans', 'valTrans', mat.transmission ?? 0.0);
    setSlider('insOpac', 'valOpac', mat.opacity ?? 1.0);
  }

  function applyLiveObjectEdits(selectedMesh) {
    const obj = findSceneObjectForMesh(selectedMesh);
    if (!obj || !selectedMesh) return;
    const px = parseFloat($('insPosX').value) || 0;
    const py = parseFloat($('insPosY').value) || 0;
    const pz = parseFloat($('insPosZ').value) || 0;
    const sc = Math.max(0.05, parseFloat($('insScale').value) || 1);
    const ry = THREE.MathUtils.degToRad(parseFloat($('insRotY').value) || 0);

    obj.position = [px, py, pz];
    obj.scale = [sc, sc, sc];
    obj.rotation = [obj.rotation ? obj.rotation[0] : 0, ry, obj.rotation ? obj.rotation[2] : 0];
    selectedMesh.position.set(px, py, pz);
    selectedMesh.scale.set(sc, sc, sc);
    selectedMesh.rotation.y = ry;

    const m = obj.material || (obj.material = {});
    m.color = $('insColor').value;
    m.roughness = parseFloat($('insRough').value);
    m.metalness = parseFloat($('insMetal').value);
    m.transmission = parseFloat($('insTrans').value);
    m.opacity = parseFloat($('insOpac').value);
    $('valRough').textContent = m.roughness.toFixed(2);
    $('valMetal').textContent = m.metalness.toFixed(2);
    $('valTrans').textContent = m.transmission.toFixed(2);
    $('valOpac').textContent = m.opacity.toFixed(2);

    const { getWire } = getThree();
    if (selectedMesh.material) selectedMesh.material.dispose();
    selectedMesh.material = buildMaterial(m, getWire());
    studio.dirty = true;
    setSub(`Scene #${studio.activeRecord.id} · unsaved edits`);
    renderSceneList();
    renderObjectList(obj.id);
  }

  async function saveSnapshotAndDownload(dataUrl) {
    if (studio.activeRecord) {
      try {
        const snap = await api(`studio3d/scenes/${studio.activeRecord.id}/snapshots`, {
          method: 'POST',
          body: JSON.stringify({ data_url: dataUrl, note: `Viewport render v${studio.activeRecord.version}` }),
        });
        setStatusNote(`Saved PNG snapshot (${snap.width}x${snap.height}) to scene #${studio.activeRecord.id}.`);
      } catch (e) {
        setStatusNote('Downloaded PNG locally (' + e.message + ')');
      }
    }
  }

  function wireEngine() {
    // Engine choice persists per device (cookie; no localStorage) and drives the hidden AI toggle the API code reads.
    const sel = $('aiModel'), saved = (document.cookie.match(/(?:^|; )nova_3d_engine=([^;]*)/) || [])[1] || '';
    if ([...sel.options].some(o => o.value === saved)) sel.value = saved;
    const sync = () => { $('useAiToggle').checked = !!sel.value; document.cookie = 'nova_3d_engine=' + sel.value + '; path=/autopilot; max-age=31536000; SameSite=Lax';
      $('btnApplyPrompt').textContent = sel.value ? 'Edit with AI' : 'Edit scene'; };
    sel.addEventListener('change', sync); sync();
  }
  function wireStudioUI(getSelectedMesh, clearSel) {
    wireEngine();
    $('tabScenes').onclick = () => switchTab('scenes');
    $('tabObjects').onclick = () => switchTab('objects');
    $('tabFiles').onclick = () => switchTab('files');

    $('btnDemoRoom').onclick = () => createFromPrompt(DEMO_PROMPT_1);
    $('emptyDemo').onclick = () => createFromPrompt(DEMO_PROMPT_1);
    $('btnNewScene').onclick = () => createFromPrompt('', 'futuristic_room');
    $('btnSaveScene').onclick = () => saveActiveScene('Saved from toolbar');
    $('insSaveBtn').onclick = () => saveActiveScene('Saved object & material edits');
    $('btnExportGlb').onclick = () => {
      if (!studio.activeRecord) return;
      const a = document.createElement('a');
      a.href = studio.activeRecord.export_glb_url;
      a.download = '';
      a.click();
      setStatusNote(`Exporting binary GLB for "${studio.activeRecord.title}"…`);
    };

    $('chipPrompt1').onclick = () => {
      $('promptInput').value = DEMO_PROMPT_1;
      createFromPrompt(DEMO_PROMPT_1);
    };
    $('chipPrompt2').onclick = () => {
      $('promptInput').value = DEMO_PROMPT_2;
      mutateByPrompt(DEMO_PROMPT_2);
    };

    $('promptForm').onsubmit = (e) => {
      e.preventDefault();
      const val = $('promptInput').value.trim();
      if (!val) return;
      mutateByPrompt(val);
    };
    $('btnCreatePrompt').onclick = () => {
      const val = $('promptInput').value.trim();
      if (!val) createFromPrompt('', 'futuristic_room');
      else createFromPrompt(val);
    };

    ['insPosX', 'insPosY', 'insPosZ', 'insScale', 'insRotY', 'insColor', 'insRough', 'insMetal', 'insTrans', 'insOpac'].forEach((id) => {
      $(id).addEventListener('input', () => {
        if (id !== 'insPreset') $('insPreset').value = 'custom';
        applyLiveObjectEdits(getSelectedMesh());
      });
    });

    $('insPreset').addEventListener('change', () => {
      const pName = $('insPreset').value;
      const preset = studio.presets[pName];
      const mesh = getSelectedMesh();
      const obj = findSceneObjectForMesh(mesh);
      if (!preset || !obj) return;
      obj.material = { ...obj.material, ...preset, preset: pName };
      populateInspector(mesh);
      applyLiveObjectEdits(mesh);
    });

    $('insDelObj').onclick = () => {
      const mesh = getSelectedMesh();
      const obj = findSceneObjectForMesh(mesh);
      if (!obj || !studio.activeRecord) return;
      studio.activeRecord.scene.objects = studio.activeRecord.scene.objects.filter((o) => o.id !== obj.id);
      clearSel();
      renderRecord(studio.activeRecord, true);
      studio.dirty = true;
      saveActiveScene(`Removed ${obj.name || obj.id}`);
    };

    $('addPrimSelect').onchange = () => {
      const val = $('addPrimSelect').value;
      $('addPrimSelect').value = '';
      if (!val || !studio.activeRecord) return;
      const isGlass = val === 'glass';
      const prim = isGlass ? 'box' : val;
      const id = `${val}_${Date.now().toString(36).slice(-4)}`;
      const newObj = {
        id,
        name: isGlass ? 'Glass Panel' : `Studio ${prim[0].toUpperCase() + prim.slice(1)}`,
        role: isGlass ? 'glass' : 'prop',
        primitive: prim,
        visible: true,
        position: [0, isGlass ? 1.2 : 0.65, 0.3],
        rotation: [0, 0, 0],
        scale: [1, 1, 1],
        params: isGlass ? { width: 1.4, height: 2.3, depth: 0.06 } : (prim === 'sphere' ? { radius: 0.5 } : { width: 0.9, height: 0.9, depth: 0.9, radius: 0.45, tube: 0.16 }),
        material: isGlass
          ? { name: 'Frosted Glass', preset: 'frosted_glass', color: '#dceeff', metalness: 0.04, roughness: 0.36, opacity: 0.6, transmission: 0.75, ior: 1.5 }
          : { name: 'Brushed Metal', preset: 'brushed_metal', color: '#c4ccd6', metalness: 0.85, roughness: 0.25, opacity: 1.0, transmission: 0.0, ior: 1.6 },
      };
      studio.activeRecord.scene.objects.push(newObj);
      renderRecord(studio.activeRecord, true);
      const addedMesh = studio.meshById.get(id);
      if (addedMesh) onSelectMesh(addedMesh);
      saveActiveScene(`Added ${newObj.name}`);
    };

    $('btnCaptureCam').onclick = () => {
      if (!studio.activeRecord) return;
      const { camera, controls } = getThree();
      studio.activeRecord.scene.camera = {
        position: [Number(camera.position.x.toFixed(3)), Number(camera.position.y.toFixed(3)), Number(camera.position.z.toFixed(3))],
        target: [Number(controls.target.x.toFixed(3)), Number(controls.target.y.toFixed(3)), Number(controls.target.z.toFixed(3))],
        fov: Number(camera.fov.toFixed(1)),
      };
      updateCameraReadout();
      saveActiveScene('Captured studio camera angle');
    };
  }

  return {
    switchTab,
    refreshScenes,
    openScene,
    createFromPrompt,
    populateInspector,
    saveSnapshotAndDownload,
    wireStudioUI,
    hasActiveScene: () => !!studio.activeRecord,
    clearActiveScene: () => {
      studio.activeRecord = null;
      $('btnSaveScene').hidden = true;
      $('btnExportGlb').hidden = true;
      $('inspectorFields').hidden = true;
      $('btnApplyPrompt').textContent = 'Create Scene';
    },
  };
}
