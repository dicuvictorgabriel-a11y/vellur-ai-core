# 3D Studio — handover for UI / UX agents

Read this before touching any 3D Studio file. Everything marked **Verified** was exercised on 2026-11-16 against the
real app in the preview (`PORT=4173 bash dev/preview.sh`, simulated services, headless Chromium with SwiftShader WebGL)
and/or by `tests/test_3d.py`. Nothing here was verified on the production server or with a real model.

## A. Purpose
**Vision:** users and NOVA agents create, manipulate, inspect, refine and export 3D work in the browser.
**Today:** a three.js viewer and a small scene editor:
1. *Scene mode* — procedural scenes made of primitives (box, sphere, cylinder, cone, plane, torus, capsule) with PBR
   materials, lights and a camera, stored in SQLite, edited by prompt or by hand, exported as GLB, captured as PNG.
2. *Task-model mode* — views GLB/glTF/OBJ/STL files that a coding-agent task produced in its workspace (and `.blend`
   files converted to GLB when Blender is installed in the agent container); also embedded in the dashboard preview.
3. *Local mode* — opens a GLB/glTF/OBJ/STL from the user's device in the browser only (no upload).

## B. Architecture map (verified file inventory)
| File | Role |
|---|---|
| `autopilot/app/studio3d.py` | Scene model (`normalize_scene`, `normalize_material`, `MATERIAL_PRESETS`, `PRIMITIVES`), procedural generators (`futuristic_room_template`, `generate_scene_from_prompt`, `mutate_scene_from_prompt`), optional LLM path (`maybe_llm_scene`), `Studio3DStore` (SQLite + snapshot files), API (`register`). |
| `autopilot/app/glb_builder.py` | Pure-Python mesh builders, `scene_to_glb(scene)` (GLB 2.0 bytes) and `render_scene_preview_png(scene)` — a simple software rasterizer (480×320) used when the browser does not send a capture. |
| `autopilot/app/static/3d/studio3d.html` / `.css` | The 3D Studio page (own stylesheet; see E). |
| `autopilot/app/static/3d/studio3d.js` | Viewer shell: three.js renderer, `OrbitControls`, loaders (GLTF/OBJ/STL), `RoomEnvironment`, picking/selection, toolbar, task-model polling, embed messaging, local file open. |
| `autopilot/app/static/3d/scene_studio.js` | Scene editor: tabs (Scenes / Scene Graph / Files), builds three.js meshes from the scene JSON, inspector, prompt dock, save, snapshot. Imported by `studio3d.js` (`createSceneStudio(ctx)`). |
| `autopilot/app/static/3d/vendor/` | three.js (module build + addons). License in `LICENSE-three.txt`. |
| `autopilot/app/api.py` | Serves the page `GET /autopilot/3d/` and assets `GET /autopilot/3d/{asset}` (login required, path confined, strict CSP in `STUDIO3D_HEADERS`). Calls `studio3d.register(...)`. |
| `autopilot/app/workspace.py` | Task-model endpoints (`/agent/tasks/{tid}/models`, `/asset/{path}`, `/blend-export`), proxied to the runner. |
| `agent/runner.py` | `model_files()` discovers and validates model files in a task workspace; `export_blend()` converts `.blend` → GLB with Blender if present (`agent/blender_export.py`). |
| `autopilot/app/db.py` | Tables `studio_scenes` (id, title, description, scene_json, history_json, version, task_id, created, updated) and `studio_snapshots` (id hex24, scene_id, mime, width, height, size, sha256, note, created). PNG files in `<data_dir>/studio3d_snapshots/`. |
| `autopilot/app/static/dashboard.html` | Embeds 3D Studio in the agent preview pane (`SANDBOX_3D`, `#pv3D`, `?task=<id>&embed=1&model=<path>`), Tools page link, listens for `vellur-3d-ready` / `vellur-3d-selection` messages. |

Runtime: three.js in the browser (WebGL required), Python stdlib only on the server (no Blender needed for scene mode).

## C. API and data contracts (scene mode; all under `/autopilot/api/`, owner login; writes need `X-Vellur-Request: 1`)
| Method + path | Body → result |
|---|---|
| `GET studio3d/scenes` | → `{scenes:[summary…], presets:{id:{name,color,metalness,roughness,opacity,transmission}}, primitives:[…]}` |
| `POST studio3d/scenes` | `{title?, description?, prompt?, preset?:"futuristic_room", scene?, task_id?, use_ai?, model?}` → full scene record |
| `GET studio3d/scenes/{id}` | → `{id,title,description,version,task_id,created,updated,scene,history,snapshots}` |
| `PUT studio3d/scenes/{id}` | `{scene, title?, description?, note?}` → record, version + 1, history entry `inspector_save` |
| `POST studio3d/scenes/{id}/prompt` | `{prompt (2–4000 chars), use_ai?, model?: vellur-coder|vellur-max|vellur-chat}` → record, version + 1, history entry `prompt_edit` |
| `DELETE studio3d/scenes/{id}` | → `{ok, deleted}` |
| `GET studio3d/scenes/{id}/export.glb` | → `model/gltf-binary` attachment |
| `POST studio3d/scenes/{id}/snapshots` | `{data_url?: "data:image/png;base64,…", note?}` (no data_url → server rasterizer) → `{id,url,width,height,…}` (≤ 10 MB, PNG validated) |
| `GET studio3d/snapshots/{id}` | → PNG |

Scene JSON (always passed through `normalize_scene`; values are clamped, unknown fields dropped):
```json
{"title":"…","environment":{"background":"#11161d","exposure":1.08,"grid":true,"studio_preset":"soft_studio"},
 "camera":{"position":[3.6,2.15,4.4],"target":[0,0.95,0],"fov":38},
 "lights":[{"id":"soft_key","name":"…","type":"directional|point|spot|ambient|hemisphere","color":"#fff8f0","intensity":2.1,"position":[4.2,6.2,4.6],"cast_shadow":true}],
 "objects":[{"id":"chrome_sphere","name":"Chrome Sphere","role":"prop","primitive":"sphere","visible":true,
   "position":[0,0.68,0],"rotation":[0,0,0],"scale":[1,1,1],"params":{"radius":0.68},
   "material":{"preset":"chrome","color":"#f4f7fb","metalness":1,"roughness":0.04,"opacity":1,"transmission":0,"ior":2.1,"emissive":"#000000","emissive_intensity":0}}]}
```
Limits: 12 lights, 80 objects. Material presets: see `MATERIAL_PRESETS` in `studio3d.py`.

## D. Functional inventory
| Capability | Status | Evidence |
|---|---|---|
| Open page, WebGL viewport, orbit/zoom/pan | **Working** (desktop) | Page loads, canvas renders (1148×842), no JS errors; `OrbitControls` in `studio3d.js` |
| Create scene from preset / prompt (procedural) | **Working** | `POST studio3d/scenes` with the demo prompt → 9 objects; `test_3d.py::…two_prompt…` |
| Edit scene by prompt (procedural keyword rules) | **Working, limited** | "more transparent / move the sphere left / adjust camera" → v2 with the expected changes. Only phrases covered by `mutate_scene_from_prompt` do anything. |
| **"AI Assist" (LLM scene synthesis)** | **Working (fixed 2026-11-16)** | `maybe_llm_scene` sends the scene-compiler prompt through `router.handle("POST","chat/completions",…)` with model `vellur-coder` (automatic routing, budget and cost recording apply); history `mode: "ai"`. If the model is unavailable the edit falls back to the procedural rules and the history summary says why ("AI Assist unavailable: …"). `tests/test_3d.py::test_ai_assist_uses_the_router_and_explains_fallbacks`. Not verified with a real provider. |
| Save / reopen / version history | **Working** | PUT/prompt bump `version`; reopen returned v2 with 9 objects; UI shows "Scene #4 · v2 · 9 objects". |
| Object selection + inspector (position, scale, rotation Y, colour, preset, roughness, metalness, transmission, opacity) | **Working in code, partly verified** | `populateInspector` / `applyLiveObjectEdits` in `scene_studio.js`; selection exercised in the UI, field edits not re-verified on 2026-11-16. |
| Add primitive / delete object | **Not yet verified** | `#addPrimSelect`, `#insDelObj` exist. |
| Transform gizmos (drag handles) | **Not implemented** | No `TransformControls`; transforms are numeric inspector fields only. |
| Lights / camera editing in UI | **Partial** | Camera can be captured from the view (`#btnCaptureCam`); lights are only changed by prompts or JSON. |
| GLB export | **Working** | 200 `model/gltf-binary`, 34 KB for the demo room. |
| Snapshot (browser capture) | **Working in code** | `btnShot` → `renderer.domElement.toDataURL` → `POST …/snapshots`. |
| Snapshot (server rasterizer) | **Working** | No `data_url` → 480×320 PNG from `render_scene_preview_png` (simplified shading, not a faithful render). |
| Task-model viewer (GLB/glTF/OBJ/STL), selection messages to the dashboard | **Working** (tests) | `test_3d.py` model discovery/serving/confinement tests. |
| `.blend` → GLB | **Optional** | Only when Blender is installed in the agent container (`test_blend_export_*`). |
| Local file open (no upload) | **Working** (test) | `test_3d_studio_opens_local_models_without_uploading`. |
| Phone layout | **Improved (2026-11-16)** | Toolbar scrolls horizontally, prompt dock fits 320–430 px (engine, prompt, Edit/New reachable). The side panel (scenes/graph) is still desktop-first. |
| Engine selector (AI model for scenes) | **Working** | `#aiModel`: Built-in rules (free) / AI · Coder / AI · Max / AI · Chat; sent as `model` (whitelisted, unknown → Coder), remembered in cookie `nova_3d_engine`, drives the hidden `#useAiToggle`. Test `test_ai_assist_uses_the_model_chosen_in_the_engine_selector`. |
| NOVA themes / Carbon in the 3D UI | **Not implemented** | The page has its own stylesheet and only reads dark/light from the `vellur_theme` cookie. |

### Agent (AI) loop — what an agent can do today
1. Create a scene — **yes via HTTP API** (owner session), but **no NOVA agent tool calls it**: the OpenCode agent has
   no 3D Studio tool; `agent/` contains no studio3d client.
2–3. Add/modify objects, materials, lights, camera — via `PUT` (full JSON) or `…/prompt` (procedural phrases only).
4. Save — yes (every write is a new version).
5. Rendered preview — yes (`…/snapshots`), server raster is approximate; browser capture needs a real WebGL page.
6. Inspect the visual result — **no automated path**: nothing feeds snapshots to a vision model for 3D scenes.
7–8. Identify and correct discrepancies in a loop — **not implemented**.
The coding agent can produce model *files* in a task workspace, which 3D Studio then displays (task-model mode).

## E. Design-system integration (rules for UI work)
- 3D Studio is part of NOVA: use the NOVA foundations — Carbon tokens (`/autopilot/carbon/carbon-tokens.css`), the
  ten themes (`/autopilot/carbon/nova-themes.css` + the `nova_theme` cookie, same boot logic as `dashboard.html`), the
  Soft Industrial geometry and material tokens (docs/DESIGN.md), IBM Plex. **Today none of this is wired into
  `studio3d.html`**; adopting it is a UI task, not an architecture change.
- It is a creative tool: viewport-first layout, tool rail, inspector and scene graph sized for 3D work — not dashboard
  cards.
- **The viewport is not themed.** Scene background, lights, materials and renders come only from the scene JSON
  (`environment.background`, lights, materials). Verified: switching the NOVA theme (Obsidian → Porcelain) left the
  rendered scene identical. Keep it that way: theme tokens may colour chrome (panels, toolbars, HUD), never the scene.

## F. UI ↔ functionality contract (preserve these)
- Element ids used by the scripts (renaming breaks behaviour): toolbar `btnNewScene btnSaveScene btnExportGlb btnOpen
  localFile btnPanel btnFit btnGrid btnWire btnSpin btnAnim btnShot btnFull`; tabs `tabScenes tabObjects tabFiles` and
  panes `paneScenes paneObjects paneFiles`; lists `sceneList historyList objList list`; inspector `insPosX insPosY
  insPosZ insScale insRotY insColor insPreset insRough insMetal insTrans insOpac insSaveBtn insDelObj`; prompt dock
  `promptForm promptInput btnApplyPrompt btnCreatePrompt aiModel useAiToggle(hidden) chipPrompt1 chipPrompt2 promptStatus`; stage
  `canvas empty loading error updatePill hud selection`. Find them with `grep -n "\$('" autopilot/app/static/3d/*.js`.
- `scene_studio.js` receives its dependencies through `createSceneStudio({ $, api, getThree, onInstallCustomRoot,
  onSelectMesh, setSub, setStatusNote })` — keep that seam; put new UI behind it rather than reaching into globals.
- URL parameters: `?task=<id>` (task-model mode), `&model=<path>`, `&embed=1` (inside the dashboard: compact chrome).
- `postMessage` to the parent (same origin only): `{type:"vellur-3d-ready", taskId, model}` and
  `{type:"vellur-3d-selection", taskId, selection}`. The dashboard relies on both.
- All writes go through `api()` in `studio3d.js` (adds `X-Vellur-Request`); scene JSON is normalised server-side — do
  not rely on extra fields surviving a save.
- CSP (`STUDIO3D_HEADERS` in `api.py`): scripts and styles from self only; no CDNs.

## G. Known limitations / technical debt
1. AI Assist was broken until 2026-11-16 (it called a non-existent `router.complete`); fixed, tested with a mocked
   provider only. Model quality for scene JSON is unverified.
2. Procedural prompt editing understands a fixed set of phrases only.
3. No transform gizmos, no light editor, no undo/redo in scene mode.
4. Server preview PNG is an approximation; faithful captures need a browser with WebGL.
5. No agent tool and no vision loop for 3D scenes.
6. Phone layout unusable; 3D UI not on NOVA themes/Carbon.
7. Demo data in the preview contains empty placeholder scenes (0 objects) seeded by `dev/preview_server.py`.

## H. Acceptance checklist for any 3D Studio UI task
- [ ] `bash dev/test.sh` passes (includes `tests/test_3d.py`).
- [ ] In the preview: `/autopilot/3d/` loads with no console errors; the canvas renders.
- [ ] Open a saved scene; select an object; change an inspector field; the right object changes; Save bumps the version;
      reload shows the change.
- [ ] Orbit, zoom, pan, Fit and Grid still work; prompt edit still applies (`chipPrompt2` on the demo room).
- [ ] Export GLB downloads a non-empty `model/gltf-binary`; snapshot returns a PNG URL.
- [ ] Task-model mode: `?task=<id>` lists and opens models; the dashboard's 3D preview still receives selection messages.
- [ ] Switch NOVA theme: chrome may change, the rendered scene must not.
- [ ] Screenshots at 390, 768 and 1440 px captured **and inspected**; phone limitations documented if not fixed.
- [ ] No new CDN/runtime dependency; CSP intact; frozen auth files untouched.
